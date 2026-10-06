"""Browser-test bookkeeping that needs no browser: request log, server sampler, assertions, artifacts.

Everything here is plain Python over small duck-typed objects (a Playwright request has `.url`,
`.method`, `.response()`, `.failure`), so tests/ui/test_harness_support.py runs it without
Playwright or Chromium, and without psutil (the process probe is the only psutil user and is
imported on first use).

The assertion order the suite follows (REPORT.md 5.4), cheapest to flake last:

1. structure: requests in flight per resource, requests after a step (cannot flake on a slow runner);
2. server CPU seconds between two steps (a loaded runner changes this by under 10%);
3. wall-clock ceilings, loose (4 to 5 times what a laptop needs).

There is no retry anywhere, on purpose: a retry turns a wrong budget into a pass.

What blocks (0.1.10.9, the first week of the suite): structure, and console and HTTP problems, fail
a test. Every timing ceiling (wall-clock `Recorder.wall_budget`, server CPU `cpu_budget`, server
memory `memory_budget_of`) is always measured and reported (`BudgetLog`: the terminal summary and
`budgets.json`), and fails a test only when `GIGAI_UI_BUDGETS=enforce`: none of them has been
measured on a CI runner yet. `report` is the default, here and in CI.

A wait that runs out of patience (Playwright's 20 s) gets a `timeout.txt` beside the other
artifacts (`timeout_report`): what was in flight, the page's heartbeat (`Heartbeat`), whether the
server still answered, the console and the server log, so a stalled browser and a slow server are
told apart afterwards.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.parse import urlsplit

#: Jobs page: at least one row, and the count line no longer says "Loading".
LIST_READY_JS = """() => document.querySelectorAll('[data-testid="job-row"]').length > 0
  && !((document.querySelector('[data-role="postings-count"]') || {}).textContent || 'Loading').startsWith('Loading')"""

#: A job page in full: the title, the state line and the pipeline timeline are drawn (a job that is still loading
#: shows only a "Loading job…" heading, and one that is not found has none of the three).
#: 0.1.11: the step timeline is part of the background pipeline's panel, which is hidden while the pipeline is off (the
#: default), so the page is READY at its title and its state.
JOB_PAGE_READY_JS = """() => !!document.querySelector('.job-page .job-title')
  && !!document.querySelector('.job-page [data-role="job-state"]')"""

#: Installed before the page's own scripts: `window.__gigaiCountLines` holds every text the Jobs count line
#: shows, in order (null while the page shown has no count line). A MutationObserver sees every committed
#: change, so a "Loading postings…" that flashes for one frame is in the list.
COUNT_LINE_WATCH_JS = """(() => {
  const seen = [];
  window.__gigaiCountLines = seen;
  const read = () => {
    const node = document.querySelector('[data-role="postings-count"]');
    const text = node ? (node.textContent || '').trim() : null;
    if (!seen.length || seen[seen.length - 1] !== text) seen.push(text);
  };
  new MutationObserver(read).observe(document, {subtree: true, childList: true, characterData: true, attributes: true});
})()"""

#: Chromium's own word for a request that a navigation, a reload or a closed page cut short.
ABORTED = "ERR_ABORTED"


def tid(name: str) -> str:
    return f'[data-testid="{name}"]'


# ---------------------------------------------------------------------------- the real home


class UnsafeHomeError(RuntimeError):
    pass


def real_user_home() -> Path:
    """The account's real home directory, whatever `HOME` currently says."""

    try:
        import pwd

        return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    except (ImportError, KeyError):  # no pwd database (Windows): fall back to the platform's idea
        return Path(os.path.expanduser("~")).resolve()


def refuse_real_home(home: Path | str, *, real_home: Path | None = None, temp_root: Path | None = None, environ: dict[str, str] | None = None) -> Path:
    """Return the resolved `home` only when it is a safe, temporary, synthetic HOME.

    Refuses: the real home, anything inside it (so its `~/.gigai`), a path with a `.gigai`
    component, anything outside the system temp directory, and a `GIGAI_HOME` that points anywhere
    else (it would override the home this harness thinks it is using).
    """

    resolved = Path(home).resolve()
    real = (real_home or real_user_home()).resolve()
    temp = (temp_root or Path(tempfile.gettempdir())).resolve()
    env = os.environ if environ is None else environ
    if resolved == real or real in resolved.parents:
        raise UnsafeHomeError(f"refusing to run: HOME {resolved} is (inside) the real home {real}")
    if ".gigai" in resolved.parts:
        raise UnsafeHomeError(f"refusing to run: HOME {resolved} is a .gigai folder")
    if resolved != temp and temp not in resolved.parents:
        raise UnsafeHomeError(f"refusing to run: HOME {resolved} is not inside the temporary directory {temp}")
    override = env.get("GIGAI_HOME")
    if override and Path(override).resolve() != resolved and resolved not in Path(override).resolve().parents:
        raise UnsafeHomeError(f"refusing to run: GIGAI_HOME={override} points outside the temporary HOME {resolved}")
    return resolved


# ---------------------------------------------------------------------------- measurements


class Measured(float):
    """A number that says what it measured, so `assert x <= limit` fails with a readable line."""

    label: str
    detail: str

    def __new__(cls, value: float, label: str, detail: str = "") -> "Measured":
        item = super().__new__(cls, value)
        item.label, item.detail = label, detail
        return item

    def __repr__(self) -> str:
        shown = f"{int(self)}" if float(self).is_integer() else f"{float(self):.2f}"
        return f"<{self.label} = {shown}{(' (' + self.detail + ')') if self.detail else ''}>"


# ---------------------------------------------------------------------------- budgets

#: The one switch. `report` (the default, also when unset): every timing ceiling (wall-clock, server CPU, server
#: memory) is measured and reported, and none fails a test. `enforce`: a ceiling that is over fails its test.
BUDGETS_ENV = "GIGAI_UI_BUDGETS"
REPORT = "report"
ENFORCE = "enforce"

#: The user-facing wall-clock budgets (REPORT.md 5.4): a click answers in 2 s, a first load in 10 s. A flow's own
#: named constant is one of these unless it says why not.
INTERACTIVE_WALL_SECONDS = 2.0
FIRST_LOAD_WALL_SECONDS = 10.0


def budgets_mode(environ: Mapping[str, str] | None = None) -> str:
    """`report` or `enforce`. Any other value is refused: a misspelt `enforce` would quietly enforce nothing."""

    raw = ((os.environ if environ is None else environ).get(BUDGETS_ENV) or "").strip().lower()
    if raw in ("", REPORT):
        return REPORT
    if raw == ENFORCE:
        return ENFORCE
    raise ValueError(f"{BUDGETS_ENV}={raw!r}: use {REPORT!r} (the default) or {ENFORCE!r}")


def budgets_enforced(environ: Mapping[str, str] | None = None) -> bool:
    return budgets_mode(environ) == ENFORCE


@dataclass(frozen=True)
class BudgetLine:
    test: str
    name: str
    kind: str  # "wall" (wall-clock seconds), "cpu" (server CPU seconds), "rss" (server memory); each blocks only when enforced
    measured: float
    limit: float
    blocking: bool
    unit: str = "s"

    @property
    def over(self) -> bool:
        return self.measured > self.limit

    def line(self) -> str:
        verdict = "ok" if not self.over else ("OVER, FAILED" if self.blocking else "OVER (reported, not enforced)")
        digits = 0 if self.unit == "MB" else 2
        return f"{self.kind:<4} {self.measured:7.{digits}f} {self.unit} of {self.limit:6.{digits}f} {self.unit}  {verdict:<30} {self.name}  [{self.test}]"


class BudgetLog:
    """Every timing ceiling a run measured, with what it measured: the report of the budgets."""

    def __init__(self) -> None:
        self.lines: list[BudgetLine] = []

    def record(self, test: str, name: str, kind: str, measured: float, limit: float, *, blocking: bool, unit: str = "s") -> BudgetLine:
        line = BudgetLine(test, name, kind, float(measured), float(limit), blocking, unit)
        self.lines.append(line)
        return line

    def over(self) -> list[BudgetLine]:
        return [line for line in self.lines if line.over]

    def report(self) -> list[str]:
        """The lines of the terminal summary: every ceiling, then how many were over."""

        if not self.lines:
            return []
        over = self.over()
        reported = [line for line in over if not line.blocking]
        tail = f"{len(self.lines)} ceilings measured, {len(over)} over"
        if reported:
            tail += f" ({len(reported)} reported only: {BUDGETS_ENV}={ENFORCE} makes them failures)"
        return [line.line() for line in self.lines] + [tail]

    def to_json(self, *, enforced: bool, run: Mapping[str, object] | None = None) -> dict[str, object]:
        """`budgets.json`. `run`: what the run itself took (seconds, home builds), for the CI job's summary."""

        data: dict[str, object] = {
            "mode": ENFORCE if enforced else REPORT,
            "ceilings": len(self.lines),
            "over": len(self.over()),
            "budgets": [
                {"test": line.test, "name": line.name, "kind": line.kind, "measured": round(line.measured, 3), "limit": line.limit, "unit": line.unit, "over": line.over, "blocking": line.blocking}
                for line in self.lines
            ],
        }
        if run:
            data["run"] = dict(run)
        return data


# ---------------------------------------------------------------------------- request bookkeeping


@dataclass
class Seen:
    method: str
    path: str  # path + query
    resource: str  # path only
    started: float
    ended: float | None = None
    status: int | None = None
    failure: str | None = None
    dropped: bool = False  # ended by `drop_open`, not by an event
    request: object = None  # kept alive: the bookkeeping is keyed by id(request), which Python reuses after a collection

    def line(self) -> str:
        outcome = "in flight" if self.ended is None else (f"{self.status}" if self.status is not None else (self.failure or "ended"))
        took = "" if self.ended is None else f" {self.ended - self.started:.2f}s"
        return f"{self.method} {self.path} -> {outcome}{took} (at {self.started:.2f}s)"


@dataclass
class Network:
    """Every `/api/` request of one page (when it started and ended, how), plus console and HTTP problems."""

    clock: Callable[[], float] = time.monotonic
    zero: float = field(default=0.0)
    lock: threading.Lock = field(default_factory=threading.Lock)
    seen: dict[int, Seen] = field(default_factory=dict)
    order: list[Seen] = field(default_factory=list)
    console: list[str] = field(default_factory=list)  # every console line, for the artifacts
    console_errors: list[str] = field(default_factory=list)
    http_errors: list[str] = field(default_factory=list)  # any same-page response with status >= 400

    def __post_init__(self) -> None:
        if not self.zero:
            self.zero = self.clock()

    def now(self) -> float:
        return self.clock() - self.zero

    def attach(self, page) -> None:
        page.on("request", self.started)
        page.on("requestfinished", self.finished)
        page.on("requestfailed", self.failed)
        page.on("response", self.responded)
        page.on("console", self.console_message)
        page.on("pageerror", lambda error: self.console_errors.append(f"page error: {error}"))

    def console_message(self, message) -> None:
        self.console.append(f"{message.type}: {message.text}")
        if message.type == "error":
            self.console_errors.append(f"console error: {message.text}")

    def responded(self, response) -> None:
        if response.status >= 400:
            url = urlsplit(response.url)
            self.http_errors.append(f"HTTP {response.status} {response.request.method} {url.path}")

    def started(self, request) -> None:
        url = urlsplit(request.url)
        if not url.path.startswith("/api/"):
            return
        item = Seen(request.method, url.path + (f"?{url.query}" if url.query else ""), url.path, self.now(), request=request)
        with self.lock:
            self.seen[id(request)] = item
            self.order.append(item)

    def finished(self, request) -> None:
        item = self.seen.get(id(request))
        if item is None:
            return
        response = request.response()
        with self.lock:
            item.ended = self.now()
            item.status = response.status if response is not None else None

    def failed(self, request) -> None:
        item = self.seen.get(id(request))
        if item is None:
            return
        with self.lock:
            item.ended = self.now()
            item.failure = request.failure or "failed"

    def drop_open(self, why: str) -> None:
        """A reload or a closed page drops its open requests WITHOUT an event: end them here, or they count as in flight for ever."""

        now = self.now()
        with self.lock:
            for item in self.order:
                if item.ended is None:
                    item.ended, item.failure, item.dropped = now, why, True

    def in_flight(self, at: float | None = None) -> list[Seen]:
        moment = self.now() if at is None else at
        with self.lock:
            return [item for item in self.order if item.started <= moment and (item.ended is None or item.ended > moment)]

    def peak_in_flight(self, resource: str, first: int = 0) -> int:
        """The most requests of `resource` open at once, among the requests from index `first` on."""

        with self.lock:
            items = [item for item in self.order[first:] if item.resource == resource]
        marks: list[tuple[float, int]] = []
        for item in items:
            marks.append((item.started, 1))
            marks.append((item.ended if item.ended is not None else float("inf"), -1))
        level = peak = 0
        for _at, step in sorted(marks, key=lambda mark: (mark[0], mark[1])):  # an end at the same instant frees its slot first
            level += step
            peak = max(peak, level)
        return peak

    def problems(self) -> list[str]:
        """What fails a test on its own: console and page errors, HTTP >= 400, a request that failed for a real reason."""

        found = list(self.console_errors) + list(self.http_errors)
        with self.lock:
            found += [f"request failed: {item.method} {item.path}: {item.failure}" for item in self.order if item.failure and not item.dropped and ABORTED not in item.failure]
        return found


# ---------------------------------------------------------------------------- the server's process


class ProcessTreeProbe:
    """CPU seconds (cumulative, children included, exited children kept) and RSS of the server's process tree."""

    def __init__(self, pid: int) -> None:
        import psutil

        self._psutil = psutil
        self.pid = pid
        self._cpu: dict[int, float] = {}
        self._lock = threading.Lock()

    def read(self) -> tuple[float, int]:
        """(cpu seconds so far, RSS bytes now). A process that is gone counts for what it used before."""

        psutil = self._psutil
        try:
            root = psutil.Process(self.pid)
            processes = [root, *root.children(recursive=True)]
        except psutil.NoSuchProcess:
            processes = []
        rss = 0
        with self._lock:
            for process in processes:
                try:
                    times = process.cpu_times()
                    self._cpu[process.pid] = max(self._cpu.get(process.pid, 0.0), times.user + times.system)
                    rss += process.memory_info().rss
                except psutil.Error:
                    continue
            return sum(self._cpu.values()), rss

    def cpu_seconds(self) -> float:
        return self.read()[0]


class Sampler(threading.Thread):
    """A few times a second: server CPU seconds and RSS, and the page's in-flight request count (for the artifacts and the RSS peak)."""

    HEADER = "t_s,server_cpu_seconds,server_rss_mb,in_flight,oldest_in_flight_s"

    def __init__(self, probe_read: Callable[[], tuple[float, int]], network: Network, interval: float = 0.25) -> None:
        super().__init__(daemon=True)
        self.probe_read, self.network, self.interval = probe_read, network, interval
        self.stop_flag = threading.Event()
        self.peak_rss_bytes = 0
        self.rows: list[str] = [self.HEADER]
        self.points: list[tuple[float, float, int]] = []  # (page clock, server CPU seconds so far, RSS bytes)

    def sample(self) -> None:
        cpu, rss = self.probe_read()
        self.peak_rss_bytes = max(self.peak_rss_bytes, rss)
        now = self.network.now()
        flying = self.network.in_flight(now)
        oldest = max((now - item.started for item in flying), default=0.0)
        self.rows.append(f"{now:.2f},{cpu:.2f},{rss / 1048576:.0f},{len(flying)},{oldest:.1f}")
        self.points.append((now, cpu, rss))

    def recent(self, seconds: float = 10.0) -> tuple[float, float, float]:
        """Over the samples of the last `seconds`: (the span they cover in s, server CPU seconds used in it, RSS in MB at the end)."""

        if not self.points:
            return 0.0, 0.0, 0.0
        last_at, last_cpu, last_rss = self.points[-1]
        first_at, first_cpu, _rss = next(point for point in self.points if point[0] >= last_at - seconds)
        return last_at - first_at, last_cpu - first_cpu, last_rss / 1048576

    def run(self) -> None:
        while not self.stop_flag.is_set():
            self.sample()
            self.stop_flag.wait(self.interval)

    def stop(self) -> None:
        self.stop_flag.set()
        if self.is_alive():
            self.join(timeout=5)

    def csv(self) -> str:
        return "\n".join(self.rows) + "\n"


# ---------------------------------------------------------------------------- steps and assertions


@dataclass(frozen=True)
class Mark:
    name: str
    at: float  # seconds since the page's zero
    cpu: float  # server CPU seconds so far
    first_request: int  # index of the first request that starts after this mark


class Recorder:
    """Named steps over one page's network log and the server's CPU, and the assertions on them.

    `ui.step("opened")` marks a boundary. The measurements take two step names (`"now"` is
    the present), and return a `Measured` number to compare with `<=`:

        assert ui.requests_after("opened") <= 2
        assert ui.server_cpu_seconds_between("start", "loaded") <= 5
        assert ui.wall_seconds_between("start", "loaded") <= 10   # last, and loose
    """

    def __init__(self, network: Network, cpu_seconds: Callable[[], float], *, budgets: BudgetLog | None = None, test: str = "") -> None:
        self.network, self._cpu = network, cpu_seconds
        self.budgets = budgets if budgets is not None else BudgetLog()
        self.test = test
        self.marks: dict[str, Mark] = {}
        self.step("start")

    def step(self, name: str) -> Mark:
        if name == "now" or name in self.marks:
            raise ValueError(f"step name {name!r} is reserved or already used")
        with self.network.lock:
            first = len(self.network.order)
        mark = Mark(name, self.network.now(), self._cpu(), first)
        self.marks[name] = mark
        return mark

    def _mark(self, name: str) -> Mark:
        if name == "now":
            with self.network.lock:
                first = len(self.network.order)
            return Mark("now", self.network.now(), self._cpu(), first)
        try:
            return self.marks[name]
        except KeyError:
            raise KeyError(f"no step named {name!r}; steps so far: {', '.join(self.marks)}") from None

    def requests_after(self, step: str, resource: str | None = None) -> Measured:
        """How many `/api/` requests started after `step` (of one `resource` path, when given)."""

        first = self._mark(step).first_request
        with self.network.lock:
            items = [item for item in self.network.order[first:] if resource is None or item.resource == resource]
        listing = "; ".join(item.line() for item in items[:8]) + ("; ..." if len(items) > 8 else "")
        return Measured(len(items), f"requests after {step!r}" + (f" for {resource}" if resource else ""), listing)

    def server_cpu_seconds_between(self, first: str, last: str = "now") -> Measured:
        a, b = self._mark(first), self._mark(last)
        return Measured(b.cpu - a.cpu, f"server CPU seconds {first!r} -> {last!r}")

    def wall_seconds_between(self, first: str, last: str = "now") -> Measured:
        a, b = self._mark(first), self._mark(last)
        return Measured(b.at - a.at, f"wall seconds {first!r} -> {last!r}")

    def requests_between(self, first: str, last: str = "now", resource: str | None = None) -> Measured:
        """How many `/api/` requests started from step `first` up to step `last` (of one `resource` path, when given)."""

        a, b = self._mark(first).first_request, self._mark(last).first_request
        with self.network.lock:
            items = [item for item in self.network.order[a:b] if resource is None or item.resource == resource]
        listing = "; ".join(item.line() for item in items[:8]) + ("; ..." if len(items) > 8 else "")
        return Measured(len(items), f"requests {first!r} -> {last!r}" + (f" for {resource}" if resource else ""), listing)

    def writes_after(self, step: str) -> list[str]:
        """Every request after `step` that is not a GET: what the page asked the server to change."""

        first = self._mark(step).first_request
        with self.network.lock:
            return [f"{item.method} {item.resource}" for item in self.network.order[first:] if item.method != "GET"]

    def _step_detail(self, first: str, last: str) -> str:
        """What a timing failure prints: wall, server CPU and the requests of the step, so "the runner was slow" and
        "the server did more work" are told apart."""

        a, b = self._mark(first), self._mark(last)
        with self.network.lock:
            items = [item.line() for item in self.network.order[a.first_request:b.first_request]]
        listing = "\n    ".join(items[:12]) + ("\n    ..." if len(items) > 12 else "")
        return f"wall {b.at - a.at:.2f} s, server CPU {b.cpu - a.cpu:.2f} s, {len(items)} request(s)" + (f":\n    {listing}" if items else "")

    def wall_budget(self, name: str, limit: float, first: str, last: str = "now") -> Measured:
        """A wall-clock ceiling on a step: always measured and reported; a failure only when GIGAI_UI_BUDGETS=enforce."""

        value = self.wall_seconds_between(first, last)
        self.wall_budget_of(name, limit, float(value), detail=f"{first!r} -> {last!r}; {self._step_detail(first, last)}")
        return value

    def wall_budget_of(self, name: str, limit: float, seconds: float, *, detail: str = "") -> None:
        """The same rule for a wall time the test measured itself (something outside this page's steps, like a second tab)."""

        enforced = budgets_enforced()
        self.budgets.record(self.test, name, "wall", seconds, limit, blocking=enforced)
        if seconds > limit and enforced:
            raise AssertionError(f"wall-clock budget {name!r}: {seconds:.2f} s is over {limit} s" + (f" ({detail})" if detail else ""))

    def cpu_budget(self, name: str, limit: float, first: str, last: str = "now") -> Measured:
        """A ceiling on the server's CPU seconds over a step: always measured and reported; a failure only when GIGAI_UI_BUDGETS=enforce."""

        value = self.server_cpu_seconds_between(first, last)
        enforced = budgets_enforced()
        self.budgets.record(self.test, name, "cpu", value, limit, blocking=enforced)
        if value > limit and enforced:
            raise AssertionError(f"server CPU budget {name!r}: {float(value):.2f} s is over {limit} s ({first!r} -> {last!r}; {self._step_detail(first, last)})")
        return value

    def memory_budget_of(self, name: str, limit_mb: float, peak_mb: float) -> float:
        """A ceiling on the server's peak resident memory, in MB: always recorded; a failure only when GIGAI_UI_BUDGETS=enforce."""

        enforced = budgets_enforced()
        self.budgets.record(self.test, name, "rss", peak_mb, limit_mb, blocking=enforced, unit="MB")
        if peak_mb > limit_mb and enforced:
            raise AssertionError(f"server memory budget {name!r}: the peak was {peak_mb:.0f} MB, over {limit_mb:.0f} MB")
        return peak_mb

    def no_more_than_one_in_flight(self, resource: str, since: str = "start") -> None:
        """Fail when two requests of `resource` were open at the same time (counting from `since`)."""

        first = self._mark(since).first_request
        peak = self.network.peak_in_flight(resource, first)
        if peak > 1:
            with self.network.lock:
                items = [item.line() for item in self.network.order[first:] if item.resource == resource]
            raise AssertionError(f"{peak} requests for {resource} were in flight at once (at most 1):\n  " + "\n  ".join(items))

    def problems(self) -> list[str]:
        return self.network.problems()

    def assert_clean(self) -> None:
        """No console error, no page error, no HTTP >= 400, no failed request."""

        found = self.problems()
        if found:
            raise AssertionError(f"{len(found)} problem(s) in the browser:\n  " + "\n  ".join(found))


# ---------------------------------------------------------------------------- a wait that ran out of patience

#: What is asked right after a wait timed out. Nothing is asked of the PAGE: with tracing on (always, here) a
#: Playwright call into a page whose script is stuck does not time out, it waits for the page (measured: a
#: `wait_for_function` with a 1 s timeout came back after the page's 6 s busy loop). The page's state is read
#: from its heartbeat instead, which needs no answer from it.
PAGE_SCREENSHOT = "the page gives a screenshot"
SERVER_HEALTH = "the server answers /api/health"

#: The page's heartbeat: every half second its own timer tells the harness the time and how many frames it has
#: drawn (one frame is asked for per beat). Beats that stop: the page's script is not running. Beats that go on
#: with a frame count that stands still: the script runs and the browser does not draw.
HEARTBEAT_SECONDS = 0.5
HEARTBEAT_BINDING = "__gigaiBeat"
#: A page that missed this many seconds of beats (or frames) is called silent in the report.
STALE_SECONDS = 3.0
#: Installed before the page's own scripts. `window.__gigaiHeartbeat` is the page's own count, for a test to wait on.
HEARTBEAT_JS = """(() => {
  if (window.top !== window) return;
  const state = (window.__gigaiHeartbeat = {count: 0, frames: 0});
  setInterval(() => {
    state.count += 1;
    requestAnimationFrame(() => { state.frames += 1; });
    try { Promise.resolve(window.%s(Date.now(), state.frames)).catch(() => {}); } catch (error) { /* no binding yet */ }
  }, %d);
})()""" % (HEARTBEAT_BINDING, int(HEARTBEAT_SECONDS * 1000))

_TIMEOUT_TEXT = re.compile(r"Timeout \d+ ?ms exceeded|\bTimeoutError\b")


def is_timeout(error: BaseException | None) -> bool:
    """A wait ran out of patience: a `TimeoutError` (Playwright's or the standard one), or a failure that names one.

    The flows that collect their failed checks, and `wait_for_jobs_list`, raise an AssertionError that keeps
    the timeout's type name or Playwright's "Timeout 20000ms exceeded" in its message.
    """

    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if any(base.__name__ == "TimeoutError" for base in type(error).__mro__) or _TIMEOUT_TEXT.search(str(error)):
            return True
        error = error.__cause__ or error.__context__
    return False


@dataclass(frozen=True)
class Probe:
    """One question asked right after a wait timed out, and whether it was answered in time."""

    name: str
    answered: bool
    detail: str
    side: str = "page"  # "page" or "server"

    def line(self) -> str:
        return f"{self.name}: {self.detail}"


@dataclass(frozen=True)
class Pulse:
    """What the page's heartbeat says at one moment (seconds; None: nothing to go by)."""

    beats: int  # beats in the window
    script_silent: float | None  # since the last beat
    frames_silent: float | None  # since the frame count last moved
    longest_gap: float  # between two beats in the window (the moment itself closes the last gap)
    gap_ended: float | None  # how long before the moment the longest gap ended

    @property
    def script_stalled(self) -> bool:
        return self.script_silent is not None and self.script_silent > STALE_SECONDS

    @property
    def drawing_stalled(self) -> bool:
        return not self.script_stalled and self.frames_silent is not None and self.frames_silent > STALE_SECONDS

    def lines(self) -> list[str]:
        if not self.beats:
            return ["no beat was received (the page never ran its heartbeat, or the harness never heard it)"]
        found = [f"{self.beats} beat(s) received; the last one {self.script_silent:.1f} s before the failure"]
        found.append("no frame was counted" if self.frames_silent is None else f"the frame count last moved {self.frames_silent:.1f} s before the failure")
        if self.longest_gap > STALE_SECONDS:
            found.append(f"the longest silence was {self.longest_gap:.1f} s" + ("" if not self.gap_ended else f", ended {self.gap_ended:.1f} s before the failure"))
        return found


class Heartbeat:
    """The beats one page sent (`HEARTBEAT_JS` through the `HEARTBEAT_BINDING` binding): its own clock and its frame count."""

    def __init__(self, keep: int = 1200) -> None:
        self.beats: list[tuple[float, int]] = []  # (the page's clock in epoch seconds, frames counted so far)
        self.keep = keep

    def beat(self, at_ms: float, frames: int) -> None:
        self.beats.append((float(at_ms) / 1000.0, int(frames)))
        del self.beats[: -self.keep]

    def pulse(self, at: float, window: float = 60.0) -> Pulse:
        """The heartbeat as of `at` (epoch seconds), over the `window` before it."""

        beats = [beat for beat in self.beats if at - window <= beat[0] <= at]
        if not beats:
            return Pulse(0, None, None, 0.0, None)
        moved_at: float | None = None
        for (_before_at, before), (now_at, now) in zip(beats, beats[1:]):
            if now != before:
                moved_at = now_at
        marks = [beat[0] for beat in beats] + [at]
        gap, ended = max((later - earlier, later) for earlier, later in zip(marks, marks[1:]))
        return Pulse(len(beats), at - beats[-1][0], None if moved_at is None else at - moved_at, gap, at - ended)


def machine_load() -> str:
    try:
        one, five, fifteen = os.getloadavg()
    except (AttributeError, OSError):  # no load average on this platform
        return f"{os.cpu_count()} CPUs (no load average here)"
    return f"load average {one:.1f}, {five:.1f}, {fifteen:.1f} on {os.cpu_count()} CPUs"


def timeout_reading(open_requests: list[tuple[Seen, float]], probes: list[Probe], pulse: Pulse | None = None) -> str:
    """One line: what the numbers point at. A guess, and said to be one; the lines under it are the evidence."""

    silent_server = [probe.name for probe in probes if probe.side == "server" and not probe.answered]
    # The heartbeat first: a slow server never stops it, and a page whose script is stopped cannot finish a
    # request the server answered long ago (it stays "in flight" on the page).
    held = f" ({len(open_requests)} request(s) still open on the page, which a stopped page cannot finish: the server log says whether they were answered)" if open_requests else ""
    if pulse is not None and not pulse.beats:
        return f"the page sent no heartbeat at all: its script never ran, or stopped within its first half second (the browser, not the server){held}."
    if pulse is not None and pulse.script_stalled:
        return f"the page's script had not run for {pulse.script_silent:.1f} s: the browser stalled, not the server{held}."
    if open_requests:
        oldest = max(age for _item, age in open_requests)
        reading = f"the page was waiting for the server: {len(open_requests)} request(s) open, the oldest for {oldest:.1f} s"
        return reading + (" (and the server did not answer when asked directly)." if silent_server else ".")
    if silent_server:
        return "nothing was in flight and the server did not answer when asked directly: the server is stuck or gone."
    if pulse is not None and pulse.drawing_stalled:
        return f"nothing was in flight, the page's script ran, and no frame was drawn for {pulse.frames_silent:.1f} s: the browser stopped drawing, not the server."
    reading = "nothing was in flight and the page was alive: it waited for something the page never showed (a selector or a state)"
    if pulse is not None and pulse.longest_gap > STALE_SECONDS:
        reading += f"; the page had been silent for {pulse.longest_gap:.1f} s before"
    return reading + "."


def timeout_report(
    *, recorder: Recorder, sampler: Sampler | None, server_log: Path | str | None, error: BaseException | str, probes: list[Probe],
    pulse: Pulse | None = None, load: str | None = None, recent_seconds: float = 10.0, at: float | None = None,
) -> str:
    """`timeout.txt`: what was true when a wait ran out of patience (`at` on the page's clock; now, when not given).

    Call it BEFORE the page is closed (closing ends the open requests). No retry follows: the test has failed;
    this only makes "the browser stalled", "the server was slow" and "the page never showed it" tell apart.
    """

    network = recorder.network
    now = network.now() if at is None else at
    open_requests = [(item, now - item.started) for item in network.in_flight(now)]
    with network.lock:
        ended = [item.line() for item in network.order if item.ended is not None and item.ended <= now][-10:]
    lines = [
        f"A wait ran out of patience. What the harness saw right after ({now:.1f} s on the page's clock):",
        "",
        f"reading (a guess from the lines below): {timeout_reading(open_requests, probes, pulse)}",
        "",
        "the failure",
        *(f"    {line}" for line in str(error).splitlines()[:12]),
        "",
        f"requests in flight: {len(open_requests)}",
        *(f"    {item.line()}, open for {age:.1f} s" for item, age in open_requests),
        f"the last {len(ended)} request(s) that ended",
        *(f"    {line}" for line in ended),
        "",
    ]
    if pulse is not None:
        lines += [f"the page's heartbeat (its own timer, every {HEARTBEAT_SECONDS} s; the last 60 s)", *(f"    {line}" for line in pulse.lines())]
    lines += ["asked right after the failure", *(f"    {probe.line()}" for probe in probes)]
    if sampler is not None:
        span, cpu, rss_mb = sampler.recent(recent_seconds)
        lines.append(f"the server's process over the last {recent_seconds:.1f} s of samples: {cpu:.2f} CPU s in {span:.1f} s, {rss_mb:.0f} MB")
    lines.append(f"the machine: {load if load is not None else machine_load()}")
    console = list(network.console)
    lines += ["", f"console: the last {min(len(console), 30)} of {len(console)} line(s)", *(f"    {line}" for line in console[-30:])]
    lines += ["", "server log: the last 60 lines", *(f"    {line}" for line in tail_text(server_log, 60).splitlines())]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------- artifacts


def safe_name(nodeid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", nodeid).strip("_")[:120] or "test"


def tail_text(path: Path | str | None, lines: int = 200) -> str:
    if not path:
        return "(no server log path)\n"
    try:
        return "\n".join(Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]) + "\n"
    except OSError as error:
        return f"(server log unreadable: {error})\n"


def write_artifacts(folder: Path, *, recorder: Recorder, sampler: Sampler | None, server_log: Path | str | None, reason: str) -> list[str]:
    """Write what a person needs to see why a test failed. Returns the file names written (screenshot and trace are the caller's)."""

    folder.mkdir(parents=True, exist_ok=True)
    network = recorder.network
    steps = [{"step": mark.name, "at_s": round(mark.at, 2), "server_cpu_seconds": round(mark.cpu, 2), "requests_before": mark.first_request} for mark in recorder.marks.values()]
    with network.lock:
        requests = [
            {"method": item.method, "path": item.path, "started_s": round(item.started, 3), "seconds": None if item.ended is None else round(item.ended - item.started, 3), "status": item.status, "failure": item.failure}
            for item in network.order
        ]
    files = {
        "reason.txt": reason.rstrip() + "\n",
        "problems.txt": "\n".join(recorder.problems()) + "\n",
        "console.txt": "\n".join(network.console) + "\n",
        "requests.json": json.dumps({"steps": steps, "requests": requests}, indent=2),
        "server-log-tail.txt": tail_text(server_log),
    }
    if sampler is not None:
        files["samples.csv"] = sampler.csv()
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    return sorted(files)

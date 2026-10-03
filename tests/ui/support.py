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
"""

from __future__ import annotations

from collections.abc import Callable
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

    def sample(self) -> None:
        cpu, rss = self.probe_read()
        self.peak_rss_bytes = max(self.peak_rss_bytes, rss)
        now = self.network.now()
        flying = self.network.in_flight(now)
        oldest = max((now - item.started for item in flying), default=0.0)
        self.rows.append(f"{now:.2f},{cpu:.2f},{rss / 1048576:.0f},{len(flying)},{oldest:.1f}")

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

    def __init__(self, network: Network, cpu_seconds: Callable[[], float]) -> None:
        self.network, self._cpu = network, cpu_seconds
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

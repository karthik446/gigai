"""0.1.10.11 SGATE: THE WRITE TIMING GATE. Every interactive save on a home the size of the operator's, timed and counted.

The bar: no interactive operation over 1 s on the operator-sized home. The
read gate (``test_operator_sized_home.py``) holds the Jobs list to it. Nothing
held a SAVE to it, and the 0.1.10.10 S1 measurement found saves that take
minutes on a home that has used the background pipeline (``PUT /api/setup``
455 s, a rename 118 to 178 s). This gate is that measurement, kept: the S1
fixture, the S1 method, a table.

FIXTURE (``tests/support/operator_home.py``: ``build`` then ``add_write_content``;
synthetic, the fixture model, no request, no real home is read): 290,000
postings x 10,350 companies, 2 profiles, a stored master with both profiles
attached to their selections, ``GIGAI_WRITE_GATE_JOBS`` finished pipeline jobs
per profile (default 100) and a question every assessed job asked.

METHOD (S1 report, sections 1.2 and 7.2). Per operation: a FRESH copy of the
fixture (its read model built and persisted) and a FRESH server process
(``python -m gigai.scout.find_jobs.present_api``, its background threads on).
The first call is the cold one; two more follow when the first took under
5 s. Writes go over HTTP with a browser's ``Origin``; ``resume add`` is the
real command in its own process, the server running beside it. Beside the
seconds: the GIT PROCESSES the operation started (an audit hook in the server
and CLI processes, ``_HOOK``; nothing under ``src/`` is touched). Counts do
not move with the load of the machine; seconds do.

An operation still running after ``GIGAI_WRITE_GATE_STOP_SECONDS`` (default
30; 0 = wait to the end) is stopped and reported as ``> 30 (stopped)`` with
the git processes it had started by then: the gate's own runtime stays
bounded while saves take minutes.

WHAT FAILS
* always: an operation that is refused or answers with an error (the gate
  measured nothing), and a finished operation over its git-process ceiling
  (``Operation.git_ceiling``: generous, set from the measured count; lower
  it when a speed packet lands);
* ``GIGAI_WRITE_GATE=enforce`` only: any operation over 1 s x
  ``GIGAI_TEST_LATENCY_SCALE`` (first call or median), or stopped.
  ``GIGAI_WRITE_GATE=report`` (the default of ``make test-operator-writes``
  and of the release pre-check today) prints the same table and passes.

The table goes to ``build/write-gate/table.md`` (``GIGAI_WRITE_GATE_OUT``) and
stdout; ``table.json`` beside it has every call and the git commands.

RUNS ONLY when ``GIGAI_WRITE_GATE`` is ``report`` or ``enforce``: the fixture's
pipeline jobs cost 5 to 7 s each to build before the pipeline's own speed
fix. With ``GIGAI_OPERATOR_GATE=1`` the full home; without it a tenth of it
and 5 jobs a profile (to work on the gate itself: about 6 minutes).
``GIGAI_WRITE_GATE_HOME=<a folder under the temporary directory>`` keeps the
built fixture there and uses it again on the next run (delete the folder
after a change to what the pipeline stores). The table's own code and the
release pre-check's step are tested in the normal suite (the three tests
below the table).

RUNTIME of ``make test-operator-writes`` (a busy 14-core laptop, 2026-10-04,
before any speed packet): 13 minutes at 25 jobs a profile, which is what the
release pre-check runs until the pipeline's speed fix lands (the fixture 7
minutes, the operations 6). At the default 100 a profile the fixture took 22
minutes to build (15 in S1, on a quieter machine) and the operations 7.5
minutes; on a kept fixture a run is the operations alone.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shutil
import socket
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from tests.support import operator_home
from tests.support.latency import latency_bound, latency_scale

MODE_ENV = "GIGAI_WRITE_GATE"
MODE = (os.environ.get(MODE_ENV) or "").strip().lower()
ENFORCE = MODE == "enforce"
FULL = os.environ.get("GIGAI_OPERATOR_GATE") == "1"
POSTINGS = operator_home.OPERATOR_POSTINGS if FULL else operator_home.OPERATOR_POSTINGS // 10
COMPANIES = operator_home.OPERATOR_COMPANIES if FULL else operator_home.OPERATOR_COMPANIES // 10
JOBS = int(os.environ.get("GIGAI_WRITE_GATE_JOBS") or (operator_home.WRITE_PIPELINE_JOBS if FULL else 5))
ASSESSED_ONLY = operator_home.WRITE_ASSESSED_ONLY if FULL else 3
STOP_SECONDS = float(os.environ.get("GIGAI_WRITE_GATE_STOP_SECONDS") or 30.0)
HOME_ENV = "GIGAI_WRITE_GATE_HOME"
OUT = Path(os.environ.get("GIGAI_WRITE_GATE_OUT") or "build/write-gate")

#: The operator's bar, for every operation.
CEILING_SECONDS = 1.0
#: A first call slower than this is not repeated (the gate's own runtime).
REPEAT_UNDER_SECONDS = 5.0
CALLS = 3
SPAWN_ENV = "GIGAI_WRITE_GATE_SPAWNS"
CONTENT_SUFFIX, WARM_SUFFIX = ".write-content", ".write-warm"

#: ``sitecustomize.py`` for the server and CLI processes: one line per process started, appended to ``$GIGAI_WRITE_GATE_SPAWNS``.
_HOOK = '''
import os, sys

_LOG = os.environ.get("GIGAI_WRITE_GATE_SPAWNS")
_TAKES_VALUE = ("-C", "-c", "--git-dir", "--work-tree", "--namespace")


def _spawned(event, args):
    if event != "subprocess.Popen":
        return
    try:
        argv = [str(part) for part in (args[1] or [args[0]])]
        name = os.path.basename(argv[0])
        command, skip = "", False
        if name == "git":
            for part in argv[1:]:
                if skip:
                    skip = False
                elif part in _TAKES_VALUE:
                    skip = True
                elif not part.startswith("-"):
                    command = part
                    break
        found = os.open(_LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(found, (name + " " + command + "\\n").encode("utf-8", "replace"))
        finally:
            os.close(found)
    except Exception:
        pass


if _LOG:
    sys.addaudithook(_spawned)
'''


# --- the table (pure: tested in the normal suite) ----------------------------------------------


@dataclass
class Measured:
    """One operation's calls: seconds and git processes each, or stopped."""

    number: int
    name: str
    #: Finished pipeline jobs per profile on the fixture this was measured on: a 25-job number is not a 100-job number.
    jobs: int
    seconds: list[float] = field(default_factory=list)
    git: list[int] = field(default_factory=list)
    stopped: bool = False
    git_ceiling: int | None = None
    note: str = ""
    commands: dict[str, int] = field(default_factory=dict)
    #: The operation was refused or answered with an error: nothing was measured.
    error: str = ""

    @property
    def first(self) -> float:
        return self.seconds[0]

    @property
    def median(self) -> float:
        return statistics.median(self.seconds)

    def over_seconds(self, ceiling: float = CEILING_SECONDS) -> bool:
        return not self.error and (self.stopped or self.first > ceiling or self.median > ceiling)

    def over_git(self) -> bool:
        """A FINISHED first call over its git-process ceiling (a stopped one has no final count)."""

        return not self.error and not self.stopped and self.git_ceiling is not None and self.git[0] > self.git_ceiling

    def to_json(self) -> dict[str, object]:
        return {
            "number": self.number, "name": self.name, "pipeline_jobs_per_profile": self.jobs, "seconds": [round(value, 3) for value in self.seconds], "git_processes": self.git,
            "stopped": self.stopped, "git_ceiling": self.git_ceiling, "over_1s": self.over_seconds(), "over_git_ceiling": self.over_git(),
            "git_commands_first_call": self.commands, "note": self.note, "error": self.error,
        }


def render_table(rows: list[Measured], *, header: str, mode: str, stop_seconds: float) -> str:
    """The Markdown table: pasteable into the job summary and the 'ready' message."""

    lines = [
        f"### Write timing gate: {header}", "",
        "| # | Operation | Pipeline jobs per profile | First call, s | Median, s | Calls | Git processes (first call) | Git ceiling | 1 s bar |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        if row.error:
            lines.append(f"| {row.number} | {row.name} | {row.jobs} | **not measured** | - | 0 | - | - | REFUSED |")
            continue
        if row.stopped:
            first, median, git = f"**> {stop_seconds:.0f} (stopped)**", "-", f">= {row.git[0]:,}"
        else:
            first = f"**{row.first:.2f}**" if row.first > CEILING_SECONDS else f"{row.first:.2f}"
            median = f"**{row.median:.2f}**" if row.median > CEILING_SECONDS else f"{row.median:.2f}"
            git = f"**{row.git[0]:,}**" if row.over_git() else f"{row.git[0]:,}"
        ceiling = "-" if row.git_ceiling is None else f"{row.git_ceiling:,}"
        verdict = "OVER" if row.over_seconds() else "ok"
        name = f"{row.name} ({row.note})" if row.note else row.name
        lines.append(f"| {row.number} | {name} | {row.jobs} | {first} | {median} | {len(row.seconds)} | {git} | {ceiling} | {verdict} |")
    over = [row for row in rows if row.over_seconds()]
    counted = [row for row in rows if row.over_git()]
    refused = [row for row in rows if row.error]
    lines += [
        "",
        f"**{len(over)} of {len(rows)} operations are over the 1 s bar**"
        + (f" ({sum(1 for row in rows if row.stopped)} of them stopped, still running, after {stop_seconds:.0f} s)" if any(row.stopped for row in rows) else "")
        + (f"; {len(counted)} over their git-process ceiling (this fails the gate in every mode)" if counted else "")
        + (f"; {len(refused)} refused or answered with an error and not measured (this fails the gate in every mode)" if refused else "")
        + f". Mode: `{mode}`"
        + (" (over the bar fails the gate)." if mode == "enforce" else " (the table is the result; `GIGAI_WRITE_GATE=enforce` makes an operation over the bar fail the gate)."),
        "",
        "Seconds are wall clock as the user waits and move with the load of the machine; git processes do not. "
        "First call = a fresh server process on a fresh copy of the home; median = of the calls made (one when the first took over "
        f"{REPEAT_UNDER_SECONDS:.0f} s).",
    ]
    return "\n".join(lines) + "\n"


def failures(rows: list[Measured], *, enforce: bool, ceiling: float = CEILING_SECONDS) -> list[str]:
    """What fails the gate: a refused operation and a git-process ceiling always, the 1 s bar only when enforced."""

    found = [f"{row.name}: not measured: {row.error}" for row in rows if row.error]
    found += [f"{row.name}: {row.git[0]} git processes, ceiling {row.git_ceiling}" for row in rows if row.over_git()]
    if enforce:
        found += [
            f"{row.name}: " + ("stopped, still running" if row.stopped else f"first call {row.first:.2f} s, median {row.median:.2f} s") + f" (ceiling {ceiling:.2f} s)"
            for row in rows if row.over_seconds(ceiling)
        ]
    return found


def test_table_marks_what_is_over_the_bar_and_report_mode_fails_only_on_counts() -> None:
    rows = [
        Measured(1, "PUT /api/setup", 25, [30.0], [4321], stopped=True, git_ceiling=None),
        Measured(2, "POST /api/master/lines (add)", 25, [2.4, 2.2, 2.3], [199, 190, 190], git_ceiling=400),
        Measured(3, "GET /api/jobs?url=", 25, [0.61, 0.41, 0.4], [52, 50, 50], git_ceiling=40),
        Measured(4, "GET /api/health", 25, [0.01, 0.01, 0.01], [0, 0, 0], git_ceiling=5),
    ]
    refused = Measured(5, "POST /api/assess", 25, error="call 1: HTTP 409 posting_requirements_unreadable")
    table = render_table(rows, header="synthetic", mode="report", stop_seconds=30.0)
    # Every row says how many finished pipeline jobs a profile had: a 25-job number is never read as a 100-job number.
    assert "| 1 | PUT /api/setup | 25 | **> 30 (stopped)** | - | 1 | >= 4,321 | - | OVER |" in table
    assert "| 2 | POST /api/master/lines (add) | 25 | **2.40** | **2.30** | 3 | 199 | 400 | OVER |" in table
    assert "| 3 | GET /api/jobs?url= | 25 | 0.61 | 0.41 | 3 | **52** | 40 | ok |" in table
    assert "**2 of 4 operations are over the 1 s bar** (1 of them stopped, still running, after 30 s); 1 over their git-process ceiling" in table
    # Report mode: only the count ceiling fails. A stopped operation has no final count, so it cannot fail on one.
    assert failures(rows, enforce=False) == ["GET /api/jobs?url=: 52 git processes, ceiling 40"]
    enforced = failures(rows, enforce=True)
    assert len(enforced) == 3 and "PUT /api/setup: stopped, still running" in enforced[1] and "first call 2.40 s" in enforced[2]
    # An operation that was refused measured nothing: its row says so, the others keep theirs, and the gate fails in every mode.
    table = render_table([*rows, refused], header="synthetic", mode="report", stop_seconds=30.0)
    assert "| 5 | POST /api/assess | 25 | **not measured** | - | 0 | - | - | REFUSED |" in table and "| 2 | POST /api/master/lines (add) | 25 |" in table
    assert "; 1 refused or answered with an error and not measured (this fails the gate in every mode)" in table
    assert failures([refused], enforce=False) == ["POST /api/assess: not measured: call 1: HTTP 409 posting_requirements_unreadable"]


def test_table_is_green_when_every_operation_is_under_the_bar() -> None:
    rows = [Measured(1, "PUT /api/setup", 100, [0.8, 0.4, 0.4], [120, 60, 60], git_ceiling=200)]
    assert failures(rows, enforce=True) == []
    assert "**0 of 1 operations are over the 1 s bar**. Mode: `enforce`" in render_table(rows, header="synthetic", mode="enforce", stop_seconds=30.0)
    # The first call alone over the bar is over the bar: the cold save is the one the user waits for.
    assert failures([Measured(1, "x", 100, [1.2, 0.4, 0.4], [10, 10, 10])], enforce=True)


def test_the_release_precheck_runs_the_gate_in_report_mode_and_shows_its_table() -> None:
    root = Path(__file__).resolve().parents[3]
    workflow, makefile = root / ".github" / "workflows" / "pull_request.yaml", root / "Makefile"
    if not workflow.is_file() or not makefile.is_file():
        pytest.skip("workflows and the Makefile are excluded from the offline container build context")
    target = (
        "\ntest-operator-writes:\n\tGIGAI_OPERATOR_GATE=1 GIGAI_WRITE_GATE=$${GIGAI_WRITE_GATE:-report} $(UV) run --locked --extra test python -m pytest -n 0 -q -s \\\n"
        "\t\ttests/behaviors/scout_pipeline/test_operator_sized_writes.py\n"
    )
    assert target in makefile.read_text(encoding="utf-8")  # the full home; report unless the caller says enforce
    job = re.split(r"\n  [A-Za-z0-9_-]+:\n", workflow.read_text(encoding="utf-8").split("\n  operator-home:\n", 1)[1], maxsplit=1)[0]
    code = "\n".join(line for line in job.splitlines() if not line.lstrip().startswith("#"))
    steps = ["      - " + step for step in ("\n" + code.split("\n    steps:\n", 1)[1]).split("\n      - ")[1:]]
    (gate,) = [step for step in steps if "run: make test-operator-writes" in step]
    built = "if: ${{ !cancelled() && steps.home.outcome == 'success' }}"
    # Report mode: an operation over the bar is a row of the table, not a failed pre-check. On the home built once in this job.
    assert f"          {MODE_ENV}: report\n" in gate and "enforce" not in code
    assert built in gate and "timeout-minutes:" in gate and "GIGAI_WRITE_GATE_OUT" not in code  # the table: build/write-gate/table.md
    (build,) = [step for step in steps if "tests.support.operator_home" in step]
    (summary,) = [step for step in steps if "write-gate/table.md >>" in step]
    assert steps.index(build) < steps.index(gate) < steps.index(summary)
    assert 'cat build/write-gate/table.md >> "${GITHUB_STEP_SUMMARY}"' in summary and built in summary
    (upload,) = [step for step in steps if "name: ui-budgets-operator-home-attempt-" in step]
    assert "build/write-gate/table.md\n" in upload and steps.index(gate) < steps.index(upload)


# --- the fixture -------------------------------------------------------------------------------


def _clone(source: Path, dest: Path) -> None:
    """``dest`` becomes a copy of ``source`` (a copy-on-write clone where the file system has one), times kept."""

    if dest.exists():
        shutil.rmtree(dest)
    flags = ["-Rpc"] if sys.platform == "darwin" else ["-Rp", "--reflink=auto"]
    if subprocess.run(["cp", *flags, str(source), str(dest)], capture_output=True, check=False).returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(source, dest, symlinks=True)


def _spawns(path: Path, offset: int = 0) -> tuple[int, Counter[str]]:
    """The size of the spawn log now, and the git commands logged after ``offset``."""

    try:
        with open(path, "rb") as found:
            found.seek(offset)
            text = found.read()
    except FileNotFoundError:
        return offset, Counter()
    lines = [line for line in text.decode("utf-8", "replace").splitlines() if line.startswith("git ")]
    return offset + len(text), Counter(line[4:] or "?" for line in lines)


class _Stopped(Exception):
    pass


@dataclass
class _Fixture:
    built: operator_home.OperatorHome
    content: dict[str, object]
    warm: Path
    hook: Path
    logs: Path

    @property
    def root(self) -> Path:
        return Path(self.built.root)

    @property
    def default(self) -> str:
        return str(self.content["default"])

    @property
    def second(self) -> str:
        return str(self.content["second"])

    def jobs(self, kind: str, profile: str) -> list[str]:
        return list(self.content[kind][profile])  # type: ignore[index]

    def restore(self) -> None:
        _clone(self.warm, self.root)

    def env(self, spawn_log: Path) -> dict[str, str]:
        """The processes' environment: the fixture seams, ``HOME`` inside the synthetic root, and the spawn hook."""

        path = os.pathsep.join(part for part in (str(self.hook), os.environ.get("PYTHONPATH", "")) if part)
        return dict(os.environ, **operator_home.SEAM_ENV, HOME=str(self.root), PYTHONPATH=path, **{SPAWN_ENV: str(spawn_log)})


class _Server:
    """The real server process on the fixture, and the log of the processes it starts."""

    def __init__(self, fixture: _Fixture, name: str) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.spawn_log = fixture.logs / f"{name}.spawns"
        self.spawn_log.unlink(missing_ok=True)
        self.log_path = fixture.logs / f"{name}.log"
        self._log = open(self.log_path, "w", encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "gigai.scout.find_jobs.present_api", "--home", fixture.built.home, "--target", fixture.built.target,
             "--port", str(self.port), "--allow-test-seams"],
            env=fixture.env(self.spawn_log), stdout=self._log, stderr=subprocess.STDOUT,
        )

    def request(self, method: str, path: str, body: object | None = None, *, timeout: float = 120.0) -> tuple[int, object, float]:
        data, headers = None, {"Host": f"127.0.0.1:{self.port}"}
        if method != "GET":
            data = json.dumps(body if body is not None else {}).encode("utf-8")
            headers.update({"Content-Type": "application/json", "Origin": self.base})
        asked = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        started = time.monotonic()
        try:
            with urllib.request.urlopen(asked, timeout=timeout) as response:
                status, payload = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, payload = error.code, error.read()
        except (TimeoutError, urllib.error.URLError) as error:
            if isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError):
                raise _Stopped() from error
            raise
        seconds = time.monotonic() - started
        try:
            return status, (json.loads(payload) if payload else None), seconds
        except ValueError:
            return status, None, seconds

    def get(self, path: str, **kw: float) -> object:
        status, body, _seconds = self.request("GET", path, **kw)
        assert status == 200, (path, status, str(body)[:300])
        return body

    def log(self) -> str:
        self._log.flush()
        return self.log_path.read_text(encoding="utf-8", errors="replace")

    def __enter__(self) -> "_Server":
        deadline = time.monotonic() + 90
        while True:
            try:
                if self.request("GET", "/api/health", timeout=2)[0] == 200:
                    return self
            except (OSError, _Stopped):
                pass
            assert self.process.poll() is None and time.monotonic() < deadline, self.log()[-2000:]
            time.sleep(0.05)

    def stop(self, *, kill: bool = False) -> None:
        if self.process.poll() is None:
            if kill:
                self.process.kill()
            else:
                self.process.terminate()
            try:
                self.process.wait(30)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(30)
        self._log.close()

    def __exit__(self, *exc: object) -> None:
        self.stop(kill=exc[0] is not None)


def _note(line: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] write gate: {line}", flush=True)


@pytest.fixture(scope="module")
def fixture(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Fixture]:
    """The S1 fixture, its read model built and persisted, kept aside as the copy every operation starts from."""

    kept_env = dict(os.environ)
    tmp = tmp_path_factory.mktemp("write-gate")
    hook = tmp / "hook"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text(_HOOK, encoding="utf-8")
    logs = tmp / "logs"
    logs.mkdir()
    try:
        prebuilt = operator_home.prebuilt_root() if FULL else None
        named = (os.environ.get(HOME_ENV) or "").strip()
        root = prebuilt if prebuilt is not None else (Path(named) / "op" if named else tmp / "op")
        operator_home.assert_synthetic_root(root)
        content_at, warm_at = root.with_name(root.name + CONTENT_SUFFIX), root.with_name(root.name + WARM_SUFFIX)
        os.environ["HOME"] = str(root)  # as the builder's own command sets it: nothing under the real home is ever a default
        built, content = _kept_content(content_at, root)
        if built is None or content is None:
            started = time.monotonic()
            if root.exists():
                shutil.rmtree(root)
            built = operator_home.take_prebuilt(postings=POSTINGS, companies=COMPANIES, log=_note) if prebuilt is not None else None
            if built is None:
                root.parent.mkdir(parents=True, exist_ok=True)
                built = operator_home.build(root, postings=POSTINGS, companies=COMPANIES, workers=None if FULL else 4, assessed=operator_home.ASSESSED if FULL else 8, log=_note)
                (root / "operator-home.json").write_text(json.dumps(built.to_json(), indent=2), encoding="utf-8")
            content = operator_home.add_write_content(built, pipeline_jobs=JOBS, assessed_only=ASSESSED_ONLY, log=_note)
            if content_at.exists():
                shutil.rmtree(content_at)
            root.rename(content_at)
            _note(f"fixture built in {time.monotonic() - started:.0f} s (kept at {content_at})")
        else:
            _note(f"fixture taken from {content_at} (built earlier in {content['seconds']} s)")
        found = _Fixture(built=built, content=content, warm=warm_at, hook=hook, logs=logs)
        # The read model, built and persisted by a server that then stops: what a user's home is after its first minute.
        _clone(content_at, root)
        started = time.monotonic()
        with _Server(found, "warm-up") as served:
            deadline = time.monotonic() + latency_bound(300.0)
            while served.request("GET", "/api/postings?limit=1")[0] != 200:
                assert time.monotonic() < deadline, served.log()[-2000:]
                time.sleep(0.2)
            for path in ("/api/postings?limit=50", "/api/new?peek=1", "/api/profiles", "/api/master", "/api/pipeline"):
                served.get(path)
        if warm_at.exists():
            shutil.rmtree(warm_at)
        root.rename(warm_at)
        _note(f"read model built and persisted in {time.monotonic() - started:.0f} s")
    finally:
        os.environ.clear()
        os.environ.update(kept_env)
    yield found


def _kept_content(content_at: Path, root: Path) -> tuple[operator_home.OperatorHome | None, dict[str, object] | None]:
    """A fixture kept by an earlier run at this root, when it is the size asked for; else (None, None)."""

    try:
        built = operator_home.OperatorHome(**json.loads((content_at / "operator-home.json").read_text(encoding="utf-8")))
        content = json.loads((content_at / operator_home.WRITE_CONTENT_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None, None
    same = (
        Path(built.root).resolve() == root.resolve() and (built.postings, built.companies) == (POSTINGS, COMPANIES)
        and len(content["pipeline_jobs"][content["default"]]) == JOBS
    )
    return (built, content) if same else (None, None)


# --- the operations (S1 report 7.2) --------------------------------------------------------------

Ctx = dict[str, object]
Call = tuple[str, str, object]


@dataclass(frozen=True)
class Operation:
    name: str
    #: ``(served, fixture, call index, ctx)`` -> ``(method, path, body)``; the method ``CLI`` runs ``path`` (a list) as ``gigai`` arguments.
    call: Callable[[_Server, _Fixture, int, Ctx], Call]
    #: Reads the page would have made first (the master's revision, a job to open): not timed.
    prep: Callable[[_Server, _Fixture], Ctx] | None = None
    after: Callable[[_Server, Ctx, object], None] | None = None
    #: Says why an answer is not the save that was asked for, or None.
    refused: Callable[[object], str | None] | None = None
    #: Git processes of the first call, when it finishes: fails the gate in every mode. None: no finished count yet.
    git_ceiling: int | None = None
    note: str = ""


def _master(served: _Server, _fixture: _Fixture | None = None) -> Ctx:
    answer = served.get("/api/master")
    master, shown = answer["master"], answer["shown_by"]  # type: ignore[index]
    bullets = [item for item in master["items"] if item["kind"] == "bullet"]
    return {
        "revision": master["revision"], "entry": master["entries"][0]["id"],
        "both": [item for item in bullets if len(shown.get(item["id"], [])) == 2],
        "none": [item for item in bullets if not shown.get(item["id"])],
    }


def _master_with_spare_lines(served: _Server, fixture: _Fixture) -> Ctx:
    """Three lines no profile shows (added here, untimed), one for each retire."""

    ctx = _master(served)
    for text in ("Wrote the runbook for 12 on-call rotations.", "Chaired the weekly incident review for 2 years.", "Kept the build under 9 minutes across 30 repositories."):
        status, body, _seconds = served.request("POST", "/api/master/lines", {"revision": ctx["revision"], "entry_id": ctx["entry"], "text": text})
        assert status in (200, 201) and body["written"] is True, (status, str(body)[:300])  # type: ignore[index]
        ctx = _master(served)
    known = {item["text"] for item in ctx["none"]}  # type: ignore[union-attr]
    assert len(known) >= CALLS, known
    return ctx


def _after_master(served: _Server, ctx: Ctx, body: object) -> None:
    found = body.get("master") if isinstance(body, dict) else None
    ctx["revision"] = found["revision"] if isinstance(found, dict) and isinstance(found.get("revision"), int) else _master(served)["revision"]


def _not_written(body: object) -> str | None:
    return None if isinstance(body, dict) and body.get("written") is True else f"not written: {str(body)[:300]}"


_ADD_LINES = (
    "Cut the deploy time of 40 services from 50 to 12 minutes.",
    "Mentored 4 engineers through their first design review.",
    "Reduced cloud spend by 18 percent with right-sized node pools.",
)


def _setup_prefs(served: _Server, _fixture: _Fixture) -> Ctx:
    prefs = served.get("/api/setup")["prefs"]  # type: ignore[index]
    return {"prefs": {key: value for key, value in prefs.items() if key not in ("schema_version", "updated_at", "created_at")}}


def _not_assessed(served: _Server, _fixture: _Fixture) -> Ctx:
    rows = served.get("/api/postings?state=not_assessed&limit=50")["postings"]["rows"]  # type: ignore[index]
    assert len(rows) >= CALLS, len(rows)
    return {"rows": [row["job_url"] for row in rows]}


def _resume_file(fixture: _Fixture, index: int) -> str:
    path = fixture.root / f"gate-resume-{index}.md"
    source = (Path(operator_home.__file__).resolve().parents[1] / "evals" / "fixtures" / "master" / "legacy-ai.md").read_text(encoding="utf-8")
    path.write_text(source.rstrip() + f"\n- Ran the write gate's review number {index + 1} for the platform group.\n", encoding="utf-8")
    return str(path)


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


# GIT CEILINGS (2026-10-04, before any speed packet). Set where the first call FINISHES on the 100-job fixture today, at
# about 1.5 times the count measured on five runs (two home sizes, Python 3.11 and 3.13; the counts differed by at most
# 8 between runs: the server's background threads start in the same window). ``None`` on the six operations whose save
# runs a pipeline trigger over every finished job (S1 cause C1): they do not finish inside the stop time, so there is
# no count to hold yet. When S2 lands they finish: give each its measured count then, and lower the others as the
# read-scope and store packets land (S1 report 7.3: "set from the measured value after each fix").
OPERATIONS: tuple[Operation, ...] = (
    Operation(
        "PUT /api/setup (save preferences)", prep=_setup_prefs,
        call=lambda _s, _f, i, ctx: ("PUT", "/api/setup", dict(ctx["prefs"], cadence_days=8 + i)),  # type: ignore[call-overload]
    ),
    Operation(
        "POST /api/answers (the question every assessed job asked)",
        call=lambda _s, _f, i, _c: ("POST", "/api/answers", {
            "question_id": operator_home.COMMON_QUESTION_ID, "question": operator_home.COMMON_QUESTION, "answer": f"GCP, {i + 3} years.",
        }),
    ),
    Operation(
        "POST /api/answers (a question no job asked)", git_ceiling=330,  # measured 213 to 218
        call=lambda _s, _f, i, _c: ("POST", "/api/answers", {"question_id": f"gate:topic-{i}", "question": f"Do you have experience with topic {i}?", "answer": f"Yes, {i + 2} years."}),
    ),
    Operation(
        "PUT /api/profiles/{id} (rename)",
        call=lambda _s, fixture, i, _c: ("PUT", f"/api/profiles/{fixture.second}", {"label": f"Platform track {i + 2}", "titles": list(operator_home.SECOND_TITLES)}),
    ),
    Operation(
        # Measured 126 to 127 by the answer; its first selection goes on in a background thread (409 in all, S1 4.5).
        "POST /api/profiles (create; the answer)", git_ceiling=450,
        call=lambda _s, _f, i, _c: ("POST", "/api/profiles", {"label": f"Backend {i}", "titles": ["Staff Backend Engineer"]}),
    ),
    Operation(
        "POST /api/master/lines (add a line)", prep=_master, after=_after_master, refused=_not_written, git_ceiling=300,  # measured 199
        call=lambda _s, _f, i, ctx: ("POST", "/api/master/lines", {"revision": ctx["revision"], "entry_id": ctx["entry"], "text": _ADD_LINES[i]}),
    ),
    Operation(
        "PUT /api/master/lines (edit a line both profiles show)", prep=_master, after=_after_master, refused=_not_written,
        call=lambda _s, _f, i, ctx: ("PUT", "/api/master/lines", {
            "revision": ctx["revision"], "id": ctx["both"][0]["id"], "use": "edit",  # type: ignore[index]
            "text": f"{ctx['both'][0]['text'].rstrip('.')}, checked in review {i + 1}."[:400],  # type: ignore[index]
        }),
    ),
    Operation(
        "PUT /api/master/lines (retire a line)", prep=_master_with_spare_lines, after=_after_master, refused=_not_written, git_ceiling=300,  # measured 199
        call=lambda _s, _f, i, ctx: ("PUT", "/api/master/lines", {"revision": ctx["revision"], "id": ctx["none"][i]["id"], "use": "retire"}),  # type: ignore[index]
    ),
    Operation(
        "gigai scout resume add FILE (the command)",
        call=lambda _s, fixture, i, _c: ("CLI", "", ["scout", "resume", "add", _resume_file(fixture, i)]),
    ),
    Operation(
        "POST /api/master/selection (refresh a profile's selection)",
        call=lambda _s, fixture, i, _c: ("POST", "/api/master/selection", {"profile_id": (fixture.default, fixture.second)[i % 2], "use": "refresh"}),
    ),
    Operation(
        "POST /api/assess (the job page's Assess, a posting not assessed yet)", prep=_not_assessed, git_ceiling=280,  # measured 185
        call=lambda _s, _f, i, ctx: ("POST", "/api/assess", {"job": {"job_url": ctx["rows"][i]}, "origin": "job_page"}),  # type: ignore[index]
    ),
    Operation(
        "GET /api/jobs?url= (the job page)", git_ceiling=270,  # measured 173 to 181
        call=lambda _s, fixture, i, _c: ("GET", "/api/jobs?url=" + _quote(fixture.jobs("pipeline_jobs", fixture.default)[i % JOBS]), None),
    ),
    Operation(
        "GET /api/postings?limit=50 (the Jobs list, a fresh server)", git_ceiling=90,  # measured 52 to 57
        call=lambda _s, _f, _i, _c: ("GET", "/api/postings?limit=50", None),
    ),
)


def _cli(fixture: _Fixture, args: list[str], spawn_log: Path, timeout: float | None) -> tuple[int, object, float]:
    started = time.monotonic()
    try:
        done = subprocess.run(
            [sys.executable, "-c", "from gigai.cli import cli; cli()", *args, "--home", fixture.built.home, "--target", fixture.built.target, "--json"],
            capture_output=True, text=True, env=fixture.env(spawn_log), check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise _Stopped() from error
    return (200 if done.returncode == 0 else 500), (done.stdout + done.stderr)[-600:], time.monotonic() - started


def _measure(number: int, operation: Operation, fixture: _Fixture) -> Measured:
    row = Measured(number, operation.name, JOBS, git_ceiling=operation.git_ceiling, note=operation.note)
    fixture.restore()  # every operation, the reads too: the one before may have been stopped in the middle of its save
    timeout = STOP_SECONDS if STOP_SECONDS > 0 else None
    with _Server(fixture, f"op{number:02d}") as served:
        ctx = operation.prep(served, fixture) if operation.prep else {}
        for index in range(CALLS):
            method, path, body = operation.call(served, fixture, index, ctx)
            log = fixture.logs / f"op{number:02d}.cli.spawns" if method == "CLI" else served.spawn_log
            offset, _before = _spawns(log)
            try:
                if method == "CLI":
                    status, answer, seconds = _cli(fixture, list(body), log, timeout)  # type: ignore[call-overload]
                else:
                    status, answer, seconds = served.request(method, path, body, timeout=timeout if timeout is not None else 7200.0)
            except _Stopped:
                _end, commands = _spawns(log, offset)
                row.seconds.append(STOP_SECONDS)
                row.git.append(sum(commands.values()))
                row.stopped = True
                if index == 0:
                    row.commands = dict(commands.most_common())
                served.stop(kill=True)  # it is still working on the save: do not wait for it
                break
            _end, commands = _spawns(log, offset)
            refused = operation.refused(answer) if operation.refused is not None else None
            assert status in (200, 201, 202) and refused is None, f"{operation.name}, call {index + 1}: HTTP {status} {refused or str(answer)[:400]}\n{served.log()[-1500:]}"
            row.seconds.append(seconds)
            row.git.append(sum(commands.values()))
            if index == 0:
                row.commands = dict(commands.most_common())
            if operation.after is not None:
                operation.after(served, ctx, answer)
            if seconds > REPEAT_UNDER_SECONDS:
                break
    return row


@pytest.mark.skipif(MODE not in ("report", "enforce"), reason=f"the write timing gate runs with {MODE_ENV}=report or enforce (make test-operator-writes)")
def test_every_interactive_save_on_the_operator_sized_home_is_timed_and_counted(fixture: _Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    started, load_before = time.monotonic(), os.getloadavg()[0]
    rows: list[Measured] = []
    with capsys.disabled():
        for number, operation in enumerate(OPERATIONS, start=1):
            try:
                row = _measure(number, operation, fixture)
            except AssertionError as error:  # refused, or an error answer: the other operations are still measured
                row = Measured(number, operation.name, JOBS, error=" ".join(str(error).split())[:600])
            rows.append(row)
            _note(
                f"{number}/{len(OPERATIONS)} {row.name}: "
                + (f"NOT MEASURED: {row.error}" if row.error else f"stopped at {STOP_SECONDS:.0f} s, >= {row.git[0]} git processes" if row.stopped else f"{', '.join(f'{value:.2f}' for value in row.seconds)} s; git processes {row.git}")
            )
        header = (
            f"{'operator-sized' if FULL else 'scaled 1/10'} home, {fixture.built.postings:,} postings x {fixture.built.companies:,} companies, 2 profiles, "
            f"{JOBS} finished pipeline jobs per profile, {fixture.content['common_question']['asked_by']} assessed jobs asked the common question; "  # type: ignore[index]
            f"{datetime.now(UTC):%Y-%m-%d %H:%M} UTC, load average {load_before:.0f} to {os.getloadavg()[0]:.0f}, "
            f"Python {sys.version_info.major}.{sys.version_info.minor} on {sys.platform}"
            + (f", latency scale {latency_scale():g}" if latency_scale() != 1 else "")
            + f"; the operations took {time.monotonic() - started:.0f} s, the fixture {fixture.content['seconds']} s to build"
        )
        table = render_table(rows, header=header, mode=MODE, stop_seconds=STOP_SECONDS)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "table.md").write_text(table, encoding="utf-8")
        (OUT / "table.json").write_text(json.dumps({"header": header, "mode": MODE, "operations": [row.to_json() for row in rows]}, indent=1), encoding="utf-8")
        print("\n" + table)
    failed = failures(rows, enforce=ENFORCE, ceiling=latency_bound(CEILING_SECONDS))
    assert not failed, "\n".join(failed)

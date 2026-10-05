"""0110-9-01: THE OPERATOR-SIZED TIMING GATE. The real server process on a home the size of the operator's.

The 0.1.10.7 PL6 gate (``test_posting_read_model.py``) is 5,000 postings on
10 boards: every posting matches, so it never measured what a real home is
made of, 10,350 companies and 290,000 postings of which about 600 match. On
that home 0.1.10.8 never finished loading the Jobs page. This is that home
(``tests/support/operator_home.py``: synthetic, no request, no real home is
read), the REAL server process (``python -m gigai.scout.find_jobs.present_api``,
its background threads running), and the browser's request pattern.

SIZE. ``GIGAI_OPERATOR_GATE=1`` (``make test-operator-home``, REQUIRED in the
release pre-check) builds the full 290,000 x 10,350 home: about 30 s to build
and 1 to 2 minutes to run. Without it (the normal suite) the same test runs
on a tenth of it (29,000 x 1,035) with the same assertions, so the gate's own
code cannot rot between releases.

WHAT IT ASSERTS

1. SINGLE FLIGHT: 8 requests that arrive together on a cold model start
   exactly ONE build (the server's own count), and every one is answered.
2. PROGRESS, NON-BLOCKING: the first build reports ``preparing`` with a
   percent that rises, and health, profiles, runs, config and the pipeline
   each answer in under a second while it runs.
3. WARM LATENCY (wall clock: this is what the user waits for):
   ``/api/postings?limit=50`` and ``/api/new?peek=1`` p50 and p95 under
   500 ms.
4. MEMORY: the server's resident memory stays under :data:`RSS_BOUND_MB`
   through the build and the reads (0.1.10.8: 4.6 GB).
5. INCREMENTAL: one company's update matches ONE board again.
6. PERSISTED: a fresh server process on the same home is warm (rows on the
   first request, no build), and ``gigai scout new --peek`` in a fresh
   process takes under 3 s.
7. A client that closes early is one ``client closed the connection`` info
   line; the server log has no "unhandled exception", no traceback, no 500.
8. THE CLI BUILDS IT ITSELF (0110-10-08): with the server stopped and the
   read model cold again, ``gigai scout new --no-assess --json`` in a fresh
   process exits 0, stdout is the response alone (valid JSON) and the
   progress lines ("preparing your postings: N% ...") are on stderr. 0.1.10.9
   crashed here with a NameError: steps 1 to 7 only ever ran the CLI on a
   model the server had built. The time is reported; the ceiling is generous.

Counts (builds, boards matched) and the server's own CPU time are what the
load of the machine cannot move; the wall-clock bounds are scaled by
``GIGAI_TEST_LATENCY_SCALE`` like every other latency bound.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import json
import os
from pathlib import Path
import resource
import shutil
import socket
import statistics
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

from tests.support import operator_home
from tests.support.latency import latency_bound

GATE_ENV = "GIGAI_OPERATOR_GATE"
FULL = os.environ.get(GATE_ENV) == "1"
POSTINGS = operator_home.OPERATOR_POSTINGS if FULL else operator_home.OPERATOR_POSTINGS // 10
COMPANIES = operator_home.OPERATOR_COMPANIES if FULL else operator_home.OPERATOR_COMPANIES // 10

#: The ticket's targets.
WARM_SECONDS = 0.5
CLI_SECONDS = 3.0
OTHER_ROUTE_SECONDS = 1.0
#: Measured on the full home: 203 MB at the peak of the build (0.1.10.8: 4.6 GB with 13 builds at once, 507 MB with one).
RSS_BOUND_MB = 600
#: The first answer on a cold server reads the watchlist once (measured 3.6 s on the full home), then says "preparing".
FIRST_ANSWER_SECONDS = 20.0
#: The first build (measured 8.7 s on the full home, alone on 14 cores).
BUILD_SECONDS = 180.0
#: The server's own CPU for the whole first build (measured 9 to 11 s on the full home; 0.1.10.8: about 40 s for ONE build).
BUILD_CPU_SECONDS = 60.0
#: `gigai scout new --no-assess` doing the whole first build itself, in its own process (measured 10.7 s wall, 8.6 s CPU on the full home, 14 cores).
COLD_CLI_SECONDS = 180.0
WARM_SAMPLES = 15
LIGHT_ROUTES = ("/api/health", "/api/profiles", "/api/runs", "/api/config", "/api/pipeline")


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Iterator[operator_home.OperatorHome]:
    kept = dict(os.environ)
    try:
        # The release pre-check builds the home once for this gate and the browser flows (tests/ui): a fresh copy of it.
        built = operator_home.take_prebuilt(postings=POSTINGS, companies=COMPANIES, log=print) if FULL else None
        if built is None:
            built = operator_home.build(tmp_path_factory.mktemp("operator-home") / "op", postings=POSTINGS, companies=COMPANIES, workers=None if FULL else 4, assessed=operator_home.ASSESSED if FULL else 8)
    finally:
        os.environ.clear()
        os.environ.update(kept)
    print(f"\noperator-sized home: {built.postings} postings x {built.companies} companies built in {built.build_seconds} s")
    yield built


@pytest.fixture(scope="module")
def cold_model(home: operator_home.OperatorHome, tmp_path_factory: pytest.TempPathFactory) -> Callable[[], None]:
    """Kept now, before any process reads the home: the pipeline file as built (no posting rows). Calling it puts that back."""

    from gigai.scout.pipeline.store import pipeline_path

    store_file = pipeline_path(home.home_root, Path(home.target))
    kept = tmp_path_factory.mktemp("cold-model")
    for found in store_file.parent.glob(store_file.name + "*"):
        shutil.copy2(found, kept / found.name)

    def restore() -> None:
        for found in store_file.parent.glob(store_file.name + "*"):
            found.unlink()
        for found in kept.iterdir():
            shutil.copy2(found, store_file.parent / found.name)

    return restore


class _Server:
    """The real server process on the home, its log, and what ``ps`` says about it."""

    def __init__(self, home: operator_home.OperatorHome, log_path: Path) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.log_path = log_path
        self._log = open(log_path, "w", encoding="utf-8")
        env = dict(os.environ, **operator_home.SEAM_ENV)
        self.process = subprocess.Popen(
            [sys.executable, "-m", "gigai.scout.find_jobs.present_api", "--home", home.home, "--target", home.target,
             "--port", str(self.port), "--allow-test-seams"],
            env=env, stdout=self._log, stderr=subprocess.STDOUT,
        )
        self.peak_rss_mb = 0
        self._stop = threading.Event()
        self._sampler = threading.Thread(target=self._sample, daemon=True)

    def _ps(self, field: str) -> str:
        return subprocess.run(["ps", "-o", f"{field}=", "-p", str(self.process.pid)], capture_output=True, text=True, check=False).stdout.strip()

    def _rss_kb(self) -> int | None:
        """Resident memory in kB: /proc on Linux (a Debian slim container has no ``ps``), ``ps`` elsewhere."""

        status = Path(f"/proc/{self.process.pid}/status")
        if status.is_file():
            try:
                for line in status.read_text(encoding="utf-8").splitlines():
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1])
            except (OSError, ValueError):
                return None
            return None
        found = self._ps("rss")
        return int(found) if found.isdigit() else None

    def _sample(self) -> None:
        while not self._stop.wait(0.25):
            found = self._rss_kb()
            if found is not None:
                self.peak_rss_mb = max(self.peak_rss_mb, found // 1024)

    def cpu_seconds(self) -> float:
        """The process's own CPU time so far: /proc/<pid>/stat on Linux, ``ps`` cputime ([[dd-]hh:]mm:ss[.cc]) elsewhere."""

        stat = Path(f"/proc/{self.process.pid}/stat")
        if stat.is_file():
            fields = stat.read_text(encoding="utf-8").rsplit(")", 1)[1].split()
            return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
        text = self._ps("cputime").replace("-", ":")
        seconds = 0.0
        for part in text.split(":"):
            seconds = seconds * 60 + float(part or 0)
        return seconds

    def get(self, path: str, *, timeout: float = 120.0) -> tuple[int, dict[str, object], float]:
        started = time.monotonic()
        try:
            with urllib.request.urlopen(self.base + path, timeout=timeout) as response:
                status, body = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, body = error.code, error.read()
        return status, json.loads(body), time.monotonic() - started

    def status(self) -> dict[str, object]:
        return self.get("/api/postings/status")[1]

    def log(self) -> str:
        self._log.flush()
        return self.log_path.read_text(encoding="utf-8")

    def __enter__(self) -> "_Server":
        deadline = time.monotonic() + 60
        while True:
            try:
                if self.get("/api/health", timeout=2)[0] == 200:
                    break
            except OSError:
                pass
            assert self.process.poll() is None and time.monotonic() < deadline, self.log()
            time.sleep(0.05)
        self._sampler.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self.process.terminate()
        try:
            self.process.wait(20)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(20)
        self._log.close()


@contextmanager
def _server(home: operator_home.OperatorHome, tmp_path: Path, name: str) -> Iterator[_Server]:
    with _Server(home, tmp_path / f"{name}.log") as served:
        yield served


def _percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(share * (len(ordered) - 1))))]


def _warm(served: _Server, path: str) -> tuple[float, float, float]:
    status, body, first = served.get(path)
    assert status == 200 and body["postings"]["rows"], (path, status)  # type: ignore[index]
    taken = []
    for _ in range(WARM_SAMPLES):
        status, _body, seconds = served.get(path)
        assert status == 200
        taken.append(seconds)
    return first, statistics.median(taken), _percentile(taken, 0.95)


def _close_early(served: _Server, path: str) -> None:
    """Ask, then close at once with the answer unread (RST), as a tab that gave up does."""

    client = socket.create_connection(("127.0.0.1", served.port), timeout=30)
    client.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{served.port}\r\n\r\n".encode("ascii"))
    client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    client.close()


def _update_one_board(home: operator_home.OperatorHome, board: int) -> None:
    """One company posts one more job: its cached body and its index file change, as after ``sources update``."""

    from gigai.scout.find_jobs.ats_board_clients import BoardCache
    from gigai.scout.find_jobs.company_index import CompanyIndex, board_list_url, index_stamp, refresh_company

    slug = operator_home.slug_of(board)
    cache = BoardCache(home.home_root / "cache" / "scout" / "ats-boards", validator_source=lambda _provider, _url: None)
    url = board_list_url("lever", slug)
    jobs = json.loads(cache.lookup("lever", url).body)  # type: ignore[union-attr]
    extra = dict(jobs[0], id=f"{slug}-99999", hostedUrl=f"https://jobs.lever.co/{slug}/{slug}-99999", text="Staff Software Engineer, Gate")
    extra["categories"], extra["country"], extra["workplaceType"] = {"location": "Remote - United States"}, "US", "remote"
    cache.store("lever", url, body=json.dumps(jobs + [extra]).encode("utf-8"), etag=None, last_modified=None, marker=None)
    refresh_company(CompanyIndex.for_home(home.home_root), cache, ats="lever", slug=slug, observed_at=index_stamp(operator_home.NOW))


def test_the_operator_sized_home_loads_one_build_fast_reads_bounded_memory(
    home: operator_home.OperatorHome, cold_model: Callable[[], None], tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    report: list[str] = [f"OPERATOR-SIZED GATE ({'FULL' if FULL else 'scaled 1/10'}): {home.postings} postings x {home.companies} companies, 2 profiles"]

    with _server(home, tmp_path, "cold") as served:
        assert served.status()["state"] == "unknown"
        cpu_before = served.cpu_seconds()

        # 1. Eight requests arrive together on a cold model.
        answers: list[tuple[int, dict[str, object], float]] = []
        paths = ["/api/postings?limit=50", "/api/new?peek=1"] * 4
        threads = [threading.Thread(target=lambda path=path: answers.append(served.get(path))) for path in paths]
        build_started = time.monotonic()
        for thread in threads:
            thread.start()

        # 2. While it builds: progress that rises, and the other routes answer.
        percents: list[int] = []
        states: list[str] = []
        slowest: dict[str, float] = {}
        while True:
            found = served.status()
            states.append(str(found["state"]))
            if found["state"] == "preparing":
                percents.append(int(found["percent"]))  # type: ignore[call-overload]
                assert found["boards_total"] in (0, home.companies)
                for path in LIGHT_ROUTES:
                    status, _body, seconds = served.get(path)
                    assert status == 200, path
                    slowest[path] = max(slowest.get(path, 0.0), seconds)
            if found["state"] == "ready":
                break
            assert time.monotonic() - build_started < latency_bound(BUILD_SECONDS), found
            time.sleep(0.05)
        build_seconds = time.monotonic() - build_started
        build_cpu = served.cpu_seconds() - cpu_before
        for thread in threads:
            thread.join(120)
        assert len(answers) == 8 and all(status in (200, 202) for status, _body, _seconds in answers), [item[0] for item in answers]
        first_answer = max(seconds for _status, _body, seconds in answers)
        assert first_answer < latency_bound(FIRST_ANSWER_SECONDS), first_answer
        for status, body, _seconds in answers:
            if status == 202:
                assert body["status"] == "preparing" and body["schema_version"] == "scout-postings-status:1"
        done = served.status()
        assert done["builds"] == 1, done  # SINGLE FLIGHT: one build for eight requests
        assert done["last_boards"] == home.companies and done["percent"] == 100
        assert build_cpu < BUILD_CPU_SECONDS, build_cpu
        if FULL:
            # On the full home the build is long enough to be watched: "preparing" with a percent that rises.
            assert any(status == 202 for status, _body, _seconds in answers)
            assert "preparing" in states and len(set(percents)) >= 3 and percents == sorted(percents), percents
        if slowest:
            assert max(slowest.values()) < latency_bound(OTHER_ROUTE_SECONDS), slowest
        report.append(
            f"  cold: 8 requests at once -> builds {done['builds']}; answered in <= {first_answer:.2f} s "
            f"({sum(1 for item in answers if item[0] == 202)} x 202 preparing); build {build_seconds:.1f} s wall, {build_cpu:.1f} s CPU; "
            f"percents seen {len(set(percents))}; other routes during the build, slowest: "
            + ", ".join(f"{path} {seconds:.2f} s" for path, seconds in sorted(slowest.items()))
        )

        # 3. Warm latency.
        for path in ("/api/postings?limit=50", "/api/new?peek=1"):
            first, p50, p95 = _warm(served, path)
            report.append(f"  warm {path}: first {first:.3f} s, p50 {p50:.3f} s, p95 {p95:.3f} s (bound {WARM_SECONDS} s)")
            assert p50 < latency_bound(WARM_SECONDS) and p95 < latency_bound(WARM_SECONDS), (path, p50, p95)
        matched = served.get("/api/postings?limit=1")[1]["counts"]["matched"]  # type: ignore[index]
        assert matched > 0
        assert served.status()["builds"] == 1  # the reads built nothing

        # 5. One company's update: one board is matched again.
        _update_one_board(home, 3)
        deadline = time.monotonic() + latency_bound(60.0)
        while served.get("/api/postings?limit=1")[1]["counts"]["matched"] != matched + 1:  # type: ignore[index]
            assert time.monotonic() < deadline, served.status()
            time.sleep(0.2)
        after = served.status()
        assert after["builds"] == 2 and after["last_boards"] == 1, after
        report.append(f"  incremental: 1 company updated -> build {after['builds']} matched {after['last_boards']} board")

        # 7. A client that closes early.
        deadline = time.monotonic() + 30
        while "client closed the connection" not in served.log():
            assert time.monotonic() < deadline, "no 'client closed the connection' line"
            _close_early(served, "/api/postings?limit=200")
            time.sleep(0.5)
        assert served.get("/api/health")[0] == 200
        time.sleep(0.5)  # 4. the last sample
        peak = served.peak_rss_mb
        log = served.log()
    closed = [line for line in log.splitlines() if "client closed the connection" in line]
    assert closed and all(" INFO " in line for line in closed), closed[:3]
    bad = [line for line in log.splitlines() if "unhandled exception" in line or "Traceback" in line or "BrokenPipeError" in line or " 500 " in line]
    assert not bad, bad[:5]
    assert 0 < peak < RSS_BOUND_MB, peak
    report.append(f"  memory: server peak RSS {peak} MB (bound {RSS_BOUND_MB} MB); client closed early: {len(closed)} info line(s), no 500")

    # 6. A fresh server process is warm, and so is the CLI in a fresh process.
    with _server(home, tmp_path, "fresh") as fresh:
        status, body, seconds = fresh.get("/api/postings?limit=50")
        assert status == 200 and body["counts"]["matched"] == matched + 1, status  # type: ignore[index]
        assert fresh.status()["builds"] == 0  # nothing was matched again: the model was read from the pipeline file
        assert seconds < latency_bound(CLI_SECONDS), seconds
        report.append(f"  fresh server process: first /api/postings?limit=50 {seconds:.2f} s, builds 0")

    walls, cpus = [], []
    for _ in range(2):
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        started = time.monotonic()
        done_cli = subprocess.run(
            [sys.executable, "-c", "from gigai.cli import cli; cli()", "scout", "new", "--peek", "--json", "--home", home.home, "--target", home.target],
            capture_output=True, text=True, env=dict(os.environ, **operator_home.SEAM_ENV), check=False,
        )
        walls.append(time.monotonic() - started)
        after_cli = resource.getrusage(resource.RUSAGE_CHILDREN)
        cpus.append((after_cli.ru_utime - before.ru_utime) + (after_cli.ru_stime - before.ru_stime))
        assert done_cli.returncode == 0, done_cli.stderr[-800:]
        assert json.loads(done_cli.stdout)["schema_version"] == "scout-new:1"
    report.append(f"  fresh process `gigai scout new --peek --json`: wall {min(walls):.2f} s, CPU {min(cpus):.2f} s (bound {CLI_SECONDS} s)")
    # CPU seconds slow down with the machine like wall seconds do: the Debian container job sets GIGAI_TEST_LATENCY_SCALE, and a flat
    # 3.0 s bound failed there at 3.32 s (two runs: 3.324 and 3.330) while the wall bound beside it was already scaled.
    assert min(cpus) < latency_bound(CLI_SECONDS), cpus
    assert min(walls) < latency_bound(CLI_SECONDS), walls

    # 8. 0110-10-08: no server, a cold read model: the CLI process does the whole first build itself.
    cold_model()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.monotonic()
    cold_cli = subprocess.run(
        [sys.executable, "-c", "from gigai.cli import cli; cli()", "scout", "new", "--no-assess", "--json", "--home", home.home, "--target", home.target],
        capture_output=True, text=True, env=dict(os.environ, **operator_home.SEAM_ENV), check=False,
    )
    cold_wall = time.monotonic() - started
    after_cold = resource.getrusage(resource.RUSAGE_CHILDREN)
    cold_cpu = (after_cold.ru_utime - before.ru_utime) + (after_cold.ru_stime - before.ru_stime)
    assert cold_cli.returncode == 0, cold_cli.stderr[-2000:]
    assert "Traceback" not in cold_cli.stderr, cold_cli.stderr[-2000:]
    assert json.loads(cold_cli.stdout)["schema_version"] == "scout-new:1"  # stdout is the response alone
    said = [line for line in cold_cli.stderr.splitlines() if line.startswith("preparing your postings: ")]
    # It matched every company again (the model was cold), and said so on stderr from the first report to the last.
    assert said and said[0] == f"preparing your postings: 0% (0 of {home.companies} companies)", cold_cli.stderr[-2000:]
    assert said[-1] == f"preparing your postings: 100% ({home.companies} of {home.companies} companies)", cold_cli.stderr[-2000:]
    report.append(
        f"  server stopped, cold model, fresh process `gigai scout new --no-assess --json`: wall {cold_wall:.2f} s, CPU {cold_cpu:.2f} s "
        f"(ceiling {COLD_CLI_SECONDS:.0f} s); exit 0, stdout valid JSON, {len(said)} progress line(s) on stderr"
    )
    assert cold_wall < latency_bound(COLD_CLI_SECONDS), cold_wall

    with capsys.disabled():
        print("\n" + "\n".join(report))

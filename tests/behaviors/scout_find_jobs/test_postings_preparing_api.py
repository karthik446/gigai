"""0110-9-01: the server while the posting read model is built, and a client that goes away.

A real in-process server (``serve()`` with the real backend) on a small
synthetic home, the build held open by the test so "during the build" is a
state, not a race:

* ``GET /api/postings`` and ``GET /api/new`` answer ``202`` with
  ``status: "preparing"`` and the progress, at once, never a hang;
  ``GET /api/postings/status`` says the same from memory;
* eight requests that arrive together are ONE build;
* the other routes (health, profiles, config, the pipeline) answer while it
  runs; when it is done the same routes answer the rows;
* a client that closes its connection before the answer is written is one
  ``client closed the connection`` line at INFO: no "unhandled exception", no
  traceback, no 500 line.
"""

from __future__ import annotations

import logging
from pathlib import Path
import socket
import struct
import threading
import time

import httpx
import pytest

from gigai.scout import postings
from gigai.scout.find_jobs.api import postings as postings_routes
from gigai.scout.find_jobs.api.server import LOGGER_NAME, ScoutFindJobsBackend, serve

from tests.support.posting_fixtures import TITLE_BOTH, PostingsFixture, build_postings_fixture, days_ago, lever_job

BOARDS = 5
PER_BOARD = 3
ANSWER_SECONDS = 5.0  # "at once" for a route during the build, with room for a loaded machine


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def lines(self) -> list[str]:
        return [f"{record.levelname} {record.getMessage()}" for record in self.records]


class _Served:
    def __init__(self, fx: PostingsFixture) -> None:
        self.server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.host, self.port = self.server.server_address[0], self.server.server_address[1]
        self.client = httpx.Client(base_url=f"http://{self.host}:{self.port}", timeout=60.0)
        self.log = _Records()

    def __enter__(self) -> "_Served":
        logging.getLogger(LOGGER_NAME).addHandler(self.log)
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(10)
        logging.getLogger(LOGGER_NAME).removeHandler(self.log)


def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    for board in range(BOARDS):
        slug = f"pr{board:02d}"
        fx.seed(slug, [lever_job(slug, n, title=TITLE_BOTH) for n in range(PER_BOARD)], seen_at=days_ago(1))
    return fx


def _hold_builds(monkeypatch: pytest.MonkeyPatch) -> tuple[threading.Event, list[int]]:
    """Every build stops after it has counted its boards until the event is set; the list gets one item per build."""

    release = threading.Event()
    builds: list[int] = []
    real = postings._match

    def held(plan, store, views, facts_of, progress):
        builds.append(1)

        def report(phase: str, done: int, total: int) -> None:
            if progress is not None:
                progress(phase, done, total)
            if done == 0:
                assert release.wait(60)

        return real(plan, store, views, facts_of, report)

    monkeypatch.setattr(postings, "_match", held)
    return release, builds


def _timed(call) -> tuple[httpx.Response, float]:
    started = time.monotonic()
    response = call()
    return response, time.monotonic() - started


def test_reads_answer_202_with_progress_during_the_first_build_and_other_routes_stay_fast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = _home(tmp_path, monkeypatch)
    monkeypatch.setattr(postings, "SMALL_BUILD_BOARDS", 0)  # five boards are a large build here: never waited for
    release, builds = _hold_builds(monkeypatch)
    with _Served(fx) as served:
        get = served.client.get
        assert get("/api/postings/status").json()["state"] == "unknown"  # nothing asked for the postings yet

        # Eight requests arrive together on a cold model: every one is answered, and there is ONE build.
        answers: list[httpx.Response] = []
        paths = ["/api/postings?limit=50", "/api/new?peek=1"] * 4
        threads = [threading.Thread(target=lambda path=path: answers.append(get(path))) for path in paths]
        started = time.monotonic()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        assert time.monotonic() - started < 4 * ANSWER_SECONDS
        assert [answer.status_code for answer in answers] == [202] * 8
        assert len(builds) == 1
        for answer in answers:
            body = answer.json()
            assert body["status"] == "preparing" and body["state"] == "preparing" and body["schema_version"] == "scout-postings-status:1"
            assert body["boards_total"] == BOARDS and 0 <= body["percent"] < 100 and body["phase"] == "matching"
            assert "postings" not in body and "rows" not in body

        # While it runs: the status from memory, the same 202 again, and every other route at once.
        status, seconds = _timed(lambda: get("/api/postings/status"))
        assert status.status_code == 200 and seconds < ANSWER_SECONDS
        assert status.json() == {**status.json(), "state": "preparing", "builds": 1, "boards_total": BOARDS}
        again, seconds = _timed(lambda: get("/api/postings"))
        assert again.status_code == 202 and seconds < ANSWER_SECONDS
        yours, seconds = _timed(lambda: get("/api/new/yours"))
        assert yours.status_code == 202 and seconds < ANSWER_SECONDS
        for path in ("/api/health", "/api/profiles", "/api/config", "/api/pipeline", "/api/runs"):
            answer, seconds = _timed(lambda path=path: get(path))
            assert answer.status_code == 200 and seconds < ANSWER_SECONDS, (path, answer.status_code, seconds)
        assert get("/api/postings/status?x=1").status_code == 422
        assert len(builds) == 1

        release.set()
        deadline = time.monotonic() + 60
        while get("/api/postings/status").json()["state"] != "ready":
            assert time.monotonic() < deadline, "the build never finished"
            time.sleep(0.02)
        done = get("/api/postings/status").json()
        assert done["percent"] == 100 and done["builds"] == 1 and done["phase"] == "idle"
        listed = get("/api/postings?limit=50")
        assert listed.status_code == 200 and listed.json()["counts"]["matched"] == BOARDS * PER_BOARD
        new = get("/api/new?peek=1")
        assert new.status_code == 200 and new.json()["schema_version"] == "scout-new:1"
        assert len(builds) == 1  # the answers after the build read what it stored


def test_with_stored_rows_a_build_is_served_from_them_never_202(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = _home(tmp_path, monkeypatch)
    with _Served(fx) as served:
        first = served.client.get("/api/postings")  # a small build: waited for, the rows come back
        assert first.status_code == 200 and first.json()["counts"]["matched"] == BOARDS * PER_BOARD
        monkeypatch.setattr(postings, "SMALL_BUILD_BOARDS", 0)
        release, builds = _hold_builds(monkeypatch)
        fx.seed("pr00", [lever_job("pr00", n, title=TITLE_BOTH) for n in range(PER_BOARD + 2)], seen_at=days_ago(0.5), watch=False)
        during, seconds = _timed(lambda: served.client.get("/api/postings"))
        assert during.status_code == 200 and seconds < ANSWER_SECONDS
        assert during.json()["counts"]["matched"] == BOARDS * PER_BOARD  # the rows as stored
        assert served.client.get("/api/postings/status").json()["state"] == "refreshing"
        release.set()
        deadline = time.monotonic() + 60
        while served.client.get("/api/postings/status").json()["state"] != "ready":
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert served.client.get("/api/postings").json()["counts"]["matched"] == BOARDS * PER_BOARD + 2 and len(builds) == 1


def test_the_wait_is_a_number_of_seconds_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(postings_routes.MODEL_WAIT_ENV, raising=False)
    assert postings_routes.model_wait_seconds() == postings_routes.MODEL_WAIT_SECONDS
    monkeypatch.setenv(postings_routes.MODEL_WAIT_ENV, "0.5")
    assert postings_routes.model_wait_seconds() == 0.5
    monkeypatch.setenv(postings_routes.MODEL_WAIT_ENV, "soon")
    assert postings_routes.model_wait_seconds() == postings_routes.MODEL_WAIT_SECONDS


def test_a_client_that_closes_early_is_one_info_line_never_an_unhandled_exception_or_a_500(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = _home(tmp_path, monkeypatch)
    arrived, gone = threading.Event(), threading.Event()
    real = postings_routes.search_postings

    def slow(*args: object, **kwargs: object):
        response = real(*args, **kwargs)
        arrived.set()
        assert gone.wait(30)  # the answer is ready; the client has closed by the time it is written
        response["padding"] = ["x" * 1024] * 4096  # larger than a socket buffer: the write meets the closed connection
        return response

    monkeypatch.setattr(postings_routes, "search_postings", slow)
    with _Served(fx) as served:
        assert served.client.get("/api/health").status_code == 200
        client = socket.create_connection((served.host, served.port), timeout=30)
        client.sendall(f"GET /api/postings HTTP/1.1\r\nHost: {served.host}:{served.port}\r\n\r\n".encode("ascii"))
        assert arrived.wait(60)
        # Closed the way a browser tab that gave up closes: at once, the unread answer refused (RST).
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        client.close()
        time.sleep(0.2)
        gone.set()
        deadline = time.monotonic() + 30
        while not any("client closed the connection" in line for line in served.log.lines()):
            assert time.monotonic() < deadline, served.log.lines()
            time.sleep(0.02)
        assert served.client.get("/api/health").status_code == 200  # the server is fine
        time.sleep(0.1)
        lines = served.log.lines()
    closed = [line for line in lines if "client closed the connection" in line]
    assert closed and all(line.startswith("INFO ") for line in closed), lines
    assert any("GET /api/postings" in line for line in closed), lines
    assert not [record for record in served.log.records if record.levelno >= logging.WARNING], lines
    assert not [line for line in lines if "unhandled exception" in line or " 500 " in line], lines
    assert not [record for record in served.log.records if record.exc_info], lines

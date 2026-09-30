"""uat-bug-003: the Scout server's log file is always empty.

Root cause (READ, not this packet's to fix): ``present_api.py``'s
``Handler.log_message`` was overridden to a bare ``return``, silencing
``BaseHTTPRequestHandler``'s per-request log line, and no app-level logging
existed anywhere in the server path. ``run_supervisor.py`` redirects the
child process's stderr to the operator-visible log file, so anything this
module logs to stderr through the stdlib ``logging`` module lands there --
this packet's whole job is to actually log something.

This module owns exactly one logger, ``"gigai.scout.server"`` (module-level
constant ``LOGGER_NAME``), with a handler attached where the server actually
starts (``serve()``/``main()``), never at import time and never touching the
root logger -- a library caller that imports this module must not have its
own logging config hijacked.

What gets logged, and what never does:
  - One request line per request: time, method, path (query string
    stripped), status, duration in ms. Never headers, never request or
    response bodies (resume text and discovery preferences travel in
    request/response bodies -- see the PUT /api/setup marker-string test
    below, which is the ticket's literal repro).
  - App events: server start, setup saved (field *names* only), run
    started/finished/failed, Discover started/finished/failed, CSRF/Origin
    rejections (route + reason), and every unhandled route exception with
    its traceback (the existing 500 JSON response is unchanged -- see
    test_present_ui.py's ``_BlowsUpBackend`` tests for that contract, still
    covered there, not duplicated here).

Tests use ``caplog`` (pytest's logging capture) rather than a real file or
stderr redirect -- ``caplog`` attaches its own handler to the named logger
and is what keeps this suite quiet in CI while still asserting exactly what
gets logged; production is not silenced by anything here.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
import types
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import httpx
import pytest

from gigai.scout.find_jobs.contracts import FindJobsConfig, PinnedResume, RunRequest, SourceToggles
from gigai.scout.find_jobs.present_api import LOGGER_NAME, Backend, ScoutFindJobsBackend, serve

from .test_assess_failure_status import _ambiguous_ollama_fixture_config
from .test_m1_end_to_end import _fixture, _run_request

SECRET_MARKER = "SECRET-MARKER-123"

# ui/src/App.jsx's own polling contract: TERMINAL_STATUSES and
# POLL_INTERVAL_MS. Polling this way (not a tight sleep(0.05) loop) is what
# the UI actually does against GET /api/runs/{id} -- a real regression in
# that route (like r1's transient-RunError 500) shows up the same way here
# it would in the browser, and a passing test here is direct evidence the
# UI's own poll loop would not have broken either.
_UI_TERMINAL_STATUSES = {"succeeded", "failed", "blocked", "cancelled", "interrupted"}
_UI_POLL_INTERVAL_SECONDS = 2.0


def _poll_like_the_ui(client: httpx.Client, run_id: str, *, max_polls: int = 30) -> dict[str, object]:
    """Poll ``GET /api/runs/{id}`` on the UI's own cadence until terminal.

    Every poll must return 200 -- App.jsx's pollStatus stops polling and
    surfaces an error on any non-200 response (see its own comment: "must
    not spin forever pretending to still be running"), so a non-200 here is
    exactly the bug this test would need to catch, not something to retry
    past.
    """

    body: dict[str, object] = {}
    for _ in range(max_polls):
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in _UI_TERMINAL_STATUSES:
            return body
        time.sleep(_UI_POLL_INTERVAL_SECONDS)
    pytest.fail(f"run {run_id} did not terminalize after {max_polls} UI-cadence polls; last body={body!r}")


def _config() -> FindJobsConfig:
    return FindJobsConfig(
        roles=("staff backend",),
        merged_queries=("staff backend",),
        location="Denver, CO",
        remote=True,
        published_after=None,
        sources=SourceToggles(exa=True, ats=True, hiringcafe=False),
        countries=("US",),
        visa_sponsorship_required=True,
    )


def _setup_body(*, city: str = "Denver, CO") -> dict[str, object]:
    return {
        "roles": ["staff backend"],
        "titles_to_avoid": [],
        "countries": ["US"],
        "work_mode": "remote",
        "city": city,
        "visa_sponsorship_required": True,
        "exclude_companies": [],
        "watch_companies": [],
        "company_stage_size": None,
        "industries_include": [],
        "industries_exclude": [],
        "must_have_stack": [],
        "dealbreaker_stack": [],
        "cadence_days": 7,
        # The PUT body's marker lives in a field the request-line assertion
        # must never see: city carries free text an operator could type
        # (the ticket calls out resume text and preferences specifically).
        "budget_usd_per_session": 0.50,
    }


@dataclass
class _LoggingBackend:
    """A minimal Backend double: just enough for a GET and a PUT to succeed."""

    config: FindJobsConfig = field(default_factory=_config)
    wrote_setup: bool = False

    def read_config(self) -> tuple[FindJobsConfig, bytes]:
        from gigai.canonical import canonical_json_bytes

        return self.config, canonical_json_bytes(self.config.to_json())

    def resume_preview(self) -> PinnedResume | None:
        return None

    def start_run(
        self,
        run_request: RunRequest,
        config_bytes: bytes,
        on_run_allocated: Callable[[str], None],
    ) -> None:
        raise NotImplementedError

    def run_status(self, run_id: str):
        raise NotImplementedError

    def run_results(self, run_id: str):
        raise NotImplementedError

    def run_progress(self, run_id: str) -> dict[str, object]:
        raise NotImplementedError

    def read_setup(self) -> dict[str, object] | None:
        return None

    def write_setup(self, prefs_fields: dict[str, object]) -> dict[str, object]:
        self.wrote_setup = True
        return {**prefs_fields}

    def start_discovery(self, on_progress: Callable[[dict[str, object]], None]) -> str:
        raise NotImplementedError

    def latest_discovery(self) -> dict[str, object] | None:
        return None

    def discovery_running(self) -> bool:
        return False


@pytest.fixture
def running_server(request: pytest.FixtureRequest):
    backend: Backend = getattr(request, "param", None) or _LoggingBackend()
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}") as client:
            yield client, backend, port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# The ticket's literal repro: GET + PUT, one request line each, no marker.
# ---------------------------------------------------------------------------


def test_request_line_logged_for_get_and_put_never_leaks_the_body(running_server, caplog) -> None:
    client, _backend, port = running_server
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        get_response = client.get("/api/health")
        assert get_response.status_code == 200

        put_response = client.put(
            "/api/setup",
            json=_setup_body(city=SECRET_MARKER),
            headers={"Origin": f"http://127.0.0.1:{port}"},
        )
        assert put_response.status_code == 200

    records = [r for r in caplog.records if r.name == LOGGER_NAME]
    messages = [r.getMessage() for r in records]
    full_text = "\n".join(messages)

    # The marker (which travels in the PUT body) must never appear anywhere
    # in anything this logger emitted -- headers included, since the ticket
    # is explicit that neither bodies nor headers are ever logged.
    assert SECRET_MARKER not in full_text

    get_lines = [m for m in messages if "GET" in m and "/api/health" in m]
    assert len(get_lines) == 1, f"expected exactly one GET request line, got: {messages}"
    assert "200" in get_lines[0]

    put_lines = [m for m in messages if "PUT" in m and "/api/setup" in m]
    assert len(put_lines) == 1, f"expected exactly one PUT request line, got: {messages}"
    assert "200" in put_lines[0]


def test_request_line_strips_query_string(running_server, caplog) -> None:
    client, _backend, _port = running_server
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        response = client.get("/api/health?token=" + SECRET_MARKER)
        assert response.status_code == 200

    records = [r for r in caplog.records if r.name == LOGGER_NAME]
    messages = [r.getMessage() for r in records]
    full_text = "\n".join(messages)

    assert SECRET_MARKER not in full_text
    assert "?" not in "\n".join(m for m in messages if "/api/health" in m)


# ---------------------------------------------------------------------------
# Route exception -> traceback logged, 500 JSON unchanged.
# ---------------------------------------------------------------------------


class _BlowsUpBackend(_LoggingBackend):
    def read_config(self) -> tuple[FindJobsConfig, bytes]:
        raise RuntimeError("boom: read_config exploded")


@pytest.mark.parametrize("running_server", [_BlowsUpBackend()], indirect=True)
def test_route_exception_logs_traceback_and_still_returns_500(running_server, caplog) -> None:
    client, _backend, _port = running_server
    with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
        response = client.get("/api/config")

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"

    records = [r for r in caplog.records if r.name == LOGGER_NAME]
    assert records, "expected the route exception to be logged"
    combined = "\n".join(r.getMessage() + (r.exc_text or "") for r in records)
    # exc_info is only rendered into exc_text once formatted; fall back to
    # checking exc_info was attached at all if the handler hasn't formatted
    # it yet.
    has_traceback_text = "Traceback" in combined or any(r.exc_info for r in records)
    assert has_traceback_text, f"expected a traceback attached to a log record, got: {records}"
    assert any("boom" in (r.getMessage() + str(r.exc_info)) for r in records)


# ---------------------------------------------------------------------------
# CSRF/Origin rejection -> route + reason logged.
# ---------------------------------------------------------------------------


def test_csrf_rejection_logs_route_and_reason(running_server, caplog) -> None:
    client, backend, port = running_server
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        response = client.put(
            "/api/setup",
            content=json.dumps(_setup_body()).encode("utf-8"),
            headers={"Content-Type": "text/plain", "Origin": "https://evil.example"},
        )

    assert response.status_code in (403, 415)
    assert backend.wrote_setup is False

    records = [r for r in caplog.records if r.name == LOGGER_NAME]
    assert records, "expected the CSRF/Origin rejection to be logged"
    combined = "\n".join(r.getMessage() for r in records)
    assert "/api/setup" in combined
    # The reason is whichever guard tripped first (content-type here); the
    # important thing is *a* reason code/string is present, not which one.
    assert any(code in combined for code in ("unsupported_media_type", "forbidden_origin"))


# ---------------------------------------------------------------------------
# uat-bug-003 follow-up: find-jobs run finished/failed, logged once per run
# when the API first observes the terminal status (the run executes in a
# spawned child process -- see ScoutFindJobsBackend._log_run_terminal_once).
# Real child-process runs, same scaffolding as test_m1_end_to_end.py.
# ---------------------------------------------------------------------------


def test_run_finished_is_logged_once_when_status_first_goes_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    home, target, _workpad = _fixture(tmp_path)
    monkeypatch.setenv("EXA_API_KEY", "m1-test-key")
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_MODEL", "1")

    backend = ScoutFindJobsBackend(home_root=home, target=target)
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}", timeout=60.0) as client:
            config_digest = client.get("/api/config").json()["config_digest"]
            response = client.post("/api/run", json=_run_request(config_digest))
            assert response.status_code == 202, response.text
            run_id = response.json()["run_id"]

            with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
                deadline_body = _poll_like_the_ui(client, run_id)
                assert deadline_body["status"] == "succeeded", deadline_body
                # One more poll after the run is already terminal -- this
                # must never log a second "run finished" line for the same
                # run_id.
                again = client.get(f"/api/runs/{run_id}")
                assert again.status_code == 200, again.text
        finished_records = [
            r
            for r in caplog.records
            if r.name == LOGGER_NAME and "find-jobs run finished" in r.getMessage() and run_id in r.getMessage()
        ]
        assert len(finished_records) == 1, f"expected exactly one 'run finished' line, got: {finished_records}"
        message = finished_records[0].getMessage()
        assert f"run_id={run_id}" in message
        assert "status=succeeded" in message
        assert "postings=" in message
        assert "assessed=" in message
        assert "duration_ms=" in message
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_run_failed_is_logged_once_with_the_failure_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    endpoints, model_targets, profiles = _ambiguous_ollama_fixture_config()
    home, target, _workpad = _fixture(
        tmp_path, endpoints=endpoints, model_targets=model_targets, profiles=profiles
    )
    monkeypatch.setenv("EXA_API_KEY", "m1-test-key")
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")

    backend = ScoutFindJobsBackend(home_root=home, target=target)
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}", timeout=60.0) as client:
            config_digest = client.get("/api/config").json()["config_digest"]
            response = client.post("/api/run", json=_run_request(config_digest))
            assert response.status_code == 202, response.text
            run_id = response.json()["run_id"]

            with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
                deadline_body = _poll_like_the_ui(client, run_id)
                assert deadline_body["status"] == "failed", deadline_body
        failed_records = [
            r
            for r in caplog.records
            if r.name == LOGGER_NAME and "find-jobs run failed" in r.getMessage() and run_id in r.getMessage()
        ]
        assert len(failed_records) == 1, f"expected exactly one 'run failed' line, got: {failed_records}"
        message = failed_records[0].getMessage()
        assert f"run_id={run_id}" in message
        assert "error=" in message
        assert "multiple configured model targets use adapter 'ollama_local'" in message
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# r1: run.read_run_details's own "transient, retryable refusal" (a
# concurrent journal writer -- the run's own child process, mid-commit --
# can make one poll observe an uncommitted or in-flight write) must never
# reach the operator as a 500. Forces the exact RunError via monkeypatch
# (once, then succeeds) rather than trying to win a real race, since the
# real race is inherently timing-dependent and this is the deterministic
# proof the coordinator's re-run needs.
# ---------------------------------------------------------------------------


def test_transient_run_details_reconciliation_error_never_surfaces_as_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    from gigai import run as run_module
    from gigai.scout.find_jobs.contracts import AggregateStatus

    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir(parents=True)
    home.mkdir(parents=True)
    backend = ScoutFindJobsBackend(home_root=home, target=target)

    # _require_run only needs a resolvable workpad, not a real run -- stub it
    # so this test exercises exactly run_status's own RunError handling, not
    # the whole resolve_workpad/run machinery. _payload (called once the
    # transient window has passed) is stubbed the same way, for the same
    # reason -- this test's only subject is run_status's RunError handling.
    monkeypatch.setattr(backend, "_require_run", lambda run_id: SimpleNamespace(path=target))
    empty_payload = SimpleNamespace(rows=(), assessments=(), node_receipts=(), status=AggregateStatus.SUCCEEDED)
    monkeypatch.setattr(backend, "_payload", lambda run_id: empty_payload)

    calls = {"n": 0}

    def _flaky_read_run_details(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise run_module.RunError(
                "run_details_reconciliation_required: Run details differ from the journal; "
                "retry if a writer is active, otherwise reconcile the journal"
            )
        return {"status": "succeeded"}

    monkeypatch.setattr(run_module, "read_run_details", _flaky_read_run_details)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        first = backend.run_status("run_flaky_001")

    assert first.status == AggregateStatus.RUNNING
    assert first.node_receipts == ()
    # Logged, but quietly -- info, never as an unhandled exception/traceback.
    records = [r for r in caplog.records if r.name == LOGGER_NAME]
    assert any("run_flaky_001" in r.getMessage() for r in records)
    assert all(r.levelno <= logging.INFO for r in records if "run_flaky_001" in r.getMessage())
    assert all(r.exc_info is None for r in records if "run_flaky_001" in r.getMessage())

    # The next poll (the writer has since committed, matching the ticket's
    # "retry if a writer is active") must go through cleanly to the real
    # terminal status -- proving this is a one-shot transient tolerance,
    # not a permanent swallow of every RunError for this run_id.
    assert calls["n"] == 1
    second = backend.run_status("run_flaky_001")
    assert calls["n"] == 2
    assert second.status == AggregateStatus.SUCCEEDED


def test_non_transient_run_error_still_surfaces_as_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """A RunError that is NOT the reconciliation-required prefix must keep
    its current behavior (propagates out of run_status, which the handler's
    generic exception boundary turns into a 500) -- this fix is narrowly
    scoped to the one documented transient code, never a blanket swallow."""

    from gigai import run as run_module

    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir(parents=True)
    home.mkdir(parents=True)
    backend = ScoutFindJobsBackend(home_root=home, target=target)
    monkeypatch.setattr(backend, "_require_run", lambda run_id: SimpleNamespace(path=target))
    monkeypatch.setattr(
        run_module,
        "read_run_details",
        lambda **_kwargs: (_ for _ in ()).throw(run_module.RunError("run_id must be canonical")),
    )

    with pytest.raises(run_module.RunError, match="run_id must be canonical"):
        backend.run_status("not-a-real-run-id")


# ---------------------------------------------------------------------------
# uat-bug-003 follow-up: Discover finished/failed now also logs spend
# (cost_usd) -- a fake discovery module installed into sys.modules, same
# pattern as test_present_setup_discover.py's _install_fake_discovery_module.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeDiscoveryPrefsForLogging:
    roles: tuple = ("staff backend",)

    def to_json(self) -> dict[str, object]:
        return {"roles": list(self.roles)}


@dataclass(frozen=True)
class _FakeDiscoveryResultForLogging:
    discovery_id: str
    status: str
    cost_usd: float
    new_boards: tuple = ()
    started_at: str = "2026-09-24T00:00:00+00:00"
    finished_at: str | None = "2026-09-24T00:05:00+00:00"
    sources: tuple = ()
    skipped: dict = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        return {
            "discovery_id": self.discovery_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cost_usd": self.cost_usd,
            "sources": list(self.sources),
            "new_boards": list(self.new_boards),
            "skipped": dict(self.skipped),
        }


def _install_fake_discovery_module_for_logging(
    monkeypatch: pytest.MonkeyPatch, *, run_discovery_impl
) -> None:
    module = types.ModuleType("gigai.scout.find_jobs.discovery")
    module.DiscoveryPrefs = _FakeDiscoveryPrefsForLogging

    def load_prefs(*, home_root: Path, target: Path):
        return _FakeDiscoveryPrefsForLogging()

    def save_prefs(*, home_root: Path, target: Path, prefs) -> None:
        pass

    def latest_discovery(*, home_root: Path, target: Path):
        return None

    module.load_prefs = load_prefs
    module.save_prefs = save_prefs
    module.latest_discovery = latest_discovery
    module.run_discovery = run_discovery_impl

    monkeypatch.setitem(sys.modules, "gigai.scout.find_jobs.discovery", module)
    import gigai.scout.find_jobs as find_jobs_package

    monkeypatch.setattr(find_jobs_package, "discovery", module, raising=False)


def test_discover_finished_logs_spend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    def run_discovery_impl(*, home_root: Path, target: Path, prefs, on_progress=None):
        if on_progress is not None:
            on_progress({"stage": "discovery_done", "discovery_id": "disc_logging_001", "status": "succeeded", "new_boards": 2})
        return _FakeDiscoveryResultForLogging(
            discovery_id="disc_logging_001", status="succeeded", cost_usd=0.1234, new_boards=({"name": "acme"}, {"name": "beta"})
        )

    _install_fake_discovery_module_for_logging(monkeypatch, run_discovery_impl=run_discovery_impl)

    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir(parents=True)
    home.mkdir(parents=True)
    backend = ScoutFindJobsBackend(home_root=home, target=target)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        request_id = backend.start_discovery(lambda _event: None)
        assert request_id
        for _ in range(200):
            if not backend.discovery_running():
                break
            import time as _time

            _time.sleep(0.01)
        assert not backend.discovery_running()

    records = [r for r in caplog.records if r.name == LOGGER_NAME and "discover finished" in r.getMessage()]
    assert len(records) == 1, f"expected exactly one 'discover finished' line, got: {records}"
    message = records[0].getMessage()
    assert "discovery_id=disc_logging_001" in message
    assert "cost_usd=0.123400" in message
    assert "new_boards=2" in message


def test_discover_failed_logs_spend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    def run_discovery_impl(*, home_root: Path, target: Path, prefs, on_progress=None):
        if on_progress is not None:
            on_progress({"stage": "discovery_done", "discovery_id": "disc_logging_002", "status": "failed", "new_boards": 0})
        return _FakeDiscoveryResultForLogging(discovery_id="disc_logging_002", status="failed", cost_usd=0.02)

    _install_fake_discovery_module_for_logging(monkeypatch, run_discovery_impl=run_discovery_impl)

    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir(parents=True)
    home.mkdir(parents=True)
    backend = ScoutFindJobsBackend(home_root=home, target=target)

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        backend.start_discovery(lambda _event: None)
        for _ in range(200):
            if not backend.discovery_running():
                break
            import time as _time

            _time.sleep(0.01)
        assert not backend.discovery_running()

    records = [r for r in caplog.records if r.name == LOGGER_NAME and "discover failed" in r.getMessage()]
    assert len(records) == 1, f"expected exactly one 'discover failed' line, got: {records}"
    message = records[0].getMessage()
    assert "discovery_id=disc_logging_002" in message
    assert "cost_usd=0.020000" in message

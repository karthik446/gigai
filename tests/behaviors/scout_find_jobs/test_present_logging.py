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
import threading
from dataclasses import dataclass, field
from typing import Callable

import httpx
import pytest

from gigai.scout.find_jobs.contracts import FindJobsConfig, PinnedResume, RunRequest, SourceToggles
from gigai.scout.find_jobs.present_api import LOGGER_NAME, Backend, serve

SECRET_MARKER = "SECRET-MARKER-123"


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

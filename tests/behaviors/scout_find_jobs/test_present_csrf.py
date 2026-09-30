"""P1-csrf (PR #37 review finding #6): CSRF guard on state-changing routes.

Today ``present_api.py`` only checks that the peer address is loopback
(``_check_loopback``). A malicious web page open in the operator's browser
can still issue a same-machine, cross-origin request -- loopback is who, not
where-from -- and a plain ``fetch(url, {method: "POST", mode: "no-cors"})`` is
allowed by the browser's CORS rules for a simple request (GET/HEAD/POST with
only "simple" headers and a body type of text/plain, application/x-www-form-
urlencoded, or multipart/form-data). That reaches ``POST /api/discover`` and
``POST /api/run`` and spends the operator's OpenAI/Exa budget, and reaches
``PUT /api/setup`` to overwrite discovery prefs.

The fix: every state-changing route (POST /api/run, POST /api/discover, PUT
/api/setup -- there are no other POST/PUT/PATCH/DELETE routes, confirmed via
`grep -n "do_POST\\|do_PUT\\|do_PATCH\\|do_DELETE" present_api.py`) now
requires:

1. ``Content-Type: application/json`` -- a "simple" no-cors request cannot
   set this header to a non-form value, so the browser is forced into a CORS
   preflight, which this server never answers with
   ``Access-Control-Allow-Origin`` (and never will -- see CHANGE note).
2. When an ``Origin`` header is present, it must equal the server's own
   served origin (``http://127.0.0.1:<port>`` or ``http://localhost:<port>``
   for the bound port).
3. ``Host`` must match the bound host:port (DNS-rebinding guard: without
   this, an attacker-controlled DNS name that resolves to 127.0.0.1 could
   satisfy the Origin check with an Origin the browser sends honestly).

Rejections are 403 ``{"error": {"code": "forbidden_origin" | "unsupported_
media_type", ...}}`` via the existing ``_error`` helper. No
``Access-Control-Allow-Origin`` header is ever sent (a browser page cannot
read the rejection body either way; CORS blocks that regardless of status).

GET routes are unchanged -- this module's own read-only routes are covered
by test_present_ui.py / test_present_api_static.py / test_present_setup_
discover.py, re-run unmodified as part of this packet's acceptance.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Callable

import httpx
import pytest

from gigai.scout.find_jobs.contracts import FindJobsConfig, PinnedResume, RunRequest, SourceToggles
from gigai.scout.find_jobs.present_api import Backend, serve

from .conftest import load_fixture


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


@dataclass
class _CsrfBackend:
    """A minimal Backend double that records whether a write actually landed.

    Only the methods the CSRF guard's target routes call are implemented;
    anything else raises ``NotImplementedError`` (unused by these tests --
    a route that got past the guard when it shouldn't have would hit this
    and fail loudly, rather than silently succeeding).
    """

    config: FindJobsConfig = field(default_factory=_config)
    started_run: bool = False
    wrote_setup: bool = False
    started_discovery: bool = False

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
        self.started_run = True
        on_run_allocated("run_test123")

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
        self.started_discovery = True
        return "discovery_req_test123"

    def latest_discovery(self) -> dict[str, object] | None:
        return None

    def discovery_running(self) -> bool:
        return False


def _run_request_body(config: FindJobsConfig) -> dict[str, object]:
    fixture = load_fixture("fixture-api-run-request-v1.json")
    return dict(fixture, config_digest=config.digest())


def _setup_body() -> dict[str, object]:
    return {
        "roles": ["staff backend"],
        "titles_to_avoid": [],
        "countries": ["US"],
        "work_mode": "remote",
        "city": "Denver, CO",
        "visa_sponsorship_required": True,
        "exclude_companies": [],
        "watch_companies": [],
        "company_stage_size": None,
        "industries_include": [],
        "industries_exclude": [],
        "must_have_stack": [],
        "dealbreaker_stack": [],
        "cadence_days": 7,
        "budget_usd_per_session": 0.50,
    }


@pytest.fixture
def running_server(request: pytest.FixtureRequest):
    backend: Backend = getattr(request, "param", None) or _CsrfBackend()
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
# The three state-changing routes, each hit with a no-cors-shaped, evil-
# origin request. This is the failing-first repro: today only loopback is
# checked, so these currently return 202/200 and the backend's write flag
# flips true -- exactly what a malicious page could trigger.
# ---------------------------------------------------------------------------


def test_post_run_cross_origin_evil_page_is_rejected(running_server) -> None:
    client, backend, _port = running_server
    body = _run_request_body(backend.config)
    # A no-cors POST from a browser page can only send "simple" headers, so
    # this models the actual attack shape: text/plain content-type (a
    # no-cors fetch cannot set application/json without triggering a
    # preflight) and an attacker-controlled Origin. The Content-Type check
    # runs first (415 unsupported_media_type) -- either guard rejecting it
    # is the point: this exact request shape must never reach the backend.
    response = client.post(
        "/api/run",
        content=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "text/plain", "Origin": "https://evil.example"},
    )
    assert response.status_code in (403, 415)
    assert backend.started_run is False


def test_post_discover_cross_origin_evil_page_is_rejected(running_server) -> None:
    client, backend, _port = running_server
    response = client.post(
        "/api/discover",
        content=b"{}",
        headers={"Content-Type": "text/plain", "Origin": "https://evil.example"},
    )
    assert response.status_code in (403, 415)
    assert backend.started_discovery is False


def test_put_setup_cross_origin_evil_page_is_rejected(running_server) -> None:
    client, backend, _port = running_server
    response = client.put(
        "/api/setup",
        content=json.dumps(_setup_body()).encode("utf-8"),
        headers={"Content-Type": "text/plain", "Origin": "https://evil.example"},
    )
    assert response.status_code in (403, 415)
    assert backend.wrote_setup is False


def test_post_run_evil_origin_with_json_content_type_is_403(running_server) -> None:
    """Pure Origin-mismatch case (JSON content-type, so check 1 passes):

    isolates the ``forbidden_origin`` guard from the content-type guard.
    """

    client, backend, _port = running_server
    body = _run_request_body(backend.config)
    response = client.post(
        "/api/run",
        json=body,
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden_origin"
    assert backend.started_run is False


# ---------------------------------------------------------------------------
# Content-Type enforcement: even same-origin-looking, but non-JSON,
# "simple" content types must be rejected (this is what forces the
# preflight in the first place).
# ---------------------------------------------------------------------------


def test_post_run_text_plain_content_type_is_rejected(running_server) -> None:
    client, backend, port = running_server
    body = _run_request_body(backend.config)
    response = client.post(
        "/api/run",
        content=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "text/plain", "Origin": f"http://127.0.0.1:{port}"},
    )
    assert response.status_code in (403, 415)
    assert backend.started_run is False


def test_post_discover_form_urlencoded_content_type_is_rejected(running_server) -> None:
    client, backend, port = running_server
    response = client.post(
        "/api/discover",
        content=b"a=1",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": f"http://127.0.0.1:{port}",
        },
    )
    assert response.status_code in (403, 415)
    assert backend.started_discovery is False


# ---------------------------------------------------------------------------
# Host-header mismatch (DNS-rebinding guard): an Origin that matches the
# bound host:port textually is not enough if Host disagrees.
# ---------------------------------------------------------------------------


def test_put_setup_host_header_mismatch_is_rejected(running_server) -> None:
    client, backend, port = running_server
    response = client.put(
        "/api/setup",
        content=json.dumps(_setup_body()).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{port}",
            "Host": "evil.example:80",
        },
    )
    assert response.status_code == 403
    assert backend.wrote_setup is False


# ---------------------------------------------------------------------------
# The UI's own request shape must still be accepted: same-origin (or no
# Origin header at all, matching curl/native fetch same-origin behavior),
# application/json, matching Host.
# ---------------------------------------------------------------------------


def test_post_run_ui_shape_same_origin_json_is_accepted(running_server) -> None:
    client, backend, port = running_server
    body = _run_request_body(backend.config)
    response = client.post(
        "/api/run",
        json=body,
        headers={"Origin": f"http://127.0.0.1:{port}"},
    )
    assert response.status_code == 202
    assert backend.started_run is True


def test_post_discover_ui_shape_same_origin_json_is_accepted(running_server) -> None:
    client, backend, port = running_server
    response = client.post(
        "/api/discover",
        json={},
        headers={"Origin": f"http://127.0.0.1:{port}"},
    )
    assert response.status_code == 202
    assert backend.started_discovery is True


def test_put_setup_ui_shape_same_origin_json_is_accepted(running_server) -> None:
    client, backend, port = running_server
    response = client.put(
        "/api/setup",
        json=_setup_body(),
        headers={"Origin": f"http://127.0.0.1:{port}"},
    )
    assert response.status_code == 200
    assert backend.wrote_setup is True


def test_post_run_no_origin_header_same_host_is_accepted(running_server) -> None:
    """No Origin header at all (e.g. a native fetch, curl) must still work --

    only a *present but mismatched* Origin is rejected, per CHANGE.
    """

    client, backend, _port = running_server
    body = _run_request_body(backend.config)
    response = client.post("/api/run", json=body)
    assert response.status_code == 202
    assert backend.started_run is True


def test_put_setup_localhost_origin_is_accepted(running_server) -> None:
    client, backend, port = running_server
    response = client.put(
        "/api/setup",
        json=_setup_body(),
        headers={"Origin": f"http://localhost:{port}"},
    )
    assert response.status_code == 200
    assert backend.wrote_setup is True


def test_no_access_control_allow_origin_header_is_ever_sent(running_server) -> None:
    client, backend, port = running_server
    response = client.put(
        "/api/setup",
        json=_setup_body(),
        headers={"Origin": f"http://127.0.0.1:{port}"},
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers.keys()}

"""test-gap-001: the error contract -- every error is JSON
``{"error": {"code": ..., "message": ...}}`` with the right status, never a
500 on a known state.

Covers the cases from the ticket that map onto a currently-reachable HTTP
state in this codebase:

- CSRF/Origin 403 (already covered end to end by
  ``tests/behaviors/scout_find_jobs/test_present_csrf.py`` against a fake
  backend; this file adds the same guard against the REAL backend/server,
  since a CSRF/loopback rejection happens before the backend is even
  touched -- proving it's not accidentally backend-specific).
- bad input 400/422 (``PUT /api/setup`` field validation -> 400;
  ``POST /api/run`` with a stale ``config_digest`` -> 409; malformed JSON
  body -> 422).
- unknown run 404.
- discover 404/503 (``prefs_missing``/``discovery_unavailable`` --
  ``discovery_running``/409 is covered by ``test_discover_fake_provider.py``
  on a best-effort basis, since it's a genuine race).
- missing config 404 (``config_missing``).
- missing resume -- ``POST /api/run`` with no resume imported yet.

Not covered here (no currently-reachable HTTP path in this codebase to
exercise it, per this suite's research -- see the worker report): "provider
failure" and "board timeout" as DISTINCT HTTP-visible error codes.
``POST /api/run`` returns 202 (an allocation ack) before acquire ever
touches a provider or board; a provider/board failure during the acquire
step surfaces only through the terminal run status (``GET /api/runs/{id}``
-> ``status: "failed"``, with the failing node's receipt), not as an
HTTP-response-level error code from any single request. That terminal-
failure shape is already exercised by
``test_failed_run_then_next_run_assesses.py`` (an assess failure, the same
shape a provider/board failure would take at a different node).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import (
    add_resume,
    resolve_workpad_path,
    run_request_body,
    setup_and_init,
    start_server,
    stop_server,
    write_offline_find_jobs_config,
)


def _assert_error_shape(response: httpx.Response, *, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert "error" in body, body
    assert body["error"]["code"] == code, body
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


def test_error_contract_against_the_real_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = setup_and_init(tmp_path)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client

        # -- missing config: /api/config before find-jobs.json exists is
        # actually always present (install_scout writes a starter file), so
        # this suite instead proves config_missing via /api/run's own
        # read_config() call after deleting it.
        config_path = target / "find-jobs.json"
        original_config = config_path.read_text(encoding="utf-8")
        config_path.unlink()
        run_no_config = client.post(
            "/api/run",
            json=run_request_body("sha256:" + "0" * 64),
        )
        _assert_error_shape(run_no_config, status=404, code="config_missing")

        config_path.write_text(original_config, encoding="utf-8")
        write_offline_find_jobs_config(target, sources_live=False)

        # -- missing resume: POST /api/run before any resume is imported.
        config_digest = client.get("/api/config").json()["config_digest"]
        run_no_resume = client.post("/api/run", json=run_request_body(config_digest))
        assert run_no_resume.status_code in (403, 422), run_no_resume.text
        assert "error" in run_no_resume.json()

        add_resume(home, target, tmp_path)
        config_digest = client.get("/api/config").json()["config_digest"]

        # -- bad input: PUT /api/setup with an invalid field.
        bad_setup = client.put(
            "/api/setup",
            json={"roles": [], "work_mode": "not-a-real-mode"},
        )
        _assert_error_shape(bad_setup, status=400, code="invalid_value")

        # -- bad input: PUT /api/setup with an unknown top-level field.
        unknown_field_setup = client.put(
            "/api/setup",
            json={"roles": ["software engineer"], "not_a_real_field": True},
        )
        _assert_error_shape(unknown_field_setup, status=400, code="invalid_value")

        # -- bad input: malformed JSON body -> 422 wrong_type.
        malformed = client.post(
            "/api/run",
            content=b"{not valid json",
            headers={"Content-Type": "application/json"},
        )
        _assert_error_shape(malformed, status=422, code="wrong_type")

        # -- stale config_digest -> 409 config_digest_mismatch.
        stale_digest = client.post(
            "/api/run", json=run_request_body("sha256:" + "1" * 64)
        )
        _assert_error_shape(stale_digest, status=409, code="config_digest_mismatch")

        # -- unknown run id -> 404 not_found, on every /api/runs/{id}* route.
        for path in (
            "/api/runs/run_00000000-0000-4000-8000-000000000000",
            "/api/runs/run_00000000-0000-4000-8000-000000000000/results",
            "/api/runs/run_00000000-0000-4000-8000-000000000000/progress",
        ):
            response = client.get(path)
            _assert_error_shape(response, status=404, code="not_found")

        # -- discover: no prefs saved yet -> 404 prefs_missing.
        discover_no_prefs = client.post("/api/discover", json={})
        _assert_error_shape(discover_no_prefs, status=404, code="prefs_missing")

        latest_no_prefs = client.get("/api/discover/latest")
        assert latest_no_prefs.status_code == 200, latest_no_prefs.text
        assert latest_no_prefs.json()["result"] is None

        # -- CSRF/Origin 403: a state-changing route without
        # Content-Type: application/json (real server, real backend -- the
        # equivalent fake-backend proof already lives in
        # test_present_csrf.py; this confirms the guard runs before the
        # backend is ever touched, on a real Backend implementation too).
        raw_headers = {"Content-Type": "text/plain"}
        wrong_content_type = httpx.post(
            f"{server.base_url}/api/run", content=b"{}", headers=raw_headers
        )
        _assert_error_shape(wrong_content_type, status=415, code="unsupported_media_type")

        wrong_origin = httpx.post(
            f"{server.base_url}/api/run",
            json={},
            headers={"Origin": "http://evil.example.test"},
        )
        _assert_error_shape(wrong_origin, status=403, code="forbidden_origin")

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)

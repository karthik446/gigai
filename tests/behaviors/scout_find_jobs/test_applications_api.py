"""P9c: ``GET``/``POST /api/applications`` -- the application pipeline +
needs-action panels, and the posting card's "Mark applied" action.

Drives a real, offline run (the same seams ``test_runs_list_api.py`` uses,
via ``test_m1_end_to_end.py``'s ``_fixture``/``_run_request``) against a
real ``ScoutFindJobsBackend``/``serve()`` server -- in-process, no spawned
subprocess -- so the "mark applied on a real posting" case joins a
genuinely committed find-jobs posting through the A1 ``linked_posting``
join, never a hand-faked fixture.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import httpx
import pytest

from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve

from .test_m1_end_to_end import _fixture, _run_request

_POLL_DEADLINE_SECONDS = 30.0


def _poll_succeeded(client: httpx.Client, run_id: str) -> dict[str, object]:
    deadline = time.monotonic() + _POLL_DEADLINE_SECONDS
    last_body: object = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        last_body = response.text
        if response.status_code == 200:
            body = response.json()
            if body["status"] in {"succeeded", "failed", "blocked", "cancelled", "interrupted"}:
                return body
        time.sleep(0.05)
    pytest.fail(f"run {run_id} did not terminalize before timeout; last response={last_body!r}")
    raise AssertionError("unreachable")


@pytest.fixture
def running_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home, target, _workpad = _fixture(tmp_path)
    monkeypatch.setenv("EXA_API_KEY", "p9c-test-key")
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_MODEL", "1")

    backend = ScoutFindJobsBackend(home_root=home, target=target)
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}", timeout=60.0) as client:
            yield client
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _real_posting_url(client: httpx.Client) -> str:
    config_digest = client.get("/api/config").json()["config_digest"]
    run_response = client.post("/api/run", json=_run_request(config_digest))
    assert run_response.status_code == 202, run_response.text
    run_id = run_response.json()["run_id"]
    status_body = _poll_succeeded(client, run_id)
    assert status_body["status"] == "succeeded", status_body
    results = client.get(f"/api/runs/{run_id}/results").json()
    rows = results["payload"]["rows"]
    assert rows, "the offline fixture must have acquired at least one posting"
    return rows[0]["posting"]["normalized_url"]


def test_applications_list_is_empty_before_any_event(running_server) -> None:
    client = running_server
    response = client.get("/api/applications")
    assert response.status_code == 200, response.text
    assert response.json() == {"schema_version": "scout-applications-response:1", "applications": []}


def test_post_applications_csrf_rejects_a_foreign_origin(running_server) -> None:
    client = running_server
    response = client.post(
        "/api/applications",
        json={"normalized_url": "https://boards.greenhouse.io/acme/jobs/101", "event_kind": "applied"},
        headers={"Origin": "http://evil.example.com"},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden_origin"


@pytest.mark.parametrize(
    "body",
    [
        {"normalized_url": "https://boards.greenhouse.io/acme/jobs/101"},  # missing event_kind
        {"event_kind": "applied"},  # missing normalized_url
        {"normalized_url": "", "event_kind": "applied"},  # empty url
        {"normalized_url": "not a url", "event_kind": "applied"},  # unparseable url
        {"normalized_url": "https://boards.greenhouse.io/acme/jobs/101", "event_kind": "not_a_real_kind"},
        {"normalized_url": "https://boards.greenhouse.io/acme/jobs/101", "event_kind": "applied", "unknown_field": 1},
    ],
)
def test_post_applications_invalid_body_is_422(running_server, body) -> None:
    client = running_server
    response = client.post("/api/applications", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"]


def test_post_applications_unknown_url_still_records_but_unlinked(running_server) -> None:
    client = running_server
    response = client.post(
        "/api/applications",
        json={"normalized_url": "https://boards.greenhouse.io/nowhere/jobs/999999", "event_kind": "saved"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["schema_version"] == "scout-application-response:1"
    assert body["status"] == "recorded"
    assert body["event"]["external_ref"] == "https://boards.greenhouse.io/nowhere/jobs/999999"

    listed = client.get("/api/applications").json()
    assert len(listed["applications"]) == 1
    assert listed["applications"][0]["linked_posting"] is None


def test_post_then_get_round_trips_linked_to_the_real_posting(running_server) -> None:
    client = running_server
    normalized_url = _real_posting_url(client)

    response = client.post(
        "/api/applications",
        json={
            "normalized_url": normalized_url,
            "event_kind": "applied",
            "occurred_at": "2026-09-25T12:00:00Z",
            "notes": "referred by a friend",
        },
    )
    assert response.status_code == 201, response.text
    recorded = response.json()
    assert recorded["event"]["event_kind"] == "applied"
    assert recorded["event"]["external_ref"] == normalized_url
    assert recorded["event"]["notes"] == "referred by a friend"

    listed = client.get("/api/applications").json()
    assert listed["schema_version"] == "scout-applications-response:1"
    assert len(listed["applications"]) == 1
    row = listed["applications"][0]
    assert row["event_kind"] == "applied"
    assert row["external_ref"] == normalized_url
    assert row["linked_posting"] is not None
    assert row["linked_posting"]["normalized_url"] == normalized_url
    # Internal bookkeeping fields are never leaked into the API response.
    assert "event_path" not in row
    assert "journal_sequence" not in row


def test_post_applications_defaults_occurred_at_to_now_when_omitted(running_server) -> None:
    client = running_server
    response = client.post(
        "/api/applications",
        json={"normalized_url": "https://boards.greenhouse.io/acme/jobs/101", "event_kind": "saved"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["event"]["occurred_at"]

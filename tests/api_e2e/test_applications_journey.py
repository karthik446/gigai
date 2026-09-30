"""P9c: ``POST``/``GET /api/applications`` journey -- run a real find-jobs
run, mark its posting applied, then confirm the projection shows it linked.

Driven the same way every other journey in this suite is: through the real,
supervised server (``run_supervisor.start``), never a library call or an
in-process fake server thread.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.api_e2e.after_journey import assert_clean_and_healthy, timed_request
from tests.api_e2e.harness import (
    add_resume,
    poll_until_terminal,
    resolve_workpad_path,
    run_request_body,
    setup_and_init,
    start_server,
    stop_server,
    write_offline_find_jobs_config,
)


def test_mark_applied_then_applications_shows_it_linked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client

        # -- before any application: the list is genuinely empty -----------
        empty_response = client.get("/api/applications")
        assert empty_response.status_code == 200, empty_response.text
        assert empty_response.json() == {"schema_version": "scout-applications-response:1", "applications": []}

        config_digest = client.get("/api/config").json()["config_digest"]
        run_response = client.post("/api/run", json=run_request_body(config_digest))
        assert run_response.status_code == 202, run_response.text
        run_id = run_response.json()["run_id"]
        status_body = poll_until_terminal(client, run_id)
        assert status_body["status"] == "succeeded", status_body

        results_body = client.get(f"/api/runs/{run_id}/results").json()
        rows = results_body["payload"]["rows"]
        assert rows, "acquire must have found the fixture Greenhouse posting"
        posting = rows[0]["posting"]
        normalized_url = posting["normalized_url"]

        # -- CSRF: no Origin header is fine (same-origin/curl); a foreign
        # Origin is refused (403) before the route body ever runs ----------
        bad_origin_response = client.post(
            "/api/applications",
            json={"normalized_url": normalized_url, "event_kind": "applied"},
            headers={"Origin": "http://evil.example.com"},
        )
        assert bad_origin_response.status_code == 403, bad_origin_response.text
        assert bad_origin_response.json()["error"]["code"] == "forbidden_origin"

        # -- invalid body: 422 ------------------------------------------------
        invalid_response = client.post("/api/applications", json={"normalized_url": normalized_url})
        assert invalid_response.status_code == 422, invalid_response.text
        assert invalid_response.json()["error"]["code"] == "invalid_value"

        # -- mark applied on the real posting --------------------------------
        applied_response, applied_latency = timed_request(
            "POST /api/applications",
            lambda: client.post(
                "/api/applications",
                json={"normalized_url": normalized_url, "event_kind": "applied", "notes": "referred by a friend"},
            ),
        )
        assert applied_response.status_code == 201, applied_response.text
        applied_body = applied_response.json()
        assert applied_body["schema_version"] == "scout-application-response:1"
        assert applied_body["status"] == "recorded"
        assert applied_body["event"]["external_ref"] == normalized_url
        assert applied_body["event"]["event_kind"] == "applied"
        applied_latency.assert_within_budget()

        # -- an unknown URL still records, unlinked --------------------------
        unknown_response = client.post(
            "/api/applications",
            json={"normalized_url": "https://boards.greenhouse.io/nowhere/jobs/999999", "event_kind": "saved"},
        )
        assert unknown_response.status_code == 201, unknown_response.text

        # -- GET shows both, the real posting linked -------------------------
        list_response, list_latency = timed_request("GET /api/applications", lambda: client.get("/api/applications"))
        assert list_response.status_code == 200, list_response.text
        list_body = list_response.json()
        assert list_body["schema_version"] == "scout-applications-response:1"
        assert len(list_body["applications"]) == 2
        by_ref = {item["external_ref"]: item for item in list_body["applications"]}
        linked = by_ref[normalized_url]
        assert linked["event_kind"] == "applied"
        assert linked["linked_posting"] is not None
        assert linked["linked_posting"]["normalized_url"] == normalized_url
        unlinked = by_ref["https://boards.greenhouse.io/nowhere/jobs/999999"]
        assert unlinked["linked_posting"] is None
        list_latency.assert_within_budget()

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)

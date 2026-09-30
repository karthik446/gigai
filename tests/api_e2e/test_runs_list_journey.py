"""P9c: ``GET /api/runs`` journey -- run a real find-jobs run, then confirm
it shows up in the runs list with its counts, newest first.

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


def test_run_then_runs_list_shows_it_with_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client

        # -- before any run: the list is genuinely empty, not an error -----
        empty_response, empty_latency = timed_request("GET /api/runs (empty)", lambda: client.get("/api/runs"))
        assert empty_response.status_code == 200, empty_response.text
        assert empty_response.json() == {"schema_version": "scout-runs-list-response:1", "runs": []}
        empty_latency.assert_within_budget()

        # -- one profile, for the profile_id assertion below ----------------
        profiles_response = client.get("/api/profiles")
        assert profiles_response.status_code == 200, profiles_response.text
        selected_profile_id = profiles_response.json()["selected_profile_id"]
        assert selected_profile_id

        config_digest = client.get("/api/config").json()["config_digest"]
        run_response = client.post("/api/run", json=run_request_body(config_digest))
        assert run_response.status_code == 202, run_response.text
        run_id = run_response.json()["run_id"]

        status_body = poll_until_terminal(client, run_id)
        assert status_body["status"] == "succeeded", status_body

        results_body = client.get(f"/api/runs/{run_id}/results").json()
        payload = results_body["payload"]
        assert payload["rows"], "acquire must have found the fixture Greenhouse posting"
        assert payload["assessments"], "assess must have produced at least one assessment"

        # -- the headline assertion: GET /api/runs shows this run ----------
        runs_response, runs_latency = timed_request("GET /api/runs", lambda: client.get("/api/runs"))
        assert runs_response.status_code == 200, runs_response.text
        runs_body = runs_response.json()
        assert runs_body["schema_version"] == "scout-runs-list-response:1"
        assert len(runs_body["runs"]) == 1
        entry = runs_body["runs"][0]
        assert entry["run_id"] == run_id
        assert entry["status"] == "succeeded"
        assert entry["profile_id"] == selected_profile_id
        assert entry["created_at"], "created_at must be populated from run-details.json's started_at"
        assert entry["counts"]["found"] == len(payload["rows"])
        assert entry["counts"]["assessed"] == len(payload["assessments"])
        assert entry["counts"]["new"] >= 1, "the fixture posting is acquired NEW on a first run"
        assert entry["counts"]["matched"] >= 0
        runs_latency.assert_within_budget()

        # -- the profile_id filter narrows correctly ------------------------
        filtered_response = client.get("/api/runs", params={"profile_id": selected_profile_id})
        assert filtered_response.status_code == 200, filtered_response.text
        assert len(filtered_response.json()["runs"]) == 1

        other_response = client.get("/api/runs", params={"profile_id": "profile_does_not_exist"})
        assert other_response.status_code == 200, other_response.text
        assert other_response.json()["runs"] == []

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)

"""test-gap-001: setup -> config -> run -> poll progress -> results.

The headline HTTP-only journey: every step driven through the real,
supervised server (``gigai scout run --no-browser``, started via
``run_supervisor.start`` -- the same entry point the CLI uses), never a
library call or an in-process fake server thread. ``GET /api/setup`` is
included in its 404-prefs-missing shape (no discovery module exists yet --
see ``harness.py``'s module docstring), which is itself the real, current
behavior of that route.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.api_e2e.after_journey import (
    CONFIG_ROUTE_LATENCY_BUDGET_SECONDS,
    assert_clean_and_healthy,
    timed_request,
)
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


def test_setup_config_run_poll_results_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client

        # -- server start sanity: /api/health -----------------------------
        health_response, health_latency = timed_request(
            "GET /api/health", lambda: client.get("/api/health")
        )
        assert health_response.status_code == 200, health_response.text
        assert health_response.json() == {"status": "ok"}
        health_latency.assert_within_budget()

        # -- setup: no discovery module landed yet -> 404 prefs_missing ---
        # with a usable prefill, not a 500. Real, current behavior of this
        # route (F1/S25 has not shipped the discovery module).
        setup_response, setup_latency = timed_request(
            "GET /api/setup", lambda: client.get("/api/setup")
        )
        assert setup_response.status_code == 404, setup_response.text
        setup_body = setup_response.json()
        assert setup_body["error"]["code"] == "prefs_missing"
        assert "prefill" in setup_body["error"]
        setup_latency.assert_within_budget()

        # find-jobs.json now has real roles/location + the offline fixture
        # sources -- the starter config's placeholders would never match
        # the fixture's Greenhouse posting.
        write_offline_find_jobs_config(target, sources_live=True)

        # -- config --------------------------------------------------------
        config_response, config_latency = timed_request(
            "GET /api/config",
            lambda: client.get("/api/config"),
            budget_seconds=CONFIG_ROUTE_LATENCY_BUDGET_SECONDS,
        )
        assert config_response.status_code == 200, config_response.text
        config_body = config_response.json()
        assert config_body["schema_version"] == "scout-find-jobs-config-response:1"
        assert config_body["resume_preview"] is not None
        assert config_body["resume_missing_hint"] is None
        config_digest = config_body["config_digest"]
        # uat-bug-008 ("13s /api/config", per the ticket's own survey)
        # landed (78fcbf2): this is a real assertion again. The budget
        # itself is scaled for CI noise, not loosened outright -- see
        # ``after_journey.py``'s ``CONFIG_ROUTE_LATENCY_BUDGET_SECONDS``
        # comment and ``tests/support/latency.py`` (ci-fix-pr37-r2).
        config_latency.assert_within_budget()

        # -- run -------------------------------------------------------------
        run_response, run_latency = timed_request(
            "POST /api/run",
            lambda: client.post("/api/run", json=run_request_body(config_digest)),
        )
        assert run_response.status_code == 202, run_response.text
        run_body = run_response.json()
        assert run_body["schema_version"] == "scout-find-jobs-run-response:1"
        assert run_body["status"] == "pending"
        run_id = run_body["run_id"]
        run_latency.assert_within_budget()

        # -- poll progress + status until terminal --------------------------
        status_body = poll_until_terminal(client, run_id)
        assert status_body["status"] == "succeeded", status_body
        assert [item["node_slug"] for item in status_body["node_receipts"]] == [
            "acquire",
            "assess",
            "present",
        ]

        progress_response, progress_latency = timed_request(
            "GET /api/runs/{run_id}/progress", lambda: client.get(f"/api/runs/{run_id}/progress")
        )
        assert progress_response.status_code == 200, progress_response.text
        progress_body = progress_response.json()
        assert progress_body["steps"]["acquire"]["status"] == "done"
        assert progress_body["steps"]["assess"]["status"] == "done"
        progress_latency.assert_within_budget()

        # -- results -----------------------------------------------------
        results_response, results_latency = timed_request(
            "GET /api/runs/{run_id}/results", lambda: client.get(f"/api/runs/{run_id}/results")
        )
        assert results_response.status_code == 200, results_response.text
        results_body = results_response.json()
        payload = results_body["payload"]
        assert payload["rows"], "acquire must have found the fixture Greenhouse posting"
        assert payload["assessments"], "assess must have produced at least one assessment"
        assert payload["assessments"][0]["matrix"]
        # P2 (v0.1.9): the verdict-carrying fixture model answer flows through
        # to the API's own assessment payload (plan section "P2").
        assert payload["assessments"][0]["verdict"] == "pending_user_answers"
        assert len(payload["node_receipts"]) == 3
        results_latency.assert_within_budget()
        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)

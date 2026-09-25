"""P6: run -> POST /api/runs/{run_id}/rank -> scores; /results carries them;
no key -> empty scores and the run still assesses (fail open).

Fake Jev via ``GIGAI_SCOUT_FIND_JOBS_TEST_JEV=1`` (``bindings._test_jev_handler``)
-- no live Jev call, mirroring the HTTP/model fixture seams the rest of this
suite already uses.
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


def _run_to_terminal(client) -> tuple[str, str]:
    config_response = client.get("/api/config")
    assert config_response.status_code == 200, config_response.text
    config_digest = config_response.json()["config_digest"]

    run_response = client.post("/api/run", json=run_request_body(config_digest))
    assert run_response.status_code == 202, run_response.text
    run_id = run_response.json()["run_id"]

    status_body = poll_until_terminal(client, run_id)
    assert status_body["status"] == "succeeded", status_body
    return run_id, config_digest


def test_run_rank_results_carries_scores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch, test_jev=True)
    try:
        client = server.client
        run_id, _config_digest = _run_to_terminal(client)

        # -- rank ------------------------------------------------------
        rank_response, rank_latency = timed_request(
            "POST /api/runs/{run_id}/rank",
            lambda: client.post(f"/api/runs/{run_id}/rank", json={}),
        )
        assert rank_response.status_code == 200, rank_response.text
        rank_body = rank_response.json()
        assert rank_body["schema_version"] == "scout-jev-rank-response:1"
        assert rank_body["run_id"] == run_id
        assert rank_body["scores"], "acquire must have found the fixture Greenhouse posting to rank"
        first_score = rank_body["scores"][0]
        assert first_score["fit"] in {"strong", "maybe", "no"}
        assert isinstance(first_score["score"], int)
        assert first_score["cached"] is False
        rank_latency.assert_within_budget()

        # -- rerun proves the cache: 0 new calls, 0 cost ----------------
        rerank_response = client.post(f"/api/runs/{run_id}/rank", json={})
        assert rerank_response.status_code == 200, rerank_response.text
        rerank_body = rerank_response.json()
        assert rerank_body["total_cost_usd"] == "0.000000"
        assert all(item["cached"] for item in rerank_body["scores"])

        # -- results carries rank_scores additively ---------------------
        results_response = client.get(f"/api/runs/{run_id}/results")
        assert results_response.status_code == 200, results_response.text
        results_body = results_response.json()
        assert "rank_scores" in results_body
        assert results_body["rank_scores"], "GET /results must carry the same scores /rank produced"
        assert results_body["rank_scores"][0]["normalized_url"] == first_score["normalized_url"]

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


def test_no_key_means_empty_scores_and_run_still_assesses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P6's own fail-open contract: no JEV_API_KEY -> rank_scores == () and
    the run still assesses normally (today's ordering, no failure)."""

    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    # test_jev left at its default (False): no GIGAI_SCOUT_FIND_JOBS_TEST_JEV,
    # no JEV_API_KEY -- exactly the "operator never added a Jev key" state.
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        run_id, _config_digest = _run_to_terminal(client)

        results_response = client.get(f"/api/runs/{run_id}/results")
        assert results_response.status_code == 200, results_response.text
        results_body = results_response.json()
        assert results_body["rank_scores"] == []
        assert results_body["payload"]["assessments"], "assess must still run with no Jev key"

        rank_response = client.post(f"/api/runs/{run_id}/rank", json={})
        assert rank_response.status_code == 200, rank_response.text
        assert rank_response.json()["scores"] == []

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)

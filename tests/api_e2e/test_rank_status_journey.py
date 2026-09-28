"""uat-bug-021: a run says what Jev did for it, through the real server.

The lesson of the ticket: Jev was measured in isolation and ``POST /rank``
was tested, but no test drove a real supervised run with a Jev key, so
nothing noticed that the run itself never scored a posting. These journeys
do: the real supervisor, a real find-jobs run in its child process, a
stored resume, a Jev key, and the fake Jev transport
(``GIGAI_SCOUT_FIND_JOBS_TEST_JEV=1``); no live call.

1. With a key: the RUN scores its postings. ``GET /progress`` carries
   ``rank_status`` ("scored N of M", what it cost, the day's usage), the
   sealed ``outputs/acquire.json`` carries the scores and the postings the
   run assessed are scored ones, the server log has the line, the spend is
   in the home's ledger, and reading the scores afterwards (``POST /rank``,
   ``GET /results``) costs nothing: they come from the cache the run filled.
2. Without a key: the run succeeds as before and says why it has no scores
   (``skipped: no_key``), in ``GET /progress`` and in the log.
3. "Score with Jev": a run whose own pass scored nothing (its cost cap
   allowed no call) is scored by ``POST /rank`` with ``"start": true``,
   which answers at once (``status: running``); the page then repeats
   ``POST /rank {}`` until the pass has ended. Reading the run before the
   click (``GET /results``, and the ``POST /rank {}`` a page sends when it
   opens a run) asked Jev nothing.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from gigai.scout.find_jobs.jev_rank import RankStatus
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

RESUME_WORDS = "Python service experience"
JEV_KEY = "api-e2e-test-jev-key"
JEV_CALL_USD = 0.0005  # bindings._test_jev_handler


def _run(client) -> str:
    config_digest = client.get("/api/config").json()["config_digest"]
    response = client.post("/api/run", json=run_request_body(config_digest))
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    status = poll_until_terminal(client, run_id)
    assert status["status"] == "succeeded", status
    return run_id


def _server_log(home: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted((home / "logs").glob("scout-*.log")))


def _ledger(home: Path) -> list[dict]:
    lines: list[dict] = []
    for path in sorted((home / "cache" / "scout" / "jev" / "spend").glob("*.jsonl")):
        lines.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    return lines


def test_a_run_with_a_jev_key_scores_its_postings_and_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIGAI_JEV_COST_CAP_USD", raising=False)
    monkeypatch.delenv("GIGAI_JEV_DAILY_BUDGET_USD", raising=False)
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch, test_jev=True)
    try:
        client = server.client
        workpad = resolve_workpad_path(home, target)
        run_id = _run(client)

        # -- the run's progress says what Jev did ------------------------------
        progress_response, progress_latency = timed_request(
            "GET /api/runs/{run_id}/progress", lambda: client.get(f"/api/runs/{run_id}/progress")
        )
        assert progress_response.status_code == 200, progress_response.text
        progress = progress_response.json()
        progress_latency.assert_within_budget()
        postings = len(progress["postings"])
        assert postings >= 1
        cost = postings * JEV_CALL_USD
        expected = RankStatus("scored", postings, postings, None, 0.25, cost, None, cost, 0.5)
        assert progress["rank_status"] == expected.to_json()
        assert progress["rank_status"]["line"].startswith(f"Jev: scored {postings} of {postings} (cost $")
        assert progress["rank_status"]["usage_line"].endswith(" of $0.50 today")

        # -- the symptom: the run's sealed output has the scores ---------------
        sealed = json.loads((workpad / "runs" / run_id / "outputs" / "acquire.json").read_text(encoding="utf-8"))
        assert "rank_scores" in sealed, "outputs/acquire.json has no rank_scores"
        assert [item["normalized_url"] for item in sealed["rank_scores"]] == [row["posting"]["normalized_url"] for row in sealed["rows"]]
        assert all(item["fit"] == "strong" and item["score"] == 89 and item["cached"] is False for item in sealed["rank_scores"])
        # The scores are used: the postings the run assessed are scored ones.
        scored = {item["normalized_url"] for item in sealed["rank_scores"] if item["score"] is not None}
        assert sealed["selected_postings"] and {item["normalized_url"] for item in sealed["selected_postings"]} <= scored

        # -- the scores were paid for once, by the run -------------------------
        ledger = _ledger(home)
        assert [(line["where"], line["run_id"], line["cost_usd"]) for line in ledger] == [("acquire", run_id, "0.000500")] * postings
        results = client.get(f"/api/runs/{run_id}/results").json()
        assert [(item["score"], item["cached"]) for item in results["rank_scores"]] == [(89, True)] * postings
        rank = client.post(f"/api/runs/{run_id}/rank", json={})
        assert rank.status_code == 200, rank.text
        rank_body = rank.json()
        assert rank_body["total_cost_usd"] == "0.000000"
        assert len(rank_body["scores"]) == postings and all(item["cached"] for item in rank_body["scores"])
        assert rank_body["rank_status"]["status"] == "scored"
        assert rank_body["rank_status"]["line"] == f"Jev: scored {postings} of {postings} (cost $0.00)"
        assert rank_body["usage"]["spent_today_usd"] == f"{cost:.6f}" and rank_body["usage"]["daily_budget_usd"] == "0.50"
        assert len(_ledger(home)) == postings, "reading the scores was paid for"

        # -- one line in the server log, without the resume or the key ---------
        log = _server_log(home)
        lines = [line for line in log.splitlines() if "scout acquire: Jev: " in line]
        assert len(lines) == 1, log
        assert " INFO " in lines[0] and f"{expected.line} [run_id={run_id} " in lines[0]
        assert JEV_KEY not in log and RESUME_WORDS not in log
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


def test_a_run_without_a_jev_key_says_why_it_has_no_scores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIGAI_JEV_DAILY_BUDGET_USD", raising=False)
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        workpad = resolve_workpad_path(home, target)
        run_id = _run(client)

        progress = client.get(f"/api/runs/{run_id}/progress").json()
        postings = len(progress["postings"])
        expected = RankStatus("skipped", 0, postings, "no_key", None, 0.0, None, 0.0, 0.5)
        assert progress["rank_status"] == expected.to_json()
        assert progress["rank_status"]["text"] == "skipped: no_key"
        assert progress["rank_status"]["line"] == "Jev: skipped (no Jev key)"
        sealed = json.loads((workpad / "runs" / run_id / "outputs" / "acquire.json").read_text(encoding="utf-8"))
        assert "rank_scores" not in sealed
        assert client.get(f"/api/runs/{run_id}/results").json()["payload"]["assessments"], "the run still assesses"

        rank_body = client.post(f"/api/runs/{run_id}/rank", json={}).json()
        assert rank_body["scores"] == [] and rank_body["rank_status"]["text"] == "skipped: no_key"
        assert _ledger(home) == []

        lines = [line for line in _server_log(home).splitlines() if "scout acquire: Jev: " in line]
        assert len(lines) == 1 and " WARNING " in lines[0]
        assert f"Jev: skipped (no Jev key) [run_id={run_id} reason=no_key " in lines[0]
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


def test_score_with_jev_answers_at_once_and_a_page_that_reads_never_spends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIGAI_JEV_DAILY_BUDGET_USD", raising=False)
    # The run's own pass may make no call: nothing is scored by the run.
    monkeypatch.setenv("GIGAI_JEV_COST_CAP_USD", "0")
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch, test_jev=True)
    try:
        client = server.client
        workpad = resolve_workpad_path(home, target)
        run_id = _run(client)

        progress = client.get(f"/api/runs/{run_id}/progress").json()
        postings = len(progress["postings"])
        assert progress["rank_status"]["status"] == "skipped" and progress["rank_status"]["reason"] == "cost_cap_reached"
        assert progress["rank_status"]["line"] == "Jev: skipped (cost cap $0.00 reached before any score)"

        # -- reading the run, again and again, asks Jev nothing ------------------
        for _ in range(3):
            results = client.get(f"/api/runs/{run_id}/results").json()
            assert [item["score"] for item in results["rank_scores"]] == [None] * postings
            opened = client.post(f"/api/runs/{run_id}/rank", json={}).json()
            assert opened["rank_status"]["reason"] == "not_requested"
            assert opened["rank_status"]["line"] == "Jev: skipped (not asked yet)"
            assert [item["score"] for item in opened["scores"]] == [None] * postings
        time.sleep(0.5)
        assert _ledger(home) == [], "a page that read the run was paid for"
        assert "scout rank: Jev: " not in _server_log(home)

        # -- "Score with Jev": answered at once, repeated until the pass ended ---
        first, first_latency = timed_request(
            "POST /api/runs/{run_id}/rank",
            lambda: client.post(f"/api/runs/{run_id}/rank", json={"start": True, "cost_cap_usd": "0.25"}),
        )
        assert first.status_code == 200, first.text
        first_latency.assert_within_budget()
        body = first.json()
        assert body["rank_status"]["status"] == "running", body["rank_status"]
        assert len(body["scores"]) == postings
        deadline = time.monotonic() + 30.0
        while body["rank_status"]["status"] == "running":
            assert time.monotonic() < deadline, body
            time.sleep(0.05)
            body = client.post(f"/api/runs/{run_id}/rank", json={}).json()
        cost = postings * JEV_CALL_USD
        assert body["rank_status"] == RankStatus("scored", postings, postings, None, 0.25, cost, None, cost, 0.5).to_json()
        assert body["total_cost_usd"] == f"{cost:.6f}" and body["unscored"] == 0
        assert [item["score"] for item in body["scores"]] == [89] * postings

        # One pass, however often it was asked for: each posting paid for once.
        assert [(line["where"], line["run_id"]) for line in _ledger(home)] == [("rank", run_id)] * postings
        results = client.get(f"/api/runs/{run_id}/results").json()
        assert [(item["score"], item["cached"]) for item in results["rank_scores"]] == [(89, True)] * postings
        again = client.post(f"/api/runs/{run_id}/rank", json={"start": True}).json()
        assert again["rank_status"]["line"] == f"Jev: scored {postings} of {postings} (cost $0.00)"
        assert len(_ledger(home)) == postings

        lines = [line for line in _server_log(home).splitlines() if "scout rank: Jev: " in line]
        assert len(lines) == 1 and f"Jev: scored {postings} of {postings} (cost $" in lines[0]
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)

"""SCOPE-ADD-3 C1: run -> the run's own ranking step -> ``POST /rank`` makes a durable rank record.

Through the real supervisor, a real find-jobs run in its child process and
the real server; the model is the suite's fake Ollama
(``GIGAI_SCOUT_FIND_JOBS_TEST_MODEL=1``). Its rank branch answers a rank-v1
prompt with VALID strict id-keyed JSON and a deterministic score read from
the posting's own digest (``fit <n>`` in the title; 60 when there is none;
``flags=`` become blockers), so every ranking pass here produces REAL scores.

This is the gap that hid uat-bug-021 for weeks: a ranking pass that silently
fails open (every posting unscored, the run carrying on in date order) looked
green. These journeys fail if that happens: they assert the fake model was
called (the sealed ``rank.json`` has model batches, calls, tokens), the scores
are present and non-empty, the order follows the scores, a blocked row is
demoted and not dropped, and the 500-row import is the top 500 BY RANK, on a
fixture where the best-ranked postings are the OLDEST (date order would drop
them). No Jev: the server has no Jev key and no Jev test seam.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from gigai.scout.find_jobs import bindings, model_rank
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
from tests.support.latency import latency_bound

BULK_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_BULK_POSTINGS"
IMPORT_CAP = 500
ACME_101 = 101 - 1000  # ``_job_number`` of the Exa fixture's own posting


def _run_to_terminal(client, *, deadline_seconds: float = 30.0) -> str:
    config_digest = client.get("/api/config").json()["config_digest"]
    run_response = client.post("/api/run", json=run_request_body(config_digest))
    assert run_response.status_code == 202, run_response.text
    run_id = run_response.json()["run_id"]
    status_body = poll_until_terminal(client, run_id, deadline_seconds=deadline_seconds)
    assert status_body["status"] == "succeeded", status_body
    return run_id


def _job_number(normalized_url: str) -> int:
    """``.../acme/jobs/1003`` -> 3 (the bulk fixture's index; 0 is the newest job)."""

    return int(normalized_url.rstrip("/").rsplit("/", 1)[1]) - 1000


def _sealed_rank(workpad: Path, run_id: str) -> dict:
    return json.loads((workpad / "runs" / run_id / "outputs" / "rank.json").read_text(encoding="utf-8"))


def _assert_the_fake_model_ranked(sealed: dict, *, postings: int) -> None:
    """The pass ran the model and every row got a score (a fail-open pass fails here)."""

    totals = sealed["totals"]
    assert sealed["status"] == "complete" and sealed["fail_open_reason"] is None, sealed["fail_open_reason"]
    assert len(sealed["postings"]) == postings
    assert all(isinstance(item["score"], int) for item in sealed["postings"]), "unscored rows: ranking failed open"
    assert totals["scored"] == postings and totals["unscored"] == 0
    # The fake was actually called: model batches, valid, with calls and tokens.
    model_batches = [batch for batch in sealed["batches"] if batch["source"] == "model"]
    assert model_batches and all(batch["valid"] for batch in model_batches)
    assert totals["calls"] >= len(model_batches) >= 1 and totals["total_tokens"]
    assert sealed["resolved_model"] == bindings.TEST_MODEL_NAME


def test_the_fixture_answers_the_real_rank_prompt() -> None:
    """The fake's rank marker is the first line of ``model_rank.PROMPT`` (the two must not drift)."""

    assert model_rank.PROMPT.splitlines()[0].startswith(bindings.TEST_MODEL_RANK_MARKER)
    assert model_rank.PROMPT_VERSION == "rank-v1"


def test_the_run_ranks_as_a_step_and_a_rank_click_makes_a_committed_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        run_id = _run_to_terminal(client)
        workpad = resolve_workpad_path(home, target)

        # -- the run's own ranking step: sealed, streamed, and REAL ------
        sealed = _sealed_rank(workpad, run_id)
        assert sealed["schema_version"] == "scout-rank:1" and sealed["kind"] == "run" and sealed["run_id"] == run_id
        assert sealed["model_target"] == "ollama_local" and sealed["prompt_version"] == "rank-v1"
        _assert_the_fake_model_ranked(sealed, postings=1)
        assert [item["score"] for item in sealed["postings"]] == [bindings.TEST_MODEL_RANK_DEFAULT_SCORE]
        lines = (workpad / "runs" / run_id / "progress" / "rank.jsonl").read_text(encoding="utf-8").splitlines()
        events = [json.loads(line)["event"] for line in lines]
        assert events[0] == "started" and "batch" in events and events[-1] == "finished"

        progress = client.get(f"/api/runs/{run_id}/progress", params={"summary": "1"}).json()
        rank_status = progress["rank_status"]
        assert rank_status["ranker"] == "model" and rank_status["ranked"] == 1 and rank_status["scored"] == 1
        assert rank_status["text"] == "scored 1 of 1"

        # -- the scores reach the reads ----------------------------------
        results = client.get(f"/api/runs/{run_id}/results").json()
        assert results["payload"]["assessments"], "assess must still run after ranking"
        assert [item["score"] for item in results["rank_scores"]] == [bindings.TEST_MODEL_RANK_DEFAULT_SCORE]
        page = client.get(f"/api/runs/{run_id}/results", params={"limit": 10}).json()
        assert [row["rank"]["score"] for row in page["payload"]["rows"]] == [bindings.TEST_MODEL_RANK_DEFAULT_SCORE]

        # -- a read starts nothing ---------------------------------------
        read = client.post(f"/api/runs/{run_id}/rank", json={}).json()
        assert read["rank_record"] is None, read
        assert [item["score"] for item in read["scores"]] == [bindings.TEST_MODEL_RANK_DEFAULT_SCORE], read
        assert not list((workpad / "runs").glob("rank_*"))

        # -- the click: a durable rank record, scored ---------------------
        started, latency = timed_request(
            "POST /api/runs/{run_id}/rank (start)",
            lambda: client.post(f"/api/runs/{run_id}/rank", json={"start": True}),
        )
        assert started.status_code == 200, started.text
        latency.assert_within_budget()
        record_id = started.json()["rank_record"]["record_id"]
        assert record_id.startswith("rank_")
        deadline = time.monotonic() + latency_bound(30.0)
        body = started.json()
        while body["rank_record"]["status"] == "running":
            assert time.monotonic() < deadline, body
            time.sleep(0.1)
            body = client.post(f"/api/runs/{run_id}/rank", json={}).json()
        assert body["rank_record"]["record_id"] == record_id and body["rank_record"]["status"] == "complete"
        assert [item["score"] for item in body["scores"]] == [bindings.TEST_MODEL_RANK_DEFAULT_SCORE]
        details = json.loads((workpad / "runs" / record_id / "run-details.json").read_text(encoding="utf-8"))
        assert details["kind"] == "rank" and details["parent_run_id"] == run_id and details["status"] == "complete"
        record_rank = json.loads((workpad / "runs" / record_id / "outputs" / "rank.json").read_text(encoding="utf-8"))
        # The run's own pass already paid for this posting: the click is answered from the score cache, same scores.
        assert record_rank["status"] == "complete" and record_rank["fail_open_reason"] is None
        assert [item["score"] for item in record_rank["postings"]] == [bindings.TEST_MODEL_RANK_DEFAULT_SCORE]
        assert all(item["cached"] for item in record_rank["postings"])

        # -- the run history is unchanged: a rank record is not a run ----
        listed = client.get("/api/runs").json()
        assert [item["run_id"] for item in listed["runs"]] == [run_id]
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


def test_a_blocked_row_is_demoted_not_dropped_and_the_order_follows_the_scores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """12 postings (under the import cap): scores drive the order; the blocked row keeps its score, ranks last, stays."""

    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.setenv(BULK_ENV, "12")
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        run_id = _run_to_terminal(client)
        workpad = resolve_workpad_path(home, target)

        sealed = _sealed_rank(workpad, run_id)
        # 12 bulk jobs plus the Exa fixture's own acme/101 row (no ``fit``: the default score).
        _assert_the_fake_model_ranked(sealed, postings=13)
        by_job = {_job_number(item["normalized_url"]): item for item in sealed["postings"]}
        assert sorted(by_job) == [ACME_101, *range(12)]
        assert by_job[ACME_101]["score"] == bindings.TEST_MODEL_RANK_DEFAULT_SCORE
        # The newest job scored 99 but names a blocker: it keeps the score and is demoted.
        assert by_job[0]["score"] == 99 and by_job[0]["demoted"] and by_job[0]["blockers"] == ["clearance"]
        assert not any(item["demoted"] for number, item in by_job.items() if number)
        assert totals_demoted(sealed) == 1

        # /progress: the postings in rank order, the blocked one last but present.
        progress = client.get(f"/api/runs/{run_id}/progress", params={"summary": "1"}).json()
        ordered = progress["postings"]
        assert len(ordered) == 13, "a demoted row must not be dropped"
        scores = [item["rank"]["score"] for item in ordered]
        assert all(isinstance(score, int) for score in scores), scores
        assert scores == sorted(scores[:-1], reverse=True) + [99], scores
        assert scores[:3] == [95, 95, 95]  # the three oldest are the best ranked
        assert ordered[-1]["rank"]["demoted"] is True and _job_number(ordered[-1]["normalized_url"]) == 0

        # /progress serves the folded rank counts and "Assessing X of Y".
        rank = progress["rank"]
        assert (rank["status"], rank["ranked"], rank["scored"], rank["demoted"], rank["total"]) == ("complete", 13, 13, 1, 13)
        assert rank["text"] == "Ranked 13 of 13" and rank["batches"] >= 1
        counts = progress["assess_counts"]
        assert counts is not None and counts["selected"] >= 1
        assert counts["status"] == "done" and 1 <= counts["finished"] <= counts["selected"]
        assert counts["text"] == f"Assessed {counts['finished']} of {counts['selected']}"

        # The grid reads: every row has its score; the blocked row is present.
        page = client.get(f"/api/runs/{run_id}/results", params={"limit": 50}).json()
        assert page["total"] == 13
        assert all(isinstance(row["rank"]["score"], int) for row in page["payload"]["rows"])
        blocked = [row for row in page["payload"]["rows"] if row["rank"]["demoted"]]
        assert len(blocked) == 1 and _job_number(blocked[0]["posting"]["normalized_url"]) == 0
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


def totals_demoted(sealed: dict) -> int:
    return sealed["totals"]["demoted"]


def test_the_import_is_the_top_500_by_rank_not_the_newest_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """506 postings (505 bulk + the Exa fixture's row): the 20 best-ranked are the OLDEST, so date order would drop 5 of them; rank order keeps all 20."""

    bulk = IMPORT_CAP + 5
    total = bulk + 1  # plus the Exa fixture's own acme/101 row
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.setenv(BULK_ENV, str(bulk))
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        started = time.monotonic()
        run_id = _run_to_terminal(client, deadline_seconds=180.0)
        print(f"bulk run took {time.monotonic() - started:.1f}s")
        workpad = resolve_workpad_path(home, target)

        sealed = _sealed_rank(workpad, run_id)
        _assert_the_fake_model_ranked(sealed, postings=total)
        scores = {_job_number(item["normalized_url"]): item["score"] for item in sealed["postings"]}
        oldest_20 = set(range(bulk - 20, bulk))
        assert all(scores[number] == 95 for number in oldest_20)

        # Only the top 500 by rank are imported.
        page = client.get(f"/api/runs/{run_id}/results", params={"limit": IMPORT_CAP}).json()
        assert page["total"] == IMPORT_CAP
        imported = {_job_number(row["posting"]["normalized_url"]) for row in page["payload"]["rows"]}
        assert len(imported) == IMPORT_CAP
        # Date order (newest first) would have dropped the 5 oldest -- all best-ranked. Rank order keeps every one.
        assert oldest_20 <= imported, sorted(oldest_20 - imported)
        # What rank order dropped: the demoted row, then the lowest scores. No dropped row outscores an imported one.
        dropped = set(scores) - imported
        assert len(dropped) == 6 and 0 in dropped
        lowest = min(scores[number] for number in imported)
        assert all(scores[number] <= lowest for number in dropped - {0}), (sorted(dropped), lowest)

        progress = client.get(f"/api/runs/{run_id}/progress", params={"summary": "1"}).json()
        assert progress["not_imported_count"] == 6
        rank = progress["rank"]
        assert (rank["status"], rank["ranked"], rank["scored"], rank["total"]) == ("complete", total, total, total)
        assert rank["text"] == f"Ranked {total:,} of {total:,}"
        assert progress["assess_counts"]["selected"] >= 1
        # Assess picks by rank, not date: the best-ranked (oldest) rows are assessed, the blocked newest one is not.
        assessed = {_job_number(item["normalized_url"]) for item in progress["assessments"] if item.get("status") == "assessed"}
        assert assessed & oldest_20 and 0 not in assessed, sorted(assessed)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)

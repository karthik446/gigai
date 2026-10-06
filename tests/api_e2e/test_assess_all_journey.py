"""uat-bug-042: "Assess all new" and a run with "All new postings", over HTTP.

Through the real supervisor, a real find-jobs run in its child process and
the real server; the model is the suite's fake Ollama
(``GIGAI_SCOUT_FIND_JOBS_TEST_MODEL=1``) and the boards are the fixture
transport with a bulk Greenhouse board (``GIGAI_SCOUT_FIND_JOBS_TEST_BULK_POSTINGS``).

Journey 1 (the Jobs page button): a run with a cap of 1 assesses one
posting; ``POST /assess-all {}`` plans every other new posting and starts
nothing; ``{"start": true}`` assesses them in the background through the job
page's own single-posting path (each lands in the quick-assess store with
origin ``job_page``); the live counts go up as they land; a second start
queues nothing and makes no new record; the workpad stays clean.

Journey 2 (the run dialog's "All new postings"): a run started with
``selection_cap: "all"`` assesses every new posting in the run itself, seals
``"all"``, and leaves nothing for "Assess all new".
"""

from __future__ import annotations

import time
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
from tests.support.latency import latency_bound

BULK_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_BULK_POSTINGS"
EXCLUDED = {"location_mismatch", "role_mismatch", "duplicate", "unchanged"}


def _run(client, **overrides) -> str:
    config_digest = client.get("/api/config").json()["config_digest"]
    response = client.post("/api/run", json=run_request_body(config_digest, **overrides))
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    status = poll_until_terminal(client, run_id, deadline_seconds=60.0)
    assert status["status"] == "succeeded", status
    return run_id


def _expected_queue(payload: dict) -> list[str]:
    assessed = {item["posting"]["normalized_url"] for item in payload["assessments"]}
    assessed |= {item["normalized_url"] for item in payload.get("carried_forward_assessments") or []}
    reasons = {item["posting"]["normalized_url"]: item["reason"] for item in payload["not_assessed"]}

    def copy_key(posting: dict) -> tuple[str, str, str]:  # the fixture's titles differ only by their text
        return (posting["company"].lower(), posting["title"].lower(), posting["location"])

    covered = {copy_key(row["posting"]) for row in payload["rows"] if row["posting"]["normalized_url"] in assessed}
    queue = []
    for row in payload["rows"]:
        url = row["posting"]["normalized_url"]
        if row["outcome"] not in {"new", "edited"} or url in assessed or reasons.get(url) in EXCLUDED:
            continue
        if copy_key(row["posting"]) in covered:
            continue  # a near-copy of a posting assessed or queued before it
        covered.add(copy_key(row["posting"]))
        queue.append(url)
    return queue


def test_assess_all_new_assesses_the_rest_of_the_run_in_the_background(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(BULK_ENV, "8")
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        run_id = _run(client, selection_cap=1)
        payload = client.get(f"/api/runs/{run_id}/results").json()["payload"]
        assert len(payload["assessments"]) == 1, "a cap of 1 assesses one posting"
        expected = _expected_queue(payload)
        assert len(expected) >= 4, f"the fixture must leave several new postings unassessed: {expected}"

        # A read plans and starts nothing.
        read, read_latency = timed_request(
            "POST /api/runs/{run_id}/assess-all (read)",
            lambda: client.post(f"/api/runs/{run_id}/assess-all", json={}),
        )
        assert read.status_code == 200, read.text
        read_latency.assert_within_budget()
        body = read.json()
        assert body["schema_version"] == "scout-find-jobs-assess-all:1" and body["skip_reason"] is None
        assert body["job"] is None
        plan = body["plan"]
        assert plan["count"] == len(expected) and plan["model_target"] == "ollama_local" and plan["concurrency"] == 4
        # The run's own assessment was timed, so the estimate is a measured one.
        assert plan["per_call_source"] == "run" and plan["per_call_samples"] == 1
        assert isinstance(plan["per_call_seconds"], float) and isinstance(plan["estimate_minutes"], int)
        assert body["counts"] == {"assessed": 1, "matched": 0, "needs_answers": 1}
        assert client.get("/api/assessments").json()["items"] == []

        started, start_latency = timed_request(
            "POST /api/runs/{run_id}/assess-all (start)",
            lambda: client.post(f"/api/runs/{run_id}/assess-all", json={"start": True}),
        )
        assert started.status_code == 200, started.text
        start_latency.assert_within_budget()
        job = started.json()["job"]
        assert job["record_id"].startswith("aa_") and job["total"] == len(expected) and job["concurrency"] == 4

        seen_assessed: list[int] = []
        deadline = time.monotonic() + latency_bound(60.0)
        body = started.json()
        while body["job"]["status"] == "running":
            assert time.monotonic() < deadline, body
            seen_assessed.append(body["counts"]["assessed"])
            time.sleep(0.05)
            body = client.post(f"/api/runs/{run_id}/assess-all", json={}).json()
        job = body["job"]
        assert job["status"] == "complete", job
        assert job["assessed"] == len(expected) and job["failed"] == 0 and job["remaining"] == 0
        assert job["text"] == f"{len(expected)} of {len(expected)} assessed"
        assert seen_assessed == sorted(seen_assessed), "the counts only go up as results land"
        # Every result is the job page's: in the store, for this run's postings.
        items = client.get("/api/assessments").json()["items"]
        assert sorted(item["job"]["job_identity"] for item in items) == sorted(expected)
        assert all(item["origin"] == "job_page" for item in items)
        assert body["counts"]["assessed"] == 1 + len(expected)
        assert body["counts"]["needs_answers"] == 1 + len(expected), "the fixture model asks about GCP on every posting"
        assert body["plan"]["count"] == 0 and body["plan"]["per_call_source"] == "assess_all"

        # Nothing left: a start queues nothing and makes no new record.
        again = client.post(f"/api/runs/{run_id}/assess-all", json={"start": True}).json()
        assert again["plan"]["count"] == 0 and again["job"]["record_id"] == job["record_id"]
        records = list((home / "scout").glob("*/assess_all/aa_*"))
        assert [path.name for path in records] == [job["record_id"]]

        bad = client.post(f"/api/runs/{run_id}/assess-all", json={"start": "yes"})
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "wrong_type"
        assert client.post("/api/runs/run_missing/assess-all", json={}).status_code == 404

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


def test_a_run_with_all_new_postings_assesses_every_new_posting_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(BULK_ENV, "8")
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        run_id = _run(client, selection_cap="all")
        payload = client.get(f"/api/runs/{run_id}/results").json()["payload"]
        new_rows = [row for row in payload["rows"] if row["outcome"] == "new"]
        reasons = {item["posting"]["normalized_url"]: item["reason"] for item in payload["not_assessed"]}
        # Nothing is over a cap; the Exa row has no posting text ("failed", as in every run).
        assert "over_cap" not in reasons.values() and set(reasons.values()) <= {"duplicate", "failed"}, reasons
        # 8 bulk + the Exa row; 1006/1007 are near-copies of 1005 (never candidates, as in every run).
        assert len(new_rows) == 9 and len(payload["assessments"]) == 6, "no per-company cap: every new posting"
        # Left for "Assess all new": only the row the run could not read (the job page path fetches it).
        assert _expected_queue(payload) == ["https://boards.greenhouse.io/acme/jobs/101"]
        page = client.get(f"/api/runs/{run_id}/results", params={"limit": 50}).json()
        assert page["counts"]["assessed"] == len(payload["assessments"])

        body = client.post(f"/api/runs/{run_id}/assess-all", json={}).json()
        assert body["plan"]["count"] == 1 and body["counts"]["assessed"] == len(payload["assessments"])
        assert body["plan"]["per_call_source"] == "run" and body["plan"]["per_call_samples"] == 6

        workpad = resolve_workpad_path(home, target)
        sealed = (workpad / "runs" / run_id / "sealed" / "find-jobs-run-input.json").read_text(encoding="utf-8")
        assert '"selection_cap":"all"' in sealed.replace(" ", "")
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)

"""0110-019: the Jobs page's posted window, over HTTP, against stored boards.

Through the real supervisor, a real find-jobs run in its child process and
the real server; the model is the suite's fake Ollama
(``GIGAI_SCOUT_FIND_JOBS_TEST_MODEL=1``), and the boards are the fixture
transport's bulk Greenhouse board with postings of known ages
(``GIGAI_SCOUT_FIND_JOBS_TEST_BULK_AGE_DAYS``: 2, 5, 20, 25, 45 and 50 days
old). Exa is off, so the only board requests are "Update sources"'s.

The operator's case (UAT 0.1.10.3): the run searched 10 days; the window is
widened to 30 and the Jobs page must show more postings without a new run.

1. A 10-day run holds the two postings of the last 10 days and assesses one
   (cap 1). ``POST /posted-window {}`` reads ``run_days`` / ``searched_days``
   10 and searches nothing.
2. An answer is recorded (it re-assesses that posting into the store): the
   assessed set and the answers before the click.
3. ``{"days": 30}``: the two postings 20 and 25 days old are added to the
   SAME run (the paged results go from 2 rows to 4; ``GET /api/runs`` still
   lists one run), with no board request and nothing committed to the
   workpad's runs but a rank record.
4. Only the added postings are ranked and assessed: the rank pass reads the
   run's two rows from the score cache; the cap of 1 assesses the top-ranked
   added posting; the run's sealed assessment, the stored assessment and the
   answers are byte-for-byte what they were.
5. A second ``{"days": 30}`` adds nothing; ``{"days": 60}`` adds the two
   older postings; a 422 for a bad ``days``; a 404 for an unknown run.

A second test: a 30-day run already holds the four postings, so 30d -> 10d is
the page's own filter (no request: ``postedWindowModel.js``, pinned under
node in ``test_ui_posted_window_model.py``); here the served ``published_at``
values are what that filter reads, and 10 of the last days keep two of four.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import time
from pathlib import Path

import httpx
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
AGES_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_BULK_AGE_DAYS"
AGES = (2, 5, 20, 25, 45, 50)
ACME_BOARD = "https://boards.greenhouse.io/acme"


def _url(index: int) -> str:
    return f"https://boards.greenhouse.io/acme/jobs/{1000 + index}"


def _prepare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, max_age_days: int):
    monkeypatch.setenv(BULK_ENV, str(len(AGES)))
    monkeypatch.setenv(AGES_ENV, ",".join(str(age) for age in AGES))
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    path = target / "find-jobs.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["sources"] = {"exa": False, "ats": True, "hiringcafe": False}
    config["max_age_days"] = max_age_days
    path.write_text(json.dumps(config), encoding="utf-8")
    return home, target


def _update_sources(client: httpx.Client) -> None:
    assert client.post("/api/watchlist", json={"url": ACME_BOARD}).status_code == 201
    assert client.post("/api/sources/update", json={}).status_code == 202
    deadline = time.monotonic() + latency_bound(60.0)
    while True:
        body = client.get("/api/sources/update").json()
        if not body["running"]:
            assert body["update"]["status"] == "succeeded", body
            return
        assert time.monotonic() < deadline, body
        time.sleep(0.2)


def _run(client: httpx.Client, **overrides) -> str:
    config_digest = client.get("/api/config").json()["config_digest"]
    response = client.post("/api/run", json=run_request_body(config_digest, **overrides))
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    assert poll_until_terminal(client, run_id, deadline_seconds=120.0)["status"] == "succeeded"
    return run_id


def _page(client: httpx.Client, run_id: str) -> dict:
    response = client.get(f"/api/runs/{run_id}/results", params={"limit": 50})
    assert response.status_code == 200, response.text
    return response.json()


def _urls(page: dict) -> list[str]:
    return sorted(row["posting"]["url"] for row in page["payload"]["rows"])


def _wait(client: httpx.Client, run_id: str, done) -> dict:
    """Poll the two follow routes (as the page does) until ``done(rank, assess_all)``."""

    deadline = time.monotonic() + latency_bound(90.0)
    while True:
        rank = client.post(f"/api/runs/{run_id}/rank", json={}).json()
        assess = client.post(f"/api/runs/{run_id}/assess-all", json={}).json()
        if done(rank, assess):
            return {"rank": rank, "assess": assess}
        assert time.monotonic() < deadline, {"rank": rank["rank_record"], "assess": assess["job"]}
        time.sleep(0.1)


def _stored(client: httpx.Client) -> dict[str, dict]:
    return {item["job"]["job_identity"]: item for item in client.get("/api/assessments").json()["items"]}


def test_widening_the_window_adds_only_the_older_postings_to_the_same_run_and_reassesses_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = _prepare(tmp_path, monkeypatch, max_age_days=10)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        workpad = resolve_workpad_path(home, target)
        _update_sources(client)

        # -- 1. the 10-day run -------------------------------------------------
        run_id = _run(client, selection_cap=1)
        before = _page(client, run_id)
        assert _urls(before) == [_url(0), _url(1)], "the run holds the postings of the last 10 days only"
        assert before["total"] == 2
        run_assessments = before["payload"]["assessments"]
        assert len(run_assessments) == 1, "a cap of 1 assesses one posting"
        assessed_by_run = run_assessments[0]["posting"]["normalized_url"]

        read = client.post(f"/api/runs/{run_id}/posted-window", json={})
        assert read.status_code == 200, read.text
        state = read.json()
        assert state["schema_version"] == "scout-find-jobs-posted-window:1" and state["skip_reason"] is None
        assert state["run_days"] == 10 and state["searched_days"] == 10
        assert state["choices"] == [7, 10, 30, 60] and state["added_total"] == 0 and state["search"] is None
        assert _page(client, run_id)["total"] == 2, "a read searches nothing"

        # -- 2. an answer, and the assessed set before the click ---------------
        answered = client.post(
            "/api/answers",
            json={"question_id": "cloud:gcp", "answer": "Yes, two years on GCP.", "reassess": {"job_identity": assessed_by_run}},
        )
        assert answered.status_code == 201, answered.text
        stored_before = _stored(client)
        assert set(stored_before) == {assessed_by_run}
        answers_before = client.get("/api/answers").json()
        assert "cloud:gcp" in json.dumps(answers_before), answers_before
        runs_before = client.get("/api/runs").json()["runs"]
        assert [run["run_id"] for run in runs_before] == [run_id]
        sealed_before = {
            path.relative_to(workpad).as_posix(): path.read_bytes()
            for path in sorted((workpad / "runs" / run_id).rglob("*"))
            if path.is_file() and "progress" not in path.parts
        }

        # -- 3. the click: 30 days ----------------------------------------------
        found, latency = timed_request(
            "POST /api/runs/{run_id}/posted-window (search)",
            lambda: client.post(f"/api/runs/{run_id}/posted-window", json={"days": 30}),
        )
        assert found.status_code == 200, found.text
        latency.assert_within_budget()
        body = found.json()
        assert body["skip_reason"] is None and body["run_days"] == 10 and body["searched_days"] == 30
        assert body["search"]["days"] == 30 and body["search"]["added"] == 2 and body["search"]["matched"] == 4
        assert body["added_total"] == 2 and body["assess"] == {"added": 2, "limit": 1}
        assert body["rank"]["record_id"].startswith("rank_") and body["rank"]["action"] == "started"

        after = _page(client, run_id)
        assert after["total"] == 4, "10d -> 30d shows more postings"
        assert _urls(after) == [_url(0), _url(1), _url(2), _url(3)]
        added_rows = {row["posting"]["url"]: row for row in after["payload"]["rows"] if row["posting"]["url"] in {_url(2), _url(3)}}
        assert all(row["outcome"] == "new" for row in added_rows.values())
        assert [run["run_id"] for run in client.get("/api/runs").json()["runs"]] == [run_id], "no new run in Runs"
        assert not (workpad / "runs" / run_id / "raw").exists(), "the search made a board request"
        one = client.get(f"/api/runs/{run_id}/posting", params={"url": _url(3)})
        assert one.status_code == 200 and one.json()["row"]["posting"]["text"], "an added posting opens with its stored text"

        # -- 4. only the added postings are ranked and assessed -------------------
        done = _wait(
            client,
            run_id,
            lambda rank, assess: rank["rank_record"] is not None
            and rank["rank_record"]["status"] != "running"
            and assess["job"] is not None
            and assess["job"]["status"] != "running",
        )
        record = done["rank"]["rank_record"]
        assert record["record_id"] == body["rank"]["record_id"] and record["status"] == "complete", record
        scores = {item["normalized_url"]: item for item in done["rank"]["scores"]}
        assert len(scores) == 4 and all(item["score"] is not None for item in scores.values())
        rank_json = json.loads((workpad / "runs" / record["record_id"] / "outputs" / "rank.json").read_text(encoding="utf-8"))
        cached = {item["normalized_url"] for item in rank_json["postings"] if item["cached"]}
        assert cached == {_url(0), _url(1)}, "the run's own rows come from the score cache: only the added rows cost a call"

        job = done["assess"]["job"]
        assert job["status"] == "complete" and job["total"] == 1 and job["assessed"] == 1, job
        stored_after = _stored(client)
        # The cap of 1: the top-ranked added posting (fit 95, the older one) and nothing else.
        assert set(stored_after) == {assessed_by_run, _url(3)}
        assert stored_after[_url(3)]["origin"] == "job_page"
        # Nothing already assessed is reassessed; the answers are untouched.
        assert stored_after[assessed_by_run] == stored_before[assessed_by_run]
        assert client.get("/api/answers").json() == answers_before
        final = _page(client, run_id)
        assert final["payload"]["assessments"] == run_assessments
        assert final["counts"] == before["counts"], "the run's own counts are the sealed run's"
        sealed_after = {
            path.relative_to(workpad).as_posix(): path.read_bytes()
            for path in sorted((workpad / "runs" / run_id).rglob("*"))
            if path.is_file() and "progress" not in path.parts
        }
        assert sealed_after == sealed_before, "the sealed run is not rewritten"
        assert [run["run_id"] for run in client.get("/api/runs").json()["runs"]] == [run_id]
        assert client.get("/api/runs").json()["runs"][0]["counts"] == runs_before[0]["counts"]

        # -- 5. again, wider, and the refusals ---------------------------------
        again = client.post(f"/api/runs/{run_id}/posted-window", json={"days": 30}).json()
        assert again["search"]["added"] == 0 and again["added_total"] == 2 and again["rank"] is None and again["assess"] is None
        assert _page(client, run_id)["total"] == 4
        wider = client.post(f"/api/runs/{run_id}/posted-window", json={"days": 60}).json()
        assert wider["search"]["added"] == 2 and wider["added_total"] == 4 and wider["searched_days"] == 60
        assert _urls(_page(client, run_id)) == [_url(index) for index in range(6)]
        _wait(
            client,
            run_id,
            lambda rank, assess: rank["rank_record"]["status"] != "running"
            and rank["rank_record"]["record_id"] == wider["rank"]["record_id"]
            and (assess["job"] is None or assess["job"]["status"] != "running"),
        )
        # 1004 and 1005 are near-copies of 1003 (same title): never queued, as in every run.
        assert set(_stored(client)) == {assessed_by_run, _url(3)}
        assert _stored(client)[assessed_by_run] == stored_before[assessed_by_run]
        assert client.post(f"/api/runs/{run_id}/posted-window", json={}).json()["searched_days"] == 60

        for bad, code in (({"days": 0}, "invalid_value"), ({"days": 366}, "invalid_value"), ({"days": "30"}, "wrong_type"), ({"window": 30}, "unknown_key")):
            refused = client.post(f"/api/runs/{run_id}/posted-window", json=bad)
            assert refused.status_code == 422 and refused.json()["error"]["code"] == code, refused.text
        assert client.post("/api/runs/run_missing/posted-window", json={}).status_code == 404
        records = list((home / "scout").glob("*/posted_window/*.json"))
        assert [path.name for path in records] == [f"{run_id}.json"]
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


def test_a_run_that_searched_30_days_holds_what_the_10_day_chip_hides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = _prepare(tmp_path, monkeypatch, max_age_days=30)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        workpad = resolve_workpad_path(home, target)
        _update_sources(client)
        run_id = _run(client, selection_cap=1)
        page = _page(client, run_id)
        assert _urls(page) == [_url(index) for index in range(4)]
        state = client.post(f"/api/runs/{run_id}/posted-window", json={}).json()
        assert state["run_days"] == 30 and state["searched_days"] == 30

        # What the 10d chip reads: each row's own posting date.
        cutoff = datetime.now(timezone.utc) - timedelta(days=10)
        published = {
            row["posting"]["url"]: datetime.fromisoformat(row["posting"]["published_at"].replace("Z", "+00:00"))
            for row in page["payload"]["rows"]
        }
        assert sorted(url for url, at in published.items() if at >= cutoff) == [_url(0), _url(1)], "30d -> 10d hides the older"
        assert [run["run_id"] for run in client.get("/api/runs").json()["runs"]] == [run_id]
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)

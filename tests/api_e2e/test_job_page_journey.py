"""Q4a (v0.1.9): the job page's data, over HTTP against the real server.

The job page (ui/src/views/JobPage.jsx) is built from existing routes only:
``GET /api/assessments`` for the posting's stored quick assessment and its
verdict history, ``POST /api/answers`` (with ``reassess``) for "Save and
re-assess", ``POST /api/assess`` for "Assess this posting", and
``GET``/``POST /api/applications`` for "Mark applied". This journey proves
the one new server-side piece -- the verdict history APPENDS on every
assess/re-assess (operator answer 4) -- through those same routes, with the
same fixture model ``test_answers_journey.py`` uses (pending with a
``cloud:gcp`` question until the prior answer reaches the prompt).
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

_JOB_URL = "https://careers.example.test/jobs/9"


def test_verdict_history_grows_on_every_reassess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client

        # "Assess this posting" on a not-assessed card: POST /api/assess by URL.
        first, first_latency = timed_request(
            "POST /api/assess (job page: assess this posting)",
            lambda: client.post("/api/assess", json={"job": {"job_url": _JOB_URL}}),
        )
        assert first.status_code == 200, first.text
        f = first.json()
        assert f["result"]["verdict"] == "pending_user_answers"
        assert f["history"] == [{"at": f["created_at"], "verdict": "pending_user_answers", "trigger": "assess"}]
        job_identity = f["job"]["job_identity"]
        first_latency.assert_within_budget()

        # "Save and re-assess" on the open question: the history APPENDS,
        # the first entry is untouched, and the trigger names the question.
        answered, answered_latency = timed_request(
            "POST /api/answers (job page: save and re-assess)",
            lambda: client.post(
                "/api/answers",
                json={"question_id": "cloud:gcp", "answer": "Yes, two years on GCP.", "reassess": {"job_identity": job_identity}},
            ),
        )
        assert answered.status_code == 201, answered.text
        r = answered.json()["reassessed"]
        assert r["result"]["verdict"] == "matched_above_threshold"
        assert len(r["history"]) == 2
        assert r["history"][0] == f["history"][0]
        assert r["history"][1] == {"at": r["updated_at"], "verdict": "matched_above_threshold", "trigger": "answer:cloud:gcp"}
        assert r["history"][1]["at"] >= r["history"][0]["at"]
        answered_latency.assert_within_budget()

        # Assessing the same job again with no new answer: a third entry, "reassess".
        again = client.post("/api/assess", json={"job": {"job_url": _JOB_URL}})
        assert again.status_code == 200, again.text
        a = again.json()
        assert a["stored_path"] == f["stored_path"]
        assert [entry["trigger"] for entry in a["history"]] == ["assess", "answer:cloud:gcp", "reassess"]
        assert a["history"][:2] == r["history"]

        # The job page reads the history back from GET /api/assessments.
        assessments = client.get("/api/assessments")
        assert assessments.status_code == 200, assessments.text
        item = next(item for item in assessments.json()["items"] if item["job"]["job_identity"] == job_identity)
        assert item["history"] == a["history"]
        assert item["job"]["normalized_url"] == job_identity  # the card <-> assessment join key

        # "Mark applied" then the badge: POST, then GET shows the event for this URL.
        applied = client.post("/api/applications", json={"normalized_url": job_identity, "event_kind": "applied"})
        assert applied.status_code in (200, 201), applied.text
        listed = client.get("/api/applications")
        assert listed.status_code == 200, listed.text
        mine = [row for row in listed.json()["applications"] if row["external_ref"] == job_identity]
        assert len(mine) == 1 and mine[0]["event_kind"] == "applied"

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


def test_save_and_reassess_on_a_posting_only_a_run_assessed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Q4b-data: a posting assessed only by a find-jobs run has no quick-assess
    store entry, so ``POST /api/answers`` with ``reassess`` used to answer
    404 ``reassess_not_found`` (Q4a's "Gap found"). The route now falls back
    to the run's own posting: the answer is recorded, the posting is
    re-fetched and re-assessed with the ``answer:<id>`` trigger, and the
    result lands in the store the job page reads."""

    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        config_digest = client.get("/api/config").json()["config_digest"]
        run_response = client.post("/api/run", json=run_request_body(config_digest))
        assert run_response.status_code == 202, run_response.text
        run_id = run_response.json()["run_id"]
        status = poll_until_terminal(client, run_id)
        assert status["status"] == "succeeded", status

        payload = client.get(f"/api/runs/{run_id}/results").json()["payload"]
        assert payload["assessments"], "the fixture posting must be assessed by the run"
        run_assessment = payload["assessments"][0]
        assert run_assessment["verdict"] == "pending_user_answers"
        job_identity = run_assessment["posting"]["normalized_url"]  # the job page's identity for a run posting

        # Nothing in the quick-assess store knows this posting yet.
        before = client.get("/api/assessments")
        assert before.status_code == 200, before.text
        assert all(item["job"]["job_identity"] != job_identity for item in before.json()["items"])

        # "Save and re-assess" straight from the run's job page.
        answered, answered_latency = timed_request(
            "POST /api/answers (job page: run-only posting)",
            lambda: client.post(
                "/api/answers",
                json={"question_id": "cloud:gcp", "answer": "Yes, two years on GCP.", "reassess": {"job_identity": job_identity}},
            ),
        )
        assert answered.status_code == 201, answered.text  # fail-before: 404 reassess_not_found
        r = answered.json()["reassessed"]
        assert r is not None
        assert r["job"]["job_identity"] == job_identity
        assert r["job"]["source_url"] == run_assessment["posting"]["url"]
        assert r["result"]["verdict"] == "matched_above_threshold"  # the prior answer reached the prompt
        # A first store entry: updated_at == created_at, so the wire omits updated_at.
        assert "updated_at" not in r
        assert r["history"] == [{"at": r["created_at"], "verdict": "matched_above_threshold", "trigger": "answer:cloud:gcp"}]
        assert r["resume"]["profile_id"] is not None  # scored against the run's profile resume, not ephemeral
        answered_latency.assert_within_budget()

        # The job page reads it back from the store, joined on the same identity.
        after = client.get("/api/assessments")
        assert after.status_code == 200, after.text
        item = next(item for item in after.json()["items"] if item["job"]["job_identity"] == job_identity)
        assert item["history"] == r["history"]
        assert item["stored_path"] == r["stored_path"]

        # The recorded answer is there too, and a second answer re-assesses through the store entry.
        assert [a["question_id"] for a in client.get("/api/answers").json()["answers"]] == ["cloud:gcp"]
        again = client.post("/api/answers", json={"question_id": "years:python", "answer": "Six.", "reassess": {"job_identity": job_identity}})
        assert again.status_code == 201, again.text
        assert [entry["trigger"] for entry in again.json()["reassessed"]["history"]] == ["answer:cloud:gcp", "answer:years:python"]

        # An identity no run or store knows is still a 404.
        unknown = client.post(
            "/api/answers",
            json={"question_id": "cloud:gcp", "answer": "Yes.", "reassess": {"job_identity": "https://boards.greenhouse.io/nobody/jobs/1"}},
        )
        assert unknown.status_code == 404, unknown.text
        assert unknown.json()["error"]["code"] == "reassess_not_found"

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)

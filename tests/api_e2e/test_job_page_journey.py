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
    resolve_workpad_path,
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

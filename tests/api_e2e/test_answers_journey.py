"""P3: the Q&A loop journey over HTTP against the real supervised server.

Pending assess -> ``POST /api/answers`` with ``reassess`` -> the verdict
flips (the fake model, keyed on prompt content via
``bindings._test_model_handler``, answers ``matched_above_threshold`` once
the prior answer for ``cloud:gcp`` reaches the prompt) -> ``GET
/api/answers`` -> ``GET /api/assessments`` shows the re-assessed item
first (ordered by ``updated_at`` desc, P3's own change from P5's
``created_at``); CSRF 403.
"""

from __future__ import annotations

from pathlib import Path

import httpx
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

def _assert_error(response: httpx.Response, *, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["error"]["code"] == code, body
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


def test_answers_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client

        # A different posting (never mentions Python), never answered, kept
        # around as a plain assess baseline the answers journey does not touch.
        baseline, baseline_latency = timed_request(
            "POST /api/assess (baseline, unrelated)",
            lambda: client.post("/api/assess", json={"job": {"job_text": "Platform role. Remote within the US."}}),
        )
        assert baseline.status_code == 200, baseline.text
        baseline_latency.assert_within_budget()

        # Pending assess, fetched by URL (not pasted text): re-assess needs
        # ``source_url`` to re-fetch the same posting, which only a fetched
        # job carries (pasted text is never persisted, C5/no-leak). The
        # fixture model's fixed pending-with-GCP-question answer
        # (bindings._test_model_handler) applies to any prompt that does
        # not otherwise select a different fixture reply.
        pending, pending_latency = timed_request(
            "POST /api/assess (pending, fetched)",
            lambda: client.post("/api/assess", json={"job": {"job_url": "https://careers.example.test/jobs/9"}}),
        )
        assert pending.status_code == 200, pending.text
        p = pending.json()
        assert p["result"]["verdict"] == "pending_user_answers"
        assert p["result"]["structured_questions"][0]["question_id"] == "cloud:gcp"
        assert p["job"]["source_url"] == "https://careers.example.test/jobs/9"
        job_identity = p["job"]["job_identity"]
        pending_latency.assert_within_budget()

        # GET /api/answers is empty before any answer.
        empty = client.get("/api/answers")
        assert empty.status_code == 200, empty.text
        assert empty.json() == {"answers": []}

        # POST /api/answers with reassess: the answer is recorded AND the
        # posting is re-assessed in the same call.
        answered, answered_latency = timed_request(
            "POST /api/answers (reassess)",
            lambda: client.post(
                "/api/answers",
                json={"question_id": "cloud:gcp", "answer": "Yes, two years on GCP.", "reassess": {"job_identity": job_identity}},
            ),
        )
        assert answered.status_code == 201, answered.text
        body = answered.json()
        assert body["question_id"] == "cloud:gcp"
        assert body["record_id"].startswith("record_")
        assert body["reassessed"] is not None
        assert body["reassessed"]["result"]["verdict"] == "matched_above_threshold"
        assert body["reassessed"]["stored_path"] == p["stored_path"]  # same (resume, job) file, overwritten
        answered_latency.assert_within_budget()

        # GET /api/answers now lists the recorded answer.
        listed = client.get("/api/answers")
        assert listed.status_code == 200, listed.text
        answers = listed.json()["answers"]
        assert len(answers) == 1
        assert answers[0]["question_id"] == "cloud:gcp"
        assert answers[0]["answer"] == "Yes, two years on GCP."
        assert "Yes, two years on GCP." in listed.text  # the answer itself is not resume/job text; fine on the wire

        # GET /api/assessments shows the re-assessed item FIRST (updated_at desc).
        assessments = client.get("/api/assessments")
        assert assessments.status_code == 200, assessments.text
        items = assessments.json()["items"]
        stored_paths = [item["stored_path"] for item in items]
        assert stored_paths[0] == p["stored_path"]
        assert p["stored_path"] in stored_paths and baseline.json()["stored_path"] in stored_paths
        reassessed_item = next(item for item in items if item["stored_path"] == p["stored_path"])
        assert reassessed_item["result"]["verdict"] == "matched_above_threshold"
        assert reassessed_item["updated_at"] > reassessed_item["created_at"]

        # An answer without reassess just records; the stored assessment is untouched.
        second_question = client.post("/api/answers", json={"question_id": "years:python", "answer": "Six years.", "reassess": None})
        assert second_question.status_code == 201, second_question.text
        assert second_question.json()["reassessed"] is None

        # A question_id never seen in any assessment is still accepted (pre-answer).
        preanswer = client.post("/api/answers", json={"question_id": "clearance:secret", "answer": "No.", "reassess": None})
        assert preanswer.status_code == 201, preanswer.text

        # Terra review P2: an id that fails the experience_question contract
        # after normalization (a "/" is not stripped by normalize_question_id)
        # is rejected as a typed 422/answer_invalid BEFORE any write -- not a
        # generic conflict from native-record schema validation.
        _assert_error(
            client.post("/api/answers", json={"question_id": "cloud:gcp/invalid", "answer": "Yes.", "reassess": None}),
            status=422, code="answer_invalid",
        )
        _assert_error(
            client.post("/api/answers", json={"question_id": f"cloud:{'a' * 200}", "answer": "Yes.", "reassess": None}),
            status=422, code="answer_invalid",
        )
        no_new_write = client.get("/api/answers")
        assert no_new_write.status_code == 200, no_new_write.text
        assert len(no_new_write.json()["answers"]) == 3  # unchanged: cloud:gcp, years:python, clearance:secret

        # CSRF: wrong Origin -> 403, wrong Content-Type -> 415.
        _assert_error(
            httpx.post(
                f"{server.base_url}/api/answers",
                json={"question_id": "cloud:gcp", "answer": "x", "reassess": None},
                headers={"Origin": "http://evil.example.test"},
            ),
            status=403, code="forbidden_origin",
        )
        _assert_error(
            httpx.post(f"{server.base_url}/api/answers", content=b"{}", headers={"Content-Type": "text/plain"}),
            status=415, code="unsupported_media_type",
        )

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


def test_reassess_unknown_job_identity_is_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        _assert_error(
            client.post(
                "/api/answers",
                json={"question_id": "cloud:gcp", "answer": "Yes.", "reassess": {"job_identity": "text:sha256:" + "0" * 64}},
            ),
            status=404, code="reassess_not_found",
        )
        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)

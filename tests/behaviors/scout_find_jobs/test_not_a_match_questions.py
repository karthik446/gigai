"""assess-prompt-v3-r1 (v0.1.9): a ``not_a_match`` result never carries a question -- through the API.

Operator decision (2026-09-25, msg_e4de88fcfa29): a question on a failed
verdict has no next action, so the product keeps none. The strip lives in
``assessment_core._normalize_and_strip`` (the one place the graph's assess
node, quick assess and the eval harness all pass through); this file proves
the end-to-end consequence over the real ``POST /api/assess`` /
``GET /api/assessments`` routes: the response, the stored file and the data
the Questions view reads (``GET /api/assessments?verdict=pending_user_answers``
minus answered ids, joined UI-side per ``hooks.js``) never see the dropped
questions, while a ``pending_user_answers`` result keeps its own.

Scaffolding (scripted binding on ``proposal_execution.resolve_model_adapter``,
the ollama-target config, the in-process server) is shared with
``test_present_assess_api.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.behaviors.scout_find_jobs.test_present_assess_api import (  # noqa: F401 - fixtures
    _install_model,
    fx,
    ollama_config,
    running_server,
)

_FAILED_POSTING = "Acme is hiring a Principal ML Engineer: 10+ years of applied ML; PhD preferred; based in a listed US state."
_PENDING_POSTING = "Acme is hiring a Staff AI Engineer: 5+ years of Python in production; GCP a plus. Remote within the US."
_NOT_A_MATCH_REASON = "The resume states six years against a 10+ year requirement."

_NOT_A_MATCH_WITH_QUESTIONS = json.dumps(
    {
        "verdict": "not_a_match",
        "matrix": [
            {"requirement": "10+ years of applied ML", "class": "hard", "status": "unmet", "resume_evidence": ["six years"]},
            {"requirement": "PhD preferred", "class": "askable", "status": "unclear", "resume_evidence": []},
            {"requirement": "Based in a listed US state", "class": "hard", "status": "unclear", "resume_evidence": []},
        ],
        "suggestions": [],
        "questions": [
            {"question_id": "education:phd", "question": "Do you hold a PhD?", "requirement": "PhD preferred"},
            {"question_id": "location:us_region", "question": "Which US state do you live in?", "requirement": "Based in a listed US state"},
        ],
        "not_a_match_reason": _NOT_A_MATCH_REASON,
    }
)
_PENDING = json.dumps(
    {
        "verdict": "pending_user_answers",
        "matrix": [
            {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]},
            {"requirement": "GCP", "class": "askable", "status": "unclear", "resume_evidence": []},
        ],
        "suggestions": [],
        "questions": [{"question_id": "cloud:gcp", "question": "Have you run workloads on GCP?", "requirement": "GCP"}],
        "not_a_match_reason": None,
    }
)


def _question_ids(result: dict) -> list[str]:
    return [item["question_id"] for item in result.get("structured_questions") or []]


@pytest.mark.usefixtures("ollama_config")
def test_not_a_match_questions_never_reach_the_stored_result_or_the_questions_view(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _fx, _port = request.getfixturevalue("running_server")
    binding = _install_model(monkeypatch, [_NOT_A_MATCH_WITH_QUESTIONS, _PENDING])

    failed = client.post("/api/assess", json={"job": {"job_text": _FAILED_POSTING, "title": "Principal ML Engineer", "company": "Acme"}})
    assert failed.status_code == 200, failed.text
    result = failed.json()["result"]
    assert result["verdict"] == "not_a_match" and result["not_a_match_reason"] == _NOT_A_MATCH_REASON
    assert _question_ids(result) == [] and result["questions"] == []
    # The matrix keeps its rows (only the questions go) and the answer was accepted on the first call.
    assert [row["status"] for row in result["matrix"]] == ["unmet", "unclear", "unclear"]
    assert len(binding.port.prompts) == 1
    assert "education:phd" not in failed.text and "location:us_region" not in failed.text
    stored_path = Path(failed.json()["stored_path"])
    stored_text = stored_path.read_text(encoding="utf-8")
    stored = json.loads(stored_text)["result"]
    assert stored["verdict"] == "not_a_match" and _question_ids(stored) == [] and stored["questions"] == []
    assert "education:phd" not in stored_text and "Do you hold a PhD?" not in stored_text

    pending = client.post("/api/assess", json={"job": {"job_text": _PENDING_POSTING, "title": "Staff AI Engineer", "company": "Acme"}})
    assert pending.status_code == 200, pending.text
    assert pending.json()["result"]["verdict"] == "pending_user_answers"
    assert _question_ids(pending.json()["result"]) == ["cloud:gcp"]

    # The Questions view's data: pending results only, and only their own questions.
    view = client.get("/api/assessments", params={"verdict": "pending_user_answers"})
    assert view.status_code == 200, view.text
    items = view.json()["items"]
    assert [item["result"]["verdict"] for item in items] == ["pending_user_answers"]
    assert [_question_ids(item["result"]) for item in items] == [["cloud:gcp"]]
    assert "education:phd" not in view.text and "location:us_region" not in view.text

    # The unfiltered listing (the job page's source) shows the failed result with no question at all.
    everything = client.get("/api/assessments")
    assert everything.status_code == 200, everything.text
    by_verdict = {item["result"]["verdict"]: item["result"] for item in everything.json()["items"]}
    assert set(by_verdict) == {"not_a_match", "pending_user_answers"}
    assert _question_ids(by_verdict["not_a_match"]) == [] and by_verdict["not_a_match"]["questions"] == []
    assert _question_ids(by_verdict["pending_user_answers"]) == ["cloud:gcp"]
    assert "education:phd" not in everything.text and "Do you hold a PhD?" not in everything.text

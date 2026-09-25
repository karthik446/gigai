"""P2 (v0.1.9): verdict, requirement classes, structured question ids.

Old-shape assessment results (no verdict, string questions, met/partial/gap
matrix statuses) must keep parsing and serializing byte-identically -- these
are additive fields, omitted at their defaults (C4). New-shape answers from
the S29 r1 prompt add a verdict, per-row requirement class, and structured
questions with stable ids; the verdict/matrix/questions consistency rules
from the plan ("P2" section, rules 3-5 of ``assess_verdict_instructions.md``)
are enforced in ``parse_assessment_proposal``, not in ``from_json`` (C4's
"stored revisions are validated ... then AssessmentResult.from_json" stays
true: an already-saved record with a verdict still round-trips even if, in
principle, nothing re-checks consistency on read).
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from gigai.scout import assessment_core
from gigai.scout.assessment_core import AssessContext, AssessJob
from gigai.scout.find_jobs.contracts import (
    AssessmentQuestion,
    AssessmentResult,
    FindJobsContractError,
    MatrixStatus,
    RequirementClass,
    RequirementMatrixRow,
    Verdict,
)
from gigai.scout.proposals import parse_assessment_proposal

from .conftest import load_fixture


def _old_shape_assessment() -> dict[str, object]:
    assessment = load_fixture("fixture-assessment-v1.json")
    return deepcopy(assessment["assessments"][0])  # type: ignore[index]


def _new_shape_assessment(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "posting": {
            "normalized_url": "https://boards.greenhouse.io/acme/jobs/101",
            "url": "https://boards.greenhouse.io/acme/jobs/101",
            "content_sha256": "sha256:" + "a" * 64,
            "role_match": True,
        },
        "matrix": [
            {"requirement": "Python", "class": "hard", "resume_evidence": ["Built Python services"], "status": "met"},
            {"requirement": "GCP", "class": "askable", "resume_evidence": [], "status": "unclear"},
        ],
        "suggestions": [],
        "questions": ["Which platform would you prefer?"],
        "structured_questions": [
            {"question_id": "cloud:gcp", "question": "Which platform would you prefer?", "requirement": "GCP"}
        ],
        "verdict": "pending_user_answers",
        "not_a_match_reason": None,
        "proposal_revision_ref": None,
    }
    value.update(overrides)
    return value


# --- old results round-trip byte-identically ---------------------------------


def test_old_shape_assessment_result_round_trips_byte_identical() -> None:
    old = _old_shape_assessment()
    assert "verdict" not in old and "structured_questions" not in old and "not_a_match_reason" not in old
    result = AssessmentResult.from_json(old)
    assert result.verdict is None
    assert result.structured_questions == ()
    assert result.not_a_match_reason is None
    assert result.to_json() == old  # byte-identical: nothing new was added


def test_old_shape_matrix_row_round_trips_byte_identical() -> None:
    old = load_fixture("fixture-assessment-v1.json")["assessments"][0]["matrix"][0]  # type: ignore[index]
    assert "class" not in old
    row = RequirementMatrixRow.from_json(old)
    assert row.requirement_class is None
    assert row.to_json() == old


# --- new results round-trip ---------------------------------------------------


def test_new_shape_assessment_result_round_trips() -> None:
    new = _new_shape_assessment()
    result = parse_assessment_proposal(new)
    assert result.verdict is Verdict.PENDING_USER_ANSWERS
    assert result.structured_questions == (AssessmentQuestion("cloud:gcp", "Which platform would you prefer?", "GCP"),)
    assert result.matrix[1].requirement_class is RequirementClass.ASKABLE
    assert result.matrix[1].status is MatrixStatus.UNCLEAR
    assert result.to_json()["verdict"] == "pending_user_answers"
    assert result.to_json()["structured_questions"][0]["question_id"] == "cloud:gcp"  # type: ignore[index]


def test_matched_above_threshold_round_trips() -> None:
    new = _new_shape_assessment(
        verdict="matched_above_threshold",
        matrix=[{"requirement": "Python", "class": "hard", "resume_evidence": ["Built Python services"], "status": "met"}],
        questions=[],
        structured_questions=[],
        not_a_match_reason=None,
    )
    result = parse_assessment_proposal(new)
    assert result.verdict is Verdict.MATCHED_ABOVE_THRESHOLD
    assert result.structured_questions == ()


def test_not_a_match_round_trips_with_reason() -> None:
    new = _new_shape_assessment(
        verdict="not_a_match",
        matrix=[{"requirement": "10+ years", "class": "hard", "resume_evidence": ["9 years"], "status": "unmet"}],
        questions=[],
        structured_questions=[],
        not_a_match_reason="Resume states 9 years against a 10+ year requirement.",
    )
    result = parse_assessment_proposal(new)
    assert result.verdict is Verdict.NOT_A_MATCH
    assert result.not_a_match_reason == "Resume states 9 years against a 10+ year requirement."


# --- consistency rules (plan "P2", r1 rules 3-5) ------------------------------


def test_not_a_match_requires_at_least_one_unmet_row() -> None:
    new = _new_shape_assessment(
        verdict="not_a_match",
        matrix=[{"requirement": "Python", "class": "hard", "resume_evidence": ["Built Python services"], "status": "met"}],
        questions=[],
        structured_questions=[],
        not_a_match_reason="claimed but nothing is unmet",
    )
    with pytest.raises(FindJobsContractError):
        parse_assessment_proposal(new)


def test_pending_user_answers_requires_at_least_one_structured_question() -> None:
    new = _new_shape_assessment(questions=[], structured_questions=[])
    with pytest.raises(FindJobsContractError):
        parse_assessment_proposal(new)


def test_matched_above_threshold_is_not_checked_against_matrix_or_questions() -> None:
    # Rule 3 is enforced by the model/prompt, not re-derived here (the plan's
    # consistency rules only forbid not_a_match-without-unmet and
    # pending_user_answers-without-a-question); a matched_above_threshold
    # verdict with zero rows in it (an edge a test might construct) is not
    # additionally second-guessed.
    new = _new_shape_assessment(
        verdict="matched_above_threshold",
        matrix=[{"requirement": "Python", "class": "hard", "resume_evidence": ["Built Python services"], "status": "met"}],
        questions=[],
        structured_questions=[],
    )
    parse_assessment_proposal(new)  # does not raise


def test_absent_verdict_is_not_checked() -> None:
    old = _old_shape_assessment()
    parse_assessment_proposal(old)  # does not raise: no verdict claimed, nothing to check


# --- structured_questions bounds ---------------------------------------------


def test_structured_questions_rejects_bad_question_id() -> None:
    new = _new_shape_assessment(
        structured_questions=[{"question_id": "NotLowercase", "question": "x?", "requirement": None}]
    )
    with pytest.raises(FindJobsContractError):
        parse_assessment_proposal(new)


def test_structured_questions_rejects_too_many() -> None:
    new = _new_shape_assessment(
        structured_questions=[
            {"question_id": f"cat:{i}", "question": "x?", "requirement": None} for i in range(13)
        ]
    )
    with pytest.raises(FindJobsContractError):
        parse_assessment_proposal(new)


def test_structured_questions_rejects_oversized_question_text() -> None:
    new = _new_shape_assessment(
        structured_questions=[{"question_id": "cat:x", "question": "x" * 701, "requirement": None}]
    )
    with pytest.raises(FindJobsContractError):
        parse_assessment_proposal(new)


def test_assessment_question_from_json_rejects_bad_id() -> None:
    with pytest.raises(FindJobsContractError):
        AssessmentQuestion.from_json({"question_id": "bad id", "question": "x?", "requirement": None})


# --- normalizer: keeps new keys, maps old status words to the new prompt's --


def test_normalizer_keeps_verdict_class_structured_questions() -> None:
    decoded = {
        "verdict": "pending_user_answers",
        "matrix": [{"requirement": "GCP", "class": "askable", "resume_evidence": [], "status": "unclear"}],
        "suggestions": [],
        "questions": [{"question_id": "CLOUD:GCP", "question": "Which platform?", "requirement": "GCP"}],
        "not_a_match_reason": None,
    }
    normalized = assessment_core._normalize_assessment_payload(decoded)
    assert normalized["verdict"] == "pending_user_answers"
    assert normalized["matrix"][0]["class"] == "askable"
    assert normalized["structured_questions"] == [
        {"question_id": "cloud:gcp", "question": "Which platform?", "requirement": "GCP"}
    ]
    assert normalized["questions"] == ["Which platform?"]  # plain-string form kept for the shipped UI (C9)
    assert normalized["not_a_match_reason"] is None


def test_normalizer_maps_old_partial_and_gap_onto_unclear_and_unmet() -> None:
    decoded = {
        "matrix": [
            {"requirement": "A", "resume_evidence": [], "status": "partial"},
            {"requirement": "B", "resume_evidence": [], "status": "gap"},
            {"requirement": "C", "resume_evidence": [], "status": "PARTIALLY"},
        ],
        "suggestions": [],
        "questions": [],
    }
    normalized = assessment_core._normalize_assessment_payload(decoded)
    assert [row["status"] for row in normalized["matrix"]] == ["unclear", "unmet", "unclear"]


def test_normalizer_passes_through_new_status_words_unchanged() -> None:
    decoded = {
        "matrix": [
            {"requirement": "A", "resume_evidence": [], "status": "met"},
            {"requirement": "B", "resume_evidence": [], "status": "unmet"},
            {"requirement": "C", "resume_evidence": [], "status": "unclear"},
        ],
        "suggestions": [],
        "questions": [],
    }
    normalized = assessment_core._normalize_assessment_payload(decoded)
    assert [row["status"] for row in normalized["matrix"]] == ["met", "unmet", "unclear"]


def test_normalizer_tolerates_a_bare_string_question_without_structuring_it() -> None:
    decoded = {"matrix": [], "suggestions": [], "questions": ["a plain question, no id"]}
    normalized = assessment_core._normalize_assessment_payload(decoded)
    assert normalized["questions"] == ["a plain question, no id"]
    assert "structured_questions" not in normalized


def test_normalizer_drops_a_malformed_question_object() -> None:
    decoded = {"matrix": [], "suggestions": [], "questions": [{"question": "no id here"}]}
    normalized = assessment_core._normalize_assessment_payload(decoded)
    assert normalized["questions"] == []
    assert "structured_questions" not in normalized


# --- render_assess_prompt: {{countries}}/{{titles}} placeholders -------------


def _job() -> AssessJob:
    return AssessJob(title="Senior Backend Engineer", company="Acme Corp", location="Denver, CO", posting_text="text")


def test_render_prompt_includes_countries_and_titles() -> None:
    ctx = AssessContext(resume_text="resume", visa_sponsorship_required=False, countries=("US", "CA"), titles=("Backend Engineer",))
    prompt = assessment_core.render_assess_prompt(_job(), ctx)
    assert "countries = US, CA" in prompt
    assert "target titles = Backend Engineer" in prompt


def test_render_prompt_defaults_countries_and_titles_when_empty() -> None:
    ctx = AssessContext(resume_text="resume", visa_sponsorship_required=False)
    prompt = assessment_core.render_assess_prompt(_job(), ctx)
    assert "countries = any" in prompt
    assert "target titles = unspecified" in prompt


def test_no_excluded_domains_placeholder_in_the_shipped_instructions() -> None:
    # Operator answer 5: countries + titles are added; excluded_domains is
    # dropped (no source for it exists anywhere in the config).
    text = assessment_core.load_assess_instructions()
    assert "{{excluded_domains}}" not in text
    assert "{{countries}}" in text
    assert "{{titles}}" in text

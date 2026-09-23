from __future__ import annotations

import pytest

from gigai.scout.find_jobs.contracts import FindJobsContractError
from gigai.scout.proposals import parse_assessment_proposal


def _assessment() -> dict[str, object]:
    digest = "sha256:" + "a" * 64
    return {
        "posting": {
            "normalized_url": "https://example.test/jobs/1",
            "url": "https://example.test/jobs/1",
            "content_sha256": digest,
            "role_match": True,
        },
        "matrix": [
            {"requirement": "Python", "resume_evidence": ["Built APIs"], "status": "met"},
            {"requirement": "Kubernetes", "resume_evidence": [], "status": "gap"},
        ],
        "suggestions": ["Emphasize API ownership."],
        "questions": ["Which deployment example should be highlighted?"],
        "proposal_revision_ref": None,
    }


def test_parse_assessment_proposal_uses_frozen_result_dto() -> None:
    result = parse_assessment_proposal(_assessment())
    assert result.matrix[0].status.value == "met"
    assert result.suggestions == ("Emphasize API ownership.",)


@pytest.mark.parametrize(
    ("field", "value"),
    [("matrix", []), ("suggestions", ["x" * 701]), ("questions", ["x" * 701])],
)
def test_parse_assessment_proposal_rejects_bounds(field: str, value: object) -> None:
    raw = _assessment()
    raw[field] = value
    with pytest.raises(FindJobsContractError):
        parse_assessment_proposal(raw)


def test_parse_assessment_proposal_rejects_unknown_fields() -> None:
    raw = _assessment()
    raw["unexpected"] = True
    with pytest.raises(FindJobsContractError):
        parse_assessment_proposal(raw)

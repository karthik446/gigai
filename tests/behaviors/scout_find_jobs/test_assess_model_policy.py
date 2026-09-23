from types import SimpleNamespace
import json
from pathlib import Path

import pytest

from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.canonical import digest_imported_bytes, parse_json_bytes
from gigai.scout.find_jobs.contracts import (
    AssessmentResult,
    MatrixStatus,
    ModelTarget,
    PinnedResume,
    Producer,
    RequirementMatrixRow,
    SelectedPosting,
)
from gigai.scout.proposal_execution import (
    ScoutProposalExecutionError,
    assess_invocation_policy,
)
from gigai.scout.proposal_records import read_proposal_revision, save_assessment_revision
from gigai.scout.proposals import parse_assessment_proposal
from tests.behaviors.scout_discovery.test_scout07_posting_inputs import _completed_find_jobs


def _input():
    return SimpleNamespace(
        selected_postings=(SimpleNamespace(normalized_url="https://example.test/job"),),
        pinned_resume=SimpleNamespace(record_id="resume-record"),
    )


def test_local_assess_policy_is_offline_and_disallows_network():
    policy = assess_invocation_policy("ollama_local", _input())
    assert policy.local_allowed is True
    assert policy.network_allowed is False
    assert policy.offline is True


@pytest.mark.parametrize("target", ["codex_cli", "openrouter_api"])
def test_hosted_assess_policy_allows_network_and_is_not_offline(target):
    policy = assess_invocation_policy(target, _input())
    assert policy.local_allowed is False
    assert policy.network_allowed is True
    assert policy.offline is False


def test_unknown_assess_target_fails_loudly():
    with pytest.raises(ScoutProposalExecutionError):
        assess_invocation_policy("unknown", _input())


class _AssessmentAdapter:
    name = "deterministic"

    def invoke(self, request):
        return InvocationResult(
            status="success",
            output_text=json.dumps({
                "matrix": [{"requirement": "Python", "resume_evidence": ["Built APIs"], "status": "met"}],
                "suggestions": ["Keep the API example."],
                "questions": [],
            }),
            resolved_model="fixture",
            raw_usage={},
            normalized_usage=NormalizedUsage(10, 8, 18),
            cost_status="unavailable",
        )


def test_real_b1_parse_and_save_revision_is_readable(tmp_path: Path):
    resolved, _selector, _snapshot, _metadata = _completed_find_jobs(tmp_path)
    posting = SelectedPosting(
        normalized_url="https://boards.greenhouse.io/acme/jobs/101",
        url="https://boards.greenhouse.io/acme/jobs/101",
        content_sha256="sha256:" + "a" * 64,
        role_match=True,
    )
    pinned = PinnedResume("record_00000000-0000-4000-8000-000000000001", "revision_00000000-0000-4000-8000-000000000002", "sha256:" + "b" * 64)
    producer = Producer("scout.find_jobs.assess", "1", "scout-assess", ModelTarget.OLLAMA_LOCAL, "deterministic")
    model_result = _AssessmentAdapter().invoke(SimpleNamespace())
    raw = json.loads(model_result.output_text)
    parsed = parse_assessment_proposal({**raw, "posting": posting.to_json(), "proposal_revision_ref": None})
    assert isinstance(parsed, AssessmentResult)
    ref = save_assessment_revision(home_root=tmp_path / "home", target=resolved, posting=posting, result=parsed, producer=producer, pinned_resume=pinned)
    record_id, revision_id = ref.split("/")[-3], ref.split("/")[-1].removesuffix(".json")
    saved = read_proposal_revision(resolved=resolved, record_id=record_id, revision_id=revision_id)
    assert saved["assessment"]["posting"] == posting.to_json()

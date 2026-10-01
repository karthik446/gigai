"""0110-034: an answer given on one posting is reused for a reworded question on another.

This file is the fail-before / pass-after proof, so it uses only what the
0.1.10.4 code has: the answer is written with ``experience_answers.
record_answer`` exactly as ``POST /api/answers`` did then (``prompt`` = the
id, no profile), and nothing here imports the story bank. It therefore also
proves "no migration": an answer stored by the old code path is in the bank
the next assessment reads.

The model is a fake that follows ``assess.md``'s STORY BANK paragraph the way
a real one is told to: when the prompt offers a bank line for the fact
(``- cloud:gcp | ...``) it marks the requirement met, cites ``Story bank
cloud:gcp: <answer>`` and asks nothing; when the prompt offers no such line
it asks the question again, under the id it would have picked for THIS
posting's wording (``tooling:google_cloud_platform``). On the 0.1.10.4 code
the prompt has no STORY BANK paragraph, so the question is asked again.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.config import Endpoint
from gigai.config import ModelTarget as ConfigModelTarget
from gigai.scout.experience_answers import record_answer
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest
from gigai.scout.find_jobs.contracts import Verdict
from gigai.scout.quick_assess import run_quick_assessment
from gigai.setup import build_config

from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

_RESUME = b"# Fixture Resume\n\nStaff AI Engineer. Built Python services for six years. (fixture only.)\n"
_POSTING_B = (
    "Borealis Freight is hiring a Staff Platform Engineer. Requirements: 5+ years of Python in production; "
    "hands-on experience with Google Cloud Platform; clear written communication. Remote within the United States."
)
_REQUIREMENT_B = "Hands-on experience with Google Cloud Platform"
_ANSWER = "Yes: two years running batch and streaming workloads on GCP (GKE, BigQuery, Pub/Sub)."
_BANK_LINE = re.compile(r"^- cloud:gcp \|(?:.*\|)? answer: (?P<answer>.*)$", re.MULTILINE)


def _model_that_follows_the_story_bank(prompt: str) -> str:
    python_row = {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]}
    line = _BANK_LINE.search(prompt)
    if line is not None:
        return json.dumps(
            {
                "verdict": "matched_above_threshold",
                "matrix": [
                    python_row,
                    {"requirement": _REQUIREMENT_B, "class": "askable", "status": "met", "resume_evidence": [f"Story bank cloud:gcp: {line.group('answer')}"]},
                ],
                "suggestions": [],
                "questions": [],
                "not_a_match_reason": None,
            }
        )
    return json.dumps(
        {
            "verdict": "pending_user_answers",
            "matrix": [python_row, {"requirement": _REQUIREMENT_B, "class": "askable", "status": "unclear", "resume_evidence": []}],
            "suggestions": [],
            "questions": [
                {"question_id": "tooling:google_cloud_platform", "question": "Do you have hands-on Google Cloud Platform experience?", "requirement": _REQUIREMENT_B}
            ],
            "not_a_match_reason": None,
        }
    )


class _Port:
    name = "fixture"
    timed_out = False

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def invoke(self, request):
        self.prompts.append(request.prompt)
        return InvocationResult(
            status="success",
            output_text=_model_that_follows_the_story_bank(request.prompt),
            resolved_model="fixture",
            raw_usage={},
            normalized_usage=NormalizedUsage(10, 20, 30),
            cost_status="unavailable",
        )


class _Binding:
    def __init__(self) -> None:
        self.port = _Port()

    def request(self, *, role: str, prompt: str, required_capabilities=frozenset({"text"})):
        return SimpleNamespace(prompt=prompt, role=role)

    def close(self) -> None:
        pass


def _config(home: Path):
    return build_config(
        home_root=home,
        workpad_root=home.parent / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        endpoints=(
            Endpoint(name="offline", adapter="deterministic"),
            Endpoint(name="ollama", adapter="ollama_local", base_url="http://127.0.0.1:11434"),
        ),
        model_targets=(
            ConfigModelTarget(name="offline-default", endpoint="offline", model="fixture-v1", capabilities=("text",), max_output_tokens=64),
            ConfigModelTarget(
                name="ollama-default", endpoint="ollama", model="fixture-model", capabilities=("text",),
                max_output_tokens=512, model_digest="sha256:" + "c" * 64,
            ),
        ),
    )


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path, resume_text=_RESUME)


def test_an_answer_from_posting_a_settles_a_reworded_question_on_posting_b(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    binding = _Binding()

    def resolve(config, adapter_target, **_kwargs):
        return binding

    setattr(resolve, "_scout_test_transport", True)
    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", resolve)

    # Posting A asked "cloud:gcp"; the user answered it. Written the way 0.1.10.4 wrote it.
    record_answer(home_root=fx.home_root, requested_target=fx.target, question_id="cloud:gcp", prompt="cloud:gcp", answer=_ANSWER)

    # Posting B words the same fact differently.
    response = run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_text=_POSTING_B)), home_root=fx.home_root, target=fx.target, config=_config(fx.home_root)
    )

    asked = [question.question_id for question in response.result.structured_questions]
    assert asked == [], f"the reworded question was asked again: {asked}"
    assert response.result.verdict is Verdict.MATCHED_ABOVE_THRESHOLD
    row = next(row for row in response.result.matrix if row.requirement == _REQUIREMENT_B)
    assert row.status.value == "met"
    assert row.resume_evidence == (f"Story bank cloud:gcp: {_ANSWER}",), "the requirement cites the bank answer"
    assert len(binding.port.prompts) == 1, "reuse costs no extra model call"

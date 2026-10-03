"""0.1.10.7 C: what the answers and stories tests share. Synthetic only.

A scripted model binding on the C1 seam (``proposal_execution.resolve_model_adapter``)
that keeps every prompt it was sent, the model outputs the tests script, and a quick
assessment of pasted posting text for one profile of a ``build_gig_with_resume`` gig.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.config import Endpoint
from gigai.config import ModelTarget as ConfigModelTarget
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.profile_records import create_profile, selected_profile
from gigai.scout.quick_assess import run_quick_assessment
from gigai.setup import build_config

from tests.support.scout_profile_fixtures import ProfileFixtureGig

RESUME = b"# Fixture Resume\n\nStaff AI Engineer. Built Python services for six years. (fixture only.)\n"

MATCH = json.dumps(
    {
        "verdict": "matched_above_threshold",
        "matrix": [{"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]}],
        "suggestions": [],
        "questions": [],
        "not_a_match_reason": None,
    }
)


def pending(question_id: str, question: str) -> str:
    """A model answer that leaves one question open."""

    return json.dumps(
        {
            "verdict": "pending_user_answers",
            "matrix": [
                {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]},
                {"requirement": "The platform", "class": "askable", "status": "unclear", "resume_evidence": []},
            ],
            "suggestions": [],
            "questions": [{"question_id": question_id, "question": question, "requirement": "The platform"}],
            "not_a_match_reason": None,
        }
    )


def citing(bank_id: str, text: str = "as the candidate said") -> str:
    """A model answer that cites one answer or story as evidence (``Story bank <id>: ...``)."""

    return json.dumps(
        {
            "verdict": "matched_above_threshold",
            "matrix": [
                {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]},
                {"requirement": "The platform", "class": "askable", "status": "met", "resume_evidence": [f"Story bank {bank_id}: {text}"]},
            ],
            "suggestions": [],
            "questions": [],
            "not_a_match_reason": None,
        }
    )


class Port:
    name = "fixture"
    timed_out = False

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.prompts: list[str] = []

    def invoke(self, request):
        self.prompts.append(request.prompt)
        return InvocationResult(
            status="success", output_text=self.outputs.pop(0), resolved_model="fixture", raw_usage={},
            normalized_usage=NormalizedUsage(10, 20, 30), cost_status="unavailable",
        )


class Binding:
    def __init__(self, outputs: list[str]) -> None:
        self.port = Port(outputs)

    def request(self, *, role: str, prompt: str, required_capabilities=frozenset({"text"})):
        return SimpleNamespace(prompt=prompt, role=role)

    def close(self) -> None:
        pass


def install_model(monkeypatch: pytest.MonkeyPatch, outputs: list[str]) -> Binding:
    """Every model call answers the next of ``outputs``; ``binding.port.prompts`` are the prompts sent."""

    binding = Binding(outputs)

    def resolve(config, adapter_target, **_kwargs):
        return binding

    setattr(resolve, "_scout_test_transport", True)
    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", resolve)
    return binding


def config(home: Path):
    return build_config(
        home_root=home, workpad_root=home.parent / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False,
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


def assess(fx: ProfileFixtureGig, profile_id: str | None, text: str, *, title: str | None = None, trigger: str | None = None):
    """One quick assessment of pasted posting ``text`` for ``profile_id`` (``None``: the selected profile)."""

    return run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_text=text, title=title), resume=AssessResumeInput(profile_id=profile_id)),
        home_root=fx.home_root, target=fx.target, config=config(fx.home_root), trigger=trigger,
    )


def two_profiles(fx: ProfileFixtureGig) -> tuple[str, str]:
    """``(default profile id, a second profile's id)`` on one gig."""

    default = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert default is not None
    other = create_profile(
        fx.resolved, label="second profile", titles=("staff backend engineer",), titles_to_avoid=(),
        queries=("staff backend engineer",), resume_ref=default.resume_ref,
    )
    return default.profile_id, other.profile_id


def paths(fx: ProfileFixtureGig) -> dict[str, object]:
    return {"home_root": fx.home_root, "target": fx.target}

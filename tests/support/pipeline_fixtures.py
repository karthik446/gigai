"""0.1.10.7 M2: what the pipeline step and runner tests share. Synthetic only.

One gig whose resume still has a contact header (marker name, email, phone),
one answer and one story, and one posting that already has an assessment for
the profile (made with ``resolved_job``: nothing is fetched). The model is a
scripted binding on the C1 seam (``proposal_execution.resolve_model_adapter``)
that answers a tailor prompt with a valid tailoring and an assess prompt with
an assessment, and keeps every prompt it was sent.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.scout import stories, story_bank
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput, ResolvedJob
from gigai.scout.profile_records import selected_profile
from gigai.scout.quick_assess import run_quick_assessment
from gigai.scout.tailored_resume import TAILOR_PROMPT_HEADER

from tests.support.answers_stories_fixtures import config
from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

EMAIL = "zora.quillfeather@zq.example.invalid"
PHONE = "+1 (555) 014-2999"
#: Nothing the pipeline writes may hold any of these.
MARKERS = ("Zora", "Quillfeather", EMAIL, PHONE, "014-2999", "zq.example.invalid")
EMAIL_SHAPE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_SHAPE = re.compile(r"\+?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}")

RESUME = (
    "# Zora Quillfeather\n"
    f"{EMAIL} | {PHONE} | Denver, CO\n"
    "\n"
    "## Experience\n"
    "Acme Corp — Senior Engineer (2019–2023)\n"
    "Built Python services for six years; cut p99 latency by 40%.\n"
    "Operated k8s clusters backed by PostgreSQL.\n"
    "\n"
    "## Skills\n"
    "Python, Kubernetes, PostgreSQL\n"
)
POSTING = (
    "Acme is hiring a Staff AI Engineer to own Python inference services. "
    "Requirements: 5+ years of Python in production; Kubernetes; Terraform; GCP experience is a plus. "
    "Remote within the United States."
)
JOB = "https://jobs.example.test/acme/staff-ai-engineer"
QUESTION_ID = "cloud:gcp"
ANSWER = "Yes, two years on GCP."
ANSWER_CHANGED = "Yes, two years on GCP, mostly GKE."

TAILORED: dict[str, object] = {
    "sections": [
        {
            "heading": "summary",
            "lines": [
                {
                    "text": "Built Python services for six years, cutting p99 latency by 40%; operated Kubernetes clusters backed by PostgreSQL.",
                    "refs": [{"kind": "resume", "line": 5}, {"kind": "resume", "line": 6}],
                    "reason": {"kind": "summary", "requirement": None, "posting_phrase": "Python inference services"},
                }
            ],
        },
        {
            "heading": "experience",
            "entries": [
                {
                    "heading_ref": [{"copy": 4}],
                    "bullets": [
                        {
                            "text": "Cut p99 latency by 40% on the Python services built over six years.",
                            "refs": [{"kind": "resume", "line": 5}],
                            "reason": {"kind": "surface", "requirement": None, "posting_phrase": "Python inference services"},
                        }
                    ],
                }
            ],
        },
        {"heading": "skills", "lines": [{"copy": 8}]},
        {
            "heading": "other",
            "lines": [
                {
                    "text": "Two years on GCP.",
                    "refs": [{"kind": "answer", "question_id": QUESTION_ID}],
                    "reason": {"kind": "answer", "requirement": None, "posting_phrase": "GCP experience is a plus"},
                }
            ],
        },
    ],
}


def assessment(*, met: int, verdict: str = "matched_above_threshold") -> str:
    """A model answer whose matrix has two requirements, ``met`` of them met."""

    rows = [
        {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]},
        {
            "requirement": "GCP experience", "class": "askable", "status": "met" if met > 1 else "unmet",
            "resume_evidence": ["Two years on GCP"] if met > 1 else [],
        },
    ]
    return json.dumps({"verdict": verdict, "matrix": rows, "suggestions": [], "questions": [], "not_a_match_reason": None})


class _Port:
    name = "fixture"
    timed_out = False

    def __init__(self, model: "PipelineModel") -> None:
        self._model = model

    def invoke(self, request):
        return self._model.answer(request.prompt)


class PipelineModel:
    """The scripted model: a tailoring for a tailor prompt, an assessment for any other. Every prompt is kept."""

    def __init__(self) -> None:
        self.port = _Port(self)
        self.tailor_prompts: list[str] = []
        self.assess_prompts: list[str] = []
        self.tailored: dict[str, object] = TAILORED
        self.assessed: str = assessment(met=2)
        #: Raised by the next call instead of answering, once.
        self.fail_next: BaseException | None = None

    @property
    def calls(self) -> int:
        return len(self.tailor_prompts) + len(self.assess_prompts)

    def answer(self, prompt: str) -> InvocationResult:
        if self.fail_next is not None:
            error, self.fail_next = self.fail_next, None
            raise error
        if prompt.lstrip().startswith(TAILOR_PROMPT_HEADER):
            self.tailor_prompts.append(prompt)
            text = json.dumps(self.tailored)
        else:
            self.assess_prompts.append(prompt)
            text = self.assessed
        return InvocationResult(
            status="success", output_text=text, resolved_model="fixture-model", raw_usage={},
            normalized_usage=NormalizedUsage(10, 20, 30), cost_status="unavailable",
        )

    def request(self, *, role: str, prompt: str, required_capabilities=frozenset({"text"})):
        return SimpleNamespace(prompt=prompt, role=role)

    def close(self) -> None:
        pass


def install_model(monkeypatch: pytest.MonkeyPatch, model: PipelineModel | None = None) -> PipelineModel:
    """Every model call of this process answers from ``model``; the CLI's configuration is the fixture's."""

    model = model or PipelineModel()

    def resolve(config, adapter_target, **_kwargs):
        return model

    setattr(resolve, "_scout_test_transport", True)
    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", resolve)
    # A CLI command loads the configuration from the home: give it the fixture's model targets.
    monkeypatch.setattr("gigai.scout.quick_assess.load_config", config)
    monkeypatch.setattr("gigai.scout.tailored_resume.load_config", config)
    return model


@dataclass(frozen=True)
class PipelineFixture:
    gig: ProfileFixtureGig
    profile_id: str
    model: PipelineModel

    @property
    def home_root(self) -> Path:
        return self.gig.home_root

    @property
    def target(self) -> Path:
        return self.gig.target

    @property
    def scout_root(self) -> Path:
        return next(path for path in (self.home_root / "scout").iterdir() if (path / "quick_assess").is_dir())

    @property
    def db(self) -> Path:
        return self.scout_root / "pipeline" / "pipeline.sqlite"

    def cli(self, *args: str) -> list[str]:
        return ["pipeline", *args, "--home", str(self.home_root), "--target", str(self.target), "--json"]


def resolved_job(url: str = JOB, text: str = POSTING) -> ResolvedJob:
    return ResolvedJob(
        job_identity=url, source_url=url, normalized_url=url, fetch_kind="generic", title="Staff AI Engineer", company="Acme",
        location="Remote", text=text, text_sha256="sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def assess_base(fx: PipelineFixture, url: str = JOB, *, met: int = 1) -> None:
    """The job's (base) assessment for the profile, as the job page's Assess stores it."""

    fx.model.assessed = assessment(met=met)
    run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=url), resume=AssessResumeInput(profile_id=fx.profile_id)),
        home_root=fx.home_root, target=fx.target, config=config(fx.home_root), resolved_job=resolved_job(url),
    )
    fx.model.assessed = assessment(met=2)
    fx.model.assess_prompts.clear()


def build_pipeline_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, base: bool = True) -> PipelineFixture:
    """A gig with a resume, one answer, one story and (``base``) one assessed posting; the model installed."""

    gig = build_gig_with_resume(tmp_path, resume_text=RESUME.encode("utf-8"))
    model = install_model(monkeypatch)
    profile = selected_profile(gig.resolved, home_root=gig.home_root, target=gig.target)
    assert profile is not None
    story_bank.save_answer(
        home_root=gig.home_root, target=gig.target, question_id=QUESTION_ID, answer=ANSWER, question="Have you run workloads on GCP?"
    )
    stories.save_story(
        home_root=gig.home_root, target=gig.target,
        fields={"title": "Moved inference to GCP", "raw": "Moved the Python inference services to GCP on Kubernetes.", "tags": ["GCP", "Python"]},
    )
    fx = PipelineFixture(gig, profile.profile_id, model)
    if base:
        assess_base(fx)
    return fx


def output_files(fx: PipelineFixture) -> list[Path]:
    """Every file the pipeline wrote: its outputs in their stores and the pipeline's own folder."""

    found: list[Path] = []
    for directory in ("resumes", "quick_assess_tailored", "ats", "label", "pipeline"):
        root = fx.scout_root / directory
        if root.is_dir():
            found.extend(path for path in sorted(root.rglob("*")) if path.is_file())
    return found


__all__ = [
    "ANSWER",
    "ANSWER_CHANGED",
    "EMAIL",
    "EMAIL_SHAPE",
    "JOB",
    "MARKERS",
    "PHONE",
    "PHONE_SHAPE",
    "POSTING",
    "QUESTION_ID",
    "RESUME",
    "PipelineFixture",
    "PipelineModel",
    "assess_base",
    "assessment",
    "build_pipeline_fixture",
    "install_model",
    "output_files",
    "resolved_job",
]

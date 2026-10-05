"""0.1.11 N5b (SPEC 10.2 item 5, N5 NOT PROVEN 9): ONE profile default for brief, pick, suggestions AND resume store.

The default is the profile whose assessment of the job is newest.  When two profiles each have a stored resume for the
job and none is named, every one of those commands refuses with ``profile_ambiguous`` and names both, so a
``--resolves`` can never be checked against one profile and applied to another.  Synthetic gig, two profiles, one job.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.scout import profile_records
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.master_store import import_master
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.quick_assess import run_quick_assessment
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailored_resume import list_tailored_resumes

from tests.behaviors.scout_pipeline.test_resume_job_cli import MASTER, RESUME
from tests.support.answers_stories_fixtures import config
from tests.support.pipeline_fixtures import JOB, PipelineFixture, build_pipeline_fixture, resolved_job


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PipelineFixture:
    monkeypatch.delenv(PIPELINE_ENV, raising=False)
    fx = build_pipeline_fixture(tmp_path, monkeypatch, resume=RESUME)
    source = tmp_path / "master.md"
    source.write_text(MASTER, encoding="utf-8")
    assert import_master(home_root=fx.home_root, target=fx.target, source=source, gig_id=fx.gig.resolved.gig_id).status == "created"
    return fx


def _second_profile(fx: PipelineFixture) -> str:
    first = next(item for item in profile_records.list_profiles(fx.gig.resolved) if item.profile_id == fx.profile_id)
    other = profile_records.create_profile(
        fx.gig.resolved, label="second", titles=("staff ai engineer",), titles_to_avoid=(), queries=("staff ai engineer",), resume_ref=first.resume_ref,
    )
    # A newer assessment of the same job under the second profile.
    run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=JOB), resume=AssessResumeInput(profile_id=other.profile_id)),
        home_root=fx.home_root, target=fx.target, config=config(fx.home_root), resolved_job=resolved_job(JOB),
    )
    return other.profile_id


def _invoke(fx: PipelineFixture, *args: str):
    return CliRunner().invoke(scout_group, [*args, "--home", str(fx.home_root), "--target", str(fx.target), "--json"])


def _last(result) -> dict:
    return json.loads(result.output.strip().splitlines()[-1])


def _store(fx: PipelineFixture, tmp_path: Path, *more: str):
    path = tmp_path / "handed-back.md"
    path.write_text(MASTER, encoding="utf-8")
    return _invoke(fx, "resume", "store", "--in", str(path), "--job-url", JOB, "--as", "agent", *more)


def test_the_default_is_the_newest_assessments_profile_and_two_resumes_refuse_to_guess(fx: PipelineFixture, tmp_path: Path) -> None:
    other = _second_profile(fx)

    # No resume yet: the default is the newest assessment's profile (the second), for pick and for store.
    assert _last(_invoke(fx, "resume", "pick", "--job-url", JOB))["profile_id"] == other
    assert _store(fx, tmp_path).exit_code == 0
    assert [item.resume.profile_id for item in list_tailored_resumes(fx.home_root, fx.target, job_identity=JOB)] == [other]
    assert _last(_invoke(fx, "resume", "brief", "--job-url", JOB))["profile_id"] == other

    # The first profile gets a resume too: now two profiles have one, and nothing is guessed.
    assert _store(fx, tmp_path, "--profile", fx.profile_id).exit_code == 0
    for args in (("resume", "pick"), ("resume", "brief"), ("resume", "brief", "--posting"), ("suggestions", "list")):
        result = _invoke(fx, *args, "--job-url", JOB)
        assert result.exit_code == 1, (args, result.output)
        error = _last(result)["error"]
        assert error["code"] == "profile_ambiguous", (args, error)
        assert fx.profile_id in str(error["message"]) and other in str(error["message"]) and "--profile" in str(error["message"])
    before = list_tailored_resumes(fx.home_root, fx.target, job_identity=JOB)
    result = _store(fx, tmp_path, "--resolves", "sg-1")
    assert result.exit_code == 1 and _last(result)["error"]["code"] == "profile_ambiguous", result.output
    assert list_tailored_resumes(fx.home_root, fx.target, job_identity=JOB) == before, "a refused store writes nothing"

    # Naming a profile works for each of them.
    for profile in (fx.profile_id, other):
        assert _last(_invoke(fx, "resume", "pick", "--job-url", JOB, "--profile", profile))["profile_id"] == profile

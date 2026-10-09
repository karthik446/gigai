"""0.1.11 N5b (SPEC 10.2 item 5, N5 NOT PROVEN 9), changed by 0.1.11.9 PJ2: a job command with no role named.

0.1.11: the default was the role whose assessment of the job was newest, and when two roles each had a stored resume
for the job every command refused (``profile_ambiguous``), so a ``--resolves`` could never be checked against one
role's resume and applied to another's.

0.1.11.9: a job has ONE assessment, ONE resume and ONE suggestion record. Two roles cannot each have a resume for it:
a resume stored under another role REPLACES the job's. So nothing is ambiguous, nothing is refused, and a named role
picks no other record. (The role a view names is the one recorded on the job's assessment.)  Synthetic gig, two roles,
one job.
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
    monkeypatch.setenv(PIPELINE_ENV, "on")
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


def test_one_job_has_one_resume_whichever_role_stores_it_and_nothing_is_ambiguous(fx: PipelineFixture, tmp_path: Path) -> None:
    other = _second_profile(fx)

    # The second role assessed the job last: its id is the one recorded on the job's one assessment.
    assert _last(_invoke(fx, "resume", "pick", "--job-url", JOB))["profile_id"] == other
    assert _store(fx, tmp_path).exit_code == 0
    assert [item.resume.profile_id for item in list_tailored_resumes(fx.home_root, fx.target, job_identity=JOB)] == [other]
    assert _last(_invoke(fx, "resume", "brief", "--job-url", JOB))["profile_id"] == other

    # The same file stored under the FIRST role: it is the job's resume already, so nothing changes and the job still
    # has ONE resume (0.1.11: a second one under that role, and every command below refused).
    assert _store(fx, tmp_path, "--profile", fx.profile_id).exit_code == 0
    (held,) = list_tailored_resumes(fx.home_root, fx.target, job_identity=JOB)
    for args in (("resume", "pick"), ("resume", "brief"), ("resume", "brief", "--posting")):
        result = _invoke(fx, *args, "--job-url", JOB)
        assert result.exit_code == 0, (args, result.output)
    # (This fixture's assessment wrote no suggestion record: the command says THAT, and no longer `profile_ambiguous`.)
    listed = _invoke(fx, "suggestions", "list", "--job-url", JOB)
    assert listed.exit_code == 1 and _last(listed)["error"]["code"] == "suggestions_not_found", listed.output

    # Naming either role reads the same job: the same resume, the same record.
    views = [_last(_invoke(fx, "resume", "pick", "--job-url", JOB, "--profile", profile)) for profile in (fx.profile_id, other)]
    unnamed = _last(_invoke(fx, "resume", "pick", "--job-url", JOB))
    assert views[0] == views[1] == unnamed and unnamed["resume"]["markdown"] == held.markdown
    assert len(list_tailored_resumes(fx.home_root, fx.target, job_identity=JOB)) == 1

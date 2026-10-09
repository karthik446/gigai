"""0.1.10.11 S2 (cause C1 of the 0.1.10.10 speed report): what a pipeline trigger costs.

A save that changes a profile, the setup, a resume, an answer or a story runs
a pipeline trigger before it answers. The trigger computes the input digest
of every finished job of the profile (four digests a job when nothing
changed), or of every job that asked the question. On the operator-sized home
with 100 finished jobs a profile, one digest was about 98 git processes and
0.6 s, nothing was shared between the jobs, and a rename took three minutes
(22,304 git processes); ``PUT /api/setup`` took seven and a half.

Pinned here on the END outcome, with counts (they do not move with the
machine's load), on the synthetic gig of ``tests/support/pipeline_fixtures``:

* a trigger starts a bounded number of git processes, whatever the number of
  jobs it looks at (nothing per job);
* what it does is what it did: the jobs it re-opens, the ones it leaves, the
  per-trigger cap;
* a journal write made just before the trigger (the save's own) is seen by it.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

from click.testing import CliRunner
import pytest

import gigai.journal as journal
import gigai.workpad as workpad_module
from gigai.scout import story_bank
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.pipeline import triggers
from gigai.scout.pipeline.runner import DRAIN_RAN, PipelineRunner
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.pipeline.store import STEPS, PipelineStore
from gigai.scout.quick_assess import run_quick_assessment
from gigai.scout.scout_cli import scout_group

from tests.support.answers_stories_fixtures import config, pending, two_profiles
from tests.support.pipeline_fixtures import PipelineFixture, assessment, build_pipeline_fixture, resolved_job

_TERRAFORM = ("tooling:terraform", "Have you used Terraform in production?")
_DONE = dict.fromkeys(STEPS, "done")

#: What one trigger may start, however many jobs it looks at, nothing kept from an earlier read (a server that just
#: started; the read after the save's own journal write). Measured on this fixture (git processes):
#:
#: =====================================  =========  ==================  ==================
#: trigger                                0.1.10.10  in the read scope   + shared inputs
#: =====================================  =========  ==================  ==================
#: profile_changed, 2 finished jobs       446        33                  25
#: profile_changed, 8 finished jobs       1,778      69                  25
#: a saved answer, 2 jobs asked           241        35                  31
#: a saved answer, 14 jobs asked          1,372      77                  25
#: =====================================  =========  ==================  ==================
PROFILE_TRIGGER_SPAWNS_MAX = 32
ANSWER_TRIGGER_SPAWNS_MAX = 38


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PipelineFixture:
    monkeypatch.setenv(PIPELINE_ENV, "on")
    return build_pipeline_fixture(tmp_path, monkeypatch, base=False)


@pytest.fixture
def spawns(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every git process this process starts."""

    started: list[str] = []
    real = subprocess.run

    def counting(args, *rest, **kwargs):
        if isinstance(args, (list, tuple)) and args and str(args[0]).endswith("git"):
            started.append(" ".join(str(word) for word in args))
        return real(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", counting)
    return started


def _forget_everything() -> None:
    """As a process that just started: no workpad check and no committed read is kept."""

    for module, names in (
        (workpad_module, ("_validated_repositories", "_resolved_targets")),
        (journal, ("_validated_workpads", "_snapshot_cache", "_artifact_cache")),
    ):
        for name in names:
            getattr(module, name).clear()


def _job(n: int) -> str:
    return f"https://jobs.example.test/acme/role-{n:02d}"


def _ask(fx: PipelineFixture, url: str, profile_id: str | None = None) -> None:
    """A stored (base) assessment of ``url`` that leaves the Terraform question open."""

    fx.model.assessed = pending(*_TERRAFORM)
    run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=url), resume=AssessResumeInput(profile_id=profile_id or fx.profile_id)),
        home_root=fx.home_root, target=fx.target, config=config(fx.home_root), resolved_job=resolved_job(url),
    )
    fx.model.assessed = assessment(met=2)
    fx.model.assess_prompts.clear()


def _finish(fx: PipelineFixture, jobs: list[str], profile_id: str | None = None) -> None:
    """``jobs`` assessed and through the whole pipeline."""

    for job in jobs:
        _ask(fx, job, profile_id)
        triggers.process_now(fx.home_root, fx.target, profile_id or fx.profile_id, job)
    assert PipelineRunner(home_root=fx.home_root, target=fx.target, config=config(fx.home_root), busy=lambda: None).drain().state == DRAIN_RAN


def _states(fx: PipelineFixture, profile_id: str | None = None) -> dict[str, dict[str, str]]:
    store = PipelineStore(fx.db)
    try:
        found: dict[str, dict[str, str]] = {}
        for step in store.steps(profile_id=profile_id or fx.profile_id):
            found.setdefault(step.job, {})[step.name] = step.state
        return found
    finally:
        store.close()


def _counted(spawns: list[str], trigger):
    _forget_everything()
    spawns.clear()
    result = trigger()
    return result, len(spawns)


def test_the_profile_trigger_starts_no_git_process_per_finished_job(fx: PipelineFixture, spawns: list[str]) -> None:
    def changed():
        return triggers.profile_changed(fx.home_root, fx.target, fx.profile_id)

    _finish(fx, [_job(n) for n in range(2)])
    fired, two = _counted(spawns, changed)
    assert fired.to_json() | {"schema_version": None} == triggers.TriggerResult(triggers.TRIGGER_PROFILE).to_json() | {"schema_version": None}

    _finish(fx, [_job(n) for n in range(2, 8)])
    fired, eight = _counted(spawns, changed)

    # Nothing changed: nothing re-opens, no model call, and eight finished jobs cost what two did.
    assert fired.state == triggers.NOTHING and not fired.enqueued and not fired.skipped
    assert _states(fx) == {_job(n): _DONE for n in range(8)}
    assert eight <= two, (two, eight)
    assert eight <= PROFILE_TRIGGER_SPAWNS_MAX, (eight, spawns)


def test_a_saved_answers_trigger_starts_no_git_process_per_asking_job(fx: PipelineFixture, spawns: list[str]) -> None:
    def answered():
        entry = story_bank.get_answer(home_root=fx.home_root, target=fx.target, question_id=_TERRAFORM[0])
        assert entry is not None
        _forget_everything()
        spawns.clear()
        return triggers.pending_answer(fx.home_root, fx.target, entry).fire()

    for job in (_job(n) for n in range(2)):
        _ask(fx, job)
    story_bank.save_answer(home_root=fx.home_root, target=fx.target, question_id=_TERRAFORM[0], answer="Yes, three years.", question=_TERRAFORM[1])
    fired = answered()
    two = len(spawns)
    assert [item["job_identity"] for item in fired.enqueued] == [_job(1), _job(0)]  # the newest assessment first

    for job in (_job(n) for n in range(2, 14)):
        _ask(fx, job)
    fired = answered()
    fourteen = len(spawns)

    # The two queued before are unchanged; of the twelve new ones the cap (10 a trigger) queues ten, two wait.
    assert fired.unchanged == 2
    assert [item["job_identity"] for item in fired.enqueued] == [_job(n) for n in range(13, 3, -1)]
    assert [item["job_identity"] for item in fired.awaiting] == [_job(3), _job(2)]
    assert fired.approval is not None and fired.approval["jobs"] == 2
    assert fourteen <= two + 6, (two, fourteen)  # the approval's estimate reads the call history once
    assert fourteen <= ANSWER_TRIGGER_SPAWNS_MAX, (fourteen, spawns)


def test_a_roles_search_settings_write_reopens_nothing_and_the_trigger_after_it_agrees(fx: PipelineFixture) -> None:
    """0.1.11.9: a role's own search settings are search filters only (one set of candidate facts), so the journal
    write of `profile update` changes no step's inputs: nothing re-opens, for that role or the other.

    Until 0.1.11.9 this test proved that the save's own write is read by its trigger (that role's re-assessments
    re-opened). No role setting changes a digest any more, so that is no longer shown HERE.

    0.1.11.9 PJ6: a job has ONE set of steps, under the role that asked first; the second role's ask is a no-op."""

    default, second = two_profiles(fx.gig)
    jobs = [_job(n) for n in range(3)]
    _finish(fx, jobs, default)
    for job in jobs:
        asked = triggers.process_now(fx.home_root, fx.target, second, job)
        assert (asked["result"], asked["profile_id"]) == ("noop_unchanged", default)
    assert triggers.profile_changed(fx.home_root, fx.target).state == triggers.NOTHING  # everything read, and kept

    updated = CliRunner().invoke(
        scout_group,
        ["profile", "update", second, "--work-mode", "onsite", "--country", "CA", "--home", str(fx.home_root), "--target", str(fx.target), "--json"],
    )
    assert updated.exit_code == 0, updated.output

    assert _states(fx, second) == {}
    assert _states(fx, default) == {job: _DONE for job in jobs}
    # ... and the trigger again, with the inputs as they now are, opens nothing more.
    again = triggers.profile_changed(fx.home_root, fx.target)
    assert again.state == triggers.NOTHING and not again.enqueued
    assert _states(fx, second) == {} and _states(fx, default) == {job: _DONE for job in jobs}


def test_the_overview_looks_the_projects_folder_up_once_whatever_the_number_of_jobs(fx: PipelineFixture, spawns: list[str]) -> None:
    """``GET /api/pipeline`` lists up to 200 jobs with their Scout labels; each label looked the bound project up again."""

    from gigai.scout.pipeline.overview import overview

    def looked() -> tuple[dict[str, object], int]:
        _forget_everything()
        spawns.clear()
        return overview(fx.home_root, fx.target, busy=lambda: None), len(spawns)

    _finish(fx, [_job(n) for n in range(2)])
    status, two = looked()
    assert [job["label"]["label"] for job in status["jobs"]] == ["recommended"] * 2  # type: ignore[index]

    _finish(fx, [_job(n) for n in range(2, 8)])
    status, eight = looked()

    assert status["counts"]["jobs"] == {"done": 8}  # type: ignore[index]
    assert sorted(job["job_identity"] for job in status["jobs"]) == [_job(n) for n in range(8)]  # type: ignore[union-attr]
    assert [job["label"]["label"] for job in status["jobs"]] == ["recommended"] * 8  # type: ignore[index]
    assert eight == two, (two, eight)

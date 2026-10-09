"""0.1.10.11 S2: a digest computed with a trigger's shared inputs is byte for byte the one computed without.

A trigger now reads what its jobs share once (``steps.SharedInputs``) inside
the read scope, where before each job's digest read everything again. If the
two paths ever gave different digests, every finished job would look changed
to the next trigger, be tailored again and spend model calls. So, for all
four steps of every job of two profiles:

* the shared path gives the digest the per-job path gives (the path the
  runner still uses when it claims a step, unchanged since 0.1.10.10), and
* both give the digest the step was DONE with, so nothing re-opens;

and again after each thing a digest reads has changed: a stored master, an
edited answer, a new story, changed candidate settings. Jobs with different
postings, so the stories picked per posting differ between them.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
import pytest

import gigai.journal as journal
import gigai.workpad as workpad_module
from gigai.scout import stories, story_bank
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.master_store import import_master
from gigai.scout.pipeline import steps, triggers
from gigai.scout.pipeline.runner import DRAIN_RAN, PipelineRunner
from gigai.scout.pipeline.settings import PIPELINE_ENV, pipeline_setting
from gigai.scout.pipeline.store import STATE_DONE, STEPS, Claim, PipelineStore
from gigai.scout.quick_assess import run_quick_assessment
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailored_resume import TAILOR_PROMPT_HEADER
from gigai.workpad import committed_read_cache

from tests.support.answers_stories_fixtures import config, pending, two_profiles
from tests.support.master_tailor import copies_what_it_is_shown
from tests.support.pipeline_fixtures import ANSWER_CHANGED, POSTING, QUESTION_ID, PipelineFixture, assessment, build_pipeline_fixture, resolved_job

from .test_pipeline_master_tailoring import MASTER, RESUME

_TERRAFORM = ("tooling:terraform", "Have you used Terraform in production?")
#: Three postings: the fixture's (its story about GCP matches it), one no story matches, one a later story matches.
_POSTINGS = {
    "https://jobs.example.test/acme/role-00": POSTING,
    "https://jobs.example.test/acme/role-01": (
        "Northwind is hiring a Senior Accountant. Requirements: CPA; five years of audit work; month-end close. On site in Denver."
    ),
    "https://jobs.example.test/acme/role-02": (
        "Contoso is hiring a Payments Engineer. Requirements: 5+ years of Python; ledger reconciliation; PCI audits. Remote within the United States."
    ),
}


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PipelineFixture:
    monkeypatch.setenv(PIPELINE_ENV, "on")
    fixture = build_pipeline_fixture(tmp_path, monkeypatch, base=False, resume=RESUME)
    answer = fixture.model.answer

    def copies(prompt: str):  # noqa: ANN202 - the scripted model's own answer shape
        if prompt.lstrip().startswith(TAILOR_PROMPT_HEADER):
            fixture.model.tailored = copies_what_it_is_shown(prompt)
        return answer(prompt)

    monkeypatch.setattr(fixture.model, "answer", copies)
    return fixture


def _forget_everything() -> None:
    """As a process that just started: no workpad check and no committed read is kept."""

    for module, names in (
        (workpad_module, ("_validated_repositories", "_resolved_targets")),
        (journal, ("_validated_workpads", "_snapshot_cache", "_artifact_cache")),
    ):
        for name in names:
            getattr(module, name).clear()


def _drain(fx: PipelineFixture) -> None:
    assert PipelineRunner(home_root=fx.home_root, target=fx.target, config=config(fx.home_root), busy=lambda: None).drain().state == DRAIN_RAN


def _claim(step) -> Claim:
    return Claim(step.profile_id, step.job, step.name, step.lane, step.model_target, step.input_digest, step.done_digest, step.generation, step.attempts, "", 0.0, 0.0)


def _digests(fx: PipelineFixture) -> tuple[dict, dict, dict]:
    """``(per job, shared, done with)``: every step's digest by the per-job path and by the trigger's shared path, and
    the digest each DONE step was done with."""

    ctx = steps.StepContext(fx.home_root, fx.target, setting=pipeline_setting(fx.home_root, fx.target))
    store = PipelineStore(fx.db)
    try:
        rows = store.steps()
        assert len(rows) == 2 * len(_POSTINGS) * len(STEPS)
        _forget_everything()
        per_job = {
            (step.profile_id, step.job, step.name): (
                steps.tailor_digest(ctx, step.profile_id, step.job, step.model_target) if step.name == "tailor"
                else steps.input_digest(ctx, store, _claim(step))
            )
            for step in rows
        }
        _forget_everything()
        shared = steps.SharedInputs(ctx)
        with committed_read_cache():
            together = {
                (step.profile_id, step.job, step.name): (
                    steps.tailor_digest(ctx, step.profile_id, step.job, step.model_target, shared=shared) if step.name == "tailor"
                    else steps.input_digest(ctx, store, _claim(step), shared=shared)
                )
                for step in rows
            }
        return per_job, together, {(step.profile_id, step.job, step.name): step.done_digest for step in rows if step.state == STATE_DONE}
    finally:
        store.close()


def _cli(fx: PipelineFixture, *args: str) -> None:
    result = CliRunner().invoke(scout_group, [*args, "--home", str(fx.home_root), "--target", str(fx.target), "--json"])
    assert result.exit_code == 0, result.output


def test_every_steps_digest_is_the_same_with_the_triggers_shared_inputs(fx: PipelineFixture, tmp_path: Path) -> None:
    default, second = two_profiles(fx.gig)
    for profile_id in (default, second):
        for url, text in _POSTINGS.items():
            fx.model.assessed = pending(*_TERRAFORM)
            run_quick_assessment(
                AssessRequest(job=AssessJobInput(job_url=url), resume=AssessResumeInput(profile_id=profile_id)),
                home_root=fx.home_root, target=fx.target, config=config(fx.home_root), resolved_job=resolved_job(url, text),
            )
            fx.model.assessed = assessment(met=2)
            triggers.process_now(fx.home_root, fx.target, profile_id, url)
    _drain(fx)

    def same(what: str, *, done: bool = True) -> dict:
        per_job, together, done_with = _digests(fx)
        assert together == per_job, what
        if done:
            assert per_job == done_with, what  # every step is done, and with the digest its inputs give now
            assert triggers.profile_changed(fx.home_root, fx.target).state == triggers.NOTHING, what
        return per_job

    first = same("as the pipeline finished them")
    assert len({digest for key, digest in first.items() if key[2] == "tailor"}) == 6  # one per job: no constant is compared with itself
    # The story matches the first posting only, so the tailor digests read different picks.
    bank = story_bank.assess_bank(home_root=fx.home_root, target=fx.target, profile_id=default)
    picked = {url: bank.for_job(title="Staff AI Engineer", text=text).job_stories for url, text in _POSTINGS.items()}
    assert picked["https://jobs.example.test/acme/role-00"] and not picked["https://jobs.example.test/acme/role-01"]

    # A master is stored: every tailor digest now names it.
    source = tmp_path / "master.md"
    source.write_text(MASTER, encoding="utf-8")
    assert import_master(home_root=fx.home_root, target=fx.target, source=source, revision=None, gig_id=fx.gig.resolved.gig_id).status == "created"
    with_master = same("a master was stored", done=False)
    changed = {key for key in first if first[key] != with_master[key]}
    assert changed == {key for key in first if key[2] == "tailor"}
    reopened = triggers.profile_changed(fx.home_root, fx.target)
    assert sorted((item["profile_id"], item["job_identity"], item["step"]) for item in reopened.enqueued) == sorted(changed)
    _drain(fx)
    from_master = same("tailored again from the master")

    # An answer is edited: the tailoring is offered another revision, the re-assessment seals another bank.
    story_bank.save_answer(home_root=fx.home_root, target=fx.target, question_id=QUESTION_ID, answer=ANSWER_CHANGED, question="Have you run workloads on GCP?")
    edited = same("an answer was edited", done=False)
    assert all(edited[key] != from_master[key] for key in edited if key[2] in ("tailor", "reassess"))  # both read the answers

    # A story the third posting matches is added.
    stories.save_story(
        home_root=fx.home_root, target=fx.target,
        fields={"title": "Reconciled the payments ledger", "raw": "Rebuilt ledger reconciliation for the payments team before a PCI audit.", "tags": ["payments", "ledger"]},
    )
    edited_story = same("a story was added", done=False)
    assert edited_story != edited

    # One role's own search settings change.
    _cli(fx, "profile", "update", second, "--work-mode", "onsite", "--country", "CA")
    settings = same("a role's search settings changed", done=False)
    # 0.1.11.9: they are search filters only. The candidate's facts (location, work mode, countries, sponsorship) are
    # one set, read from the shared config whichever role asked, so no step's digest moves and no assessment goes
    # stale. (Until 0.1.11.9 that role's re-assessment and label digests changed: it was assessed for ITS settings.)
    assert {key[:1] + key[2:] for key in settings if settings[key] != edited_story[key]} == set()
    # The answer and the story were saved without their triggers; the look every save makes finds what they changed.
    assert {item["step"] for item in triggers.profile_changed(fx.home_root, fx.target).enqueued} == {"tailor"}
    _drain(fx)
    same("everything run again")

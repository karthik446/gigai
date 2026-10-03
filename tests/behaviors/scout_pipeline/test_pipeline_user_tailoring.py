"""0.1.10.7 fix1: the background tailoring never destroys the user's work, and a profile rename re-tailors nothing.

On the END outcome, with the synthetic gig and scripted model of
``tests/support/pipeline_fixtures`` (every model call is counted):

(a) a resume the user tailored and edited by hand survives an answer-triggered
    pipeline run byte for byte, no model re-tailors it, and the re-assessment,
    the ATS check and the label run against it;
(b) an edit (or a tailoring on demand) that lands WHILE the tailor step's
    model call is out is not lost, and the revision the user holds
    (``updated_at``) is still the stored one: their next choice is not refused;
(c) a resume the pipeline made, with nothing of the user's on it, is still
    tailored again when an answer changes;
(d) renaming a profile or changing its titles re-opens no step and calls no
    model; a new resume re-opens the tailoring.

The user's writes here are the routes' own: ``_tailor_by_hand`` is
``POST /api/tailored-resumes`` (``run_tailored_resume``) and ``_put_line`` is
``PUT /api/tailored-resumes/lines`` (the revision check, then the line edit).
The routes themselves, with the server's runner running, are
``tests/api_e2e/test_agent_api_journey.py``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.canonical import digest_imported_bytes
from gigai.private_records import create_record, import_reference
from gigai.scout import story_bank
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.find_jobs.contracts import PinnedResume
from gigai.scout.pipeline import steps, triggers
from gigai.scout.pipeline.runner import DRAIN_RAN, PipelineRunner, pipeline_status
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.pipeline.store import STEPS, PipelineStore
from gigai.scout.profile_records import write_profile
from gigai.scout.quick_assess import run_quick_assessment
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailored_resume import (
    TAILOR_PROMPT_HEADER,
    TailorRequest,
    TailorResponse,
    apply_line_edit,
    list_tailored_resumes,
    run_tailored_resume,
    save_tailor_response,
)

from tests.support.answers_stories_fixtures import config, pending
from tests.support.pipeline_fixtures import (
    ANSWER_CHANGED,
    JOB,
    QUESTION_ID,
    RESUME,
    PipelineFixture,
    assessment,
    build_pipeline_fixture,
    resolved_job,
)
from tests.support.scout_profile_fixtures import uuids

_TERRAFORM = ("tooling:terraform", "Have you used Terraform in production?")
_ANSWER = "Yes, three years of Terraform modules for our clusters."
_ASKED = "https://jobs.example.test/acme/role-01"
_WORDING = "Kept wording: ran the Python inference fleet on Kubernetes."
_KEPT = "tailor_kept_user_edits"
_DONE = dict.fromkeys(STEPS, "done")


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PipelineFixture:
    monkeypatch.delenv(PIPELINE_ENV, raising=False)
    return build_pipeline_fixture(tmp_path, monkeypatch)


def _ask(fx: PipelineFixture, url: str) -> None:
    """A stored (base) assessment of ``url`` that leaves the Terraform question open."""

    fx.model.assessed = pending(*_TERRAFORM)
    run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=url), resume=AssessResumeInput(profile_id=fx.profile_id)),
        home_root=fx.home_root, target=fx.target, config=config(fx.home_root), resolved_job=resolved_job(url),
    )
    fx.model.assessed = assessment(met=2)
    fx.model.assess_prompts.clear()


def _cli(fx: PipelineFixture, *args: str) -> dict[str, object]:
    result = CliRunner().invoke(scout_group, [*args, "--home", str(fx.home_root), "--target", str(fx.target), "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _runner(fx: PipelineFixture) -> PipelineRunner:
    return PipelineRunner(home_root=fx.home_root, target=fx.target, config=config(fx.home_root), busy=lambda: None)


def _states(fx: PipelineFixture, job: str) -> dict[str, str]:
    store = PipelineStore(fx.db)
    try:
        return {step.name: step.state for step in store.steps(profile_id=fx.profile_id, job=job)}
    finally:
        store.close()


def _stored(fx: PipelineFixture, job: str) -> TailorResponse:
    (item,) = list_tailored_resumes(fx.home_root, fx.target, profile_id=fx.profile_id, job_identity=job)
    return item


def _tailor_by_hand(fx: PipelineFixture, job: str) -> TailorResponse:
    """What ``POST /api/tailored-resumes`` does: the user (or their agent) tailors this job's resume on demand."""

    return run_tailored_resume(
        TailorRequest(job=AssessJobInput(job_url=job), resume=AssessResumeInput(profile_id=fx.profile_id)),
        home_root=fx.home_root, target=fx.target, config=config(fx.home_root), resolved_job=resolved_job(job),
    )


def _body_line_id(item: TailorResponse) -> str:
    for section in item.result.sections:
        for line in (*section.lines, *(bullet for entry in section.entries for bullet in entry.bullets)):
            if line.kind != "custom" and line.id:
                return line.id
    raise AssertionError("the tailored resume has no body line")


def _put_line(fx: PipelineFixture, job: str, held: TailorResponse, text: str = _WORDING) -> TailorResponse | str:
    """What ``PUT /api/tailored-resumes/lines`` does with the revision the user holds: the edited resume, or the 409's code."""

    stored = _stored(fx, job)
    if stored.updated_at != held.updated_at:
        return "tailored_resume_changed"
    updated = apply_line_edit(stored, _body_line_id(held), text)
    save_tailor_response(updated)
    return updated


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _tailor_entries(drained) -> list[dict[str, object]]:
    return [step for step in drained.steps if step["name"] == "tailor"]


# --- (a) a hand-tailored resume with the user's line survives an answer-triggered run; downstream uses it -----


def test_a_a_hand_tailored_resume_with_an_edited_line_survives_an_answer_triggered_run_and_downstream_uses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(PIPELINE_ENV, raising=False)
    fx = build_pipeline_fixture(tmp_path, monkeypatch, base=False)
    _ask(fx, _ASKED)
    held = _tailor_by_hand(fx, _ASKED)
    edited = _put_line(fx, _ASKED, held)
    assert isinstance(edited, TailorResponse) and _WORDING in edited.markdown
    stored_bytes = Path(edited.stored_path).read_bytes()
    assert len(fx.model.tailor_prompts) == 1  # the user's own tailoring

    saved = _cli(fx, "answers", "save", _TERRAFORM[0], "--answer-text", _ANSWER, "--question", _TERRAFORM[1])
    assert saved["pipeline"]["enqueued"] == [{"profile_id": fx.profile_id, "job_identity": _ASKED, "step": "tailor"}]
    drained = _runner(fx).drain()

    assert drained.state == DRAIN_RAN and _states(fx, _ASKED) == _DONE
    # The resume is the user's, byte for byte, and no model tailored it again.
    assert Path(edited.stored_path).read_bytes() == stored_bytes
    assert Path(edited.markdown_path).read_text(encoding="utf-8") == edited.markdown
    assert len(fx.model.tailor_prompts) == 1
    (tailor,) = _tailor_entries(drained)
    assert (tailor["outcome"], tailor.get("code"), tailor["model_calls"]) == ("done", _KEPT, 0)
    # Downstream ran against what the user chose: the re-assessment's prompt, the ATS record, then the label.
    assert len(fx.model.assess_prompts) == 1 and _WORDING in fx.model.assess_prompts[0] and drained.model_calls == 1
    ats = steps.read_ats(fx.home_root, fx.target, fx.profile_id, _ASKED)
    assert ats is not None and ats["tailored_sha256"] == _sha(edited.markdown.encode("utf-8"))
    assert steps.read_label(fx.home_root, fx.target, fx.profile_id, _ASKED) is not None
    # The status says so by code; the user's next choice, with the revision they hold, is accepted.
    status = pipeline_status(fx.home_root, fx.target, profile_id=fx.profile_id, job=_ASKED, busy=lambda: None)
    assert status["outputs"]["tailored_resume"]["outcome"] == _KEPT
    assert isinstance(_put_line(fx, _ASKED, held, "A second kept wording for the same line."), TailorResponse)


def test_a_a_resume_tailored_on_demand_is_the_users_even_before_any_line_choice(fx: PipelineFixture) -> None:
    held = _tailor_by_hand(fx, JOB)

    answer = _cli(fx, "pipeline", "process", JOB)

    assert len(fx.model.tailor_prompts) == 1 and _stored(fx, JOB).updated_at == held.updated_at
    # The edit the user was about to make is not refused: nothing replaced the resume under them.
    assert isinstance(_put_line(fx, JOB, held), TailorResponse)
    (tailor,) = [step for step in answer["drain"]["steps"] if step["name"] == "tailor"]
    assert (tailor["outcome"], tailor.get("code")) == ("done", _KEPT)
    # Not even --force replaces it: a new tailoring of the user's resume is the user's to ask for.
    forced = _cli(fx, "pipeline", "process", JOB, "--force")
    assert [step.get("code") for step in forced["drain"]["steps"] if step["name"] == "tailor"] == [_KEPT]
    assert len(fx.model.tailor_prompts) == 1 and _WORDING in _stored(fx, JOB).markdown


def test_a_a_line_edited_on_the_pipelines_own_resume_makes_it_the_users(fx: PipelineFixture) -> None:
    _cli(fx, "pipeline", "process", JOB)
    held = _stored(fx, JOB)
    assert isinstance(_put_line(fx, JOB, held), TailorResponse)

    story_bank.save_answer(home_root=fx.home_root, target=fx.target, question_id=QUESTION_ID, answer=ANSWER_CHANGED)
    changed = _cli(fx, "pipeline", "process", JOB)

    assert len(fx.model.tailor_prompts) == 1 and _WORDING in _stored(fx, JOB).markdown
    assert _WORDING in fx.model.assess_prompts[-1]  # the edited line is what was assessed
    outcomes = {step["name"]: (step["outcome"], step.get("code")) for step in changed["drain"]["steps"]}
    assert outcomes["tailor"] == ("done", _KEPT) and outcomes["reassess"] == ("done", None) and outcomes["ats"] == ("done", None)


# --- (b) what lands while the tailor step's model call is out is kept; the user's revision still holds ---------


def _during_the_pipelines_tailoring(fx: PipelineFixture, monkeypatch: pytest.MonkeyPatch, act) -> list[object]:
    """Run ``act`` once, inside the next tailor model call (the step is running, its model call is out)."""

    real, done = fx.model.answer, []

    def answer(prompt: str):
        if prompt.lstrip().startswith(TAILOR_PROMPT_HEADER) and not done:
            done.append(None)
            done[0] = act()
        return real(prompt)

    monkeypatch.setattr(fx.model, "answer", answer)
    return done


def test_b_a_line_edit_that_lands_while_the_tailor_step_runs_is_not_lost_and_the_users_revision_holds(
    fx: PipelineFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _cli(fx, "pipeline", "process", JOB)  # the pipeline's own resume: it may tailor it again
    held = _stored(fx, JOB)
    story_bank.save_answer(home_root=fx.home_root, target=fx.target, question_id=QUESTION_ID, answer=ANSWER_CHANGED)
    landed = _during_the_pipelines_tailoring(fx, monkeypatch, lambda: _put_line(fx, JOB, held))

    changed = _cli(fx, "pipeline", "process", JOB)

    assert isinstance(landed[0], TailorResponse), "the edit was accepted while the step ran"
    assert len(fx.model.tailor_prompts) == 2  # the step did call the model: the resume was its own when it started
    after = _stored(fx, JOB)
    assert _WORDING in after.markdown, "the user's line was written over by the background tailoring"
    assert after.updated_at == held.updated_at  # the revision the user holds: their next choice gets no 409
    assert isinstance(_put_line(fx, JOB, held, "A later wording, with the same revision."), TailorResponse)
    outcomes = {step["name"]: (step["outcome"], step.get("code")) for step in changed["drain"]["steps"]}
    assert outcomes["tailor"] == ("done", _KEPT)
    assert _WORDING in fx.model.assess_prompts[-1] and _states(fx, JOB) == _DONE


def test_b_a_tailoring_on_demand_that_lands_while_the_tailor_step_runs_is_the_one_kept(
    fx: PipelineFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    steps.enqueue_job(fx.profile_id, JOB, home_root=fx.home_root, target=fx.target)
    landed = _during_the_pipelines_tailoring(fx, monkeypatch, lambda: _tailor_by_hand(fx, JOB))

    drained = _runner(fx).drain()

    held = landed[0]
    assert isinstance(held, TailorResponse) and len(fx.model.tailor_prompts) == 2
    assert _stored(fx, JOB).updated_at == held.updated_at, "the background tailoring replaced the one the user asked for"
    assert [(step["outcome"], step.get("code")) for step in _tailor_entries(drained)] == [("done", _KEPT)]
    assert isinstance(_put_line(fx, JOB, held), TailorResponse)  # the agent journey's order: tailor, then edit; no 409
    assert _states(fx, JOB) == _DONE


# --- (c) the pipeline's own resume, with nothing of the user's on it, is still tailored again -------------------


def test_c_a_resume_without_user_edits_is_still_tailored_again_when_an_answer_changes(fx: PipelineFixture) -> None:
    first = _cli(fx, "pipeline", "process", JOB)
    assert [step.get("code") for step in first["drain"]["steps"] if step["name"] == "tailor"] == ["tailored"]
    before = _stored(fx, JOB)

    story_bank.save_answer(home_root=fx.home_root, target=fx.target, question_id=QUESTION_ID, answer=ANSWER_CHANGED)
    changed = _cli(fx, "pipeline", "process", JOB)

    outcomes = {step["name"]: (step["outcome"], step.get("code")) for step in changed["drain"]["steps"]}
    assert outcomes["tailor"] == ("done", "tailored")
    assert len(fx.model.tailor_prompts) == 2 and ANSWER_CHANGED in fx.model.tailor_prompts[1]
    assert _stored(fx, JOB).updated_at != before.updated_at  # a new tailoring replaced the pipeline's own
    assert changed["status"]["outputs"]["tailored_resume"]["outcome"] == "tailored"


# --- (d) the tailoring's digest: what changes the tailored text, nothing else of the profile -------------------


def test_d_renaming_a_profile_or_changing_its_titles_reopens_nothing_and_a_new_resume_reopens_the_tailoring(
    fx: PipelineFixture, tmp_path: Path,
) -> None:
    _cli(fx, "pipeline", "process", JOB)
    assert _states(fx, JOB) == _DONE
    calls = fx.model.calls
    digest = steps.tailor_digest(steps.StepContext(fx.home_root, fx.target), fx.profile_id, JOB)

    renamed = write_profile(fx.gig.resolved, profile_id=fx.profile_id, label="renamed profile")
    fired = triggers.profile_changed(fx.home_root, fx.target, fx.profile_id)
    assert renamed.label == "renamed profile"
    assert (fired.state, fired.enqueued, fired.awaiting) == ("nothing", (), ())
    assert _states(fx, JOB) == _DONE

    retitled = write_profile(fx.gig.resolved, profile_id=fx.profile_id, titles=("principal platform engineer",))
    fired = triggers.profile_changed(fx.home_root, fx.target, fx.profile_id)
    assert retitled.titles == ("principal platform engineer",) and retitled.revision > renamed.revision  # the record did change
    assert (fired.state, fired.enqueued, fired.awaiting) == ("nothing", (), ()), "a title change re-opened the tailoring"
    assert _states(fx, JOB) == _DONE
    assert steps.tailor_digest(steps.StepContext(fx.home_root, fx.target), fx.profile_id, JOB) == digest
    assert _runner(fx).drain().steps == [] and fx.model.calls == calls  # zero re-opened steps, zero model calls

    # A new resume is what the tailoring is made from: pinning one re-opens it.
    text = (RESUME + "Led the migration of the inference services to Terraform-managed clusters.\n").encode("utf-8")
    path = tmp_path / "resume-v2.md"
    path.write_bytes(text)
    gig = fx.gig
    imported = import_reference(
        home_root=gig.home_root, requested_target=gig.target, gig_id=gig.created.gig_id, kind="resume", source=path,
        operation_key=f"scout-resume-add:resume-v2.md:{digest_imported_bytes(text)}", uuid_factory=uuids(60),
    )
    record = create_record(
        home_root=gig.home_root, requested_target=gig.target, gig_id=gig.created.gig_id, kind="imported_reference",
        content_family="g45_reference", content_id=imported.item_id, actor={"kind": "operator", "id": "local-user"},
        origin="imported", operation_key=f"scout-resume-record:{imported.item_id}", uuid_factory=uuids(61),
    )
    write_profile(
        fx.gig.resolved, profile_id=fx.profile_id, resume_ref=PinnedResume(record.record_id, record.revision_id, digest_imported_bytes(text))
    )
    fired = triggers.profile_changed(fx.home_root, fx.target, fx.profile_id)

    assert fired.enqueued == ({"profile_id": fx.profile_id, "job_identity": JOB, "step": "tailor"},)
    assert steps.tailor_digest(steps.StepContext(fx.home_root, fx.target), fx.profile_id, JOB) != digest
    assert _states(fx, JOB) == {"tailor": "ready", "reassess": "blocked", "ats": "blocked", "label": "blocked"}
    assert fx.model.calls == calls  # queued; pinning the resume called no model

"""0.1.11.3 packet 1: an assessed job that suggests a resume HAS one, and "Pick it now" picks it. On the END outcome.

The end outcome is what the job page reads and what Apply prints: ``gigai scout resume pick --job-url URL`` shows a
picked resume, and ``gigai scout resume pdf --job-url URL`` is its PDF. The background pipeline is OFF in every test.

A fake model (the pipeline fixture's scripted one) on the synthetic home of ``test_assessment_v9_flow`` (one profile,
a small invented master, one posting). So:

- assess, then the job has its picked resume and the PDF, with no second model call;
- an assessment whose pick could not be made (the pages could not be measured, or the record could not be written)
  leaves no resume: ``resume pick --refresh`` (the page's "Pick it now") then picks it, with no model call;
- a re-pick replaces the pick's own resume; a resume the user changed is kept and the new selection waits as proposed;
- a held job gets a draft on request, marked as a draft, and a plain refresh of it is refused in plain words;
- a job with no master, a profile that uses its own resume, a stale assessment and a job never assessed are each
  refused by a code, in words a user can act on: never the name of a module, a function or an error code.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re

from click.testing import CliRunner
import pytest

from gigai.scout import job_actions, job_resume_port, pick, suggestions
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailored_resume import TailorEdit, read_tailored_resume, save_tailor_response

from tests.behaviors.scout_find_jobs.test_assessment_v9_flow import (  # noqa: F401 - `fx` is the fixture
    _JOB, _URL, PYTHON_LINE, RESUME, _assess, _record, _resume_path, _v8_answer, _v9_answer, fx,
)
from tests.support.posting_fixtures import PostingsFixture

#: What no message a user reads may hold: a module path, a function, an error code, a Python name.
_INTERNAL = re.compile(r"scout\.pick|settle_stored|pick_not_available|job_resume_port|[a-z]+_[a-z]+_?[a-z]*\(|Traceback|\b[a-z]+(?:_[a-z]+)+:")
_MATCHED = "matched_above_threshold"


@pytest.fixture(autouse=True)
def _pipeline_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PIPELINE_ENV, "off")


def _cli(fx: PostingsFixture, *args: str, as_json: bool = True):
    return CliRunner().invoke(scout_group, [*args, "--home", str(fx.home_root), "--target", str(fx.target), *(["--json"] if as_json else [])])


def _ok(fx: PostingsFixture, *args: str) -> dict:
    result = _cli(fx, *args)
    assert result.exit_code == 0, result.output
    return json.loads(result.output.strip().splitlines()[-1])


def _refused(fx: PostingsFixture, *args: str) -> dict[str, object]:
    result = _cli(fx, *args)
    assert result.exit_code == 1, result.output
    error = json.loads(result.output.strip().splitlines()[-1])["error"]
    assert not _INTERNAL.search(str(error["message"])), error["message"]
    return error


def _view(fx: PostingsFixture) -> dict:
    return _ok(fx, "resume", "pick", "--job-url", _URL)


def _picked_and_pdf(fx: PostingsFixture, tmp_path: Path, name: str = "job.pdf") -> dict:
    """THE END OUTCOME: the job page's read shows a picked resume, and Apply's PDF is that resume."""

    view = _view(fx)
    assert view["resume"] is not None, "the job has no picked resume"
    assert view["resume"]["made_by"] == "scout.pick" and view["picked"] is not None and view["selection_error"] is None
    assert PYTHON_LINE in view["resume"]["markdown"]
    out = tmp_path / name
    pdf = _ok(fx, "resume", "pdf", "--job-url", _URL, "--out", str(out))
    assert pdf["pages"] >= 1 and out.read_bytes().startswith(b"%PDF")
    return view


# --- assess, then picked, then the PDF -------------------------------------------------------------------------------


def test_assess_then_the_job_has_its_picked_resume_and_the_pdf_with_the_pipeline_off(fx: PostingsFixture, tmp_path: Path) -> None:
    stored, _prompt = _assess(fx, _v9_answer(fx))
    assert stored.result.verdict.value == _MATCHED and len(fx.base.model.assess_prompts) == 1
    view = _picked_and_pdf(fx, tmp_path)
    assert view["gate"] == {"decision": "suggest", "ready": True, "reasons": []} and view["stale"] == []
    assert (view["basis"], view["master_stored"]) == ("master", True)
    assert len(fx.base.model.assess_prompts) == 1


@pytest.mark.parametrize("failure", ["pages_unmeasured", "record_not_written"])
def test_a_pick_the_assessment_could_not_make_is_made_by_pick_it_now(fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    """The operator's dead end: a fully met job, 'No resume is stored for this job yet', and a button that picked nothing."""

    with monkeypatch.context() as broken:
        if failure == "pages_unmeasured":
            def no_renderer(*_args: object, **_kwargs: object):
                raise pick.PickError("pages_unmeasured", "the pages could not be measured (no renderer); no selection was made")

            broken.setattr(pick, "settle", no_renderer)
        else:
            def disk_full(*_args: object, **_kwargs: object):
                raise OSError("no space left on device")

            broken.setattr(suggestions, "store_assessed", disk_full)
        stored, _prompt = _assess(fx, _v9_answer(fx))
    assert stored.result.verdict.value == _MATCHED and stored.resume_gate.decision == "suggest"
    before = _view(fx)
    assert before["resume"] is None and not _resume_path(fx).exists()  # fully met, and no resume: the page offers "Pick it now"
    assert before["selection_error"] == ("pages_unmeasured" if failure == "pages_unmeasured" else None)

    calls = len(fx.base.model.assess_prompts)
    picked = _ok(fx, "resume", "pick", "--job-url", _URL, "--refresh")
    assert picked["action"] == "refresh" and picked["resume"] is not None
    view = _picked_and_pdf(fx, tmp_path)
    assert view["gate"]["decision"] == "suggest" and view["gate"]["ready"] is True and view["proposed"] is None
    assert len(fx.base.model.assess_prompts) == calls, "picking calls no model"
    record = _record(fx)
    assert record.selection is not None and record.selection_error is None and record.selection["draft"] is False
    assert record.selection["made_from"]["result_digest"] == suggestions.result_digest(stored)
    assert [(item.kind, item.source) for item in record.suggestions] == [("reword", "assessment"), ("gap", "assessment")]


def test_an_answer_in_the_older_shape_has_no_record_and_pick_it_now_still_picks(fx: PostingsFixture, tmp_path: Path) -> None:
    _assess(fx, _v8_answer())
    assert suggestions.read_suggestions(fx.home_root, fx.target, fx.default_profile_id, _JOB) is None and _view(fx)["resume"] is None
    _ok(fx, "resume", "pick", "--job-url", _URL, "--refresh")
    view = _picked_and_pdf(fx, tmp_path)
    assert view["gate"]["decision"] == "suggest" and view["picked"]["picked_by"] == "code"


# --- refresh: the pick's own resume is replaced, the user's is kept ---------------------------------------------------


def test_a_refresh_replaces_the_picks_own_resume_and_keeps_the_suggestions(fx: PostingsFixture, tmp_path: Path) -> None:
    _assess(fx, _v9_answer(fx))
    first = _record(fx)
    again = _ok(fx, "resume", "pick", "--job-url", _URL, "--refresh")
    second = _record(fx)
    assert again["proposed"] is None and second.selection is not None and second.selection["made_at"] >= first.selection["made_at"]
    assert [item.id for item in second.suggestions] == [item.id for item in first.suggestions] == ["sg-1", "sg-2"]  # no new assessment: nothing is renumbered
    assert second.created_at == first.created_at and second.basis == first.basis
    resume = read_tailored_resume(_resume_path(fx))
    assert resume.producer.callable == "scout.pick" and suggestions.is_replaceable(resume, second)
    _picked_and_pdf(fx, tmp_path)


def test_a_resume_the_user_changed_is_kept_and_the_new_selection_waits_as_proposed(fx: PostingsFixture) -> None:
    _assess(fx, _v9_answer(fx))
    selected = _record(fx).selection
    mine = replace(read_tailored_resume(_resume_path(fx)), edited=TailorEdit("operator", "2026-10-05T12:00:00Z"))
    save_tailor_response(mine, home_root=fx.home_root)
    kept = _resume_path(fx).read_bytes()

    view = _ok(fx, "resume", "pick", "--job-url", _URL, "--refresh")
    record = _record(fx)
    sibling = suggestions.proposed_resume_path(Path(record.stored_path))
    assert _resume_path(fx).read_bytes() == kept, "a resume the user changed is never replaced by a pick"
    assert record.selection == selected and record.proposed is not None and sibling.is_file()
    assert view["proposed"] is not None and view["resume"]["replaceable"] is False and view["resume"]["edited"]["written_by"] == "operator"
    # The one explicit step that replaces it.
    taken = _ok(fx, "resume", "pick", "--job-url", _URL, "--use-proposed")
    assert taken["proposed"] is None and taken["resume"]["edited"] is None and not sibling.exists()


# --- draft ------------------------------------------------------------------------------------------------------


def test_a_held_job_gets_a_draft_on_request_and_a_plain_refresh_is_refused_in_plain_words(fx: PostingsFixture, tmp_path: Path) -> None:
    _assess(fx, _v9_answer(fx, kubernetes="unmet", said=_MATCHED))
    held = _view(fx)
    assert held["gate"]["decision"] == "hold_unmet" and held["resume"] is None

    refused = _refused(fx, "resume", "pick", "--job-url", _URL, "--refresh")
    assert refused["code"] == "resume_held" and "draft" in str(refused["message"]) and not _resume_path(fx).exists()

    calls = len(fx.base.model.assess_prompts)
    drafted = _ok(fx, "resume", "pick", "--job-url", _URL, "--draft")
    assert drafted["action"] == "draft" and drafted["resume"]["made_by"] == "scout.pick"
    assert drafted["picked"]["draft"] is True and drafted["picked"]["fallback"] == "draft_requested" and drafted["picked"]["picked_by"] == "code"
    assert drafted["gate"]["decision"] == "hold_unmet" and drafted["gate"]["ready"] is False  # a draft never makes a held job ready
    out = tmp_path / "draft.pdf"
    assert _ok(fx, "resume", "pdf", "--job-url", _URL, "--out", str(out))["pages"] >= 1 and out.read_bytes().startswith(b"%PDF")
    # A draft is refreshed as a draft (its master line changed, say): still a draft, still held.
    again = _ok(fx, "resume", "pick", "--job-url", _URL, "--refresh")
    assert again["picked"]["draft"] is True and again["gate"]["decision"] == "hold_unmet"
    assert len(fx.base.model.assess_prompts) == calls

    _assess(fx, _v9_answer(fx))  # nothing held now
    not_needed = _refused(fx, "resume", "pick", "--job-url", _URL, "--draft")
    assert not_needed["code"] == "draft_not_needed"


# --- the refusals, each by a code and in plain words ------------------------------------------------------------------


def test_a_stale_assessment_is_still_refused_and_nothing_is_picked(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    _assess(fx, _v9_answer(fx))
    before = _resume_path(fx).read_bytes()
    record = _record(fx)
    real_view = job_actions.pick_view
    monkeypatch.setattr(job_actions, "pick_view", lambda *args, **kwargs: {**real_view(*args, **kwargs), "stale": ["assessment_stale:older_prompt"]})
    result = _cli(fx, "resume", "pick", "--job-url", _URL, "--refresh")
    error = json.loads(result.output.strip().splitlines()[-1])["error"]
    assert result.exit_code == 1 and error["code"] == "assessment_stale" and "Re-assess" in str(error["message"])
    assert _resume_path(fx).read_bytes() == before and _record(fx) == record


def test_without_a_master_the_refusal_says_what_to_do(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout import tailor_master

    _assess(fx, _v9_answer(fx))
    _resume_path(fx).unlink()
    monkeypatch.setattr(tailor_master, "stored_master", lambda *_args, **_kwargs: None)
    assert (_view(fx)["basis"], _view(fx)["master_stored"]) == ("profile_resume", False)
    error = _refused(fx, "resume", "pick", "--job-url", _URL, "--refresh")
    assert error["code"] == "no_master" and "master resume" in str(error["message"]) and "gigai scout resume master" in str(error["message"])
    assert not _resume_path(fx).exists()


def test_a_profile_on_a_resume_put_in_by_hand_gets_no_pick_and_is_told_how_to_get_one(fx: PostingsFixture, tmp_path: Path) -> None:
    """THE OPERATOR'S SCREEN, reproduced: a fully met job, not stale, no resume, no error, and a button that could pick nothing."""

    from gigai.scout import master_profiles, profile_records
    from gigai.scout.find_jobs.contracts import PinnedResume
    from gigai.scout.resume_import import import_resume_file

    assert master_profiles.first_selection(home_root=fx.home_root, target=fx.target, profile_id=fx.default_profile_id) is not None
    source = tmp_path / "replaced.md"
    source.write_text(RESUME.replace("six years", "seven years"), encoding="utf-8")  # what `gigai scout resume add FILE --profile ID` writes
    added = import_resume_file(home_root=fx.home_root, requested_target=fx.target, source=source, gig_id=fx.base.gig.resolved.gig_id)
    profile_records.write_profile(
        fx.base.gig.resolved, profile_id=fx.default_profile_id, resume_ref=PinnedResume(added.record_id, added.revision_id, added.content_sha256),
    )
    stored, _prompt = _assess(fx, _v9_answer(fx))
    view = _view(fx)
    assert stored.result.verdict.value == _MATCHED and view["gate"]["decision"] == "suggest" and view["stale"] == []
    assert view["resume"] is None and view["selection_error"] is None
    # What the page reads to say why, instead of offering a button that cannot work.
    assert (view["basis"], view["master_stored"]) == ("profile_resume", True)
    error = _refused(fx, "resume", "pick", "--job-url", _URL, "--refresh")
    assert error["code"] == "profile_resume_in_use" and "by hand" in str(error["message"])
    assert "gigai scout resume master selection refresh" in str(error["message"]) and not _resume_path(fx).exists()
    plain = _cli(fx, "resume", "pick", "--job-url", _URL, as_json=False)
    assert plain.exit_code == 0 and "Resume: none stored for this job. This profile uses the resume you put in by hand" in plain.output


def test_a_job_never_assessed_is_refused_in_plain_words(fx: PostingsFixture) -> None:
    for flag in ("--refresh", "--draft"):
        error = _refused(fx, "resume", "pick", "--job-url", _URL, flag)
        assert error["code"] == "assessment_missing" and "assess it first" in str(error["message"])


def test_a_pick_that_fails_says_so_in_plain_words_and_never_names_an_internal(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    _assess(fx, _v9_answer(fx))
    _resume_path(fx).unlink()

    def no_renderer(*_args: object, **_kwargs: object):
        raise pick.PickError("pages_unmeasured", "the pages could not be measured (no renderer); no selection was made")

    monkeypatch.setattr(pick, "settle", no_renderer)
    unmeasured = _refused(fx, "resume", "pick", "--job-url", _URL, "--refresh")
    assert unmeasured["code"] == "pages_unmeasured" and _record(fx).selection_error == "pages_unmeasured"

    def broken(*_args: object, **_kwargs: object):
        raise KeyError("b-zzzzzz")

    monkeypatch.setattr(pick, "settle", broken)
    failed = _refused(fx, "resume", "pick", "--job-url", _URL, "--refresh")
    assert failed["code"] == "pick_failed" and "re-assess" in str(failed["message"]) and "b-zzzzzz" not in str(failed["message"])
    # Plain text too (no --json): one sentence a user can act on.
    plain = _cli(fx, "resume", "pick", "--job-url", _URL, "--refresh", as_json=False)
    assert plain.exit_code != 0 and not _INTERNAL.search(plain.output) and "Traceback" not in plain.output, plain.output


def test_the_door_to_the_pick_never_names_an_internal_to_the_user(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """The message the operator saw ("scout.pick.settle_stored is not part of it") is gone, whatever this GigAI holds."""

    assert callable(job_resume_port.settle_stored()) and job_resume_port.settle_stored() is pick.settle_stored
    _assess(fx, _v9_answer(fx))
    monkeypatch.delattr(pick, job_resume_port.SETTLE_STORED)
    error = _refused(fx, "resume", "pick", "--job-url", _URL, "--refresh")
    assert error["code"] == "pick_not_available" and "Re-assess" in str(error["message"])


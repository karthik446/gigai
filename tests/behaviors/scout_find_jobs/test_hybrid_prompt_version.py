"""Decision #207: the hybrid assess prompt has its own version; the others keep theirs.

0110-048 changed what a HYBRID candidate's CANDIDATE WORK MODE paragraph says
("remote roles, and hybrid or on-site roles in their own area"; it said
"hybrid roles in their own area") and left the version name at v5. An
assessment a hybrid profile got from the earlier words then read as current.

Now a hybrid prompt is ``assess-prompt-v6``. The prompts 0110-048 did not
touch render the same bytes and keep their names: no work mode is v4, remote
only and on-site are v5. So:

* a hybrid profile's stored assessment sealed as v5 reads as made with older
  settings (``older_prompt``), and a run does not carry it forward;
* a remote-only, an on-site and a no-work-mode profile's assessments stand.

Synthetic gig, fake model that answers only from the prompt text (0110-038's).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from gigai.scout import assessment_core, quick_assess
from gigai.scout.assessment_basis import BasisCheck
from gigai.scout.assessment_core import assess_prompt_version, render_assess_prompt
from gigai.scout.find_jobs import market_acquisition
from gigai.scout.find_jobs.assess_contracts import AssessResponse
from gigai.scout.find_jobs.contracts import RowOutcome, WorkModePreference

from tests.support.scout_profile_fixtures import ProfileFixtureGig

from .test_assess_work_mode import _JOB, _ctx, _run, _with_mode, _write_find_jobs, binding, fx  # noqa: F401 - binding, fx: fixtures
from .test_run_assess_sealed_config import _job
from .test_stale_assessments import _assess, _calls, _reason, _state
from .test_story_bank_run_assess import _Binding, _run_id

V4, V5, V6 = "assess-prompt-v4", "assess-prompt-v5", "assess-prompt-v6"

#: sha256 of the prompt each work mode renders for ``_JOB`` / ``_ctx``. The
#: first three are also what the commit before 0110-048 rendered (compared
#: against that commit when this was written): those prompts keep their names.
PROMPT_SHA256 = {
    "": "0530b337241f5ac72850270409e3ef40d474c0e904db218de25435da605c5237",
    "remote": "70b84d9af4e5005814904378e8bc860d55bdf4ef747c4b425e2d443a8c278563",
    "onsite": "2518d6083291a8e28ae9aadd5ab3e617985f4ce0858ac9d96d701632e3b333a9",
    "hybrid": "880926fe0c46592ab9c2235ed61fd6e4233f9a08f8851babeba418c010b0e96e",
}
#: What a hybrid prompt rendered before 0110-048: the bytes the v5 name stood for.
HYBRID_SHA256_BEFORE_048 = "4cd39caa2764befe3c1ef590c6d2ac03ea6759cb7f4cd8623198dae1478091b5"


def _sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


# --- the names ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "version"),
    [
        ("", V4), (None, V4), ("any", V4), (WorkModePreference.ANY, V4),
        ("remote", V5), (WorkModePreference.REMOTE, V5),
        ("onsite", V5), (WorkModePreference.ONSITE, V5),
        ("hybrid", V6), (WorkModePreference.HYBRID, V6),
    ],
)
def test_each_work_mode_names_the_prompt_it_renders(mode: object, version: str) -> None:
    assert assess_prompt_version(mode) == version


def test_the_shipped_version_names() -> None:
    assert assessment_core.ASSESS_PROMPT_VERSION_HYBRID == V6
    assert assessment_core.ASSESS_PROMPT_VERSION == V5
    assert assessment_core.ASSESS_PROMPT_VERSION_NO_WORK_MODE == V4
    assert assessment_core.CURRENT_ASSESS_PROMPT_VERSIONS == {V4, V5, V6}


def test_only_the_hybrid_prompt_has_new_bytes() -> None:
    for mode, digest in PROMPT_SHA256.items():
        assert _sha256(render_assess_prompt(_JOB, _ctx(mode))) == digest, f"the {mode or 'no work mode'} prompt changed bytes"
    hybrid = render_assess_prompt(_JOB, _ctx("hybrid"))
    assert "CANDIDATE WORK MODE: hybrid (remote roles, and hybrid or on-site roles in their own area)" in hybrid
    assert _sha256(hybrid) != HYBRID_SHA256_BEFORE_048
    assert _sha256(hybrid.replace("and hybrid or on-site roles in their own area", "and hybrid roles in their own area", 1)) == HYBRID_SHA256_BEFORE_048


# --- a stored assessment --------------------------------------------------------------------------------


def _stored_under(item: AssessResponse, version: str) -> AssessResponse:
    """Rewrite the stored file as one sealed under ``version``: the same answer, the same constraints digest."""

    path = Path(item.stored_path)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["prompt_version"] != version
    stored["prompt_version"] = version
    path.write_text(json.dumps(stored, indent=2, sort_keys=True), encoding="utf-8")
    earlier = quick_assess._read_stored(path)
    assert earlier is not None and earlier.prompt_version == version and earlier.constraints_digest == item.constraints_digest
    return earlier


def test_a_hybrid_profiles_assessment_from_the_earlier_words_reads_as_made_with_older_settings(
    fx: ProfileFixtureGig, binding: _Binding
) -> None:
    _write_find_jobs(fx, _with_mode(WorkModePreference.HYBRID))

    item = _assess(fx)

    assert item.prompt_version == V6
    assert _reason(fx, item) is None and "assessment_stale" not in _state(fx, item)

    # As it was stored before the bump: made with the earlier hybrid words, named v5.
    earlier = _stored_under(item, V5)
    made = _calls(binding)

    assert _reason(fx, earlier) == "older_prompt"
    state = _state(fx, earlier)
    assert state["state"] == "needs_answers", "the verdict still reads"
    assert state["assessment_stale"] == {"reason": "older_prompt"}
    check = BasisCheck(home_root=fx.home_root, target=fx.target, resolved=fx.resolved)
    assert check.served(earlier) == {"basis_stale": True, "basis_stale_reason": "older_prompt"}
    assert _calls(binding) == made, "a read never calls a model"

    again = _assess(fx)  # assessed again: sealed under the hybrid prompt's own name, and current

    assert again.prompt_version == V6 and _reason(fx, again) is None
    assert [entry.trigger for entry in again.history] == ["assess", "reassess"], "the earlier verdict stays in the history"


@pytest.mark.parametrize(
    ("mode", "version"),
    [(WorkModePreference.REMOTE, V5), (WorkModePreference.ONSITE, V5), (None, V4)],
)
def test_a_remote_only_an_onsite_and_a_plain_profiles_assessments_stand(
    fx: ProfileFixtureGig, binding: _Binding, mode: WorkModePreference | None, version: str
) -> None:
    _write_find_jobs(fx, _with_mode(mode))

    item = _assess(fx)

    assert item.prompt_version == version, "sealed under the name it had before the hybrid bump"
    assert _reason(fx, item) is None and "assessment_stale" not in _state(fx, item)
    assert BasisCheck(home_root=fx.home_root, target=fx.target, resolved=fx.resolved).served(item) == {"basis_stale": False}


# --- a run's unchanged skip -----------------------------------------------------------------------------


def _seal_first_run_under(fx: ProfileFixtureGig, number: int, version: str) -> None:
    path = fx.created.workpad / "runs" / _run_id(number) / "outputs" / "assess.json"
    sealed = json.loads(path.read_text(encoding="utf-8"))
    assert sealed["prompt_version"] != version
    sealed["prompt_version"] = version
    path.write_text(json.dumps(sealed, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def test_a_run_assesses_a_hybrid_profiles_unchanged_posting_again(
    fx: ProfileFixtureGig, binding: _Binding, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(market_acquisition, "_rank_candidates", lambda *args, **kwargs: ())
    posting = _job(900, "Lumen", "United States", "Join our platform team. Our Dallas studio is open to visitors.")
    hybrid = _with_mode(WorkModePreference.HYBRID)

    _first_acquire, first = _run(fx, 911, posting, hybrid)
    assert first is not None and first.prompt_version == V6
    # Nothing changed: carried forward, no model call.
    second_acquire, second = _run(fx, 912, posting, hybrid)
    assert {row.outcome for row in second_acquire.rows} == {RowOutcome.UNCHANGED}
    assert second is None and len(second_acquire.carried_forward_assessments) == 1
    assert len(binding.port.prompts) == 1

    # As a run before the bump sealed it: the earlier hybrid words, named v5.
    _seal_first_run_under(fx, 911, V5)
    third_acquire, third = _run(fx, 913, posting, hybrid)

    assert third_acquire.carried_forward_assessments == (), "a verdict made with the earlier hybrid words is not served"
    assert third is not None and third.prompt_version == V6
    assert len(binding.port.prompts) == 2


def test_a_run_still_carries_a_remote_only_profiles_v5_assessment(
    fx: ProfileFixtureGig, binding: _Binding, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(market_acquisition, "_rank_candidates", lambda *args, **kwargs: ())
    posting = _job(900, "Lumen", "United States", "Join our platform team. Our Dallas studio is open to visitors.")
    remote = _with_mode(WorkModePreference.REMOTE)

    _first_acquire, first = _run(fx, 921, posting, remote)
    assert first is not None and first.prompt_version == V5
    second_acquire, second = _run(fx, 922, posting, remote)

    assert second is None and len(second_acquire.carried_forward_assessments) == 1
    assert len(binding.port.prompts) == 1

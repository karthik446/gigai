"""The assess prompt's version names: one name per wording, and an older wording reads as older.

Decision #207 (0110-048) gave a HYBRID candidate's prompt its own name (v6)
when that paragraph's words changed, and left the others theirs (no work
mode v4, remote only and on-site v5).

0.1.10.7 P5 fences the posting as untrusted in EVERY assess prompt
(``untrusted_text``: the UNTRUSTED TEXT rule and the two marker lines), so
every prompt has new bytes and every work mode's prompt is now
``assess-prompt-v7``. So:

* a stored assessment sealed as v4, v5 or v6 reads as made with older
  settings (``older_prompt``), whatever the profile's work mode, and a run
  does not carry it forward;
* one made now is sealed as v7 and is current.

The fence is the only change: taking the rule and the marker lines out of a
v7 prompt gives back, byte for byte, the prompt the earlier name stood for.

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
from gigai.scout.untrusted_text import FENCE_CLOSE, FENCE_OPEN, UNTRUSTED_POSTING_RULE

from tests.support.scout_profile_fixtures import ProfileFixtureGig

from .test_assess_work_mode import _JOB, _ctx, _run, _with_mode, _write_find_jobs, binding, fx  # noqa: F401 - binding, fx: fixtures
from .test_run_assess_sealed_config import _job
from .test_stale_assessments import _assess, _calls, _reason, _state
from .test_story_bank_run_assess import _Binding, _run_id

V4, V5, V6, V7 = "assess-prompt-v4", "assess-prompt-v5", "assess-prompt-v6", "assess-prompt-v7"
#: The name each work mode's prompt had before the fence (P5).
EARLIER = {"": V4, "remote": V5, "onsite": V5, "hybrid": V6}

#: sha256 of the prompt each work mode renders for ``_JOB`` / ``_ctx`` now (assess-prompt-v7).
PROMPT_SHA256 = {
    "": "3cd9a61e5ac09e4adf8553848938ad217d0f0b9ead93f111b96b3963bfd95f7d",
    "remote": "fa8743d3a2e5682b81c3fdb3b44d8682688ab2c78fb8f16287984db82440e5be",
    "onsite": "efd3d149b4970f43f707e009355e9b6fcddb03ab0e4ff2e5a303e38528342d35",
    "hybrid": "e850cb7379d0c3a825003291f16c686936ad17e843c88ef2efa48528f8674b00",
}
#: The same, before the fence: what v4, v5, v5 and v6 rendered (pinned here since 0110-048).
PROMPT_SHA256_BEFORE_THE_FENCE = {
    "": "0530b337241f5ac72850270409e3ef40d474c0e904db218de25435da605c5237",
    "remote": "70b84d9af4e5005814904378e8bc860d55bdf4ef747c4b425e2d443a8c278563",
    "onsite": "2518d6083291a8e28ae9aadd5ab3e617985f4ce0858ac9d96d701632e3b333a9",
    "hybrid": "880926fe0c46592ab9c2235ed61fd6e4233f9a08f8851babeba418c010b0e96e",
}
_POSTING_HEAD = "POSTING (fenced as untrusted; inside the fence, the ROLE, COMPANY and LOCATION lines and then the posting's own text):"


def _sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _without_the_fence(prompt: str) -> str:
    """``prompt`` with the P5 additions taken out: the rule, the block's heading and the two marker lines."""

    added = f"{UNTRUSTED_POSTING_RULE}\n\n{_POSTING_HEAD}\n{FENCE_OPEN}\n"
    assert prompt.count(added) == 1 and prompt.count(f"\n{FENCE_CLOSE}\n") == 1
    return prompt.replace(added, "").replace(f"\n{FENCE_CLOSE}\n", "\n")


# --- the names ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode",
    ["", None, "any", WorkModePreference.ANY, "remote", WorkModePreference.REMOTE, "onsite", WorkModePreference.ONSITE, "hybrid", WorkModePreference.HYBRID],
)
def test_each_work_mode_names_the_prompt_it_renders(mode: object) -> None:
    assert assess_prompt_version(mode) == V7


def test_the_shipped_version_names() -> None:
    assert assessment_core.ASSESS_PROMPT_VERSION_HYBRID == V7
    assert assessment_core.ASSESS_PROMPT_VERSION == V7
    assert assessment_core.ASSESS_PROMPT_VERSION_NO_WORK_MODE == V7
    assert assessment_core.CURRENT_ASSESS_PROMPT_VERSIONS == {V7}
    assert not {V4, V5, V6} & assessment_core.CURRENT_ASSESS_PROMPT_VERSIONS, "the unfenced prompts are older wording"


def test_every_prompt_has_new_bytes_and_the_fence_is_the_only_change() -> None:
    for mode, digest in PROMPT_SHA256.items():
        prompt = render_assess_prompt(_JOB, _ctx(mode))
        assert _sha256(prompt) == digest, f"the {mode or 'no work mode'} prompt changed bytes"
        assert digest != PROMPT_SHA256_BEFORE_THE_FENCE[mode]
        assert _sha256(_without_the_fence(prompt)) == PROMPT_SHA256_BEFORE_THE_FENCE[mode], f"the {mode or 'no work mode'} prompt changed more than the fence"
    hybrid = render_assess_prompt(_JOB, _ctx("hybrid"))
    assert "CANDIDATE WORK MODE: hybrid (remote roles, and hybrid or on-site roles in their own area)" in hybrid


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


_MODES = [(WorkModePreference.HYBRID, V6), (WorkModePreference.REMOTE, V5), (WorkModePreference.ONSITE, V5), (None, V4)]


@pytest.mark.parametrize(("mode", "earlier_version"), _MODES)
def test_an_assessment_from_the_unfenced_prompt_reads_as_made_with_older_settings(
    fx: ProfileFixtureGig, binding: _Binding, mode: WorkModePreference | None, earlier_version: str
) -> None:
    _write_find_jobs(fx, _with_mode(mode))

    item = _assess(fx)

    assert item.prompt_version == V7, "a new assessment seals the fenced prompt's name"
    assert _reason(fx, item) is None and "assessment_stale" not in _state(fx, item)
    assert BasisCheck(home_root=fx.home_root, target=fx.target, resolved=fx.resolved).served(item) == {"basis_stale": False}
    assert FENCE_OPEN in binding.port.prompts[-1] and UNTRUSTED_POSTING_RULE in binding.port.prompts[-1]

    verdict_state = _state(fx, item)["state"]
    # As it was stored before P5: the same answer and constraints, made with the unfenced prompt.
    earlier = _stored_under(item, earlier_version)
    made = _calls(binding)

    assert _reason(fx, earlier) == "older_prompt"
    state = _state(fx, earlier)
    assert state["state"] == verdict_state, "the verdict still reads"
    assert state["assessment_stale"] == {"reason": "older_prompt"}
    check = BasisCheck(home_root=fx.home_root, target=fx.target, resolved=fx.resolved)
    assert check.served(earlier) == {"basis_stale": True, "basis_stale_reason": "older_prompt"}
    assert _calls(binding) == made, "a read never calls a model"

    again = _assess(fx)  # assessed again: sealed under the fenced prompt's name, and current

    assert again.prompt_version == V7 and _reason(fx, again) is None
    assert [entry.trigger for entry in again.history] == ["assess", "reassess"], "the earlier verdict stays in the history"


# --- a run's unchanged skip -----------------------------------------------------------------------------


def _seal_first_run_under(fx: ProfileFixtureGig, number: int, version: str) -> None:
    path = fx.created.workpad / "runs" / _run_id(number) / "outputs" / "assess.json"
    sealed = json.loads(path.read_text(encoding="utf-8"))
    assert sealed["prompt_version"] != version
    sealed["prompt_version"] = version
    path.write_text(json.dumps(sealed, sort_keys=True, separators=(",", ":")), encoding="utf-8")


@pytest.mark.parametrize(("mode", "earlier_version"), _MODES)
def test_a_run_assesses_an_unchanged_posting_again_when_its_assessment_is_from_the_unfenced_prompt(
    fx: ProfileFixtureGig, binding: _Binding, monkeypatch: pytest.MonkeyPatch, mode: WorkModePreference | None, earlier_version: str
) -> None:
    monkeypatch.setattr(market_acquisition, "_rank_candidates", lambda *args, **kwargs: ())
    posting = _job(900, "Lumen", "United States", "Join our platform team. Our Dallas studio is open to visitors.")
    config = _with_mode(mode)

    _first_acquire, first = _run(fx, 911, posting, config)
    assert first is not None and first.prompt_version == V7
    # Nothing changed: carried forward, no model call. A v7 assessment stands.
    second_acquire, second = _run(fx, 912, posting, config)
    assert {row.outcome for row in second_acquire.rows} == {RowOutcome.UNCHANGED}
    assert second is None and len(second_acquire.carried_forward_assessments) == 1
    assert len(binding.port.prompts) == 1

    # As a run before P5 sealed it: the unfenced prompt, under the name it had then.
    _seal_first_run_under(fx, 911, earlier_version)
    third_acquire, third = _run(fx, 913, posting, config)

    assert third_acquire.carried_forward_assessments == (), "a verdict made with the unfenced prompt is not served"
    assert third is not None and third.prompt_version == V7
    assert len(binding.port.prompts) == 2
    assert FENCE_OPEN in binding.port.prompts[-1] and UNTRUSTED_POSTING_RULE in binding.port.prompts[-1]

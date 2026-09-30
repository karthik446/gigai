"""Q4a (v0.1.9): the quick assessment's verdict history.

Every assess/re-assess of one (resume, job) pair APPENDS a
``VerdictHistoryEntry {at, verdict, trigger}`` to the stored
``AssessResponse.history`` (operator answer 4), so the job page can show
the whole timeline. A stored file written before the field existed still
parses (``history == ()``) and, on its next re-assessment, its one known
state is reconstructed as the first entry rather than lost.

Reuses ``test_quick_assess.py``'s gig fixture, scripted model binding
(the C1 seam) and fixture replies so the history is asserted against the
exact code path ``POST /api/assess`` and ``POST /api/answers`` call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigai.scout.find_jobs.assess_contracts import AssessResponse, VerdictHistoryEntry
from gigai.scout.find_jobs.contracts import FindJobsContractError, Verdict
from gigai.scout.quick_assess import (
    TRIGGER_ANSWER_PREFIX,
    TRIGGER_ASSESS,
    TRIGGER_REASSESS,
    list_quick_assessments,
    run_quick_assessment,
)

from tests.behaviors.scout_find_jobs.test_quick_assess import (
    _GOOD_MATCH,
    _GOOD_PENDING,
    _config_with_ollama,
    _install,
    _pasted,
    _run,
)
from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

_RESUME = b"# Fixture Resume\n\nStaff AI Engineer. Built Python services for six years. (fixture only.)\n"

# The fixture reply with no verdict at all (an older prompt's shape): the
# history still records the assessment, with a null verdict.
_NO_VERDICT = json.dumps(
    {
        "matrix": [{"requirement": "5+ years of Python", "status": "met", "resume_evidence": ["six years"]}],
        "suggestions": [],
        "questions": [],
    }
)


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path, resume_text=_RESUME)


def _stored(response: AssessResponse) -> dict[str, object]:
    return json.loads(Path(response.stored_path).read_text(encoding="utf-8"))


def test_first_assessment_records_one_history_entry(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [_GOOD_PENDING])

    response = _run(fx, _pasted())

    assert response.history == (
        VerdictHistoryEntry(at=response.updated_at, verdict=Verdict.PENDING_USER_ANSWERS, trigger=TRIGGER_ASSESS),
    )
    assert response.updated_at == response.created_at
    stored = _stored(response)
    assert stored["history"] == [{"at": response.created_at, "verdict": "pending_user_answers", "trigger": "assess"}]
    assert AssessResponse.from_json(stored).history == response.history


def test_reassessment_appends_and_keeps_the_earlier_entries(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [_GOOD_PENDING, _GOOD_MATCH, _GOOD_PENDING])

    first = _run(fx, _pasted())
    second = _run(fx, _pasted())
    third = run_quick_assessment(
        _pasted(), home_root=fx.home_root, target=fx.target, config=_config_with_ollama(fx.home_root),
        trigger=TRIGGER_ANSWER_PREFIX + "cloud:gcp",
    )

    assert third.stored_path == first.stored_path
    assert [entry.trigger for entry in third.history] == [TRIGGER_ASSESS, TRIGGER_REASSESS, "answer:cloud:gcp"]
    assert [entry.verdict for entry in third.history] == [
        Verdict.PENDING_USER_ANSWERS, Verdict.MATCHED_ABOVE_THRESHOLD, Verdict.PENDING_USER_ANSWERS,
    ]
    assert third.history[0] == first.history[0]
    assert third.history[1] == second.history[1]
    assert third.history[-1].at == third.updated_at
    assert [entry.at for entry in third.history] == sorted(entry.at for entry in third.history)
    # The listing (GET /api/assessments) serves the same history from disk.
    listed = list_quick_assessments(fx.home_root, fx.target)
    assert len(listed) == 1 and listed[0].history == third.history


def test_a_stored_file_without_history_parses_and_is_reconstructed_on_reassess(
    fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch, [_GOOD_PENDING, _GOOD_MATCH])
    first = _run(fx, _pasted())

    # Rewrite the stored file the way a pre-Q4a server left it: no history key.
    stored = _stored(first)
    del stored["history"]
    Path(first.stored_path).write_text(json.dumps(stored), encoding="utf-8")
    parsed = AssessResponse.from_json(stored)
    assert parsed.history == ()
    assert parsed.to_json() == stored  # byte-identical round trip: no history key invented

    second = _run(fx, _pasted())

    assert second.history == (
        VerdictHistoryEntry(at=first.updated_at, verdict=Verdict.PENDING_USER_ANSWERS, trigger=TRIGGER_ASSESS),
        VerdictHistoryEntry(at=second.updated_at, verdict=Verdict.MATCHED_ABOVE_THRESHOLD, trigger=TRIGGER_REASSESS),
    )


def test_a_result_without_a_verdict_records_a_null_verdict(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [_NO_VERDICT])

    response = _run(fx, _pasted())

    assert response.result.verdict is None
    assert response.history[0].verdict is None
    assert _stored(response)["history"][0]["verdict"] is None
    assert AssessResponse.from_json(_stored(response)).history == response.history


def test_history_entry_contract_fails_closed() -> None:
    entry = VerdictHistoryEntry(at="2026-09-25T10:00:00Z", verdict=None, trigger="assess")
    assert VerdictHistoryEntry.from_json(entry.to_json()) == entry
    with pytest.raises(FindJobsContractError) as empty_trigger:
        VerdictHistoryEntry(at="2026-09-25T10:00:00Z", verdict=None, trigger="")
    assert empty_trigger.value.code == "invalid_value"
    with pytest.raises(FindJobsContractError) as bad_verdict:
        VerdictHistoryEntry.from_json({"at": "2026-09-25T10:00:00Z", "verdict": "maybe", "trigger": "assess"})
    assert bad_verdict.value.code == "bad_enum"
    with pytest.raises(FindJobsContractError) as extra_key:
        VerdictHistoryEntry.from_json({"at": "x", "verdict": None, "trigger": "assess", "note": "no"})
    assert extra_key.value.code == "unknown_key"

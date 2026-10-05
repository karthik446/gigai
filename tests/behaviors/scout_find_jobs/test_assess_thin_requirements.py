"""0.1.11 GUARDFIX (orchestrator #87): a thin answer is stored with a note, not refused.

The real fourth-set posting 01 (a 4.8 KB posting with TWO requirement bullets): both rows were correct, twice, and the guard
"fewer than 3 requirement rows for a 1,200+ character posting" refused a CORRECT assessment (an ERROR for the user). Now the
answer gets its ONE retry; if the retry is also thin it is stored as the model gave it, with
``requirements_note`` ("Only N requirements were read from this posting. Open the posting to check."). The error stays for an
answer with no usable row. Synthetic postings only; the model is the scripted binding of ``test_quick_assess``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigai.scout import quick_assess
from gigai.scout.call_metrics import capture_calls
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResponse
from gigai.scout.find_jobs.contracts import Verdict
from gigai.scout.quick_assess import QuickAssessError, list_quick_assessments
from tests.behaviors.scout_find_jobs.test_quick_assess import _install, _no_ambient_jev_key, _run, fx  # noqa: F401  (fixtures)

NOTE_2 = "Only 2 requirements were read from this posting. Open the posting to check."
_BOILERPLATE = "Acme builds scheduling software for clinics and values careful, humble engineering. " * 20


def _two_bullet_posting() -> str:
    return f"{_BOILERPLATE}\n\nRequirements:\n- 5+ years of Python in production\n- Experience operating GCP workloads\n"


def _eight_bullet_posting() -> str:
    bullets = "\n".join(f"- Requirement {n}: a skill the role needs" for n in range(8))
    return f"{_BOILERPLATE}\n\nRequirements:\n{bullets}\n"


def _answer(requirements: list[str]) -> str:
    return json.dumps(
        {
            "verdict": "matched_above_threshold",
            "matrix": [{"requirement": item, "class": "hard", "status": "met", "resume_evidence": ["six years"]} for item in requirements],
            "suggestions": [], "questions": [], "not_a_match_reason": None,
        }
    )


_TWO = _answer(["5+ years of Python in production", "Experience operating GCP workloads"])
_EIGHT = _answer([f"Requirement {n}: a skill the role needs" for n in range(8)])


def _assess(fx, text: str) -> AssessResponse:  # noqa: ANN001, F811
    return _run(fx, AssessRequest(job=AssessJobInput(job_text=text)))


def test_a_two_bullet_posting_is_stored_matched_with_the_note_after_exactly_one_retry(fx, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001, F811
    binding, _asked = _install(monkeypatch, [_TWO, _TWO])
    before = len(quick_assess.GUARD_RETRIES)
    with capture_calls() as calls:
        response = _assess(fx, _two_bullet_posting())
    assert response.result.verdict is Verdict.MATCHED_ABOVE_THRESHOLD
    assert [row.requirement for row in response.result.matrix] == ["5+ years of Python in production", "Experience operating GCP workloads"]
    assert response.requirements_note == NOTE_2
    assert len(binding.port.prompts) == 2 and len(calls) == 2  # the first call + the one retry, never a third
    assert len(quick_assess.GUARD_RETRIES) == before + 1
    # The note is stored, served on the response body, and read back from the stored file.
    assert response.to_json()["requirements_note"] == NOTE_2
    [stored] = list_quick_assessments(fx.home_root, fx.target)
    assert stored.requirements_note == NOTE_2 and stored.result.verdict is Verdict.MATCHED_ABOVE_THRESHOLD
    assert AssessResponse.from_json(json.loads(Path(response.stored_path).read_text())).requirements_note == NOTE_2


def test_one_row_reads_in_the_singular_and_two_or_more_keep_the_plural(fx, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001, F811
    one = _answer(["5+ years of Python in production"])
    _install(monkeypatch, [one, one])
    response = _assess(fx, _two_bullet_posting())
    assert response.requirements_note == "Only 1 requirement was read from this posting. Open the posting to check."
    assert quick_assess.requirements_note_text(1) == response.requirements_note
    assert quick_assess.requirements_note_text(2) == NOTE_2
    assert quick_assess.requirements_note_text(5) == "Only 5 requirements were read from this posting. Open the posting to check."


def test_the_retry_answer_is_the_one_stored(fx, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001, F811
    one = _answer(["5+ years of Python in production"])
    _install(monkeypatch, [one, _TWO])
    response = _assess(fx, _two_bullet_posting())
    assert len(response.result.matrix) == 2 and response.requirements_note == NOTE_2


def test_two_rows_for_eight_bullets_gets_its_retry_and_eight_rows_store_with_no_note(fx, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001, F811
    binding, _asked = _install(monkeypatch, [_TWO, _EIGHT])
    with capture_calls() as calls:
        response = _assess(fx, _eight_bullet_posting())
    assert len(response.result.matrix) == 8
    assert response.requirements_note is None and "requirements_note" not in response.to_json()
    assert len(binding.port.prompts) == 2 and len(calls) == 2
    [stored] = list_quick_assessments(fx.home_root, fx.target)
    assert len(stored.result.matrix) == 8 and stored.requirements_note is None


def test_a_full_first_answer_is_one_call_and_no_note(fx, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001, F811
    binding, _asked = _install(monkeypatch, [_EIGHT])
    with capture_calls() as calls:
        response = _assess(fx, _eight_bullet_posting())
    assert response.requirements_note is None and len(binding.port.prompts) == 1 and len(calls) == 1


def test_an_answer_with_no_usable_row_still_raises_the_error_after_its_one_retry(fx, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001, F811
    empty = _answer(["May work remotely anywhere in the US"])  # eligibility only: no usable requirement row
    binding, _asked = _install(monkeypatch, [empty, empty])
    with pytest.raises(QuickAssessError) as excinfo:
        _assess(fx, _two_bullet_posting())
    assert excinfo.value.code == "posting_requirements_unreadable"
    assert excinfo.value.reason == quick_assess.REASON_TOO_FEW_REQUIREMENTS
    assert len(binding.port.prompts) == 2
    assert list_quick_assessments(fx.home_root, fx.target) == ()


def test_a_thin_first_answer_then_no_row_raises_the_error(fx, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001, F811
    _install(monkeypatch, [_TWO, _answer(["May work remotely anywhere in the US"])])
    with pytest.raises(QuickAssessError) as excinfo:
        _assess(fx, _two_bullet_posting())
    assert excinfo.value.code == "posting_requirements_unreadable"
    assert list_quick_assessments(fx.home_root, fx.target) == ()


def test_a_short_posting_with_two_requirements_is_unchanged_no_retry_no_note(fx, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001, F811
    binding, _asked = _install(monkeypatch, [_TWO])
    response = _assess(fx, "Requirements:\n- 5+ years of Python in production\n- Experience operating GCP workloads\n")
    assert response.requirements_note is None and len(binding.port.prompts) == 1


def test_a_stored_file_without_the_field_round_trips_byte_equal(fx, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001, F811
    _install(monkeypatch, [_EIGHT])
    response = _assess(fx, _eight_bullet_posting())
    raw = json.loads(Path(response.stored_path).read_text())
    assert "requirements_note" not in raw
    assert AssessResponse.from_json(raw).to_json() == raw


def test_the_batch_and_the_terminal_say_which_assessment_was_thinly_read() -> None:
    batch = {"requirements_notes": [{"job_identity": "https://example.test/jobs/1", "profile_id": "p", "text": NOTE_2}]}
    assert quick_assess.requirements_note_lines(batch) == [f"  {NOTE_2} https://example.test/jobs/1"]
    assert quick_assess.requirements_note_lines({}) == []

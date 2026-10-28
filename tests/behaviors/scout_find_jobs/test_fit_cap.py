"""0.1.11.12 F2a (gigai-18i): a must-have that is not shown met caps the fit number a reader sees.

The shown fit (``fit.shown_fit``) is the raw percent (``fit.fit_percent``, unchanged) with a ceiling: at most
:data:`fit.UNMET_CAP` (49) when a ``hard`` row is ``unmet``, at most :data:`fit.OPEN_CAP` (69) when an ``askable`` row is
``unmet`` or a ``hard`` or ``askable`` row is ``unclear`` or ``answered``; the lower cap wins, optional rows never cap,
and a raw percent below
the cap stays as it is. ``fit.fit_cap`` names the row and says why. An ``answered`` must-have (the user said yes, no
resume line says it) also holds the resume at ``hold_question`` (``test_resume_gate``).
"""

from __future__ import annotations

from gigai.scout import fit, resume_gate
from gigai.scout.find_jobs.contracts import MatrixStatus, RequirementClass, RequirementMatrixRow
from gigai.scout.resume_gate import HOLD_QUESTION, gate

HARD, ASKABLE, LIST_ITEM, NICE = RequirementClass.HARD, RequirementClass.ASKABLE, RequirementClass.LIST_ITEM, RequirementClass.NICE_TO_HAVE
MET, UNMET, UNCLEAR, ANSWERED = MatrixStatus.MET, MatrixStatus.UNMET, MatrixStatus.UNCLEAR, MatrixStatus.ANSWERED


def _row(n: int, kind: RequirementClass | None, status: MatrixStatus, *, v9: bool = True) -> RequirementMatrixRow:
    evidence = ("Built it at Acme",) if status is MET else ()
    return RequirementMatrixRow(f"Requirement {n}", evidence, status, kind, id=f"req-{n:06x}" if v9 else None)


def _met(count: int, *, v9: bool = True) -> list[RequirementMatrixRow]:
    return [_row(n, HARD, MET, v9=v9) for n in range(1, count + 1)]


def test_an_unmet_hard_row_caps_the_shown_fit_at_49_and_names_the_row() -> None:
    rows = [*_met(9), _row(10, HARD, UNMET)]
    assert fit.fit_percent(rows) == 90  # the raw percent stays what it was
    assert fit.shown_fit(rows) == fit.UNMET_CAP == 49
    cap = fit.fit_cap(rows)
    assert cap is not None and cap.ceiling == 49
    assert cap.to_json() == {"row": "req-00000a", "reason": "A hard requirement is unmet, so the fit shows at most 49%."}


def test_an_unclear_askable_row_caps_the_shown_fit_at_69() -> None:
    rows = [*_met(9), _row(10, ASKABLE, UNCLEAR)]
    assert (fit.fit_percent(rows), fit.shown_fit(rows)) == (90, 69)
    assert fit.fit_cap(rows).to_json() == {"row": "req-00000a", "reason": "An askable requirement is unclear, so the fit shows at most 69%."}  # type: ignore[union-attr]


def test_an_answered_hard_row_caps_the_fit_at_69_and_holds_the_resume() -> None:
    rows = [*_met(9), _row(10, HARD, ANSWERED)]
    assert (fit.fit_percent(rows), fit.shown_fit(rows)) == (90, 69)
    assert fit.fit_cap(rows).to_json() == {  # type: ignore[union-attr]
        "row": "req-00000a", "reason": "A hard requirement is answered yes but not on your resume, so the fit shows at most 69%.",
    }
    found = gate(rows, [], "matched_above_threshold")
    assert found.decision == HOLD_QUESTION
    assert [(reason.code, reason.requirement_id) for reason in found.reasons] == [("answered", "req-00000a")]


def test_the_lower_cap_wins_whatever_the_row_order() -> None:
    rows = [*_met(8), _row(9, ASKABLE, UNCLEAR), _row(10, HARD, UNMET)]
    assert fit.shown_fit(rows) == 49 and fit.fit_cap(rows).row == "req-00000a"  # type: ignore[union-attr]
    assert fit.shown_fit(list(reversed(rows))) == 49
    # Between two rows of the same cap the first in matrix order is named.
    twice = [*_met(8), _row(9, ASKABLE, UNCLEAR), _row(10, HARD, ANSWERED)]
    assert fit.shown_fit(twice) == 69 and fit.fit_cap(twice).row == "req-000009"  # type: ignore[union-attr]


def test_optional_rows_never_cap_and_an_unmet_askable_row_caps_at_69() -> None:
    assert fit.shown_fit(_met(10)) == 100 and fit.fit_cap(_met(10)) is None
    rows = [*_met(10), _row(11, LIST_ITEM, UNCLEAR), _row(12, NICE, UNMET), _row(13, NICE, UNCLEAR), _row(14, LIST_ITEM, UNMET)]
    assert fit.fit_cap(rows) is None and fit.shown_fit(rows) == fit.fit_percent(rows)
    # Coordinator ruling (F2a fix round): an unmet askable row is a definite gap, never shown above an open one: 69.
    gap = [*_met(9), _row(10, ASKABLE, UNMET)]
    assert (fit.fit_percent(gap), fit.shown_fit(gap)) == (90, 69)
    assert fit.fit_cap(gap).to_json() == {"row": "req-00000a", "reason": "An askable requirement is unmet, so the fit shows at most 69%."}  # type: ignore[union-attr]
    # Beside an unmet hard row the lower cap still wins.
    assert fit.shown_fit([*gap, _row(11, HARD, UNMET)]) == 49


def test_the_cap_is_a_ceiling_never_a_floor() -> None:
    rows = [_row(1, HARD, MET), _row(2, HARD, UNMET), _row(3, ASKABLE, UNCLEAR), _row(4, HARD, UNMET)]
    assert fit.fit_percent(rows) == 25 and fit.shown_fit(rows) == 25
    assert fit.fit_cap(rows).ceiling == 49  # type: ignore[union-attr]  # still named: the reader can say why
    assert fit.shown_fit([]) is None and fit.fit_cap([]) is None


def test_the_sponsorship_label_of_a_v9_matrix_never_caps() -> None:
    sponsorship = RequirementMatrixRow("Visa sponsorship is not available", (), UNCLEAR, HARD, id="elig-sponsorship")
    rows = [*_met(9), sponsorship]
    assert fit.fit_cap(rows) is None and fit.shown_fit(rows) == fit.fit_percent(rows)
    # An old (v8) matrix: its gate reads the sponsorship row as a row (``hard_unmet``), so the cap does too.
    old_label = RequirementMatrixRow("Visa sponsorship is not available", (), UNMET, HARD)
    assert resume_gate.is_authorization_row(old_label)
    old = [*_met(9, v9=False), old_label]
    assert gate(old, [], "matched_above_threshold").decision == resume_gate.NOT_A_MATCH
    assert (fit.fit_percent(old), fit.shown_fit(old)) == (90, 49)


def test_an_old_stored_assessment_parses_and_keeps_its_number() -> None:
    # A v8 row as an assessment made before 0.1.11 stored it: no id, no class_basis, no fit_cap anywhere.
    stored = [{"requirement": f"Requirement {n}", "resume_evidence": ["Built it"], "status": "met", "class": "hard"} for n in range(1, 4)]
    stored.append({"requirement": "Rust", "resume_evidence": [], "status": "unmet", "class": "nice_to_have"})
    rows = [RequirementMatrixRow.from_json(row) for row in stored]
    assert [row.to_json() for row in rows] == stored  # byte for byte what it was
    assert fit.fit_cap(rows) is None and fit.shown_fit(rows) == fit.fit_percent(rows) == 86
    # A row with no class is an old one and reads hard: it caps like one, and has no id to name.
    old = [*_met(9, v9=False), RequirementMatrixRow.from_json({"requirement": "Clearance", "resume_evidence": [], "status": "unmet"})]
    cap = fit.fit_cap(old)
    assert fit.shown_fit(old) == 49 and cap is not None
    assert cap.to_json() == {"row": None, "reason": "A hard requirement is unmet, so the fit shows at most 49%."}

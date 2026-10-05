"""0.1.11 N3 (SPEC 1.5): the gate. ONE pure function decides whether a resume is suggested for an assessed job.

``gate(matrix, structured_questions, verdict) -> ResumeGate(decision, reasons)``.
``requirement_weights.settled_verdict`` (the model boundary) and
``requirement_weights.blocking_question_count`` (the validator, the pipeline's
label) both ask here, so the stored verdict and the gate are decided by the
same rule and cannot disagree.

The decisions, in the order they are checked (a mandatory row is ``hard`` or
``askable``; an optional row is ``list_item`` or ``nice_to_have``; a row with
no class is an old one and reads ``hard``):

==================  ==========================================================  =====================
``not_a_match``     the verdict is ``not_a_match``, or a ``hard`` row is        Not a match
                    ``unmet``
``hold_question``   a question waits on a mandatory row (or on no row of the    Needs your answers
                    matrix: nothing says it is minor); or, v9 rows only, a
                    mandatory row is ``unclear`` with no question on it
``hold_unmet``      v9 rows only (OD1): an ``askable`` row is ``unmet`` and     Has a gap
                    nothing above holds. The model's verdict stays stored as
                    it came; no resume is made until the user asks for a draft
``suggest``         none of the above: every mandatory row is supported, or     Matched (· N minor gaps)
                    what is open is optional
==================  ==========================================================  =====================

A row with ``alternatives`` whose status is ``met`` is supported like any
other ``met`` row: the model decided one of them is.

WHICH RULES A MATRIX IS READ BY (``uses_v9_rules``).  A matrix is v9 when one
of its rows carries a v9 field: an ``id``, a ``class_basis``, ``alternatives``
or ``sources``.  The v9 prompt asks for ``class_basis`` on every row of a
first assessment and code gives every row an id, so a stored v9 assessment
always reads v9.  Any other matrix (every assessment made before 0.1.11, and
an answer in the old shape) is read by the rules it was made under:

- OD2: on v9 rows an optional row never holds the verdict, whatever the count.
  On older rows the 0110-10-03 threshold stands: more than
  ``requirement_weights.MINOR_GAPS_ALLOWED`` open ``list_item`` questions
  hold it.
- OD1: ``hold_unmet`` exists on v9 rows only.  An older Matched assessment
  with an unmet ``askable`` row stays ``suggest``: a job that reads Matched
  today reads "Has a gap" only after its v9 assessment.

AN UNRESOLVED MUST-HAVE HOLDS AND ASKS (0.1.11 N3b, orchestrator decision #11).
On v9 rows a mandatory row that is ``unclear`` holds the verdict whether or
not the model asked about it (:func:`unasked_rows`): the accepted table says
an unresolved must-have holds for the answer, and "the model forgot the
question" is not an answer.  So that the hold is never a dead end, no stored
assessment carries such a row without its question: the validator refuses the
answer (``proposals``; the message names the row, and the model boundary
spends the one retry on it), and when the retry's answer still asks nothing
code asks the row's own question (``assessment_core``).  An optional row that
is ``unclear`` with no question stays as it is and never holds.  Older rows:
nothing of this; such a row reads Matched, as it always did.

``ready`` is not decided here: it is the final-selection check's
(``suggestions.check_selection``), made after the pick.

Pure: no file, no model, no clock.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .find_jobs.assess_contracts import (
    GATE_HOLD_QUESTION,
    GATE_HOLD_UNMET,
    GATE_NOT_A_MATCH,
    GATE_REASON_ASKABLE_UNMET,
    GATE_REASON_HARD_UNMET,
    GATE_REASON_QUESTION_OPEN,
    GATE_SUGGEST,
    GateReason,
    GateRecord,
)
from .find_jobs.contracts import is_row_id
from .requirement_weights import ASKABLE, HARD, MINOR_GAPS_ALLOWED, _field, _key, question_weights, row_class

SUGGEST = GATE_SUGGEST
HOLD_QUESTION = GATE_HOLD_QUESTION
HOLD_UNMET = GATE_HOLD_UNMET
NOT_A_MATCH = GATE_NOT_A_MATCH
#: The decisions under which no resume is made unless the user asks for a draft.
HOLDS: frozenset[str] = frozenset({HOLD_QUESTION, HOLD_UNMET, NOT_A_MATCH})

_V9_ROW_FIELDS = ("id", "class_basis", "alternatives", "sources")


def uses_v9_rules(matrix: Iterable[object]) -> bool:
    """Whether ``matrix`` is read by the v9 rules: one of its rows carries a v9 field (the module text)."""

    return any(_field(row, name) for row in matrix for name in _V9_ROW_FIELDS)


def is_mandatory(row: object) -> bool:
    """A must-have row: ``hard`` or ``askable`` (a row with no class is an old one, read as ``hard``)."""

    return row_class(row) in (HARD, ASKABLE, None)


def holding_questions(matrix: Iterable[object], questions: object) -> int:
    """How many questions hold the verdict at "needs your answers"; 0 when all that is open is optional.

    Every question counts once one of them holds. v9 rows: one holds when it
    is on a mandatory row or names no row. Older rows: also when more than
    ``MINOR_GAPS_ALLOWED`` of them are on ``list_item`` rows (0110-10-03).
    """

    rows = list(matrix)
    blocking, minor = question_weights(rows, questions)
    if uses_v9_rules(rows):
        return blocking + minor if blocking else 0
    return blocking + minor if blocking or minor > MINOR_GAPS_ALLOWED else 0


def unasked_rows(matrix: Iterable[object], questions: object) -> list[object]:
    """The mandatory rows of a v9 matrix that are ``unclear`` with no question on them, in matrix order (the module text).

    A question is on a row when it names the row's requirement words (the
    model boundary turns a row id into them). Always empty for an older
    matrix.
    """

    rows = list(matrix)
    if not uses_v9_rules(rows):
        return []
    asked = {_key(_field(question, "requirement")) for question in questions} if isinstance(questions, (list, tuple)) else set()
    asked.discard("")  # a question that names no row is on none
    return [row for row in rows if _field(row, "status") == "unclear" and is_mandatory(row) and _key(_field(row, "requirement")) not in asked]


#: Rows named after the first in :func:`unasked_message` (the model boundary feeds back the first 300 characters).
_UNASKED_NAMED = 3


def unasked_message(names: Sequence[str]) -> str:
    """The validation error for :func:`unasked_rows`, each row named by its id: ``unclear mandatory row req-xxxx has no question``.

    It is fed back to the model on the one retry, so it says the rule too
    (the model cannot see the answer that was refused). Ids only: never a
    requirement's words, which are the posting's.
    """

    rest = list(names[1:])
    more = ""
    if rest:
        more = f" (and {len(rest)} more: {', '.join(rest[:_UNASKED_NAMED])}{', ...' if len(rest) > _UNASKED_NAMED else ''})"
    return (
        f"unclear mandatory row {names[0]} has no question{more}: every hard or askable row whose status is unclear needs "
        "its own entry in questions, with requirement set to that row's requirement text; or settle the row met or unmet"
    )


@dataclass(frozen=True)
class GateRow:
    """One row behind a decision: why, the row's id (v9 rows) and its requirement words (the posting's)."""

    code: str
    requirement_id: str | None
    requirement: str


@dataclass(frozen=True)
class ResumeGate:
    """The gate's answer for one assessment: the decision and the rows that hold it."""

    decision: str
    reasons: tuple[GateRow, ...] = ()

    @property
    def suggests(self) -> bool:
        return self.decision == SUGGEST

    def record(self) -> GateRecord:
        """What is stored with the assessment: the decision, and each reason by row id (never a requirement's words)."""

        return GateRecord(self.decision, tuple(GateReason(row.code, row.requirement_id) for row in self.reasons))


def _row(code: str, row: object) -> GateRow:
    row_id = _field(row, "id")
    return GateRow(code, row_id if is_row_id(row_id) else None, str(_field(row, "requirement") or ""))  # type: ignore[arg-type]


def gate(matrix: Iterable[object], structured_questions: object, verdict: object) -> ResumeGate:
    """The decision for one assessment's rows, questions and verdict (the module text). Pure.

    ``matrix`` rows are JSON objects (the model boundary) or
    ``RequirementMatrixRow`` (a stored assessment); ``structured_questions``
    the questions it left open; ``verdict`` the verdict word or enum, or
    ``None`` for a result that carries none.
    """

    rows = list(matrix)
    verdict = getattr(verdict, "value", verdict)
    hard_unmet = [row for row in rows if _field(row, "status") == "unmet" and row_class(row) in (HARD, None)]
    if hard_unmet or verdict == NOT_A_MATCH:
        return ResumeGate(NOT_A_MATCH, tuple(_row(GATE_REASON_HARD_UNMET, row) for row in hard_unmet))
    questions = structured_questions if isinstance(structured_questions, (list, tuple)) else ()
    unasked = unasked_rows(rows, questions)  # v9 rows only: an unresolved must-have holds, asked about or not
    if holding_questions(rows, questions) or unasked:
        by_text = {_key(_field(row, "requirement")): row for row in reversed(rows)}
        asked: list[GateRow] = []
        seen: set[int] = set()
        for question in questions:
            row = by_text.get(_key(_field(question, "requirement")))
            if row is None:
                asked.append(GateRow(GATE_REASON_QUESTION_OPEN, None, str(_field(question, "requirement") or "")))
            elif is_mandatory(row) and id(row) not in seen:
                seen.add(id(row))
                asked.append(_row(GATE_REASON_QUESTION_OPEN, row))
        asked.extend(_row(GATE_REASON_QUESTION_OPEN, row) for row in unasked)
        return ResumeGate(HOLD_QUESTION, tuple(asked))
    if uses_v9_rules(rows):
        gaps = [row for row in rows if _field(row, "status") == "unmet" and row_class(row) == ASKABLE]
        if gaps:
            return ResumeGate(HOLD_UNMET, tuple(_row(GATE_REASON_ASKABLE_UNMET, row) for row in gaps))
    return ResumeGate(SUGGEST)


def stored_gate(item: object) -> ResumeGate | None:
    """The gate of a stored ``AssessResponse``, or ``None`` for one that stores none (made before 0.1.11, or in the old shape).

    Derived again from the stored rows by :func:`gate` (so it carries the
    requirement words for a reader to show); the stored ``resume_gate`` says
    that the assessment is a v9 one.
    """

    if getattr(item, "resume_gate", None) is None:
        return None
    result = item.result  # type: ignore[attr-defined]
    return gate(result.matrix, result.structured_questions, result.verdict)


def decision_of(item: object) -> str | None:
    """The stored gate decision of an ``AssessResponse``; ``None``: it stores none."""

    record = getattr(item, "resume_gate", None)
    return None if record is None else record.decision


__all__ = [
    "HOLDS",
    "HOLD_QUESTION",
    "HOLD_UNMET",
    "NOT_A_MATCH",
    "SUGGEST",
    "GateRow",
    "ResumeGate",
    "decision_of",
    "gate",
    "holding_questions",
    "is_mandatory",
    "stored_gate",
    "unasked_message",
    "unasked_rows",
    "uses_v9_rules",
]

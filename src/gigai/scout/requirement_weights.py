"""0110-10-03 (a) and (b): what a requirement row weighs, and how many rows an assessment keeps.

A posting's sentence "Experience with AWS, GCP, or similar cloud platforms, as
well as Docker, Helm, and Kubernetes" became one askable row per tool, and
Helm alone (nothing in the resume about it) held a job that met everything
else at "needs your answers": one tool inside a list weighed what "8+ years"
weighs. And the matrix was capped at 12 rows by the prompt, so a posting with
more stated requirements lost some with nothing said.

Weights. A row's class says how much it weighs:

* ``hard``, ``askable``: a must-have. An open question on one waits for the
  user's answer (``pending_user_answers``), as before.
* ``list_item``: one tool or technology among three or more that a single
  sentence or bullet names together. Its question is still asked, and up to
  :data:`MINOR_GAPS_ALLOWED` of them hold nothing up: the job is matched,
  with "1 minor gap: Helm". More than that many unknowns is no longer minor.
* ``nice_to_have``: a bonus. Never a question, never changes the verdict.

A ``list_item`` or ``nice_to_have`` row that is not met is a MINOR GAP
(:func:`minor_gaps`); it never makes a job not a match.

Rows. An assessment keeps every row the model returns up to
:data:`MAX_MATRIX_ROWS`, must-haves first (:func:`must_haves_first`); past
that the rest are counted, never dropped in silence (:func:`bound_rows`:
"+N not shown").

Everything here is a pure function over the rows as JSON objects (the model
boundary) or as ``RequirementMatrixRow`` (a stored assessment), so the model
boundary, the validator, the API, the CLI and the grid agree by construction.

0.1.11 N3 (SPEC 1.5). What holds a verdict is decided in ONE place,
``resume_gate.gate``: :func:`settled_verdict` and
:func:`blocking_question_count` ask it. On the rows of a v9 assessment
(``resume_gate.uses_v9_rules``: a row carries an id, a ``class_basis``,
``alternatives`` or ``sources``) an optional row never holds the verdict,
whatever the count (OD2); :data:`MINOR_GAPS_ALLOWED` is the threshold for
every older matrix only. :func:`minor_gaps` and :func:`minor_gap_text` are
unchanged: "Matched · 2 minor gaps: Cassandra, ClickHouse".
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

HARD = "hard"
ASKABLE = "askable"
LIST_ITEM = "list_item"
NICE_TO_HAVE = "nice_to_have"

#: Classes whose rows never hold a match up by themselves.
MINOR_CLASSES = frozenset({LIST_ITEM, NICE_TO_HAVE})
#: Open questions on ``list_item`` rows a match tolerates, in a matrix made before 0.1.11 (a v9 matrix tolerates any number).
MINOR_GAPS_ALLOWED = 1
#: Rows one assessment keeps. A sanity bound on a model's answer, far above what a posting states; past it: "+N not shown".
MAX_MATRIX_ROWS = 40
#: Gaps named in one line of text before "+N more".
GAPS_NAMED = 3

MATCHED = "matched_above_threshold"
PENDING = "pending_user_answers"

# A row with no class is an old one, from before rows had classes: a must-have, as the validator has always read it.
_ORDER = {HARD: 0, None: 0, ASKABLE: 1, LIST_ITEM: 2, NICE_TO_HAVE: 3}


def _field(row: object, name: str) -> object:
    if isinstance(row, Mapping):
        return row.get(name)
    value = getattr(row, "requirement_class" if name == "class" else name, None)
    return getattr(value, "value", value)


def row_class(row: object) -> str | None:
    value = _field(row, "class")
    return value if isinstance(value, str) else None


def is_minor(row: object) -> bool:
    return row_class(row) in MINOR_CLASSES


def minor_gaps(matrix: Iterable[object]) -> list[str]:
    """The requirement of every ``list_item`` or ``nice_to_have`` row that is not met, in matrix order."""

    return [str(_field(row, "requirement")) for row in matrix if is_minor(row) and _field(row, "status") != "met"]


def minor_gap_text(gaps: Sequence[str]) -> str | None:
    """``"1 minor gap: Helm"`` / ``"4 minor gaps: Helm, Istio, Argo CD +1 more"``; ``None`` for none."""

    if not gaps:
        return None
    named = ", ".join(gaps[:GAPS_NAMED]) + (f" +{len(gaps) - GAPS_NAMED} more" if len(gaps) > GAPS_NAMED else "")
    return f"{len(gaps)} minor gap{'' if len(gaps) == 1 else 's'}: {named}"


def must_haves_first(matrix: Sequence[object]) -> list[object]:
    """``matrix`` with must-haves first (hard, askable, one-of-a-list, bonus); rows of one class keep their order."""

    return sorted(matrix, key=lambda row: _ORDER.get(row_class(row), 0))


def bound_rows(matrix: Sequence[object]) -> tuple[list[object], int]:
    """``(the rows kept, how many are not shown)``: must-haves first, at most :data:`MAX_MATRIX_ROWS` kept."""

    ordered = must_haves_first(matrix)
    return ordered[:MAX_MATRIX_ROWS], max(0, len(ordered) - MAX_MATRIX_ROWS)


def _key(text: object) -> str:
    return " ".join(str(text).split()).casefold() if isinstance(text, str) else ""


def question_weights(matrix: Iterable[object], questions: object) -> tuple[int, int]:
    """``(blocking, minor)``: how many of ``questions`` are on a must-have row, and how many on a one-of-a-list row.

    A question names its row by the row's requirement text. One that names no
    row of the matrix is blocking: nothing says it is minor.
    """

    if not isinstance(questions, (list, tuple)):
        return 0, 0
    classes: dict[str, str | None] = {}
    for row in matrix:
        key = _key(_field(row, "requirement"))
        if key and key not in classes:
            classes[key] = row_class(row)
    blocking = minor = 0
    for question in questions:
        key = _key(_field(question, "requirement"))
        if key and classes.get(key, HARD) in MINOR_CLASSES:
            minor += 1
        else:
            blocking += 1
    return blocking, minor


def blocking_question_count(matrix: Iterable[object], questions: object) -> int:
    """How many questions hold the verdict at "needs your answers": 0 when all that is open is a minor gap.

    Every question counts once one of them is on a must-have row, or (a
    matrix made before 0.1.11 only) once more than :data:`MINOR_GAPS_ALLOWED`
    one-of-a-list rows are open. The rule is the gate's
    (``resume_gate.holding_questions``).
    """

    from .resume_gate import holding_questions  # the gate reads rows through this module

    return holding_questions(matrix, questions)


def settled_verdict(verdict: object, matrix: Iterable[object], questions: object) -> object:
    """``verdict`` with the gate's rule applied: matched and pending are decided by what the questions are on.

    A model that asked one question on a one-of-a-list row and answered
    "pending" (the rule every earlier prompt taught) is read as matched; one
    that answered "matched" with a must-have question open is read as
    pending. Any other verdict, and an answer with no question, is returned
    as it came: the validator still checks it against the rows.

    The decision is ``resume_gate.gate``'s (0.1.11 N3): ``hold_question`` is
    pending, ``suggest`` and ``hold_unmet`` (an unmet ``askable`` row of a v9
    matrix: the verdict stays the model's, the gate holds the resume) are
    matched, and a hard gap is not this rule's to settle.
    """

    if verdict not in (MATCHED, PENDING) or not isinstance(questions, (list, tuple)) or not questions:
        return verdict
    from .resume_gate import HOLD_QUESTION, NOT_A_MATCH, gate  # the gate reads rows through this module

    decision = gate(matrix, questions, verdict).decision
    if decision == NOT_A_MATCH:
        return verdict  # a hard gap: not this rule's to settle
    return PENDING if decision == HOLD_QUESTION else MATCHED


__all__ = [
    "ASKABLE",
    "GAPS_NAMED",
    "HARD",
    "LIST_ITEM",
    "MAX_MATRIX_ROWS",
    "MINOR_CLASSES",
    "MINOR_GAPS_ALLOWED",
    "NICE_TO_HAVE",
    "blocking_question_count",
    "bound_rows",
    "is_minor",
    "minor_gap_text",
    "minor_gaps",
    "must_haves_first",
    "question_weights",
    "row_class",
    "settled_verdict",
]

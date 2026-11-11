"""What gig code writes against for ``gigai <gig> eval run``: ``postprocess``, scorers and ``Score``.

- ``Postprocess``: ``(raw output text) -> (parsed value, parse status)``, one of ``records.PARSE_STATUSES``. It must
  be pure: ``eval run --from-traces`` and ``eval rescore`` run it again on stored outputs and expect the same answer.
- ``Scorer``: ``(trace, reference) -> Score``. ``trace`` is a ``ScoredTrace`` (the stored input, raw output and
  parsed output of one call); ``reference`` is the dataset row's ``expected`` value. A scorer is pure too.
- ``Score``: inspect_ai's shape (``value, answer, explanation, metadata``). ``value`` is ``CORRECT``/``INCORRECT``/
  ``PARTIAL``/``NOANSWER``, a number, or a yes/no/true/false string; ``score_value`` turns it into the number the
  ``scores`` row stores.

``match``, ``includes`` and ``pattern`` are inspect_ai's text scorers (vendored in ``_vendor/inspect_ai/scorers``)
as gigai scorers; a manifest names them ``gigai.evals.contracts:match`` and so on. ``match`` compares numbers when
every target is a number (``match(numeric=True)``) and text otherwise. ``judge_prompt``/``judge_score`` are
inspect_ai's ``model_graded_qa`` template and grade pattern, which a ``step:<id>`` judge scorer sends and reads.
"""

from __future__ import annotations

import math
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from ..canonical import digest_imported_bytes, sha256_hex
from ._vendor.inspect_ai._metric import (
    CORRECT,
    INCORRECT,
    NOANSWER,
    PARTIAL,
    Score,
    is_finite_number,
)
from ._vendor.inspect_ai.scorers import (
    DEFAULT_MODEL_GRADED_QA_TEMPLATE,
    default_instructions,
)
from ._vendor.inspect_ai.scorers import includes as _includes
from ._vendor.inspect_ai.scorers import match as _match
from ._vendor.inspect_ai.scorers import pattern as _pattern
from ._vendor.inspect_ai.scorers._shim import ModelOutput, TaskState
from ._vendor.inspect_ai.scorers._target import Target
from ._vendor.inspect_ai.scorers.judge_template import (
    _GRADE_WORD_VALUES,
    _PERMISSIVE_GRADE_PATTERN,
    _trailing_verdict_disagrees,
)
from .records import EvalsError, Postprocess

#: The ``scorer_version`` of the vendored scorers and judge template: the inspect_ai commit they were lifted from.
LIFTED_VERSION = "inspect_ai@9567b431"
_WORDS = {"yes": 1.0, "true": 1.0, "no": 0.0, "false": 0.0}
_LETTERS = {CORRECT: 1.0, PARTIAL: 0.5, INCORRECT: 0.0, NOANSWER: 0.0}
_JUDGE_INSTRUCTIONS = default_instructions(False)
#: The grades ``default_instructions(False)`` offers the judge (upstream's ``offered_grades`` without partial credit).
_OFFERED_GRADES = (CORRECT, INCORRECT)
#: The judge prompt's identity in ``scores`` and ``judge_validations``: the template and its instructions.
JUDGE_PROMPT_SHA256 = digest_imported_bytes(f"{DEFAULT_MODEL_GRADED_QA_TEMPLATE}\0{_JUDGE_INSTRUCTIONS}".encode())


@dataclass(frozen=True)
class ScoredTrace:
    """One stored call as a scorer sees it."""

    trace_id: str
    step_id: str
    item_id: str
    repeat_index: int
    #: The prompt sent, as stored.
    input: str
    #: The model's raw output text.
    output: str
    #: What the step's ``postprocess`` made of ``output`` (``None`` when the step has none).
    parsed: Any
    parse_status: str


Scorer = Callable[[ScoredTrace, Any], Score]


def score_value(score: object) -> float:
    """The number a ``scores`` row stores for ``score``; a scorer that returned something else is an ``EvalsError``.

    Non-finite numbers are refused: the vendored metrics give wrong intervals on them (``_vendor/inspect_ai/DIFF.md``).
    """

    if not isinstance(score, Score):
        raise EvalsError(f"a scorer returns a Score, not {type(score).__name__}")
    value = score.value
    if isinstance(value, str) and value in _LETTERS:
        number = _LETTERS[value]
    elif isinstance(value, int | float):  # bool included
        number = float(value)
    elif isinstance(value, str) and value.lower() in _WORDS:
        number = _WORDS[value.lower()]
    elif isinstance(value, str) and is_finite_number(value):
        number = float(value)
    else:
        raise EvalsError(f"score value {value!r} is not C/I/P/N, a number or yes/no/true/false")
    if not math.isfinite(number):
        raise EvalsError(f"score value {value!r} is not finite")
    return number


def lifted(body: Callable[[TaskState, Target], Awaitable[Score]]) -> Scorer:
    """A gigai scorer that runs a vendored inspect_ai scorer body on the trace's raw output."""

    def score(trace: ScoredTrace, reference: Any) -> Score:
        return _complete(body(TaskState(ModelOutput(trace.output)), Target(_targets(reference))))

    return score


def match(trace: ScoredTrace, reference: Any) -> Score:
    """inspect_ai ``match()`` at the end of the output: ``numeric=True`` when every target is a number."""

    numeric = all(is_finite_number(target) for target in _targets(reference))
    return lifted(_match(numeric=numeric))(trace, reference)


def includes(trace: ScoredTrace, reference: Any) -> Score:
    """inspect_ai ``includes()``: the target appears in the output (case ignored)."""

    return _INCLUDES(trace, reference)


def pattern(regex: str, ignore_case: bool = True, match_all: bool = False) -> Scorer:
    """inspect_ai ``pattern(regex)``: the first group must equal the target as text (``"1,234"`` is not ``"1234"``)."""

    return lifted(_pattern(regex, ignore_case=ignore_case, match_all=match_all))


_INCLUDES = lifted(_includes())
#: The scorers ``eval rescore --scorer <name>`` takes by name.
LIFTED_SCORERS: dict[str, Scorer] = {"match": match, "includes": includes}


def judge_prompt(input: str, output: str, reference: Any) -> str:
    """The ``model_graded_qa`` prompt grading ``output`` (the answer to ``input``) against ``reference``.

    The field markers' nonce is the first 8 hex digits of the SHA-256 of input and output (upstream draws it at
    random), so the same trace always gets the same prompt.
    """

    nonce = sha256_hex(f"{input}\0{output}".encode())[:8]
    return DEFAULT_MODEL_GRADED_QA_TEMPLATE.format(
        question=input,
        answer=output,
        criterion="\n".join(_targets(reference)),
        instructions=_JUDGE_INSTRUCTIONS,
        grader_nonce=nonce,
    )


def judge_score(reply: str) -> Score:
    """The judge's final verdict, read as upstream reads it under its default C/I instructions.

    Upstream's permissive pattern binds the last ``GRADE: <word>`` in the reply; the word must name a grade the
    instructions offered (C or I, or ``Correct``/``Incorrect``), and a later verdict line in a form the pattern does
    not read must agree. Anything else (no verdict, ``GRADE: X``, ``GRADE: CI``, ``GRADE: P``) is ``NOANSWER``: an
    off-menu final verdict never falls back to an earlier mention (inspect_ai ``_model.py`` lines 270-359).
    """

    found = re.search(_PERMISSIVE_GRADE_PATTERN, reply)
    if found is None:
        return Score(NOANSWER, explanation="the judge's reply has no GRADE")
    word = found.group(1).strip().strip("_")
    grade = _GRADE_WORD_VALUES.get(word.lower(), word.upper() if len(word) == 1 else None)
    if grade not in _OFFERED_GRADES or _trailing_verdict_disagrees(reply, found.end(), grade):
        return Score(NOANSWER, explanation="the judge's final GRADE is not one of the offered grades")
    return Score(grade, answer=grade, explanation=reply)


def _targets(reference: Any) -> list[str]:
    if isinstance(reference, Sequence) and not isinstance(reference, str | bytes):
        return [str(item) for item in reference]
    return [str(reference)]


def _complete(coroutine: Awaitable[Score]) -> Score:
    """Drive a scorer coroutine that never awaits (the lifted bodies do no I/O) to its result, without an event loop."""

    try:
        coroutine.send(None)  # type: ignore[attr-defined]
    except StopIteration as done:
        return done.value
    coroutine.close()  # type: ignore[attr-defined]
    raise EvalsError("a lifted scorer awaited something; only synchronous scorer bodies are supported")


__all__ = [
    "CORRECT",
    "INCORRECT",
    "JUDGE_PROMPT_SHA256",
    "LIFTED_SCORERS",
    "LIFTED_VERSION",
    "NOANSWER",
    "PARTIAL",
    "Postprocess",
    "Score",
    "ScoredTrace",
    "Scorer",
    "includes",
    "judge_prompt",
    "judge_score",
    "lifted",
    "match",
    "pattern",
    "score_value",
]

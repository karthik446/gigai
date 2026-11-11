"""Code scorers and the ``solve`` postprocess the eval fixture's ``[evals]`` table names (``gig check`` item 11
imports them).

A scorer takes the stored trace (``trace.output`` the raw reply, ``trace.parsed`` what the step's postprocess made
of it) and the dataset row's ``expected``, and returns a ``Score``; ``eval run`` records its value per trace.
"""

import re

from gigai.sdk import CORRECT, INCORRECT, Score, pattern

#: ``inspect_ai``'s ``pattern()`` on the ``ANSWER: <digits>`` marker: the captured text must equal the target as
#: text, so ``"ANSWER: 1,234"`` captures ``"1"`` and is incorrect (``match`` would normalise the number).
answer_marker = pattern(r"ANSWER:\s*(\d+)")

_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def exact_match(trace, reference):
    """Correct when the parsed answer equals ``reference`` (case and surrounding space ignored)."""

    answer = trace.parsed.get("answer") if isinstance(trace.parsed, dict) else None
    if not isinstance(answer, str):
        return Score(INCORRECT, explanation=f"parse status {trace.parse_status}")
    right = answer.strip().casefold() == str(reference).strip().casefold()
    return Score(CORRECT if right else INCORRECT, answer=answer)


def final_number(text):
    """The ``solve`` step's postprocess: the last number in the reply, commas dropped; ``invalid`` without one."""

    found = _NUMBER.findall(text)
    if not found:
        return None, "invalid"
    return {"answer": found[-1].replace(",", "")}, "ok"

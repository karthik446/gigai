"""0110-10-13: the typed causes of an assessment failure Scout controls, and what to do about each.

An agent that runs ``gigai scout jobs assess`` (or the API's ``POST /api/assess``
/ ``POST /api/postings/assess``) gets an error CODE today. A code alone does
not say what an agent has to know before it retries or reports: did a model
call start, may it have used tokens, and is there a fresh assessment now.
This module is the one table of that, read by the CLI's JSON, the API's
error bodies, the batch's ``assessed.failed`` items and (through the API) the
job page.

For each cause (``CAUSES``), three facts and one next action:

- ``model_call_started``: whether a request left for the model target.
- ``may_have_used_tokens``: whether the user's model account may have been
  charged. True whenever a call started: a call that failed late, timed out
  or answered something unusable cannot be ruled out as free.
- ``fresh_assessment_stored``: always false for a failure. Said on purpose:
  the assessment shown for the job, if any, is the earlier one, and "done"
  must not be claimed. Compare ``assessed_at`` before and after to be sure.
- ``next_action``: one sentence, for the user or the agent acting for them.

The causes are the codes ``quick_assess.run_quick_assessment`` raises for the
model side of an assessment, as they are (``assess_timeout`` is the timeout):
nothing is renamed, the facts are added beside the code. A code that is not
in the table (a bad URL, a missing profile) has no facts added: no model was
involved and its message says what is wrong.

What this cannot type: a refusal by the AGENT RUNTIME (its sandbox or its
model-provider approval) happens before the ``gigai`` command starts, so
Scout never sees it and returns nothing for it. The agent guide says what an
agent does then.

Pure: no I/O, no model call.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Cause:
    """One typed cause: whether a model call started (and so may have used tokens), and the next action."""

    code: str
    model_call_started: bool
    next_action: str

    def fields(self) -> dict[str, object]:
        """The facts as they are added beside an error's ``code`` (or a failed item's ``error_code``)."""

        return {
            "model_call_started": self.model_call_started,
            "may_have_used_tokens": self.model_call_started,
            "fresh_assessment_stored": False,
            "next_action": self.next_action,
        }

    def sentence(self) -> str:
        """The three facts in words, as the terminal and the job page say them."""

        if self.model_call_started:
            return "A model call started and may have used tokens. No new assessment was stored."
        return "No model call was made and no tokens were used. No new assessment was stored."


_CAUSES = (
    Cause(
        "model_target_unavailable", False,
        "Fix the model target, then assess again: `gigai models` shows what is configured and `gigai setup` changes it "
        "(a codex_cli or claude_cli target needs that command on PATH and logged in).",
    ),
    Cause(
        "model_denied", False,
        "GigAI's own settings refused this call before anything was sent. Ask the user to allow the model target in "
        "`gigai setup` or to pick another one; do not retry unchanged.",
    ),
    Cause(
        "model_unavailable", True,
        "The model target did not answer. Check that it is running and logged in (`gigai models`), then assess again; "
        "that is one more model call.",
    ),
    Cause(
        "assess_timeout", True,
        "The model call ran out of time. Check `assessed_at` for this job first; if it is unchanged, assess again "
        "(one more model call) or pick a faster model target.",
    ),
    Cause(
        "model_output_invalid", True,
        "The model answered twice with something that is not an assessment. Assess again (one more model call) or "
        "pick another model target.",
    ),
    Cause(
        "assessment_not_stored", True,
        "The model answered but the result could not be written. Check free disk space and that the GigAI home is "
        "writable, then assess again; that is one more model call.",
    ),
)
#: Every typed cause, by its error code.
CAUSES: Mapping[str, Cause] = {cause.code: cause for cause in _CAUSES}
#: What the ticket calls "timeout".
TIMEOUT = "assess_timeout"


def cause_fields(code: object) -> dict[str, object]:
    """The facts and the next action for ``code``; empty for a code that is not a typed cause."""

    cause = CAUSES.get(code) if isinstance(code, str) else None
    return cause.fields() if cause is not None else {}


def cause_lines(code: object) -> tuple[str, ...]:
    """What the terminal prints under a typed cause's message: the three facts, then the next action."""

    cause = CAUSES.get(code) if isinstance(code, str) else None
    return () if cause is None else (cause.sentence(), f"Next: {cause.next_action}")


def failure_lines(failed: object) -> list[str]:
    """Under a batch's "not assessed" lines: each typed cause among ``failed`` once, with its facts and next action."""

    codes = [item.get("error_code") for item in failed if isinstance(item, Mapping)] if isinstance(failed, (list, tuple)) else []
    return [f"  {code}: {' '.join(cause_lines(code))}" for code in dict.fromkeys(codes) if code in CAUSES]


__all__ = ["CAUSES", "TIMEOUT", "Cause", "cause_fields", "cause_lines", "failure_lines"]

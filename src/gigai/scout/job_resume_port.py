"""0.1.11 N5: the one door from the job commands to the step that picks a STORED job's resume again.

``gigai scout resume pick --refresh | --draft`` and ``POST /api/job-resumes/pick`` (``job_actions.pick_action``) call
one action of ``scout.pick`` (:data:`SETTLE_STORED`, there since 0.1.11.3): settle the stored assessment's pick
against the master as it is now, keep a resume that is the user's (the new selection then waits as ``proposed``),
and write the suggestion record.

It is looked up HERE, by name, when it is called, and nowhere else. A GigAI without it answers :class:`NotBuilt`
(a ``NotImplementedError`` with an API/CLI ``code``, ``pick_not_available``): a typed refusal, never an
``AttributeError`` and never a silent nothing. Its message is for the USER, like every refusal of a pick: what to do
instead, in plain words, and never the name of a module or a function (0.1.11.3: the operator read
"scout.pick.settle_stored is not part of it" on a job page).

No model call, no file of its own.
"""

from __future__ import annotations

from collections.abc import Callable

#: ``pick.<this>(home_root, target, profile_id, job_identity, *, action, now) -> suggestions.SuggestionRecord``:
#: settle the STORED assessment's pick against the master as it is now (``action``: :data:`ACTIONS`), keep a
#: resume that is the user's (the new selection then waits as ``proposed``), write the record. No model call.
SETTLE_STORED = "settle_stored"
ACTION_REFRESH = "refresh"
ACTION_DRAFT = "draft"
ACTIONS: tuple[str, ...] = (ACTION_REFRESH, ACTION_DRAFT)
#: 0.1.11.3 item 15, "Shorten automatically": ``pick.<this>(home_root, target, profile_id, job_identity, *, now)
#: -> pick.Shortened``: the same pick under a tighter page budget, and what it left out. No model call.
SHORTEN_STORED = "shorten_stored"
ACTION_SHORTEN = "shorten"

NOT_AVAILABLE_MESSAGE = (
    "A resume cannot be picked again for this job here. Re-assess the job to get a new pick: `gigai scout jobs assess URL --again` "
    "(a job assessed by its URL: `gigai scout assess --job-url URL`; one model call, on your yes)."
)


class NotBuilt(NotImplementedError):
    """A part this GigAI does not hold; ``code`` is the API/CLI error code, the message says what to do instead."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def settle_stored() -> Callable[..., object]:
    """The re-pick / draft action of ``scout.pick`` (:data:`SETTLE_STORED`); :class:`NotBuilt` (``pick_not_available``) without it."""

    from . import pick

    action = getattr(pick, SETTLE_STORED, None)
    if action is None:
        raise NotBuilt("pick_not_available", NOT_AVAILABLE_MESSAGE)
    return action


def shorten_stored() -> Callable[..., object]:
    """The shorten action of ``scout.pick`` (:data:`SHORTEN_STORED`); :class:`NotBuilt` (``pick_not_available``) without it."""

    from . import pick

    action = getattr(pick, SHORTEN_STORED, None)
    if action is None:
        raise NotBuilt("pick_not_available", NOT_AVAILABLE_MESSAGE)
    return action


__all__ = [
    "ACTIONS", "ACTION_DRAFT", "ACTION_REFRESH", "ACTION_SHORTEN", "NOT_AVAILABLE_MESSAGE", "SETTLE_STORED", "SHORTEN_STORED", "NotBuilt",
    "settle_stored", "shorten_stored",
]

"""0.1.11 N5: the one door from the job commands to the step that picks a STORED job's resume again.

``gigai scout resume pick --refresh | --draft`` and ``POST /api/job-resumes/pick`` (``job_actions.pick_action``) need
one action of ``scout.pick`` that this tree does not hold yet (:data:`SETTLE_STORED`): settle the stored
assessment's pick against the master as it is now, keep a resume that is the user's (the new selection then waits
as ``proposed``), and write the suggestion record. ``pick.settle`` (the selection itself) and ``suggestions`` (the
record and its store) are there; what is missing is that one function over a stored job.

It is looked up HERE, by name, when it is called, and nowhere else. A GigAI without it answers :class:`NotBuilt`
(a ``NotImplementedError`` with an API/CLI ``code``, ``pick_not_available``): a typed refusal that says which part
is missing and what to do instead, never an ``AttributeError`` and never a silent nothing. The day ``scout.pick``
defines the function, the commands and the route call it with no change here.

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


class NotBuilt(NotImplementedError):
    """A part this GigAI does not hold yet; ``code`` is the API/CLI error code, the message names the part."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def settle_stored() -> Callable[..., object]:
    """The re-pick / draft action of ``scout.pick`` (:data:`SETTLE_STORED`); :class:`NotBuilt` (``pick_not_available``) without it."""

    from . import pick

    action = getattr(pick, SETTLE_STORED, None)
    if action is None:
        raise NotBuilt(
            "pick_not_available",
            f"this GigAI cannot pick a job's resume again yet (scout.pick.{SETTLE_STORED} is not part of it); "
            "re-assess the job to get a new pick: `gigai scout jobs assess URL --again` (a job assessed by its URL: `gigai scout assess --job-url URL`)",
        )
    return action


__all__ = ["ACTIONS", "ACTION_DRAFT", "ACTION_REFRESH", "SETTLE_STORED", "NotBuilt", "settle_stored"]

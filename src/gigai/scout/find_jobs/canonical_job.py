"""0.1.11.8 N2: THE rule that names the one canonical job of several copies of the same job.

The same job posted once per country is ONE row in the lists (``job_copies``),
and that row stands for ONE posting: its canonical job. Assess, Mark applied
and the job page act on it; the other copies only add their locations.

:func:`pick_canonical` is the whole rule, and the only place it is written:

1. a copy with a US or US-remote location (``job_copies.PLACE_US``: the
   location clearly names the US) before any other;
2. among those (or among all, when none is in the US) the EARLIEST posted;
   a copy with no posted date after every dated one;
3. a tie by the posting id (as text).

So the choice depends only on the copies themselves, never on the order they
were read in, the list they are shown in or what was assessed. Pure: no I/O.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

_M = TypeVar("_M")


def _attribute(name: str) -> Callable[[object], object]:
    return lambda member: getattr(member, name)


def canonical_order(
    members: Sequence[_M],
    *,
    us: Callable[[_M], bool] | None = None,
    posted: Callable[[_M], str | None] | None = None,
    posting_id: Callable[[_M], str] | None = None,
) -> list[_M]:
    """``members`` with the canonical one first, then the others by the same rule (US first, earliest, posting id)."""

    is_us = us or _attribute("us")
    when = posted or _attribute("posted")
    ident = posting_id or _attribute("posting_id")

    def key(member: _M) -> tuple[int, int, str, str]:
        at = when(member) or ""
        return (0 if is_us(member) else 1, 0 if at else 1, str(at), str(ident(member)))

    return sorted(members, key=key)


def pick_canonical(
    members: Sequence[_M],
    *,
    us: Callable[[_M], bool] | None = None,
    posted: Callable[[_M], str | None] | None = None,
    posting_id: Callable[[_M], str] | None = None,
) -> _M:
    """The canonical job of ``members`` (the copies of one job; at least one). See the module docstring.

    ``us(member)``: whether the copy's location is clearly in the US.
    ``posted(member)``: its posted instant as a string that sorts by time (a
    UTC stamp), or ``None`` / ``""`` when unknown. ``posting_id(member)``: the
    board's id of the posting. Each defaults to the attribute of that name.
    Raises ``ValueError`` for no members.
    """

    if not members:
        raise ValueError("a job has at least one posting")
    return canonical_order(members, us=us, posted=posted, posting_id=posting_id)[0]


__all__ = ["canonical_order", "pick_canonical"]

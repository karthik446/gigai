"""0110-025 (R1): which boards one background refresh tick asks.

The sources-refresh spike measured that changes are concentrated: a board
with 20 or more live postings, or one that changed at its previous check,
is far more likely to change again. So a tick (one an hour) asks

* every **busy** board: 20+ live postings in the company index, or the
  posting set changed at the board's previous check;
* one **slice** of the **quiet** boards (everything else, including a board
  not in the index yet): the least recently checked sixth, so every quiet
  board is asked once per six ticks.

Pure: no filesystem, no clock, no request. The caller hands in what the
company index says about each board (:class:`BoardFacts`), the rotation
stamps (``last-fetched.json``: when each board was last *attempted*) and
``now``. Nothing new is stored: a quiet board is due again when its stamp
is one quiet period old, and the slice is the oldest stamps first, so a
tick that was missed (Scout closed) just leaves more boards due and the
next ticks catch up one slice at a time.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
import math

#: A board with at least this many live postings is busy.
BUSY_LIVE_POSTINGS = 20
#: Seconds between ticks.
TICK_INTERVAL_SECONDS = 3600.0
#: How many ticks it takes to ask every quiet board once (6 ticks = 6 hours).
QUIET_SLICES = 6

TIER_BUSY = "busy"
TIER_QUIET = "quiet"


@dataclass(frozen=True)
class BoardFacts:
    """What the company index knows about one board (``None`` stamps: never checked / never changed)."""

    live: int = 0
    checked_at: str | None = None
    changed_at: str | None = None

    @property
    def changed_at_previous_check(self) -> bool:
        """The posting set changed at the last check that reached the index.

        ``observe_company`` stamps ``changed_at`` and ``checked_at`` with the
        same value when a check finds a change, and moves only
        ``checked_at`` when it finds none.
        """

        return self.changed_at is not None and self.changed_at == self.checked_at


def tier(facts: BoardFacts | None, *, busy_live_postings: int = BUSY_LIVE_POSTINGS) -> str:
    """``busy`` or ``quiet``; a board with no index entry is quiet (nothing says it changes)."""

    if facts is None:
        return TIER_QUIET
    if facts.live >= busy_live_postings or facts.changed_at_previous_check:
        return TIER_BUSY
    return TIER_QUIET


@dataclass(frozen=True)
class RefreshPlan:
    """One tick's boards, as ``"<provider>:<board token>"`` keys in the order given."""

    busy: tuple[str, ...]
    quiet_slice: tuple[str, ...]
    quiet_total: int
    quiet_due: int  # quiet boards whose last check is a quiet period old (or missing)
    slice_size: int
    slices: int = QUIET_SLICES

    @property
    def keys(self) -> tuple[str, ...]:
        return self.busy + self.quiet_slice

    @property
    def total(self) -> int:
        return len(self.busy) + len(self.quiet_slice)

    def to_json(self) -> dict[str, object]:
        return {
            "boards": self.total,
            "busy": len(self.busy),
            "quiet": len(self.quiet_slice),
            "quiet_total": self.quiet_total,
            "quiet_due": self.quiet_due,
            "slice_size": self.slice_size,
            "slices": self.slices,
        }


def _parse(value: str | None) -> datetime | None:
    if type(value) is not str or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def plan_tick(
    keys: Sequence[str],
    *,
    facts: Mapping[str, BoardFacts],
    stamps: Mapping[str, str],
    now: datetime,
    busy_live_postings: int = BUSY_LIVE_POSTINGS,
    slices: int = QUIET_SLICES,
    tick_interval_seconds: float = TICK_INTERVAL_SECONDS,
) -> RefreshPlan:
    """The boards the tick at ``now`` asks, out of the watchlist ``keys``.

    Busy boards are asked every tick. A quiet board is *due* when it has no
    stamp or its stamp is a quiet period old (``slices`` ticks, less half a
    tick so a tick that fires a little early still finds its slice); the
    tick takes the ``ceil(quiet / slices)`` due boards with the oldest
    stamps (never-checked first, ties by key). Within one cycle no quiet
    board is taken twice: once asked it is not due again for a full period.
    """

    slices = max(1, int(slices))
    period = timedelta(seconds=tick_interval_seconds * slices)
    slack = timedelta(seconds=tick_interval_seconds / 2)
    busy: list[str] = []
    quiet: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        (busy if tier(facts.get(key), busy_live_postings=busy_live_postings) == TIER_BUSY else quiet).append(key)

    due: list[tuple[int, datetime | None, str]] = []
    for key in quiet:
        checked = _parse(stamps.get(key))
        if checked is None:
            due.append((0, None, key))
        elif now - checked >= period - slack:
            due.append((1, checked, key))
    due.sort(key=lambda item: (item[0], item[1].timestamp() if item[1] is not None else 0.0, item[2]))
    slice_size = math.ceil(len(quiet) / slices) if quiet else 0
    return RefreshPlan(
        busy=tuple(busy),
        quiet_slice=tuple(key for _rank, _at, key in due[:slice_size]),
        quiet_total=len(quiet),
        quiet_due=len(due),
        slice_size=slice_size,
        slices=slices,
    )


__all__ = [
    "BUSY_LIVE_POSTINGS",
    "QUIET_SLICES",
    "TICK_INTERVAL_SECONDS",
    "TIER_BUSY",
    "TIER_QUIET",
    "BoardFacts",
    "RefreshPlan",
    "plan_tick",
    "tier",
]

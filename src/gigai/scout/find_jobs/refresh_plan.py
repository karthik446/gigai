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
from datetime import date, datetime, time, timedelta, tzinfo
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


# ---------------------------------------------------------------------------
# 0110-029: when the background checks run (additive: ``plan_tick`` above is unchanged)
# ---------------------------------------------------------------------------

#: The default check times, in the machine's local time. Weekdays: eight a
#: day, one overnight and seven through the work day (who updates a board at
#: night?). Weekends: two.
DEFAULT_WEEKDAY_TIMES = ("03:00", "07:00", "09:00", "11:00", "13:00", "15:00", "17:00", "19:00")
DEFAULT_WEEKEND_TIMES = ("09:00", "18:00")
#: A quiet board is asked about twice a day: it is due again this long after
#: its last check, and one check takes a quarter of the quiet boards (eight
#: weekday checks ask each one twice; nothing is asked in a burst).
CHECK_QUIET_PERIOD_SECONDS = 12 * 3600.0
CHECK_QUIET_SLICES = 4
#: An update (manual or a check) that started this close before a scheduled
#: time counts as that time's check: no second pass over the busy boards.
CHECK_MIN_GAP_SECONDS = 45 * 60.0
#: How many times one day's list may hold.
MAX_CHECK_TIMES = 24


def parse_check_times(values: object) -> tuple[str, ...]:
    """``["07:00", "9:30"]`` -> ``("07:00", "09:30")``: 24-hour ``HH:MM``, sorted, no repeats.

    Raises ``ValueError`` for anything else (not a list, a bad time, more
    than :data:`MAX_CHECK_TIMES`). An empty list is allowed: no check that day.
    """

    if not isinstance(values, (list, tuple)) or len(values) > MAX_CHECK_TIMES:
        raise ValueError("check times must be a list of at most 24 HH:MM times")
    minutes: set[int] = set()
    for value in values:
        if type(value) is not str:
            raise ValueError("a check time must be an HH:MM string")
        hour, sep, minute = value.strip().partition(":")
        if not sep or not hour.isdigit() or not minute.isdigit() or len(minute) != 2 or len(hour) > 2:
            raise ValueError(f"not an HH:MM time: {value!r}")
        if int(hour) > 23 or int(minute) > 59:
            raise ValueError(f"not an HH:MM time: {value!r}")
        minutes.add(int(hour) * 60 + int(minute))
    return tuple(f"{value // 60:02d}:{value % 60:02d}" for value in sorted(minutes))


def _local(moment: datetime, tz: tzinfo | None) -> datetime:
    """``moment`` on the machine's wall clock (``tz`` for tests; ``None``: the system's local time)."""

    return moment.astimezone(tz) if tz is not None else moment.astimezone()


def _at(day: date, text: str, tz: tzinfo | None) -> datetime:
    hour, _, minute = text.partition(":")
    naive = datetime.combine(day, time(int(hour), int(minute)))
    # A naive value is read as the system's local time, so a daylight-saving change between two days is honoured.
    return naive.replace(tzinfo=tz) if tz is not None else naive.astimezone()


@dataclass(frozen=True)
class CheckSchedule:
    """When the background checks run: times of day (weekdays, weekends), or every ``interval_seconds``.

    The fixed interval is the 0110-025 behaviour (a check one interval after
    the last update started); it is what a caller that names an interval
    gets. Without one, the times decide.
    """

    weekdays: tuple[str, ...] = DEFAULT_WEEKDAY_TIMES
    weekends: tuple[str, ...] = DEFAULT_WEEKEND_TIMES
    interval_seconds: float | None = None

    def times_on(self, day: date) -> tuple[str, ...]:
        return self.weekends if day.weekday() >= 5 else self.weekdays

    def _slots(self, around: datetime, tz: tzinfo | None, days: range) -> list[datetime]:
        local = _local(around, tz)
        slots: list[datetime] = []
        for offset in days:
            day = local.date() + timedelta(days=offset)
            slots.extend(_at(day, text, tz) for text in self.times_on(day))
        return sorted(slots)

    def latest_slot(self, now: datetime, *, tz: tzinfo | None = None) -> datetime | None:
        """The most recent scheduled time at or before ``now`` (looking back a week), or ``None``."""

        past = [slot for slot in self._slots(now, tz, range(-7, 1)) if slot <= now]
        return past[-1] if past else None

    def next_slot(self, now: datetime, *, tz: tzinfo | None = None) -> datetime | None:
        """The first scheduled time after ``now`` (looking ahead a week), or ``None``."""

        for slot in self._slots(now, tz, range(0, 8)):
            if slot > now:
                return slot
        return None

    def due(self, last_started: datetime, now: datetime, *, tz: tzinfo | None = None) -> bool:
        """Whether a check should run at ``now``, the last update (manual or a check) having started at ``last_started``.

        Times: when a scheduled time has passed that the last update did not
        cover (it started more than :data:`CHECK_MIN_GAP_SECONDS` before
        it). Only the most recent time counts, so a machine that was closed
        over several of them runs ONE check when it comes back, never a pile.
        """

        if self.interval_seconds is not None:
            return now >= last_started + timedelta(seconds=self.interval_seconds)
        slot = self.latest_slot(now, tz=tz)
        return slot is not None and last_started < slot - timedelta(seconds=CHECK_MIN_GAP_SECONDS)

    def next_after(self, last_started: datetime, now: datetime, *, tz: tzinfo | None = None) -> datetime | None:
        """When the next check is due, given that none is due at ``now``."""

        if self.interval_seconds is not None:
            return last_started + timedelta(seconds=self.interval_seconds)
        moment = now
        for _ in range(MAX_CHECK_TIMES * 8):
            slot = self.next_slot(moment, tz=tz)
            if slot is None:
                return None
            if last_started < slot - timedelta(seconds=CHECK_MIN_GAP_SECONDS):
                return slot
            moment = slot  # the update that just ran covers this time: the one after it
        return None

    def checks_on(self, day: date) -> int:
        return len(self.times_on(day))

    def to_json(self) -> dict[str, object]:
        if self.interval_seconds is not None:
            return {"kind": "interval", "interval_seconds": self.interval_seconds, "weekdays": None, "weekends": None}
        return {"kind": "times", "interval_seconds": None, "weekdays": list(self.weekdays), "weekends": list(self.weekends)}


__all__ = [
    "BUSY_LIVE_POSTINGS",
    "CHECK_MIN_GAP_SECONDS",
    "CHECK_QUIET_PERIOD_SECONDS",
    "CHECK_QUIET_SLICES",
    "DEFAULT_WEEKDAY_TIMES",
    "DEFAULT_WEEKEND_TIMES",
    "MAX_CHECK_TIMES",
    "QUIET_SLICES",
    "TICK_INTERVAL_SECONDS",
    "TIER_BUSY",
    "TIER_QUIET",
    "BoardFacts",
    "CheckSchedule",
    "RefreshPlan",
    "parse_check_times",
    "plan_tick",
    "tier",
]

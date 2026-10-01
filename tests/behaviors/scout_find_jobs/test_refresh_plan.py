"""0110-025 R1: the tier rule and the slice planner (``refresh_plan``), over a synthetic index.

Pure: board facts, rotation stamps and ``now`` go in, a plan comes out. A
"tick" in these tests stamps the boards the plan picked with the tick's
time, which is what the board fetch does to ``last-fetched.json``.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from gigai.scout.find_jobs.company_index import index_stamp
from gigai.scout.find_jobs.refresh_plan import (
    BUSY_LIVE_POSTINGS,
    QUIET_SLICES,
    TIER_BUSY,
    TIER_QUIET,
    BoardFacts,
    plan_tick,
    tier,
)

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
CHECKED = "2026-10-01T05:00:00.000Z"
EARLIER = "2026-09-30T05:00:00.000Z"


def _synthetic() -> tuple[list[str], dict[str, BoardFacts], set[str], set[str]]:
    """103 boards: 20 big, 10 small-but-just-changed (busy); 70 small unchanged and 3 not indexed (quiet)."""

    facts: dict[str, BoardFacts] = {}
    for n in range(20):
        facts[f"greenhouse:big{n:02d}"] = BoardFacts(live=BUSY_LIVE_POSTINGS + n, checked_at=CHECKED, changed_at=EARLIER)
    for n in range(10):
        facts[f"lever:moved{n:02d}"] = BoardFacts(live=3, checked_at=CHECKED, changed_at=CHECKED)
    for n in range(70):
        facts[f"ashby:small{n:02d}"] = BoardFacts(live=n % BUSY_LIVE_POSTINGS, checked_at=CHECKED, changed_at=EARLIER if n % 2 else None)
    unindexed = [f"lever:new{n}" for n in range(3)]
    keys = sorted(facts) + unindexed
    busy = {key for key in facts if key.startswith(("greenhouse:big", "lever:moved"))}
    return keys, facts, busy, set(keys) - busy


def _run_ticks(keys, facts, stamps, *, first: int, count: int) -> list:
    plans = []
    for n in range(first, first + count):
        now = T0 + timedelta(hours=n)
        plan = plan_tick(keys, facts=facts, stamps=stamps, now=now)
        for key in plan.keys:
            stamps[key] = index_stamp(now)
        plans.append(plan)
    return plans


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (BoardFacts(live=20, checked_at=CHECKED, changed_at=None), TIER_BUSY),
        (BoardFacts(live=19, checked_at=CHECKED, changed_at=None), TIER_QUIET),
        (BoardFacts(live=0, checked_at=CHECKED, changed_at=CHECKED), TIER_BUSY),
        # It changed once, but the previous check found nothing new.
        (BoardFacts(live=5, checked_at=CHECKED, changed_at=EARLIER), TIER_QUIET),
        (BoardFacts(live=0, checked_at=None, changed_at=None), TIER_QUIET),
        (None, TIER_QUIET),
    ],
)
def test_busy_is_twenty_live_postings_or_changed_at_the_previous_check(facts: BoardFacts | None, expected: str) -> None:
    assert tier(facts) == expected


@pytest.mark.parametrize("start", ["never_checked", "all_checked_by_one_update"])
def test_six_ticks_ask_every_quiet_board_exactly_once_and_busy_boards_every_tick(start: str) -> None:
    keys, facts, busy, quiet = _synthetic()
    stamps = {} if start == "never_checked" else {key: index_stamp(T0 - timedelta(hours=7)) for key in keys}

    plans = _run_ticks(keys, facts, stamps, first=0, count=QUIET_SLICES)

    for plan in plans:
        assert set(plan.busy) == busy
        assert plan.quiet_total == len(quiet) == 73
        assert plan.slice_size == 13  # ceil(73 / 6)
        assert len(plan.quiet_slice) <= plan.slice_size
        assert not set(plan.quiet_slice) & busy
    asked = Counter(key for plan in plans for key in plan.quiet_slice)
    assert set(asked) == quiet
    assert set(asked.values()) == {1}
    assert [len(plan.quiet_slice) for plan in plans] == [13, 13, 13, 13, 13, 8]

    # The next six ticks are the next cycle: every quiet board once more,
    # in the same order (the least recently checked slice leads).
    again = _run_ticks(keys, facts, stamps, first=QUIET_SLICES, count=QUIET_SLICES)
    assert all(set(plan.busy) == busy for plan in again)
    assert Counter(key for plan in again for key in plan.quiet_slice) == asked
    assert [plan.quiet_slice for plan in again] == [plan.quiet_slice for plan in plans]


def test_never_checked_boards_lead_the_slice_then_the_oldest_stamps() -> None:
    keys = [f"lever:q{n}" for n in range(12)]
    facts = {key: BoardFacts(live=1, checked_at=CHECKED) for key in keys}
    stamps = {key: index_stamp(T0 - timedelta(hours=8 + n)) for n, key in enumerate(keys)}
    del stamps["lever:q3"]

    plan = plan_tick(keys, facts=facts, stamps=stamps, now=T0)

    assert plan.busy == ()
    assert plan.quiet_slice == ("lever:q3", "lever:q11")
    assert plan.to_json() == {"boards": 2, "busy": 0, "quiet": 2, "quiet_total": 12, "quiet_due": 12, "slice_size": 2, "slices": 6}


def test_a_quiet_board_checked_within_the_period_is_not_due() -> None:
    keys = [f"lever:q{n}" for n in range(6)]
    facts = {key: BoardFacts(live=1, checked_at=CHECKED) for key in keys}
    stamps = {key: index_stamp(T0 - timedelta(hours=1)) for key in keys}

    fresh = plan_tick(keys, facts=facts, stamps=stamps, now=T0)
    assert fresh.quiet_slice == () and fresh.quiet_due == 0 and fresh.total == 0

    # Six hours on (a tick may fire a little early: half a tick of slack).
    due = plan_tick(keys, facts=facts, stamps=stamps, now=T0 + timedelta(hours=4, minutes=45))
    assert due.quiet_due == 6 and due.quiet_slice == ("lever:q0",)


def test_missed_ticks_catch_up_one_slice_at_a_time_and_a_board_listed_twice_counts_once() -> None:
    keys, facts, busy, quiet = _synthetic()
    stamps = {key: index_stamp(T0 - timedelta(days=3)) for key in keys}

    plan = plan_tick(keys + keys[:5], facts=facts, stamps=stamps, now=T0)

    assert len(plan.busy) == len(busy) and plan.quiet_total == len(quiet)
    assert plan.quiet_due == len(quiet) and len(plan.quiet_slice) == plan.slice_size == 13
    assert len(set(plan.keys)) == plan.total == len(busy) + 13

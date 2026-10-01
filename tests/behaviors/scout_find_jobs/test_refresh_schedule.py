"""0110-029: the background checks run at times of day, at a moderate pace, and back off on 429.

Replaces the hourly tick spread over the whole hour (0110-025 R2/R3 pacing).
Time is a fake clock in a fixed zone (UTC-6), so a "weekday" and a "weekend"
are dates, not whatever today is. No network: board requests go to
``test_sources_update._Boards`` or to a transport that answers 429.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading

import httpx
import pytest

from gigai.scout.find_jobs import sources_update
from gigai.scout.find_jobs.company_index import index_stamp
from gigai.scout.find_jobs.refresh_plan import (
    CHECK_QUIET_SLICES,
    DEFAULT_WEEKDAY_TIMES,
    DEFAULT_WEEKEND_TIMES,
    CheckSchedule,
    parse_check_times,
)
from gigai.scout.find_jobs.refresh_tick import (
    AUTO_REFRESH_ENV,
    STATE_DUE,
    STATE_WAITING,
    RefreshTicker,
    background_status,
    check_schedule_setting,
    decide,
    schedule_status,
    settings_path,
)
from gigai.scout.find_jobs.sources_update import (
    CHECK_MIN_INTERVAL_SECONDS,
    STATUS_SUCCEEDED,
    _BackoffClients,
    read_status,
    run_refresh_tick,
    run_sources_update,
    tick_limits,
)

from tests.behaviors.scout_find_jobs.test_acquire_scale import _limits

from .test_refresh_core import _project
from .test_sources_update import _Boards

ZONE = timezone(timedelta(hours=-6))
THURSDAY = datetime(2026, 10, 1, 0, 0, tzinfo=ZONE)
SATURDAY = datetime(2026, 10, 3, 0, 0, tzinfo=ZONE)
FAST = _limits(concurrency=1)


def _snapshot(started: datetime) -> dict[str, object]:
    stamp = index_stamp(started)
    return {"status": "succeeded", "started_at": stamp, "finished_at": stamp, "updated_at": stamp}


def _checks_over(day: datetime, *, hours: int = 24, schedule: CheckSchedule | None = None) -> list[str]:
    """Walk ``day`` minute by minute as the thread would look; every time a check is due it "runs" (and becomes the last update)."""

    plan = schedule if schedule is not None else CheckSchedule()
    last = day - timedelta(hours=2)  # an update the evening before, already covered
    ran: list[str] = []
    for minute in range(hours * 60):
        now = day + timedelta(minutes=minute)
        decision = decide(_snapshot(last), indexed=True, enabled=True, now=now, schedule=plan, tz=ZONE)
        if decision.state == STATE_DUE:
            ran.append(now.strftime("%H:%M"))
            last = now
        else:
            assert decision.state == STATE_WAITING and decision.next_tick_at is not None and decision.next_tick_at > now
    return ran


# --- the schedule (pure) ---------------------------------------------------------


def test_a_weekday_has_eight_checks_at_the_default_times_and_a_weekend_day_two() -> None:
    assert _checks_over(THURSDAY) == list(DEFAULT_WEEKDAY_TIMES) == ["03:00", "07:00", "09:00", "11:00", "13:00", "15:00", "17:00", "19:00"]
    assert _checks_over(SATURDAY) == list(DEFAULT_WEEKEND_TIMES) == ["09:00", "18:00"]
    # Seven of the eight are in work hours; nothing is asked between 19:00 and 03:00.
    assert sum("07:00" <= text <= "19:00" for text in DEFAULT_WEEKDAY_TIMES) == 7


def test_a_closed_night_is_one_catch_up_check_never_a_pile() -> None:
    schedule = CheckSchedule()
    last = THURSDAY + timedelta(hours=19)  # Thursday's last check; Scout is closed until Friday 09:10
    back = THURSDAY + timedelta(days=1, hours=9, minutes=10)

    assert schedule.due(last, back, tz=ZONE)  # 03:00, 07:00 and 09:00 were all missed: one check now
    caught_up = decide(_snapshot(back), indexed=True, enabled=True, now=back + timedelta(minutes=5), schedule=schedule, tz=ZONE)
    assert caught_up.state == STATE_WAITING
    assert caught_up.next_tick_at == THURSDAY + timedelta(days=1, hours=11)  # then the next scheduled time, as usual

    # Closed over a whole weekend: still one check on Monday morning.
    monday = THURSDAY + timedelta(days=4, hours=8)
    assert schedule.due(last, monday, tz=ZONE)
    assert not schedule.due(monday, monday + timedelta(minutes=30), tz=ZONE)


def test_an_update_shortly_before_a_scheduled_time_counts_as_that_check() -> None:
    schedule = CheckSchedule()
    manual = THURSDAY + timedelta(hours=10, minutes=40)  # the operator's Update sources, 20 minutes before 11:00

    at_eleven = decide(_snapshot(manual), indexed=True, enabled=True, now=THURSDAY + timedelta(hours=11, minutes=1), schedule=schedule, tz=ZONE)

    assert at_eleven.state == STATE_WAITING and at_eleven.next_tick_at == THURSDAY + timedelta(hours=13)
    # An update two hours before it does not.
    assert schedule.due(THURSDAY + timedelta(hours=9), THURSDAY + timedelta(hours=11, minutes=1), tz=ZONE)


def test_check_times_are_validated_and_a_fixed_interval_still_works() -> None:
    assert parse_check_times(["9:30", "07:00", "09:30"]) == ("07:00", "09:30")
    assert parse_check_times([]) == ()
    for bad in (None, "07:00", ["25:00"], ["07:60"], ["7"], [7], ["07:00"] * 30, ["a:b"]):
        with pytest.raises(ValueError):
            parse_check_times(bad)

    hourly = CheckSchedule(interval_seconds=3600.0)
    assert not hourly.due(THURSDAY, THURSDAY + timedelta(minutes=59)) and hourly.due(THURSDAY, THURSDAY + timedelta(minutes=60))
    assert hourly.to_json() == {"kind": "interval", "interval_seconds": 3600.0, "weekdays": None, "weekends": None}
    assert CheckSchedule(weekdays=(), weekends=()).next_slot(THURSDAY, tz=ZONE) is None  # no times: no check, and no loop


# --- the setting -------------------------------------------------------------------


def test_the_schedule_is_a_setting_with_these_defaults(tmp_path: Path) -> None:
    home, target = _project(tmp_path)
    path = settings_path(home, target)

    default = check_schedule_setting(home, target)
    assert (default.schedule, default.source) == (CheckSchedule(), "default")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": "scout-settings:1", "sources": {"check_times": {"weekdays": ["08:00", "12:00", "16:00"]}}}), encoding="utf-8")
    stored = check_schedule_setting(home, target)
    assert stored.source == "setting" and stored.schedule.weekdays == ("08:00", "12:00", "16:00") and stored.schedule.weekends == DEFAULT_WEEKEND_TIMES
    assert _checks_over(THURSDAY, schedule=stored.schedule) == ["08:00", "12:00", "16:00"]

    status = schedule_status(home, target, now=THURSDAY + timedelta(hours=12))
    assert status["kind"] == "times" and status["weekdays"] == ["08:00", "12:00", "16:00"] and status["source"] == "setting"

    # Times that cannot be used are not guessed at: the defaults run and the status says so.
    path.write_text(json.dumps({"schema_version": "scout-settings:1", "sources": {"check_times": {"weekdays": ["noon"]}}}), encoding="utf-8")
    broken = check_schedule_setting(home, target)
    assert (broken.schedule, broken.source) == (CheckSchedule(), "settings_unreadable")


# --- the thread on a fake clock ------------------------------------------------------


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def test_the_thread_checks_at_the_scheduled_times_on_a_fake_clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(AUTO_REFRESH_ENV, raising=False)
    home, target = _project(tmp_path)
    boards = _Boards()
    clock = _Clock(THURSDAY + timedelta(hours=7, minutes=30))
    monkeypatch.setattr(sources_update, "index_stamp", lambda moment=None: index_stamp(moment if moment is not None else clock.now))
    with boards.client() as client:
        assert run_sources_update(home_root=home, target=target, client=client, limits=FAST).status == STATUS_SUCCEEDED  # the first update, 07:30

    def fast_tick(*args, **more):
        return run_refresh_tick(*args, limits=FAST, **more)

    ticker = RefreshTicker(home_root=home, target=target, client_factory=boards.client, clock=clock, run_tick=fast_tick, tz=ZONE, model_tags=False)

    clock.now = THURSDAY + timedelta(hours=8, minutes=59)
    assert ticker.step() == STATE_WAITING
    # No fixed interval any more; the times are in the `refresh` block's schedule.
    assert background_status(home, target, ticker=ticker, now=clock.now)["interval_seconds"] is None

    clock.now = THURSDAY + timedelta(hours=9)
    assert ticker.step() == "ticked"
    snapshot = read_status(home, now=clock.now)["update"]
    assert snapshot["trigger"] == "auto" and snapshot["tick"]["slices"] == CHECK_QUIET_SLICES and snapshot["backoff"] is None

    clock.now = THURSDAY + timedelta(hours=10, minutes=59)
    assert ticker.step() == STATE_WAITING
    clock.now = THURSDAY + timedelta(hours=11)
    assert ticker.step() == "ticked"
    # 19:00 was the day's last: nothing runs until 03:00.
    clock.now = THURSDAY + timedelta(hours=19)
    assert ticker.step() == "ticked"
    clock.now = THURSDAY + timedelta(hours=23, minutes=30)
    assert ticker.step() == STATE_WAITING
    assert schedule_status(home, target, ticker=ticker, now=clock.now)["checks_today"] == 8


# --- the pace ---------------------------------------------------------------------------


def test_a_check_of_3982_boards_is_done_within_fifteen_minutes_at_the_set_rate() -> None:
    """Each provider is asked at its own fixed rate, in parallel: the slowest provider decides how long a check takes."""

    from gigai.scout.find_jobs.company_catalog import load_company_catalog

    limits = tick_limits({})
    assert limits.spread_seconds is None and limits.min_request_interval_seconds == CHECK_MIN_INTERVAL_SECONDS
    assert 2.0 <= 1 / limits.min_request_interval_seconds <= 3.0  # 2-3 requests a second to one provider

    # 3,982 boards split between the providers as the bundled catalog is (the operator's check).
    shares = Counter(str(getattr(record.provider, "value", record.provider)) for record in load_company_catalog().records)
    total = sum(shares.values())
    per_provider = {provider: round(3982 * count / total) for provider, count in shares.items()}
    seconds = max(count * limits.interval_for(count) for count in per_provider.values())

    assert seconds <= 15 * 60, (per_provider, seconds)
    assert limits.time_budget_seconds is not None and seconds < limits.time_budget_seconds  # the budget never cuts such a check short


# --- 429: back off -------------------------------------------------------------------------


class _Refusing:
    def __init__(self, answers: list[str | None]) -> None:
        self.answers = answers
        self.asked: list[str] = []
        self.failure: str | None = None

    def fetch_board(self, client, provider, board_token, config, **kwargs):
        del client, config, kwargs
        self.asked.append(board_token)
        self.failure = self.answers.pop(0)
        if self.failure is not None:
            raise RuntimeError("http_error")
        return "ok"

    def last_failure(self) -> str | None:
        return self.failure


def test_a_provider_that_answers_429_is_left_alone_longer_each_time_and_an_answer_ends_it() -> None:
    now = [0.0]
    waits: list[float] = []

    def wait(seconds: float) -> None:
        waits.append(round(seconds, 3))
        now[0] += seconds

    inner = _Refusing(["http_429", "http_429", None, "http_404", "http_429"])
    clients = _BackoffClients(inner, None, clock=lambda: now[0], wait=wait)

    def ask(token: str) -> None:
        try:
            clients.fetch_board(inner, "greenhouse", token, None)
        except RuntimeError:
            pass

    ask("a")  # 429: greenhouse is paused 30 s
    ask("b")  # waits the 30 s, is refused again: 60 s
    ask("c")  # waits the 60 s, answers: the streak is over
    ask("d")  # a dead board (404) is not a rate limit: no wait before it, no pause after
    ask("e")  # 429 again: back to 30 s
    inner.answers.append(None)
    assert clients.fetch_board(inner, "lever", "f", None) == "ok"  # another provider is never held

    assert waits == [30.0, 60.0]
    assert clients.to_json() == {"greenhouse": {"pauses": 3, "paused_seconds": 120.0}}
    assert inner.asked == ["a", "b", "c", "d", "e", "f"]

    # A stop during a pause leaves the board unasked (it leads the next check).
    stop = threading.Event()
    held = _BackoffClients(_Refusing(["http_429", None]), stop, clock=lambda: 0.0, wait=lambda _seconds: stop.set())
    with pytest.raises(RuntimeError):
        held.fetch_board(held._inner, "greenhouse", "a", None)
    with pytest.raises(sources_update._PassCancelled):
        held.fetch_board(held._inner, "greenhouse", "b", None)
    assert held._inner.asked == ["a"]


def test_a_background_check_backs_off_on_429_and_a_manual_update_does_not(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = _project(tmp_path)
    boards = _Boards()
    with boards.client() as client:
        assert run_sources_update(home_root=home, target=target, client=client, limits=FAST).status == STATUS_SUCCEEDED
    monkeypatch.setattr(sources_update, "CHECK_BACKOFF_SECONDS", 0.01)

    def refusing(request: httpx.Request) -> httpx.Response:
        if request.url.host == "boards-api.greenhouse.io":
            return httpx.Response(429, json={"error": "slow down"})
        return boards.handler(request)

    with httpx.Client(transport=httpx.MockTransport(refusing)) as client:
        # Thirteen hours on, every board is due (quiet boards about twice a day).
        check = run_refresh_tick(home, target, client=client, now=datetime.now(timezone.utc) + timedelta(hours=13), limits=FAST).to_json()
        manual = run_sources_update(home_root=home, target=target, client=client, limits=FAST, full_refresh=True).to_json()

    assert check["trigger"] == "auto" and check["failures"]["codes"].get("http_429", 0) >= 1
    assert check["backoff"] is not None and check["backoff"]["greenhouse"]["pauses"] >= 1 and "lever" not in check["backoff"]
    assert manual["trigger"] == "manual" and manual["failures"]["codes"]["http_429"] == 2 and manual["backoff"] is None

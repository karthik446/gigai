"""0110-025 R5 + R2 + the tick entry point: failure codes, spread pacing, cancel, ``run_refresh_tick``.

No network: an ``httpx.MockTransport`` or a fake board client on a fake
clock. The planner's own rules are in ``test_refresh_plan.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from gigai.scout.find_jobs import market_acquisition, sources_update
from gigai.scout.find_jobs.ats_board_clients import BoardFetchResult, BoardFetchStats
from gigai.scout.find_jobs.company_index import CompanyIndex, board_list_url, index_stamp
from gigai.scout.find_jobs.contracts import ATSProvider
from gigai.scout.find_jobs.market_acquisition import AcquireLimits, _PassCancelled, _RateLimiter
from gigai.scout.find_jobs.sources_update import (
    STATUS_PARTIAL,
    STATUS_SUCCEEDED,
    SourcesUpdateRunningError,
    board_cache_for_home,
    read_status,
    run_refresh_tick,
    run_sources_update,
    tick_limits,
    update_sources,
)

from tests.behaviors.scout_find_jobs.test_acquire_scale import _board, _config, _limits

from .test_sources_update import _add, _Boards, _installed, _update, _write_running


# --- R5: per-board failure codes ------------------------------------------------


def _lever_jobs(token: str) -> list[dict]:
    return [{"id": f"{token}-1", "text": "Software Engineer", "hostedUrl": f"https://jobs.lever.co/{token}/1", "descriptionPlain": "Build."}]


def _pushback(request: httpx.Request) -> httpx.Response:
    token = request.url.path.strip("/").split("/")[-1]
    if token.startswith("limited"):
        return httpx.Response(429, headers={"retry-after": "30"}, json={"error": "slow down"})
    if token == "gone":
        return httpx.Response(404, json={"error": "no such board"})
    if token == "broken":
        return httpx.Response(503)
    if token == "slow":
        raise httpx.ReadTimeout("timed out", request=request)
    if token == "unreachable":
        raise httpx.ConnectError("refused", request=request)
    if token == "garbled":
        return httpx.Response(200, content=b"<html>not json</html>")
    return httpx.Response(200, json=_lever_jobs(token))


def test_the_snapshot_counts_failed_boards_by_code(tmp_path: Path) -> None:
    tokens = ["fine", "limited1", "limited2", "limited3", "gone", "broken", "slow", "unreachable", "garbled"]
    watchlist = [_board(ATSProvider.LEVER, token, catalog=True) for token in tokens]

    with httpx.Client(transport=httpx.MockTransport(_pushback)) as client:
        result = update_sources(
            watchlist,
            cache=board_cache_for_home(tmp_path),
            index=CompanyIndex.for_home(tmp_path),
            client=client,
            config=_config(),
            limits=_limits(concurrency=2),
        )

    snapshot = result.to_json()
    assert result.status == STATUS_SUCCEEDED
    assert snapshot["boards"]["failed"] == 8 and snapshot["boards"]["fetched"] == 1
    assert snapshot["failures"]["total"] == 8
    assert snapshot["failures"]["codes"] == {
        "bad_json": 1,
        "connect_error": 1,
        "http_404": 1,
        "http_429": 3,
        "http_503": 1,
        "timeout": 1,
    }
    assert sorted((row["board"], row["code"]) for row in snapshot["failures"]["boards"]) == [
        ("lever:broken", "http_503"),
        ("lever:garbled", "bad_json"),
        ("lever:gone", "http_404"),
        ("lever:limited1", "http_429"),
        ("lever:limited2", "http_429"),
        ("lever:limited3", "http_429"),
        ("lever:slow", "timeout"),
        ("lever:unreachable", "connect_error"),
    ]
    # The stored snapshot (what the API and the CLI read) carries the same block.
    assert read_status(tmp_path)["update"]["failures"] == snapshot["failures"]


def test_an_update_with_no_failure_has_an_empty_histogram_and_manual_defaults(tmp_path: Path) -> None:
    snapshot = _update(tmp_path, _Boards(), watchlist=[_board(ATSProvider.GREENHOUSE, "acme")]).to_json()

    assert snapshot["failures"] == {"total": 0, "codes": {}, "boards": []}
    assert snapshot["trigger"] == "manual" and snapshot["tick"] is None and snapshot["cancelled"] is False
    assert "spread_seconds" not in snapshot["limits"]


def test_the_failed_board_list_is_capped_but_the_histogram_counts_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sources_update, "FAILED_BOARDS_LISTED", 2)
    watchlist = [_board(ATSProvider.LEVER, f"limited{n}", catalog=True) for n in range(5)] + [_board(ATSProvider.LEVER, "fine", catalog=True)]

    with httpx.Client(transport=httpx.MockTransport(_pushback)) as client:
        snapshot = update_sources(
            watchlist, cache=board_cache_for_home(tmp_path), index=CompanyIndex.for_home(tmp_path), client=client, config=_config(), limits=_limits(concurrency=1)
        ).to_json()

    assert snapshot["failures"]["codes"] == {"http_429": 5} and snapshot["failures"]["total"] == 5
    assert len(snapshot["failures"]["boards"]) == 2


def test_the_failed_board_list_stops_at_50_and_the_histogram_counts_all_60(tmp_path: Path) -> None:
    """0110-10-hf2: the real limit, with more failed boards than it (the test above lowers the limit to 2 instead)."""

    assert sources_update.FAILED_BOARDS_LISTED == 50
    watchlist = [_board(ATSProvider.LEVER, f"limited{n:02d}", catalog=True) for n in range(60)] + [_board(ATSProvider.LEVER, "fine", catalog=True)]

    with httpx.Client(transport=httpx.MockTransport(_pushback)) as client:
        snapshot = update_sources(
            watchlist, cache=board_cache_for_home(tmp_path), index=CompanyIndex.for_home(tmp_path), client=client, config=_config(), limits=_limits(concurrency=1)
        ).to_json()

    assert snapshot["status"] == "succeeded" and snapshot["boards"]["failed"] == 60 and snapshot["boards"]["checked"] == 61
    assert snapshot["failures"]["codes"] == {"http_429": 60} and snapshot["failures"]["total"] == 60
    listed = snapshot["failures"]["boards"]
    assert len(listed) == 50 and len({item["board"] for item in listed}) == 50 and {item["code"] for item in listed} == {"http_429"}
    assert read_status(tmp_path)["update"]["failures"] == snapshot["failures"]  # the stored snapshot holds the same 50


# --- R2: spread pacing (fake clock) -----------------------------------------------


class _Clock:
    """``time`` for ``market_acquisition``: ``monotonic`` moves only when something sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.lock = threading.Lock()
        self.on_sleep = None

    def monotonic(self) -> float:
        with self.lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self.lock:
            self.now += seconds
        if self.on_sleep is not None:
            self.on_sleep()


class _RequestingATS:
    """One board = one request through the paced client; leaves a Lever body in the cache."""

    def __init__(self, *, stop: threading.Event | None = None, stop_after: int | None = None) -> None:
        self.calls: list[str] = []
        self.stop = stop
        self.stop_after = stop_after

    def fetch_board(self, client, provider, board_token, config, *, cache=None):
        self.calls.append(board_token)
        if client is not None:
            client.get(f"https://api.example/{provider}/{board_token}")
        cache.store(provider, board_list_url(provider, board_token), body=json.dumps(_lever_jobs(board_token)).encode(), etag=None, last_modified=None, marker=None)
        if self.stop is not None and len(self.calls) == self.stop_after:
            self.stop.set()
        return BoardFetchResult((), BoardFetchStats(requests=1, cache="miss", listed=1))


class _StartsClient:
    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.starts: list[float] = []

    def get(self, url, *args, **kwargs):
        self.starts.append(self.clock.monotonic())
        return SimpleNamespace(status_code=200, content=b"[]", headers={})


@pytest.mark.parametrize("with_stop_event", [False, True])
def test_a_spread_pass_starts_its_requests_evenly_over_the_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_stop_event: bool) -> None:
    clock = _Clock()
    monkeypatch.setattr(market_acquisition, "time", clock)
    watchlist = [_board(ATSProvider.LEVER, f"b{n:02d}", catalog=True) for n in range(12)]
    client = _StartsClient(clock)
    limits = AcquireLimits(concurrency_per_provider=1, min_request_interval_seconds=0.125, time_budget_seconds=None, spread_seconds=60.0)

    result = update_sources(
        watchlist,
        cache=board_cache_for_home(tmp_path),
        index=CompanyIndex.for_home(tmp_path),
        client=client,
        ats=_RequestingATS(),
        config=_config(),
        limits=limits,
        stop=threading.Event() if with_stop_event else None,
    )

    assert result.status == STATUS_SUCCEEDED
    # 12 boards over 60 s: one request start every 5 s, the first at once.
    # Never a burst: the polite 0.125 s interval would have finished in 1.4 s.
    assert client.starts == pytest.approx([5.0 * n for n in range(12)])
    assert result.to_json()["limits"]["spread_seconds"] == 60.0
    assert result.to_json()["cancelled"] is False


def test_the_interval_comes_from_each_providers_board_count_and_never_beats_the_polite_minimum() -> None:
    spread = AcquireLimits(min_request_interval_seconds=0.125, spread_seconds=3000.0)

    # The spike's tick: Greenhouse ~2,500 boards, Lever ~1,000, Ashby ~1,400.
    assert spread.interval_for(2500) == pytest.approx(1.2)
    assert spread.interval_for(1000) == pytest.approx(3.0)
    assert spread.interval_for(1) == pytest.approx(3000.0)
    # More boards than the window fits at the polite rate: the floor holds.
    assert spread.interval_for(100_000) == 0.125
    assert spread.interval_for(0) == 0.125
    # No spread (every manual update and find-jobs run): the fixed interval.
    assert AcquireLimits(min_request_interval_seconds=0.125).interval_for(2500) == 0.125
    assert "spread_seconds" not in AcquireLimits().to_json()


def test_check_limits_are_a_moderate_fixed_rate_and_the_env_can_spread_or_unpace_them() -> None:
    # 0110-029: a background check is no longer spread over the hour. 2.5 requests a second to each provider.
    default = tick_limits({})
    assert default.spread_seconds is None
    assert default.min_request_interval_seconds == sources_update.CHECK_MIN_INTERVAL_SECONDS == 0.4
    assert default.time_budget_seconds == sources_update.CHECK_BUDGET_SECONDS and default.concurrency_per_provider == 4

    unpaced = tick_limits({"GIGAI_SCOUT_REFRESH_SPREAD_SECONDS": "0"})  # the polite maximum, as a manual update
    assert unpaced.spread_seconds is None and unpaced.min_request_interval_seconds == 0.125
    assert tick_limits({"GIGAI_SCOUT_REFRESH_SPREAD_SECONDS": "120"}).spread_seconds == 120.0
    assert tick_limits({"GIGAI_SCOUT_REFRESH_SPREAD_SECONDS": "garbage"}) == default
    # An interval the environment names is used as it is (tests, an operator who wants another pace).
    assert tick_limits({"GIGAI_SCOUT_ATS_MIN_INTERVAL_SECONDS": "0"}).min_request_interval_seconds == 0.0
    assert tick_limits({"GIGAI_SCOUT_ATS_MIN_INTERVAL_SECONDS": "1.5"}).min_request_interval_seconds == 1.5


# --- R2: stop / cancel -------------------------------------------------------------


def test_a_stop_between_boards_leaves_a_clean_partial_snapshot_and_the_next_update_continues(tmp_path: Path) -> None:
    watchlist = [_board(ATSProvider.LEVER, f"b{n}", catalog=True) for n in range(8)]
    cache, index = board_cache_for_home(tmp_path), CompanyIndex.for_home(tmp_path)
    stop = threading.Event()
    ats = _RequestingATS(stop=stop, stop_after=3)

    result = update_sources(watchlist, cache=cache, index=index, client=None, ats=ats, config=_config(), limits=_limits(concurrency=1), stop=stop)

    assert ats.calls == ["b0", "b1", "b2"]
    snapshot = result.to_json()
    assert result.status == STATUS_PARTIAL
    assert snapshot["cancelled"] is True and snapshot["error"] is None
    assert snapshot["finished_at"] is not None
    assert snapshot["boards"] == {"total": 8, "done": 8, "checked": 3, "fetched": 3, "cached": 0, "failed": 0, "skipped": 5, "never_checked": 5, "up_to_date": 0}
    assert snapshot["remaining"] == 5
    assert snapshot["failures"] == {"total": 0, "codes": {}, "boards": []}
    assert snapshot["postings"]["new"] == 3
    # Clean: what it reached is indexed and stamped, the snapshot is settled
    # (not `running`), and nothing blocks the next update.
    assert sorted(slug for _ats, slug in index.keys()) == ["b0", "b1", "b2"]
    assert sorted(cache.load_fetch_index().boards) == ["lever:b0", "lever:b1", "lever:b2"]
    status = read_status(tmp_path)
    assert status["running"] is False and status["update"]["status"] == STATUS_PARTIAL and status["update"]["cancelled"] is True

    # The boards the stop left behind lead the next (incremental) update.
    after = _RequestingATS()
    second = update_sources(watchlist, cache=cache, index=index, client=None, ats=after, config=_config(), limits=_limits(concurrency=1))
    assert after.calls == ["b3", "b4", "b5", "b6", "b7"]
    assert second.status == STATUS_SUCCEEDED and second.to_json()["cancelled"] is False
    assert second.to_json()["boards"]["up_to_date"] == 3 and second.to_json()["boards"]["never_checked"] == 0


def test_a_stop_set_before_the_update_asks_no_board(tmp_path: Path) -> None:
    boards = _Boards()
    stop = threading.Event()
    stop.set()

    result = _update(tmp_path, boards, stop=stop)

    assert boards.requests == []
    assert result.status == STATUS_PARTIAL and result.to_json()["cancelled"] is True
    assert result.to_json()["boards"]["checked"] == 0 and result.to_json()["remaining"] == 4
    assert list(CompanyIndex.for_home(tmp_path).keys()) == []


def test_a_stop_during_a_paced_wait_starts_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    monkeypatch.setattr(market_acquisition, "time", clock)
    stop = threading.Event()
    limiter = _RateLimiter(600.0, stop=stop)
    limiter.wait()  # the first slot is free
    sleeps: list[float] = []

    def on_sleep() -> None:
        sleeps.append(clock.monotonic())
        if len(sleeps) == 3:
            stop.set()

    clock.on_sleep = on_sleep
    with pytest.raises(_PassCancelled):
        limiter.wait()

    # Woke every half second, and gave up 1.5 s into a 600 s wait.
    assert sleeps == pytest.approx([0.5, 1.0, 1.5])
    with pytest.raises(_PassCancelled):
        limiter.wait()


def test_a_stopped_spread_pass_skips_the_boards_waiting_for_their_slot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    monkeypatch.setattr(market_acquisition, "time", clock)
    watchlist = [_board(ATSProvider.LEVER, f"b{n}", catalog=True) for n in range(6)]
    client = _StartsClient(clock)
    stop = threading.Event()

    def on_sleep() -> None:
        if clock.monotonic() >= 25.0:  # in the wait before the fourth request (due at 30 s)
            stop.set()

    clock.on_sleep = on_sleep
    result = update_sources(
        watchlist,
        cache=board_cache_for_home(tmp_path),
        index=CompanyIndex.for_home(tmp_path),
        client=client,
        ats=_RequestingATS(),
        config=_config(),
        limits=AcquireLimits(concurrency_per_provider=1, min_request_interval_seconds=0.0, time_budget_seconds=None, spread_seconds=60.0),
        stop=stop,
    )

    assert client.starts == pytest.approx([0.0, 10.0, 20.0])
    assert result.status == STATUS_PARTIAL and result.to_json()["cancelled"] is True
    assert result.to_json()["boards"]["checked"] == 3 and result.to_json()["remaining"] == 3
    assert sorted(slug for _ats, slug in CompanyIndex.for_home(tmp_path).keys()) == ["b0", "b1", "b2"]


# --- the tick entry point ----------------------------------------------------------


def _project(tmp_path: Path) -> tuple[Path, Path]:
    home, target = _installed(tmp_path)
    _add(home, target, "https://boards.greenhouse.io/acme")
    _add(home, target, "https://boards.greenhouse.io/globex")
    _add(home, target, "https://jobs.lever.co/initech")
    return home, target


def _paths(boards: _Boards) -> list[str]:
    return [path for path, _etag in boards.requests]


def test_one_tick_asks_the_busy_boards_and_one_slice_of_the_quiet_ones(tmp_path: Path) -> None:
    home, target = _project(tmp_path)
    boards = _Boards()
    fast = _limits(concurrency=2)
    with boards.client() as client:
        run_sources_update(home_root=home, target=target, client=client, limits=fast)
        # acme's posting set changes; a manual Full refresh sees it, so acme
        # "changed at the previous check": busy. The other two stay quiet.
        boards.version = 2
        boards.greenhouse["acme"].append({"id": 13, "title": "Data Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/13", "location": {"name": "Remote"}, "updated_at": "2026-09-22T00:00:00Z"})
        manual = run_sources_update(home_root=home, target=target, client=client, limits=fast, full_refresh=True)
        assert manual.to_json()["trigger"] == "manual" and manual.to_json()["full_refresh"] is True

        boards.requests.clear()
        now = datetime.now(timezone.utc)
        first = run_refresh_tick(home, target, client=client, now=now + timedelta(hours=1), limits=fast)

        snapshot = first.to_json()
        assert first.status == STATUS_SUCCEEDED
        assert _paths(boards) == ["/v1/boards/acme/jobs"]
        assert snapshot["trigger"] == "auto" and snapshot["full_refresh"] is False and snapshot["cancelled"] is False
        assert snapshot["tick"] == {"boards": 1, "busy": 1, "quiet": 0, "quiet_total": 2, "quiet_due": 0, "slice_size": 1, "slices": 4}
        assert snapshot["boards"] == {"total": 1, "done": 1, "checked": 1, "fetched": 0, "cached": 1, "failed": 0, "skipped": 0, "never_checked": 0, "up_to_date": 2}
        assert read_status(home)["update"]["trigger"] == "auto"

        # That check found acme unchanged, so it is quiet now. Twelve hours
        # on (0110-029: a quiet board about twice a day, a quarter of them
        # per check) all three are due and the tick takes one slice: the
        # least recently checked board (ties by key).
        boards.requests.clear()
        second = run_refresh_tick(home, target, client=client, now=now + timedelta(hours=13), limits=fast)

        assert second.to_json()["tick"] == {"boards": 1, "busy": 0, "quiet": 1, "quiet_total": 3, "quiet_due": 3, "slice_size": 1, "slices": 4}
        assert _paths(boards) == ["/v1/boards/globex/jobs"]
        assert second.to_json()["boards"]["up_to_date"] == 2


def test_a_tick_is_refused_while_an_update_is_live_and_a_stopped_tick_is_partial(tmp_path: Path) -> None:
    home, target = _project(tmp_path)
    boards = _Boards()
    with boards.client() as client:
        _write_running(home, updated_at=index_stamp())
        with pytest.raises(SourcesUpdateRunningError):
            run_refresh_tick(home, target, client=client, limits=_limits(concurrency=1))
        assert boards.requests == []

        # The live update ends; a tick whose stop event is already set (the
        # server is shutting down) asks nothing and settles the snapshot.
        CompanyIndex.for_home(home).update_summary_path.unlink()
        stop = threading.Event()
        stop.set()
        stopped = run_refresh_tick(home, target, client=client, stop_event=stop, limits=_limits(concurrency=1))

    assert boards.requests == []
    assert stopped.status == STATUS_PARTIAL and stopped.to_json()["cancelled"] is True and stopped.to_json()["trigger"] == "auto"
    assert read_status(home)["running"] is False

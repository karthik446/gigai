"""0110-025 R3 + the 0110-024 hooks: the hourly tick thread, the tag/text hooks, the ``background`` status block.

No network: every board request goes to ``test_sources_update._Boards``
(an ``httpx.MockTransport``). Time is a fake clock (``_FakeClock``): the
ticker reads it, and the snapshot stamps are written from it, so "an hour
later" is one line and no test sleeps for it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from gigai.scout.find_jobs import posting_tags, sources_update, text_index
from gigai.scout.find_jobs.company_index import CompanyIndex, index_stamp
from gigai.scout.find_jobs.contracts import ATSProvider
from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve
from gigai.scout.find_jobs.refresh_tick import (
    AUTO_REFRESH_ENV,
    MESSAGE_NEEDS_FIRST_UPDATE,
    STATE_DISABLED,
    STATE_DUE,
    STATE_INACTIVE,
    STATE_NEEDS_FIRST_UPDATE,
    STATE_RUNNING,
    STATE_WAITING,
    RefreshTicker,
    auto_refresh_setting,
    background_status,
    decide,
    settings_path,
)
from gigai.scout.find_jobs.sources_update import (
    STATUS_PARTIAL,
    STATUS_SUCCEEDED,
    SourcesUpdateRunningError,
    board_cache_for_home,
    read_status,
    run_refresh_tick,
    run_sources_update,
    snapshot_is_live,
    start_background_update,
    update_sources,
)

from tests.behaviors.scout_find_jobs.test_acquire_scale import _board, _config, _limits

from .test_refresh_core import _paths, _project
from .test_sources_update import _Boards, _poll, _write_running

FAST = _limits(concurrency=1)


class _FakeClock:
    def __init__(self) -> None:
        self.now = datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    """A fake clock that also stamps the update snapshots (started/updated/finished)."""

    fake = _FakeClock()
    monkeypatch.setattr(sources_update, "index_stamp", lambda moment=None: index_stamp(moment if moment is not None else fake.now))
    monkeypatch.delenv(AUTO_REFRESH_ENV, raising=False)
    return fake


def _ticker(home: Path, target: Path, boards: _Boards, clock: _FakeClock, **kwargs) -> RefreshTicker:
    def fast_tick(*args, **more):
        return run_refresh_tick(*args, limits=FAST, **more)

    kwargs.setdefault("run_tick", fast_tick)
    return RefreshTicker(home_root=home, target=target, client_factory=boards.client, clock=clock, **kwargs)


def _first_update(home: Path, target: Path, boards: _Boards) -> None:
    with boards.client() as client:
        assert run_sources_update(home_root=home, target=target, client=client, limits=FAST).status == STATUS_SUCCEEDED


def _make_every_board_busy(home: Path, target: Path, boards: _Boards) -> None:
    """Change all three boards and let a Full refresh see it: each "changed at the previous check"."""

    boards.version = 2
    boards.greenhouse["acme"].append({"id": 13, "title": "Data Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/13", "location": {"name": "Remote"}, "updated_at": "2026-09-22T00:00:00Z"})
    boards.greenhouse["globex"].append({"id": 22, "title": "Staff Engineer", "absolute_url": "https://boards.greenhouse.io/globex/jobs/22", "location": {"name": "Remote"}, "updated_at": "2026-09-22T00:00:00Z"})
    boards.lever["initech"].append({"id": "lev-3", "text": "Product Designer", "hostedUrl": "https://jobs.lever.co/initech/lev-3", "categories": {"location": "Remote"}, "country": "US", "createdAt": 1758412800000, "descriptionPlain": "Design the kanban flow."})
    with boards.client() as client:
        assert run_sources_update(home_root=home, target=target, client=client, limits=FAST, full_refresh=True).status == STATUS_SUCCEEDED


def _wait_for(condition, *, seconds: float = 20.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "the condition never became true"
        time.sleep(0.01)


# --- the decision (pure) ---------------------------------------------------------


def test_the_decision_from_the_last_snapshot() -> None:
    now = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    done = {"status": "succeeded", "started_at": index_stamp(now - timedelta(minutes=30)), "finished_at": index_stamp(now - timedelta(minutes=20))}

    assert decide(done, indexed=True, enabled=False, now=now).state == STATE_DISABLED

    never = decide(None, indexed=True, enabled=True, now=now)
    assert (never.state, never.message, never.next_tick_at) == (STATE_NEEDS_FIRST_UPDATE, MESSAGE_NEEDS_FIRST_UPDATE, None)
    assert decide(done, indexed=False, enabled=True, now=now).state == STATE_NEEDS_FIRST_UPDATE

    waiting = decide(done, indexed=True, enabled=True, now=now)
    assert (waiting.state, waiting.next_tick_at) == (STATE_WAITING, now + timedelta(minutes=30))

    # One hour after the last update STARTED, however long it ran.
    assert decide(done, indexed=True, enabled=True, now=now + timedelta(minutes=29)).state == STATE_WAITING
    due = decide(done, indexed=True, enabled=True, now=now + timedelta(minutes=30))
    assert (due.state, due.next_tick_at) == (STATE_DUE, now + timedelta(minutes=30))

    live = {"status": "running", "started_at": index_stamp(now - timedelta(hours=3)), "updated_at": index_stamp(now - timedelta(seconds=5))}
    assert decide(live, indexed=True, enabled=True, now=now).state == STATE_RUNNING
    # A running snapshot nobody beats any more is an interrupted update: due again.
    dead = {**live, "updated_at": index_stamp(now - timedelta(minutes=10))}
    assert decide(dead, indexed=True, enabled=True, now=now).state == STATE_DUE


# --- the setting -------------------------------------------------------------------


def test_auto_refresh_is_on_by_default_and_the_settings_file_or_the_environment_turns_it_off(tmp_path: Path) -> None:
    home, target = _project(tmp_path)
    path = settings_path(home, target)
    assert path.parent == home / "scout" / path.parent.name and path.name == "settings.json"

    assert auto_refresh_setting(home, target, environ={}).to_json() == {"enabled": True, "source": "default"}

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": "scout-settings:1", "sources": {"auto_refresh": False}}), encoding="utf-8")
    assert auto_refresh_setting(home, target, environ={}).to_json() == {"enabled": False, "source": "setting"}
    assert auto_refresh_setting(home, target, environ={AUTO_REFRESH_ENV: "1"}).to_json() == {"enabled": True, "source": "environment"}

    path.write_text(json.dumps({"schema_version": "scout-settings:1", "sources": {"auto_refresh": True}}), encoding="utf-8")
    assert auto_refresh_setting(home, target, environ={}).to_json() == {"enabled": True, "source": "setting"}
    assert auto_refresh_setting(home, target, environ={AUTO_REFRESH_ENV: "off"}).to_json() == {"enabled": False, "source": "environment"}

    path.write_text(json.dumps({"schema_version": "scout-settings:1", "sources": {}}), encoding="utf-8")
    assert auto_refresh_setting(home, target, environ={}).to_json() == {"enabled": True, "source": "default"}

    # A file that cannot be understood does not start background requests.
    for broken in ("{not json", json.dumps({"schema_version": "scout-settings:9"}), json.dumps({"schema_version": "scout-settings:1", "sources": {"auto_refresh": "no"}})):
        path.write_text(broken, encoding="utf-8")
        assert auto_refresh_setting(home, target, environ={}).to_json() == {"enabled": False, "source": "settings_unreadable"}


# --- the tick on a fake clock ------------------------------------------------------


def test_a_tick_fires_an_hour_after_the_last_update_on_a_fake_clock(tmp_path: Path, clock: _FakeClock) -> None:
    home, target = _project(tmp_path)
    boards = _Boards()
    _first_update(home, target, boards)
    _make_every_board_busy(home, target, boards)
    ticker = _ticker(home, target, boards, clock)

    boards.requests.clear()
    assert ticker.step() == STATE_WAITING
    clock.advance(minutes=59)
    assert ticker.step() == STATE_WAITING
    assert boards.requests == []

    clock.advance(minutes=1)
    assert ticker.step() == "ticked"

    snapshot = read_status(home, now=clock.now)["update"]
    assert snapshot["trigger"] == "auto" and snapshot["status"] == STATUS_SUCCEEDED
    assert snapshot["tick"]["busy"] == 3 and snapshot["boards"]["checked"] == 3
    assert sorted(_paths(boards)) == ["/v0/postings/initech", "/v1/boards/acme/jobs", "/v1/boards/globex/jobs"]

    # The tick it just ran is the last update now: the next one is an hour on.
    boards.requests.clear()
    assert ticker.step() == STATE_WAITING
    clock.advance(minutes=59)
    assert ticker.step() == STATE_WAITING
    assert boards.requests == []
    clock.advance(minutes=1)
    assert ticker.step() == "ticked"
    # That first tick found all three unchanged, so they are quiet now and
    # not due for six hours: the second tick runs and has nothing to ask.
    second = read_status(home, now=clock.now)["update"]
    assert second["update_id"] != snapshot["update_id"] and second["trigger"] == "auto"
    assert (second["tick"]["boards"], second["tick"]["quiet_total"], second["boards"]["up_to_date"]) == (0, 3, 3)
    assert boards.requests == []


def test_a_server_started_with_a_stale_last_update_ticks_at_once(tmp_path: Path, clock: _FakeClock) -> None:
    home, target = _project(tmp_path)
    boards = _Boards()
    _first_update(home, target, boards)
    _make_every_board_busy(home, target, boards)
    boards.requests.clear()

    clock.advance(hours=9)  # Scout was closed overnight
    assert _ticker(home, target, boards, clock).step() == "ticked"

    assert len(boards.requests) == 3
    assert read_status(home, now=clock.now)["update"]["trigger"] == "auto"


def test_the_thread_ticks_on_the_fake_clock_and_ends_when_it_is_stopped(tmp_path: Path, clock: _FakeClock) -> None:
    home, target = _project(tmp_path)
    _first_update(home, target, _Boards())
    ticks: list[datetime] = []

    def fake_tick(home_root, target_root, *, client, now, stop_event, config):
        del target_root, client, stop_event, config
        ticks.append(now)
        stamp = index_stamp(now)
        CompanyIndex.for_home(home_root).write_update_summary({"update_id": f"tick_{len(ticks)}", "status": "succeeded", "trigger": "auto", "started_at": stamp, "updated_at": stamp, "finished_at": stamp})
        return sources_update.SourcesUpdateResult({"status": "succeeded", "summary": "", "update_id": f"tick_{len(ticks)}"})

    def fake_wait(seconds: float) -> None:
        clock.advance(seconds=seconds)
        time.sleep(0.001)

    ticker = RefreshTicker(home_root=home, target=target, client_factory=lambda: None, clock=clock, wait=fake_wait, run_tick=fake_tick, poll_seconds=60.0)
    ticker.start()
    try:
        _wait_for(lambda: len(ticks) >= 3)
    finally:
        assert ticker.stop(timeout=10) is True

    assert ticker.alive is False
    gaps = [(later - earlier).total_seconds() for earlier, later in zip(ticks, ticks[1:])]
    assert all(gap == 3600.0 for gap in gaps[:2]), gaps
    count = len(ticks)
    time.sleep(0.05)
    assert len(ticks) == count  # nothing ticks after the stop


# --- one update at a time -----------------------------------------------------------


def test_no_tick_starts_while_an_update_is_live(tmp_path: Path, clock: _FakeClock) -> None:
    home, target = _project(tmp_path)
    boards = _Boards()
    _first_update(home, target, boards)
    clock.advance(hours=2)
    _write_running(home, updated_at=index_stamp(clock.now))
    boards.requests.clear()
    ticker = _ticker(home, target, boards, clock)

    assert ticker.step() == STATE_RUNNING
    assert boards.requests == []
    assert read_status(home, now=clock.now)["update"]["update_id"] == "sources_update_other"

    # A manual update that claims the snapshot between the look and the tick: the tick yields.
    def claimed_first(*args, **kwargs):
        raise SourcesUpdateRunningError()

    CompanyIndex.for_home(home).update_summary_path.unlink()
    _first_update(home, target, boards)
    clock.advance(hours=2)
    boards.requests.clear()
    assert _ticker(home, target, boards, clock, run_tick=claimed_first).step() == "yielded"
    assert boards.requests == []


def test_no_manual_update_starts_while_a_tick_is_live_but_a_full_refresh_stops_the_tick_and_takes_over(
    tmp_path: Path, clock: _FakeClock
) -> None:
    home, target = _project(tmp_path)
    boards = _Boards()
    _first_update(home, target, boards)
    _make_every_board_busy(home, target, boards)
    clock.advance(hours=1)
    ticker = _ticker(home, target, boards, clock)
    index = CompanyIndex.for_home(home)

    boards.requests.clear()
    boards.gate = threading.Event()
    outcome: list[str] = []
    ticking = threading.Thread(target=lambda: outcome.append(ticker.step()), daemon=True)
    ticking.start()
    try:
        # One request per provider is on the wire (held at the gate); globex waits behind acme.
        _wait_for(lambda: index.root in sources_update._LIVE_TICKS and (index.read_update_summary() or {}).get("tick") is not None)
        tick_id = index.read_update_summary()["update_id"]

        # A plain Update sources is refused: the tick is the live update.
        with pytest.raises(SourcesUpdateRunningError):
            start_background_update(home_root=home, target=target, client_factory=boards.client, limits=FAST)
        assert index.read_update_summary()["update_id"] == tick_id

        # A Full refresh wins: it stops the tick, waits for it to settle, then starts.
        finished: list[dict] = []
        started: list[str] = []
        manual = threading.Thread(
            target=lambda: started.append(
                start_background_update(home_root=home, target=target, client_factory=boards.client, limits=FAST, full_refresh=True, on_finished=finished.append)
            ),
            daemon=True,
        )
        manual.start()
        _wait_for(lambda: sources_update._LIVE_TICKS[index.root].stop.is_set())
        assert started == []  # still waiting for the tick: never two at once
    finally:
        boards.gate.set()
    ticking.join(timeout=20)
    manual.join(timeout=20)
    _wait_for(lambda: finished != [])

    assert outcome == ["ticked"] and not ticking.is_alive()
    assert started and started[0] != tick_id
    final = finished[0]
    assert final["update_id"] == started[0]
    assert (final["status"], final["trigger"], final["full_refresh"], final["cancelled"]) == (STATUS_SUCCEEDED, "manual", True, False)
    assert final["boards"]["checked"] == 3
    assert index.root not in sources_update._LIVE_TICKS


def test_a_tick_stopped_mid_way_leaves_a_clean_partial_snapshot(tmp_path: Path, clock: _FakeClock) -> None:
    """What shutdown (or a Full refresh) leaves behind: partial, cancelled, the board not asked left for later."""

    home, target = _project(tmp_path)
    boards = _Boards()
    _first_update(home, target, boards)
    _make_every_board_busy(home, target, boards)
    clock.advance(hours=1)
    results: list[dict] = []

    def recording_tick(*args, **kwargs):
        result = run_refresh_tick(*args, limits=FAST, **kwargs)
        results.append(result.to_json())
        return result

    ticker = _ticker(home, target, boards, clock, run_tick=recording_tick)
    index = CompanyIndex.for_home(home)
    boards.requests.clear()
    boards.gate = threading.Event()
    ticking = threading.Thread(target=ticker.step, daemon=True)
    ticking.start()
    try:
        _wait_for(lambda: index.root in sources_update._LIVE_TICKS and (index.read_update_summary() or {}).get("tick") is not None)
        assert ticker.stop(timeout=0.2) is True  # no thread of its own here: only the tick is told to stop
        assert sources_update._LIVE_TICKS[index.root].stop.is_set()
    finally:
        boards.gate.set()
    ticking.join(timeout=20)

    tick = results[0]
    assert (tick["status"], tick["trigger"], tick["cancelled"]) == (STATUS_PARTIAL, "auto", True)
    assert tick["finished_at"] is not None and tick["boards"]["skipped"] >= 1
    assert tick["boards"]["checked"] + tick["boards"]["skipped"] == 3 and tick["remaining"] == tick["boards"]["skipped"]
    status = read_status(home, now=clock.now)
    assert status["running"] is False and status["update"]["status"] == STATUS_PARTIAL


# --- shutdown ---------------------------------------------------------------------------


def test_the_server_starts_the_thread_only_when_asked_and_shutdown_ends_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = _project(tmp_path)
    monkeypatch.delenv(AUTO_REFRESH_ENV, raising=False)
    backend = ScoutFindJobsBackend(home_root=home, target=target)

    plain = serve(backend=backend, bind=("127.0.0.1", 0))
    try:
        assert plain.refresh_ticker is None
    finally:
        plain.server_close()

    server = serve(backend=backend, bind=("127.0.0.1", 0), background_refresh=True)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        ticker = server.refresh_ticker
        assert ticker is not None and ticker.alive
        assert (ticker.home_root, ticker.target) == (backend.home_root, backend.target)
        names = [thread.name for thread in threading.enumerate()]
        assert names.count("scout-sources-refresh") == 1
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=5)

    assert ticker.alive is False
    assert "scout-sources-refresh" not in [thread.name for thread in threading.enumerate()]


# --- off, and never checked ------------------------------------------------------------


@pytest.mark.parametrize("how", ["setting", "environment"])
def test_auto_refresh_off_makes_no_request(tmp_path: Path, clock: _FakeClock, monkeypatch: pytest.MonkeyPatch, how: str) -> None:
    home, target = _project(tmp_path)
    boards = _Boards()
    _first_update(home, target, boards)
    _make_every_board_busy(home, target, boards)
    clock.advance(hours=5)
    before = CompanyIndex.for_home(home).read_update_summary()
    if how == "setting":
        path = settings_path(home, target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": "scout-settings:1", "sources": {"auto_refresh": False}}), encoding="utf-8")
    else:
        monkeypatch.setenv(AUTO_REFRESH_ENV, "0")
    boards.requests.clear()

    def must_not_tick(*args, **kwargs):
        raise AssertionError("a tick ran with auto refresh off")

    ticker = _ticker(home, target, boards, clock, run_tick=must_not_tick, wait=lambda seconds: time.sleep(0.001))
    assert ticker.step() == STATE_DISABLED
    ticker.start()
    time.sleep(0.05)  # the thread loops, and still does nothing
    assert ticker.stop(timeout=5) is True

    assert boards.requests == []
    assert CompanyIndex.for_home(home).read_update_summary() == before
    block = background_status(home, target, ticker=None, now=clock.now)
    assert block["auto_refresh"] == {"enabled": False, "source": how, "active": False}
    assert (block["state"], block["next_tick_at"], block["message"]) == (STATE_DISABLED, None, "Automatic refresh is off.")


def test_a_never_checked_index_is_not_filled_by_ticks(tmp_path: Path, clock: _FakeClock) -> None:
    home, target = _project(tmp_path)
    boards = _Boards()
    ticker = _ticker(home, target, boards, clock)

    clock.advance(hours=30)
    assert ticker.step() == STATE_NEEDS_FIRST_UPDATE
    assert boards.requests == [] and CompanyIndex.for_home(home).read_update_summary() is None

    ticker.start()
    try:
        block = background_status(home, target, ticker=ticker, now=clock.now)
    finally:
        ticker.stop()
    assert block["state"] == STATE_NEEDS_FIRST_UPDATE and block["next_tick_at"] is None
    assert block["message"] == "Run Update sources once. Automatic refresh starts after the first update."
    assert block["last_update"] is None and block["in_progress"] is False

    # An index with companies but no update stamp (the snapshot was deleted): the same.
    _first_update(home, target, boards)
    CompanyIndex.for_home(home).update_summary_path.unlink()
    boards.requests.clear()
    assert ticker.step() == STATE_NEEDS_FIRST_UPDATE and boards.requests == []


# --- the hooks: tags and text follow the company files -----------------------------------


def _tag(home: Path, title: str):
    store = posting_tags.default_store(home)
    try:
        return store.get(posting_tags.normalize_title(title))
    finally:
        store.close()


def _text_hits(home: Path, query: str) -> set[tuple[str, str]]:
    result = text_index.search(home, query)
    assert result.available, result.reason
    return {(hit.company_key, hit.posting_id) for hit in result.hits}


def test_after_an_update_the_tag_store_and_the_text_index_hold_the_new_postings(tmp_path: Path) -> None:
    home, target = _project(tmp_path)
    boards = _Boards()
    with boards.client() as client:
        first = run_sources_update(home_root=home, target=target, client=client, limits=FAST).to_json()

        # Five postings, four distinct titles ("Software Engineer" is on two boards).
        assert first["stores"] == {
            "tags": {"titles_tagged": 4, "titles_backfilled": 0, "failures": 0},
            "text": {"companies_written": 3, "companies_removed": 0, "failures": 0},
        }
        engineer = _tag(home, "Senior Software Engineer")
        assert (engineer.level, engineer.function, engineer.function_source) == ("senior", "software", "rules")
        assert _tag(home, "Office Manager").level == "manager"
        assert _tag(home, "Data Engineer") is None
        assert _text_hits(home, "office") == {("lever:initech", "lev-2")}
        assert _text_hits(home, "things") == {("lever:initech", "lev-1")}  # only in the description
        assert _text_hits(home, "kanban") == set()

        # An hour later a tick sees a new posting, a retitled one and a removed one.
        boards.version = 2
        boards.greenhouse["acme"] = [
            {"id": 11, "title": "Principal Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/11", "location": {"name": "Denver, CO"}, "updated_at": "2026-09-25T00:00:00Z"},
            {"id": 13, "title": "Data Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/13", "location": {"name": "Remote"}, "updated_at": "2026-09-22T00:00:00Z"},
        ]
        boards.lever["initech"] = [boards.lever["initech"][0], {"id": "lev-3", "text": "UX Designer", "hostedUrl": "https://jobs.lever.co/initech/lev-3", "categories": {"location": "Remote"}, "country": "US", "createdAt": 1758412800000, "descriptionPlain": "Design the kanban flow."}]
        full = run_sources_update(home_root=home, target=target, client=client, limits=FAST, full_refresh=True).to_json()
        assert full["postings"]["new"] == 2 and full["postings"]["removed"] == 2
        tick = run_refresh_tick(home, target, client=client, now=datetime.now(timezone.utc) + timedelta(hours=1), limits=FAST).to_json()

    assert full["stores"]["tags"] == {"titles_tagged": 3, "titles_backfilled": 0, "failures": 0}
    assert full["stores"]["text"] == {"companies_written": 2, "companies_removed": 0, "failures": 0}
    assert tick["trigger"] == "auto" and tick["stores"]["tags"]["failures"] == 0 and tick["stores"]["text"]["failures"] == 0
    assert (_tag(home, "Data Engineer").level, _tag(home, "Data Engineer").function) == ("mid", "data")
    assert _tag(home, "Principal Software Engineer").level == "principal"
    assert _tag(home, "UX Designer").function == "design"
    assert _text_hits(home, "kanban") == {("lever:initech", "lev-3")}
    assert _text_hits(home, "office") == set()  # the removed posting left the text index
    # Greenhouse lists carry no description and this update fetched no detail:
    # those postings are in the index as "text not checked", never as a hit.
    stats = text_index.stats(home)
    assert _text_hits(home, "data") == set() and stats.without_text == 3 and stats.with_text == 2
    assert stats.postings == sum(len(CompanyIndex.for_home(home).read(ats, slug).live()) for ats, slug in CompanyIndex.for_home(home).keys())


def test_a_board_that_left_the_watchlist_leaves_the_text_index(tmp_path: Path) -> None:
    boards = _Boards()
    watchlist = [_board(ATSProvider.GREENHOUSE, "acme"), _board(ATSProvider.LEVER, "initech", catalog=True)]

    def update(entries):
        with boards.client() as client:
            return update_sources(
                entries,
                cache=board_cache_for_home(tmp_path),
                index=CompanyIndex.for_home(tmp_path),
                client=client,
                config=_config(),
                limits=FAST,
                full_refresh=True,
                home_root=tmp_path,
            ).to_json()

    assert update(watchlist)["stores"]["text"] == {"companies_written": 2, "companies_removed": 0, "failures": 0}
    assert _text_hits(tmp_path, "office") == {("lever:initech", "lev-2")}

    dropped = update(watchlist[:1])

    assert dropped["stores"]["text"] == {"companies_written": 0, "companies_removed": 1, "failures": 0}
    assert _text_hits(tmp_path, "office") == set()
    assert ("greenhouse:acme", "11") in _text_hits(tmp_path, "software")


def test_an_update_without_a_home_touches_neither_store(tmp_path: Path) -> None:
    boards = _Boards()
    with boards.client() as client:
        result = update_sources(
            [_board(ATSProvider.GREENHOUSE, "acme")],
            cache=board_cache_for_home(tmp_path),
            index=CompanyIndex.for_home(tmp_path),
            client=client,
            config=_config(),
            limits=FAST,
        ).to_json()

    assert result["status"] == STATUS_SUCCEEDED and result["stores"] is None
    assert not posting_tags.default_store(tmp_path).path.exists()
    assert not text_index.text_index_path(tmp_path).exists()


def test_a_deleted_tag_store_is_refilled_by_the_next_update(tmp_path: Path) -> None:
    home, target = _project(tmp_path)
    boards = _Boards()
    _first_update(home, target, boards)
    store_path = posting_tags.default_store(home).path
    for suffix in ("", "-wal", "-shm"):
        Path(f"{store_path}{suffix}").unlink(missing_ok=True)
    assert background_status(home, target)["tags"]["available"] is False

    # Nothing changed on any board, so no hook fires: the refill is the update's first step.
    with boards.client() as client:
        again = run_sources_update(home_root=home, target=target, client=client, limits=FAST, full_refresh=True).to_json()

    assert again["postings"]["new"] == 0 and again["companies"]["untouched"] == 3
    assert again["stores"]["tags"] == {"titles_tagged": 0, "titles_backfilled": 4, "failures": 0}
    assert _tag(home, "Senior Software Engineer").level == "senior" and _tag(home, "Office Manager") is not None
    assert background_status(home, target)["tags"] == {"available": True, "titles": 4, "with_function": 4, "lacking_function": 0}


def test_a_tag_store_failure_does_not_fail_the_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    home, target = _project(tmp_path)
    boards = _Boards()

    def broken(store, titles):
        raise RuntimeError("tags.sqlite is locked")

    monkeypatch.setattr(posting_tags, "tag_new_titles", broken)
    with boards.client() as client:
        result = run_sources_update(home_root=home, target=target, client=client, limits=FAST).to_json()

    assert result["status"] == STATUS_SUCCEEDED and result["error"] is None
    assert result["postings"] == {"new": 5, "changed": 0, "removed": 0, "live": 5}
    assert result["stores"]["tags"] == {"titles_tagged": 0, "titles_backfilled": 0, "failures": 3}
    # The text index is a separate store: it still followed the update.
    assert result["stores"]["text"] == {"companies_written": 3, "companies_removed": 0, "failures": 0}
    assert _text_hits(home, "office") == {("lever:initech", "lev-2")}
    assert len(list(CompanyIndex.for_home(home).keys())) == 3
    assert capsys.readouterr().err.count("the tag store was not updated (RuntimeError)") == 1


def test_a_text_index_failure_does_not_fail_the_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = _project(tmp_path)
    boards = _Boards()

    def broken(home_root, key, postings):
        raise OSError("disk full")

    monkeypatch.setattr(text_index, "upsert_company", broken)
    with boards.client() as client:
        result = run_sources_update(home_root=home, target=target, client=client, limits=FAST).to_json()

    assert result["status"] == STATUS_SUCCEEDED
    assert result["stores"] == {
        "tags": {"titles_tagged": 4, "titles_backfilled": 0, "failures": 0},
        "text": {"companies_written": 0, "companies_removed": 0, "failures": 3},
    }
    assert _tag(home, "Office Manager") is not None


def test_a_sqlite_without_per_company_delete_rebuilds_the_text_index_once_at_the_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = _project(tmp_path)
    boards = _Boards()
    rebuilds: list[Path] = []
    real = text_index.rebuild_from_cache

    def counting(home_root):
        rebuilds.append(home_root)
        return real(home_root)

    monkeypatch.setattr(text_index, "_SUPPORTS_CONTENTLESS_DELETE", False)
    monkeypatch.setattr(text_index, "rebuild_from_cache", counting)
    with boards.client() as client:
        result = run_sources_update(home_root=home, target=target, client=client, limits=FAST).to_json()

    assert len(rebuilds) == 1 and result["stores"]["text"]["failures"] == 0
    assert _text_hits(home, "office") == {("lever:initech", "lev-2")}


# --- one update at a time across a tick's quiet stretches ------------------------------


def test_a_running_update_keeps_its_snapshot_alive_between_boards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = _project(tmp_path)
    boards = _Boards()
    monkeypatch.setattr(sources_update, "HEARTBEAT_INTERVAL_SECONDS", 0.05)
    index = CompanyIndex.for_home(home)
    boards.gate = threading.Event()
    done: list[str] = []

    def run() -> None:
        with boards.client() as client:
            done.append(run_sources_update(home_root=home, target=target, client=client, limits=FAST).status)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        _wait_for(lambda: (index.read_update_summary() or {}).get("boards", {}).get("total") == 3)
        first = index.read_update_summary()["updated_at"]
        # No board settles (all held at the gate), yet the snapshot is rewritten.
        _wait_for(lambda: index.read_update_summary()["updated_at"] > first)
        assert index.read_update_summary()["status"] == "running" and index.read_update_summary()["boards"]["checked"] == 0
    finally:
        boards.gate.set()
    worker.join(timeout=20)

    assert done == [STATUS_SUCCEEDED]
    time.sleep(0.2)  # a late heartbeat must not rewrite the final snapshot as running
    assert index.read_update_summary()["status"] == STATUS_SUCCEEDED


def test_a_running_snapshot_whose_writer_process_is_gone_is_not_live() -> None:
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait(timeout=30)
    stamp = index_stamp()
    running = {"status": "running", "started_at": stamp, "updated_at": stamp}

    assert snapshot_is_live(running) is True  # no pid recorded: the heartbeat alone decides
    assert snapshot_is_live({**running, "pid": finished.pid}) is False
    assert sources_update.settled_snapshot({**running, "pid": finished.pid})["status"] == "interrupted"
    assert snapshot_is_live({**running, "pid": os.getpid()}) is True


# --- the status block ---------------------------------------------------------------------


def test_the_background_block_reports_the_refresh_the_tags_and_the_text_index(tmp_path: Path, clock: _FakeClock) -> None:
    home, target = _project(tmp_path)
    boards = _Boards()

    empty = background_status(home, target, now=clock.now)
    assert empty["tags"] == {"available": False, "titles": 0, "with_function": 0, "lacking_function": 0}
    assert empty["text"] == {"available": False, "postings": 0, "with_text": 0, "unchecked": 0}
    # A status read creates neither cache.
    assert not posting_tags.default_store(home).path.exists() and not text_index.text_index_path(home).exists()
    # No tick thread in this process (the CLI, a test server): inactive, no next tick.
    assert (empty["state"], empty["message"], empty["next_tick_at"]) == (STATE_INACTIVE, MESSAGE_NEEDS_FIRST_UPDATE, None)

    boards.lever["initech"].append({"id": "lev-9", "text": "Wizard of Light Bulb Moments", "hostedUrl": "https://jobs.lever.co/initech/lev-9", "categories": {"location": "Remote"}, "country": "US", "createdAt": 1758412800000, "descriptionPlain": ""})
    _first_update(home, target, boards)
    started = CompanyIndex.for_home(home).read_update_summary()

    idle = background_status(home, target, now=clock.now)
    assert (idle["state"], idle["message"], idle["next_tick_at"]) == (STATE_INACTIVE, None, None)

    ticker = _ticker(home, target, boards, clock, wait=lambda seconds: time.sleep(0.001))
    ticker.start()
    try:
        clock.advance(minutes=12)
        block = background_status(home, target, ticker=ticker, now=clock.now)
    finally:
        ticker.stop()

    assert block["auto_refresh"] == {"enabled": True, "source": "default", "active": True}
    assert (block["state"], block["message"], block["in_progress"], block["trigger"]) == (STATE_WAITING, None, False, "manual")
    assert block["last_update"] == {
        "update_id": started["update_id"],
        "status": "succeeded",
        "trigger": "manual",
        "started_at": started["started_at"],
        "finished_at": started["finished_at"],
    }
    assert block["next_tick_at"] == index_stamp(datetime.fromisoformat(started["started_at"].replace("Z", "+00:00")) + timedelta(hours=1))
    assert block["interval_seconds"] == 3600.0
    # Six postings, five distinct titles, one the rules cannot place in a function.
    assert block["tags"] == {"available": True, "titles": 5, "with_function": 4, "lacking_function": 1}
    text = block["text"]
    assert text["available"] is True and text["postings"] == 6
    assert text["with_text"] + text["unchecked"] == 6 and text["unchecked"] >= 1  # lev-9 has no description

    # While an update is live the block says so, and who started it.
    _write_running(home, updated_at=index_stamp(clock.now))
    live = background_status(home, target, now=clock.now)
    assert (live["state"], live["in_progress"], live["last_update"], live["next_tick_at"]) == (STATE_RUNNING, True, None, None)


def test_a_status_read_never_rebuilds_or_replaces_a_store_it_cannot_read(tmp_path: Path) -> None:
    """The stores' own openers discard a file they cannot open; a status poll must only look."""

    tags = posting_tags.default_store(tmp_path).path
    text = text_index.text_index_path(tmp_path)
    tags.parent.mkdir(parents=True)
    tags.write_bytes(b"not a database, or one that is being created")
    text.write_bytes(b"not a database either")

    block = background_status(tmp_path, None)

    assert block["tags"]["available"] is False and block["text"]["available"] is False
    assert tags.read_bytes() == b"not a database, or one that is being created"
    assert text.read_bytes() == b"not a database either"
    assert sorted(item.name for item in tags.parent.iterdir()) == ["tags.sqlite", "text.sqlite"]


def test_the_api_status_carries_the_background_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = _project(tmp_path)
    boards = _Boards()
    monkeypatch.delenv(AUTO_REFRESH_ENV, raising=False)
    monkeypatch.setattr("gigai.scout.find_jobs.api.sources._board_http_client", boards.client)
    monkeypatch.setenv("GIGAI_SCOUT_ATS_MIN_INTERVAL_SECONDS", "0")
    server = serve(backend=ScoutFindJobsBackend(home_root=home, target=target), bind=("127.0.0.1", 0), background_refresh=True)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}", timeout=30.0) as client:
            before = client.get("/api/sources/update").json()
            assert before["background"]["auto_refresh"] == {"enabled": True, "source": "default", "active": True}
            assert before["background"]["state"] == "needs_first_update"
            assert before["background"]["message"] == "Run Update sources once. Automatic refresh starts after the first update."
            assert boards.requests == []  # the thread is up and asks nothing: the index was never checked

            assert client.post("/api/sources/update", json={}).status_code == 202
            after = _poll(client)
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=5)

    update, block = after["update"], after["background"]
    assert update["status"] == "succeeded" and update["trigger"] == "manual"
    assert update["stores"] == {"tags": {"titles_tagged": 4, "titles_backfilled": 0, "failures": 0}, "text": {"companies_written": 3, "companies_removed": 0, "failures": 0}}
    assert set(block) == {"auto_refresh", "state", "message", "in_progress", "trigger", "last_update", "next_tick_at", "interval_seconds", "tags", "text"}
    assert (block["state"], block["in_progress"], block["trigger"]) == ("waiting", False, "manual")
    assert block["last_update"]["update_id"] == update["update_id"] and block["last_update"]["finished_at"] == update["finished_at"]
    assert block["next_tick_at"] > update["finished_at"]
    assert block["tags"] == {"available": True, "titles": 4, "with_function": 4, "lacking_function": 0}
    assert block["text"]["available"] is True and block["text"]["postings"] == 5

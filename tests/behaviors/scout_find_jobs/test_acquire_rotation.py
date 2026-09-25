"""acquire-rotation: page through the whole watchlist by last fetch, with a cursor across runs.

Operator direction 2026-09-25: keep every board on the watchlist; bound the
cost by rotating. Each ATS pass takes the next PAGE of boards ordered by
last-attempted stamp ascending (never-fetched first; ties by the catalog's
``us_posting_count`` desc, then provider/token), fetches what the time budget
fits, stamps every board it attempted (a ``304`` and a failure both count),
and stops; the next run continues from there.

The stamp lives in ``<home>/cache/scout/ats-boards/last-fetched.json``
(``BoardCache.load_fetch_index``/``store_fetch_index``): a cache next to the
response cache, never a journal record -- losing it just restarts the
rotation. Nothing here touches the network: ``_ClockATS`` is a fake board
client that advances a fake clock one tick per board, so a budget of ``n +
0.5`` fits exactly ``n + 1`` boards (boards start at ticks 0..n) and the page
is deterministic; the ``304`` case runs the real ``ATSBoardClients`` over an
``httpx.MockTransport``.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import httpx
import pytest

from gigai.scout.find_jobs.ats_board_clients import (
    ATSBoardClients,
    BoardCache,
    BoardFetchIndex,
    BoardFetchResult,
    BoardFetchStats,
    LAST_FETCHED_SCHEMA,
)
from gigai.scout.find_jobs.company_catalog import load_company_catalog
from gigai.scout.find_jobs.contracts import ATSProvider
from gigai.scout.find_jobs.market_acquisition import (
    BUDGET_EXCEEDED_CODE,
    _plan_rotation,
    _rotation_line,
    acquire_node,
)
from gigai.scout.find_jobs.progress import ProgressWriter, read_progress

from tests.behaviors.scout_find_jobs.test_acquire_scale import (
    _Exa,
    _GreenhouseFixture,
    _Watchlist,
    _board,
    _context,
    _input,
    _limits,
    _managed,
    _patch_import,
    _real_context,
    _row,
)
from tests.support.workpad_assertions import assert_managed_workpad_clean


class _Clock:
    """A fake ``time`` module for ``market_acquisition``: monotonic ticks, no real sleeping."""

    def __init__(self) -> None:
        self.now = 0.0
        self.lock = threading.Lock()

    def monotonic(self) -> float:
        with self.lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self.lock:
            self.now += seconds

    def tick(self, seconds: float = 1.0) -> None:
        self.sleep(seconds)


class _ClockATS:
    """``fetch_board`` fake: one board = one clock tick; ``dead`` tokens raise."""

    def __init__(self, clock: _Clock, *, step: float = 1.0, dead: frozenset[str] = frozenset(), on_call=None):
        self.clock = clock
        self.step = step
        self.dead = dead
        self.on_call = on_call
        self.calls: list[str] = []
        self.lock = threading.Lock()

    def fetch_board(self, client, provider, board_token, config, *, cache=None):
        with self.lock:
            self.calls.append(board_token)
            n = len(self.calls)
        if self.on_call is not None:
            self.on_call(n, board_token)
        self.clock.tick(self.step)
        if board_token in self.dead:
            raise RuntimeError("board is gone")
        return BoardFetchResult((_row(ATSProvider(provider), board_token, "1"),), BoardFetchStats(requests=1, cache="miss", listed=1))


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    _patch_import(monkeypatch)
    fake = _Clock()
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.time", fake)
    return fake


def _catalog_boards(*tokens: str) -> list:
    return [_board(ATSProvider.GREENHOUSE, token, catalog=True) for token in tokens]


def _run(substrate, boards, *, ats, n: int, budget: float | None = None, client=None):
    home, target, workpad, gig_id = substrate
    return acquire_node(
        _real_context(home, target, workpad, gig_id, key=f"rotation-{n}", run_id=f"run_{n:02d}"),
        _input(),
        http_client=client,
        exa=_Exa(),
        ats=ats,
        watchlist=_Watchlist(boards),
        home_root=home,
        target=target,
        limits=_limits(concurrency=1, budget=budget),
    )


def _index(home: Path) -> BoardFetchIndex:
    return BoardCache(home / "cache" / "scout" / "ats-boards").load_fetch_index()


def _rotation(workpad: Path, n: int) -> dict:
    rotation = read_progress(workpad / "runs" / f"run_{n:02d}").boards["rotation"]
    assert isinstance(rotation, dict)
    return rotation


# --- the page + the cursor across runs ---------------------------------------


def test_a_second_run_continues_where_the_first_stopped(clock: _Clock, tmp_path: Path) -> None:
    """A budget-exceeded run leaves the rest for next time, and the next run takes it first.

    Six catalog boards, a budget that fits four: run 1 takes g0-g3 and skips
    g4-g5 (recorded); run 2 leads with the two never-fetched boards, then
    the least recently attempted; run 3 opens a new cycle with the boards
    run 1 stamped and run 2 did not reach.
    """

    substrate = _managed(tmp_path)
    home, _target, workpad, _gig = substrate
    boards = _catalog_boards("g0", "g1", "g2", "g3", "g4", "g5")

    first_ats = _ClockATS(clock)
    first = _run(substrate, boards, ats=first_ats, n=1, budget=3.5)
    assert first_ats.calls == ["g0", "g1", "g2", "g3"]
    assert [f.code for f in first.failures] == [BUDGET_EXCEEDED_CODE]
    assert "2 of 6" in first.failures[0].message
    stamps = _index(home).boards
    assert set(stamps) == {f"greenhouse:g{i}" for i in range(4)}, "only ATTEMPTED boards are stamped; skipped ones keep none"
    assert len(set(stamps.values())) == 1, "every board attempted in one run carries that run's stamp"

    clock.now = 0.0
    second_ats = _ClockATS(clock)
    _run(substrate, boards, ats=second_ats, n=2, budget=3.5)
    assert second_ats.calls == ["g4", "g5", "g0", "g1"], "never-fetched first, then least recently attempted"

    clock.now = 0.0
    third_ats = _ClockATS(clock)
    _run(substrate, boards, ats=third_ats, n=3, budget=3.5)
    assert third_ats.calls == ["g2", "g3", "g0", "g1"], "run 1's leftovers (oldest stamp) lead; then run 2's, by token"

    # The cursor the progress files carry: "boards N-M of 6 this run; full
    # rotation every ~2 runs" (K = ceil(6 boards / a 4-board page)).
    run1, run2, run3 = _rotation(workpad, 1), _rotation(workpad, 2), _rotation(workpad, 3)
    assert (run1["cycle"], run1["first"], run1["last"], run1["total"]) == (1, 1, 4, 6)
    assert run1["page_size"] == 4 and run1["runs_per_rotation"] == 2 and run1["estimated"] is False
    assert run1["providers"] == {"greenhouse": {"total": 6, "page_size": 4, "runs_per_rotation": 2}}
    assert (run2["cycle"], run2["first"], run2["last"]) == (1, 5, 6), "run 2 continues at board 5; g0/g1 were refetched, not newly covered"
    assert (run3["cycle"], run3["first"], run3["last"]) == (2, 1, 4), "every board covered once -> a new cycle"
    assert _index(home).cycle == 2
    assert _rotation_line(run1) == "scout acquire: boards 1-4 of 6 this run (cycle 1); full rotation every ~2 runs"

    # The index is a cache under the home, never in the workpad (regression-001).
    assert (home / "cache" / "scout" / "ats-boards" / "last-fetched.json").is_file()
    assert_managed_workpad_clean(workpad)


def test_the_live_progress_line_carries_the_previous_page_as_an_estimate(clock: _Clock, tmp_path: Path) -> None:
    substrate = _managed(tmp_path)
    _home, _target, workpad, _gig = substrate
    boards = _catalog_boards("g0", "g1", "g2")
    _run(substrate, boards, ats=_ClockATS(clock), n=1, budget=1.5)

    seen: list[dict] = []

    def snapshot_while_running(n: int, token: str) -> None:
        if n == 1:
            seen.append(read_progress(workpad / "runs" / "run_02").boards["rotation"])

    clock.now = 0.0
    _run(substrate, boards, ats=_ClockATS(clock, on_call=snapshot_while_running), n=2, budget=1.5)
    (live,) = seen
    # Planned: first = 3 (two covered by run 1), last unknown, K from run 1's
    # 2-board page = ceil(3 / 2) = 2, flagged as an estimate.
    assert live["first"] == 3 and live["last"] is None and live["estimated"] is True
    assert live["page_size"] == 2 and live["runs_per_rotation"] == 2
    final = _rotation(workpad, 2)
    assert final["first"] == 3 and final["last"] == 3 and final["estimated"] is False


def test_a_never_fetched_board_goes_first_even_when_added_last(clock: _Clock, tmp_path: Path) -> None:
    """A company added between runs (Add company / a new catalog record) is never starved."""

    substrate = _managed(tmp_path)
    _run(substrate, _catalog_boards("a", "b"), ats=_ClockATS(clock), n=1)

    clock.now = 0.0
    ats = _ClockATS(clock)
    added = _catalog_boards("a", "b", "c") + [_board(ATSProvider.GREENHOUSE, "mine")]
    _run(substrate, added, ats=ats, n=2, budget=1.5)
    # The operator's own board first (every run), then the never-fetched
    # catalog board -- the two boards run 1 already saw wait.
    assert ats.calls == ["mine", "c"]


def test_user_added_boards_are_fetched_every_run_before_the_catalog_page(clock: _Clock, tmp_path: Path) -> None:
    substrate = _managed(tmp_path)
    boards = [_board(ATSProvider.GREENHOUSE, "mine")] + _catalog_boards("cat-a", "cat-b")
    first = _ClockATS(clock)
    _run(substrate, boards, ats=first, n=1, budget=1.5)
    assert first.calls == ["mine", "cat-a"]
    clock.now = 0.0
    second = _ClockATS(clock)
    _run(substrate, boards, ats=second, n=2, budget=1.5)
    assert second.calls == ["mine", "cat-b"], "the user's board is re-checked every run; the catalog page rotates"


def test_a_failed_board_is_stamped_and_rotates_like_a_live_one(clock: _Clock, tmp_path: Path) -> None:
    """A dead board (404, bad JSON) must not lead every run forever as 'never fetched'."""

    substrate = _managed(tmp_path)
    home = substrate[0]
    boards = _catalog_boards("dead", "live")
    first = _ClockATS(clock, dead=frozenset({"dead"}))
    out = _run(substrate, boards, ats=first, n=1)
    assert first.calls == ["dead", "live"]
    assert [f.code for f in out.failures] == ["runtimeerror"]
    stamps = _index(home).boards
    assert stamps["greenhouse:dead"] == stamps["greenhouse:live"]

    clock.now = 0.0
    second = _ClockATS(clock, dead=frozenset({"dead"}))
    _run(substrate, _catalog_boards("dead", "live", "new"), ats=second, n=2, budget=0.5)
    assert second.calls == ["new"]


def test_a_304_counts_as_fetched_now(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Real ``ATSBoardClients`` over a fake transport: the conditional GET's 304 advances the stamp."""

    _patch_import(monkeypatch)
    fixture = _GreenhouseFixture()
    substrate = _managed(tmp_path)
    home, _target, workpad, _gig = substrate
    boards = [_board(ATSProvider.GREENHOUSE, "acme")]

    with httpx.Client(transport=httpx.MockTransport(fixture.handler)) as client:
        _run(substrate, boards, ats=ATSBoardClients(), n=1, client=client)
    before = _index(home).boards["greenhouse:acme"]

    fixture.requests.clear()
    time.sleep(0.002)  # the stamp has millisecond resolution
    with httpx.Client(transport=httpx.MockTransport(fixture.handler)) as client:
        _run(substrate, boards, ats=ATSBoardClients(), n=2, client=client)
    assert fixture.requests == [("/v1/boards/acme/jobs", fixture.etag)], "one conditional GET, answered 304"
    assert read_progress(workpad / "runs" / "run_02").boards["cached"] == 1
    after = _index(home).boards["greenhouse:acme"]
    assert after > before, "a 304 is an attempt: the board moves to the back of the rotation"
    assert_managed_workpad_clean(workpad)


def test_the_index_is_flushed_during_a_long_pass_not_only_at_the_end(clock: _Clock, tmp_path: Path) -> None:
    """A run killed mid-pass still advances the rotation for the boards it reached."""

    substrate = _managed(tmp_path)
    home = substrate[0]
    path = home / "cache" / "scout" / "ats-boards" / "last-fetched.json"
    seen_at_third_call: dict = {}

    def check(n: int, token: str) -> None:
        if n == 3:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    payload = json.loads(path.read_text())
                except (OSError, ValueError):
                    payload = {}
                if "greenhouse:g0" in payload.get("boards", {}):
                    seen_at_third_call.update(payload)
                    return
                time.sleep(0.01)

    # Each board advances the clock 31 s: past ROTATION_FLUSH_INTERVAL_SECONDS
    # after the first settles, so g0's stamp is on disk before g2 runs.
    _run(substrate, _catalog_boards("g0", "g1", "g2", "g3"), ats=_ClockATS(clock, step=31.0, on_call=check), n=1)
    assert "greenhouse:g0" in seen_at_third_call.get("boards", {})
    assert seen_at_third_call["schema_version"] == LAST_FETCHED_SCHEMA


def test_ties_break_by_the_catalog_us_posting_count_then_provider_and_token() -> None:
    catalog = load_company_catalog()
    greenhouse = [r for r in catalog.records if r.provider is ATSProvider.GREENHOUSE and r.us_posting_count is not None]
    busiest = max(greenhouse, key=lambda r: (r.us_posting_count or 0, r.board_token))
    quiet = min(greenhouse, key=lambda r: (r.us_posting_count or 0, r.board_token))
    assert (busiest.us_posting_count or 0) > (quiet.us_posting_count or 0)
    entries = [r.to_watchlist_entry(revision=catalog.revision, observed_at="2026-09-25T00:00:00Z") for r in (quiet, busiest)]
    counts = {(r.provider.value, r.board_token): r.us_posting_count for r in catalog.records if r.us_posting_count is not None}
    plan = _plan_rotation(entries, index=BoardFetchIndex(), catalog_counts=counts, stamp="2026-09-25T00:00:00.000Z")
    assert [b.board_token for b in plan.ordered] == [busiest.board_token, quiet.board_token], "more US postings first"

    # Same stamp, same count: (provider, token) -- a plain string sort.
    same = [_board(ATSProvider.LEVER, "b", catalog=True), _board(ATSProvider.GREENHOUSE, "z", catalog=True), _board(ATSProvider.GREENHOUSE, "a", catalog=True)]
    plan = _plan_rotation(same, index=BoardFetchIndex(), catalog_counts=None, stamp="2026-09-25T00:00:00.000Z")
    assert [(b.provider.value, b.board_token) for b in plan.ordered] == [("greenhouse", "a"), ("greenhouse", "z"), ("lever", "b")]
    # A board the catalog does not know (no count) sorts after any counted one.
    known = _board(ATSProvider.GREENHOUSE, "known", catalog=True)
    unknown = _board(ATSProvider.GREENHOUSE, "a-unknown", catalog=True)
    plan = _plan_rotation([unknown, known], index=BoardFetchIndex(), catalog_counts={("greenhouse", "known"): 0}, stamp="x")
    assert [b.board_token for b in plan.ordered] == ["known", "a-unknown"]


def test_the_cursor_opens_a_new_cycle_only_once_every_board_is_covered() -> None:
    boards = _catalog_boards("a", "b", "c")
    fresh = _plan_rotation(boards, index=BoardFetchIndex(), catalog_counts=None, stamp="2026-09-25T00:00:01.000Z")
    assert (fresh.index.cycle, fresh.index.cycle_started_at, fresh.covered_before) == (1, "2026-09-25T00:00:01.000Z", 0)

    partly = BoardFetchIndex(boards={"greenhouse:a": "2026-09-25T00:00:01.000Z", "greenhouse:b": "2026-09-24T00:00:00.000Z"}, cycle=1, cycle_started_at="2026-09-25T00:00:01.000Z")
    plan = _plan_rotation(boards, index=partly, catalog_counts=None, stamp="2026-09-25T00:00:02.000Z")
    assert (plan.index.cycle, plan.covered_before) == (1, 1), "b's stamp predates the cycle: not covered"
    assert [b.board_token for b in plan.ordered] == ["c", "b", "a"]

    done = BoardFetchIndex(boards={f"greenhouse:{t}": "2026-09-25T00:00:01.000Z" for t in "abc"}, cycle=1, cycle_started_at="2026-09-25T00:00:01.000Z")
    plan = _plan_rotation(boards, index=done, catalog_counts=None, stamp="2026-09-25T00:00:02.000Z")
    assert (plan.index.cycle, plan.index.cycle_started_at, plan.covered_before) == (2, "2026-09-25T00:00:02.000Z", 0)


# --- the index file ---------------------------------------------------------


def test_the_index_round_trips_and_a_corrupt_or_foreign_file_restarts_the_rotation(tmp_path: Path) -> None:
    cache = BoardCache(tmp_path / "ats-boards")
    assert cache.load_fetch_index() == BoardFetchIndex(), "missing -> empty, never an error"
    index = BoardFetchIndex(boards={"lever:acme": "2026-09-25T00:00:00.000Z"}, cycle=3, cycle_started_at="2026-09-25T00:00:00.000Z", page_sizes={"lever": 12})
    cache.store_fetch_index(index)
    assert cache.load_fetch_index() == index
    payload = json.loads(cache.fetch_index_path.read_text())
    assert payload["schema_version"] == LAST_FETCHED_SCHEMA
    assert not list(cache.root.glob("*.tmp*")), "temp file + os.replace, nothing left behind"

    cache.fetch_index_path.write_text("{not json")
    assert cache.load_fetch_index() == BoardFetchIndex()
    cache.fetch_index_path.write_text(json.dumps({"schema_version": "something-else:9", "boards": {"lever:acme": "x"}}))
    assert cache.load_fetch_index() == BoardFetchIndex()
    # Malformed entries inside a well-formed file are dropped, not fatal.
    cache.fetch_index_path.write_text(json.dumps({"schema_version": LAST_FETCHED_SCHEMA, "cycle": "3", "boards": {"lever:acme": 5, "ashby:x": "ok"}, "page_sizes": {"lever": -1, "ashby": 2}}))
    assert cache.load_fetch_index() == BoardFetchIndex(boards={"ashby:x": "ok"}, cycle=1, page_sizes={"ashby": 2})


def test_without_a_home_nothing_persists_and_the_order_is_user_first_then_provider_token(clock: _Clock, tmp_path: Path) -> None:
    boards = _catalog_boards("z", "a") + [_board(ATSProvider.LEVER, "mine")]
    ats = _ClockATS(clock)
    acquire_node(_context(tmp_path), _input(), http_client=None, exa=_Exa(), ats=ats, watchlist=_Watchlist(boards), limits=_limits(concurrency=1))
    assert ats.calls[0] == "mine" and set(ats.calls[1:]) == {"a", "z"}
    assert not list(tmp_path.rglob("last-fetched.json"))
    rotation = read_progress(tmp_path / "runs" / "run_01").boards["rotation"]
    assert rotation["total"] == 3 and rotation["first"] == 1 and rotation["last"] == 3 and rotation["runs_per_rotation"] == 1


def test_the_progress_writer_keeps_rotation_across_planned_and_finished(tmp_path: Path) -> None:
    writer = ProgressWriter(tmp_path / "runs" / "run_01")
    writer.boards_planned(total=3, budget_seconds=None)
    assert "rotation" not in read_progress(tmp_path / "runs" / "run_01").boards, "additive: absent when acquire passes none"
    planned = {"cycle": 1, "total": 3, "first": 1, "last": None, "estimated": True}
    writer.boards_planned(total=3, budget_seconds=None, rotation=planned)
    assert read_progress(tmp_path / "runs" / "run_01").boards["rotation"] == planned
    writer.boards_finished({"total": 3, "fetched": 3, "rotation": {**planned, "last": 3, "estimated": False}})
    assert read_progress(tmp_path / "runs" / "run_01").boards["rotation"]["last"] == 3

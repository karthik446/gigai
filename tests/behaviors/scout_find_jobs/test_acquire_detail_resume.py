"""gh-detail-resume: a Greenhouse board cut mid-detail resumes from the cache next run.

Pins what a second run REQUESTS after the first one was cut during a board's
detail phase. ``_cached_request`` stores every response as it arrives (the
list before any detail, each detail under its job's ``updated_at`` marker),
so nothing already fetched is fetched again:

* the time budget only gates a board's START: a board in flight at the
  deadline finishes its details, and the next run is one ``304``;
* a board interrupted mid-detail (it fails) or a pass killed mid-detail
  leaves its fetched details in the cache: the next run gets a ``304`` on the
  list and requests only the missing details;
* a failed board is stamped (attempted) and rotates; a killed pass stamps
  nothing, so its board leads the next run;
* under a budget the rotation serves never-fetched boards first, so the
  boards a run fetched are SKIPPED (not refetched) by the next budget-bound
  run and get their ``304`` once the rotation reaches them again.

Real ``ATSBoardClients`` + ``BoardCache`` through ``acquire_node`` over an
``httpx.MockTransport``; the transport advances a fake clock one tick per
request (``market_acquisition.time`` monkeypatched), so the budget cut is
deterministic. No network.
"""

from __future__ import annotations

import threading
from pathlib import Path

import httpx
import pytest

from gigai.scout.find_jobs.ats_board_clients import ATSBoardClients
from gigai.scout.find_jobs.contracts import ATSProvider
from gigai.scout.find_jobs.market_acquisition import BOARDS_FROM_FETCH, BUDGET_EXCEEDED_CODE, acquire_node
from gigai.scout.find_jobs.progress import read_progress

from tests.behaviors.scout_find_jobs.test_acquire_rotation import _Clock, _index
from tests.behaviors.scout_find_jobs.test_acquire_scale import (
    _Exa,
    _Watchlist,
    _board,
    _input,
    _limits,
    _managed,
    _patch_import,
    _real_context,
)
from tests.support.workpad_assertions import assert_managed_workpad_clean


class _Killed(BaseException):
    """Stands in for the process dying mid-pass (not an ``Exception``: nothing catches it)."""


class _Transport:
    """Greenhouse boards of ``n`` matching jobs: an ETag'd content-free list, one detail per job id.

    Every request costs one clock tick. ``fail_at``/``kill_at`` = ``(token,
    job id)`` whose detail request raises instead of answering.
    """

    def __init__(self, clock: _Clock, jobs_per_board: dict[str, int], *, fail_at=None, kill_at=None):
        self.clock = clock
        self.jobs_per_board = jobs_per_board
        self.fail_at = fail_at
        self.kill_at = kill_at
        self.lock = threading.Lock()
        self.requests: dict[str, list[str]] = {}

    def _log(self, token: str, what: str) -> None:
        with self.lock:
            self.requests.setdefault(token, []).append(what)

    def handler(self, request: httpx.Request) -> httpx.Response:
        parts = request.url.path.split("/")  # /v1/boards/{token}/jobs[/{id}]
        token = parts[3]
        count = self.jobs_per_board[token]
        self.clock.tick()
        if len(parts) == 5:
            etag = f'W/"{token}-{count}"'
            if request.headers.get("if-none-match") == etag:
                self._log(token, "list:304")
                return httpx.Response(304, headers={"etag": etag})
            self._log(token, "list:200")
            jobs = [
                {
                    "id": i, "title": "Software Engineer", "absolute_url": f"https://boards.greenhouse.io/{token}/jobs/{i}",
                    "location": {"name": "Denver, CO"}, "updated_at": "2026-09-20T00:00:00Z",
                }
                for i in range(count)
            ]
            return httpx.Response(200, json={"jobs": jobs}, headers={"etag": etag})
        job_id = int(parts[5])
        if self.kill_at == (token, job_id):
            raise _Killed()
        if self.fail_at == (token, job_id):
            raise RuntimeError("transport broke mid-detail")
        self._log(token, f"detail:{job_id}")
        return httpx.Response(
            200,
            json={"id": job_id, "title": "Software Engineer", "updated_at": "2026-09-20T00:00:00Z", "content": f"&lt;p&gt;Build {job_id} for {token}.&lt;/p&gt;"},
        )


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    _patch_import(monkeypatch)
    fake = _Clock()
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.time", fake)
    return fake


def _greenhouse(*tokens: str) -> list:
    return [_board(ATSProvider.GREENHOUSE, token, catalog=True) for token in tokens]


def _run(substrate, boards, transport: _Transport, *, n: int, budget: float | None = None):
    """One acquire pass from clock 0 (each run gets its whole budget)."""

    home, target, workpad, gig_id = substrate
    transport.clock.now = 0.0
    with httpx.Client(transport=httpx.MockTransport(transport.handler)) as client:
        return acquire_node(
            _real_context(home, target, workpad, gig_id, key=f"detail-resume-{n}", run_id=f"run_{n:02d}"),
            _input(),
            http_client=client,
            exa=_Exa(),
            ats=ATSBoardClients(),
            watchlist=_Watchlist(boards),
            home_root=home,
            target=target,
            limits=_limits(concurrency=1, budget=budget),
            boards_from=BOARDS_FROM_FETCH,
        )


def _boards_block(workpad: Path, n: int) -> dict:
    return read_progress(workpad / "runs" / f"run_{n:02d}").boards


def test_a_board_in_flight_at_the_deadline_finishes_and_the_next_run_is_one_304(clock: _Clock, tmp_path: Path) -> None:
    """The budget runs out during the detail phase: nothing is cut, nothing repeats."""

    substrate = _managed(tmp_path)
    home, _target, workpad, _gig_id = substrate
    boards = _greenhouse("big")

    # List at tick 0-1, six details after it; the budget ends at 3.5, between
    # the second and third detail.
    first = _Transport(clock, {"big": 6})
    out = _run(substrate, boards, first, n=1, budget=3.5)
    assert first.requests == {"big": ["list:200"] + [f"detail:{i}" for i in range(6)]}
    assert clock.now == 7.0, "the board stopped at the deadline instead of finishing"
    assert len(out.rows) == 6 and all(row.posting.text for row in out.rows)
    assert out.failures == ()
    run1 = _boards_block(workpad, 1)
    assert run1["fetched"] == 1 and run1["skipped"] == 0 and run1["detail_fetched"] == 6

    second = _Transport(clock, {"big": 6})
    again = _run(substrate, boards, second, n=2, budget=3.5)
    assert second.requests == {"big": ["list:304"]}
    assert [row.posting.content_sha256 for row in again.rows] == [row.posting.content_sha256 for row in out.rows]
    run2 = _boards_block(workpad, 2)
    assert run2["cached"] == 1 and run2["requests"] == 1
    assert run2["detail_cached"] == 6 and run2["detail_fetched"] == 0
    assert "greenhouse:big" in _index(home).boards
    assert_managed_workpad_clean(workpad)


def test_a_board_interrupted_mid_detail_refetches_only_the_missing_details(clock: _Clock, tmp_path: Path) -> None:
    """An exception at detail 3 of 6 fails the board; details 0-2 are already in the cache."""

    substrate = _managed(tmp_path)
    home, _target, workpad, _gig_id = substrate
    boards = _greenhouse("big", "other")
    jobs = {"big": 6, "other": 1}

    first = _Transport(clock, jobs, fail_at=("big", 3))
    out = _run(substrate, boards, first, n=1)
    assert first.requests == {"big": ["list:200", "detail:0", "detail:1", "detail:2"], "other": ["list:200", "detail:0"]}
    assert [row.posting.board_token for row in out.rows] == ["other"]
    assert [(failure.query_key, failure.code) for failure in out.failures] == [("big", "runtimeerror")]
    # A failed board is an attempted board: stamped, so it rotates like a live one.
    stamps = _index(home).boards
    assert set(stamps) == {"greenhouse:big", "greenhouse:other"}

    second = _Transport(clock, jobs)
    again = _run(substrate, boards, second, n=2)
    assert second.requests == {"big": ["list:304", "detail:3", "detail:4", "detail:5"], "other": ["list:304"]}
    assert [row.posting.board_token for row in again.rows] == ["big"] * 6 + ["other"]
    assert all(row.posting.text for row in again.rows)
    assert again.failures == ()
    run2 = _boards_block(workpad, 2)
    assert run2["requests"] == 5
    assert run2["detail_cached"] == 4 and run2["detail_fetched"] == 3
    assert_managed_workpad_clean(workpad)


def test_a_killed_pass_resumes_from_the_cache_and_its_board_leads_the_next_run(clock: _Clock, tmp_path: Path) -> None:
    """The pass dies at detail 3 of 6: no stamp is written, the fetched details stay cached."""

    substrate = _managed(tmp_path)
    home, _target, workpad, _gig_id = substrate
    boards = _greenhouse("done", "big")

    # Run 1 covers `done` only, so it carries a stamp and `big` does not.
    _run(substrate, _greenhouse("done"), _Transport(clock, {"done": 1}), n=1)
    assert set(_index(home).boards) == {"greenhouse:done"}

    killed = _Transport(clock, {"done": 1, "big": 6}, kill_at=("big", 3))
    with pytest.raises(_Killed):
        _run(substrate, boards, killed, n=2)
    assert killed.requests["big"] == ["list:200", "detail:0", "detail:1", "detail:2"]
    assert set(_index(home).boards) == {"greenhouse:done"}, "a board cut by a killed pass was stamped as attempted"

    # A budget that fits one board: the unstamped, cut board is the one taken.
    resumed = _Transport(clock, {"done": 1, "big": 6})
    out = _run(substrate, boards, resumed, n=3, budget=0.5)
    assert resumed.requests == {"big": ["list:304", "detail:3", "detail:4", "detail:5"]}
    assert [row.posting.board_token for row in out.rows] == ["big"] * 6
    assert all(row.posting.text for row in out.rows)
    run3 = _boards_block(workpad, 3)
    assert run3["skipped_boards"] == ["greenhouse:done"]
    assert run3["detail_cached"] == 3 and run3["detail_fetched"] == 3
    assert set(_index(home).boards) == {"greenhouse:done", "greenhouse:big"}
    assert_managed_workpad_clean(workpad)


def test_boards_fetched_under_a_budget_are_skipped_not_refetched_by_the_next_run(clock: _Clock, tmp_path: Path) -> None:
    """Zero ``304``s in a budget-bound second run is the rotation, not a cache miss.

    catalog-repin's observation: four boards of four requests each, a budget
    that fits two. Run 2 takes the two never-fetched boards and the budget
    skips the two that run 1 fetched -- no request at all for them. Run 3
    fits everything: one ``304`` per board, no detail request.
    """

    substrate = _managed(tmp_path)
    _home, _target, workpad, _gig_id = substrate
    boards = _greenhouse("g0", "g1", "g2", "g3")
    jobs = {token: 3 for token in ("g0", "g1", "g2", "g3")}
    cold = ["list:200", "detail:0", "detail:1", "detail:2"]

    first = _Transport(clock, jobs)
    _run(substrate, boards, first, n=1, budget=7.5)
    assert first.requests == {"g0": cold, "g1": cold}

    second = _Transport(clock, jobs)
    out = _run(substrate, boards, second, n=2, budget=7.5)
    assert second.requests == {"g2": cold, "g3": cold}
    assert [failure.code for failure in out.failures] == [BUDGET_EXCEEDED_CODE]
    run2 = _boards_block(workpad, 2)
    assert sorted(run2["skipped_boards"]) == ["greenhouse:g0", "greenhouse:g1"]
    assert run2["cached"] == 0 and run2["cache_hits"] == 0

    third = _Transport(clock, jobs)
    _run(substrate, boards, third, n=3, budget=7.5)
    assert third.requests == {token: ["list:304"] for token in ("g0", "g1", "g2", "g3")}
    run3 = _boards_block(workpad, 3)
    assert run3["cached"] == 4 and run3["skipped"] == 0
    assert run3["detail_cached"] == 12 and run3["detail_fetched"] == 0

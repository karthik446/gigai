"""0.1.10.11 S9 (cause C8): a new UTC day drops the postings that left the posted window, and matches no board for it.

The UTC day was part of every board's match stamp, so the first read of the
Jobs list after 00:00 UTC matched every watched board again: 9 to 13 s on the
operator-sized home (10,350 boards), every day, and ``POST /api/new`` and
"Assess these" waited for it.

The posted window (``max_age_days``, 60 days here) is the reason the day was
in the stamp, and its rule is unchanged: a posting published before
``now - max_age_days`` at the first read of a UTC day is not listed. What
changed is how much is read to apply it. These tests hold both edges with a
clock (the ``now`` every read takes):

* a posting just INSIDE the window before and after midnight stays, and the
  new day matches NO board;
* a posting inside before midnight and outside after it leaves: its row is
  dropped by the search's own rule on its board's index entry, no board is
  matched, and the watched boards are not read from the journal (what a fresh
  process paid 4 s for on the operator-sized home);
* a posting just OUTSIDE the window is listed neither before nor after;
* when more than the day moved (a company's update), that company's board and
  the boards holding a posting that left are matched, and no other;
* at every step the stored rows are the rows a match of every board at that
  moment gives (``_matched_from_scratch``: the search's own rule, nothing
  stored used).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gigai.scout import postings
from gigai.scout.find_jobs import watchlist
from gigai.scout.find_jobs.contracts import DEFAULT_MAX_AGE_DAYS

from tests.support.posting_fixtures import NOW, TITLE_BOTH, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job

from .test_posting_model_single_flight import _Matches

WINDOW = timedelta(days=DEFAULT_MAX_AGE_DAYS)
#: The fixture's day is 2026-10-03; the next UTC day starts here.
MIDNIGHT = datetime(2026, 10, 4, 0, 0, tzinfo=UTC)
BEFORE = NOW  # 15:00 the day before
AFTER = MIDNIGHT + timedelta(hours=1)
LATER_SAME_DAY = MIDNIGHT + timedelta(hours=3)
NEXT_DAY = MIDNIGHT + timedelta(days=1, minutes=30)

#: Inside the window before midnight AND one hour after it; outside two hours later (the same UTC day) and the day after.
STAYS = MIDNIGHT - WINDOW + timedelta(hours=2)
#: Inside before midnight, outside one hour after it.
LEAVES = MIDNIGHT - WINDOW - timedelta(hours=2)
#: Outside already before midnight.
NEVER = BEFORE - WINDOW - timedelta(hours=1)
FRESH = days_ago(3)

QUIET = ("wq00", "wq01", "wq02")  # boards whose postings are nowhere near the edge


def _seed(fx: PostingsFixture) -> None:
    for slug in QUIET:
        fx.seed(slug, [lever_job(slug, n, title=TITLE_BOTH, created=FRESH) for n in range(2)], seen_at=days_ago(1))
    fx.seed("wstays", [lever_job("wstays", 0, title=TITLE_BOTH, created=STAYS), lever_job("wstays", 1, title=TITLE_BOTH, created=FRESH)], seen_at=days_ago(1))
    fx.seed("wleaves", [lever_job("wleaves", 0, title=TITLE_BOTH, created=LEAVES), lever_job("wleaves", 1, title=TITLE_BOTH, created=FRESH)], seen_at=days_ago(1))
    fx.seed("wnever", [lever_job("wnever", 0, title=TITLE_BOTH, created=NEVER), lever_job("wnever", 1, title=TITLE_BOTH, created=FRESH)], seen_at=days_ago(1))


def _listed(fx: PostingsFixture) -> set[tuple[str, str]]:
    store = postings.open_store(fx.home_root, fx.target)
    try:
        return {(row.job, row.profile_id) for row in store.postings()}
    finally:
        store.close()


def _stamps(fx: PostingsFixture) -> dict[str, dict[str, str]]:
    store = postings.open_store(fx.home_root, fx.target)
    try:
        return {profile_id: store.posting_board_stamps(profile_id) for profile_id in (fx.default_profile_id, fx.second_profile_id)}
    finally:
        store.close()


def _jobs(fx: PostingsFixture) -> set[str]:
    return {job for job, _profile in _listed(fx)}


def _matched_from_scratch(fx: PostingsFixture, moment: datetime) -> set[tuple[str, str]]:
    """The rows a match of EVERY board at ``moment`` gives: the search's own rule, nothing stored used."""

    _resolved, views = postings.active_profiles(fx.home_root, fx.target)
    found: set[tuple[str, str]] = set()
    for view in views:
        for row in postings.profile_posting_rows(view, fx.home_root, fx.target, moment):
            found.add((row.normalized_url, view.profile_id))  # type: ignore[attr-defined]
    return found


def _fresh_process(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """What a new process starts with (nothing kept in memory); the list gets an item for every read of the watchlist from the journal."""

    postings._BOARDS.clear()
    postings._FLIGHTS.clear()
    postings._TEXTS.clear()
    reads: list[int] = []
    real = watchlist.list_active

    def counting(*args: object, **kwargs: object):
        reads.append(1)
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(watchlist, "list_active", counting)
    return reads


def test_a_new_utc_day_reads_no_board_when_no_posting_left_the_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    for slug in QUIET:
        fx.seed(slug, [lever_job(slug, n, title=TITLE_BOTH, created=FRESH) for n in range(2)], seen_at=days_ago(1))
    fx.seed("wstays", [lever_job("wstays", 0, title=TITLE_BOTH, created=STAYS)], seen_at=days_ago(1))
    matches = _Matches(monkeypatch)
    built = postings.refresh(fx.home_root, fx.target, now=BEFORE)
    assert sorted(set(matches.boards[0])) == sorted(f"lever:{slug}" for slug in (*QUIET, "wstays"))
    before = _listed(fx)
    assert job_url("wstays", 0) in _jobs(fx)

    watchlist_reads = _fresh_process(monkeypatch)
    after = postings.refresh(fx.home_root, fx.target, now=AFTER)  # 01:00 UTC the next day
    read = [board for build in matches.boards[1:] for board in build]
    assert read == [], f"the day changed and no posting left the window, yet these boards were matched again: {sorted(set(read))}"
    assert watchlist_reads == [], "the day changed and nothing was matched, yet the watched boards were read from the journal"
    assert after.rows == built.rows and _listed(fx) == before == _matched_from_scratch(fx, AFTER)
    # The day is settled: the next reads of that day are plain reads.
    builds = len(matches.boards)
    assert set(postings.refresh(fx.home_root, fx.target, now=AFTER + timedelta(minutes=5)).builds.values()) == {postings.BUILD_UNCHANGED}
    assert len(matches.boards) == builds
    # The server's read (a ``wait``): answered with the rows, and what it started matched nothing.
    served = postings.refresh(fx.home_root, fx.target, now=NEXT_DAY - timedelta(hours=20), wait=30.0)
    assert served.rows == built.rows and postings.model_status(fx.home_root, fx.target)["last_boards"] == 0


def test_both_edges_of_the_window_before_and_after_midnight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _seed(fx)
    matches = _Matches(monkeypatch)

    # Before midnight: just inside (both), just outside (never).
    postings.refresh(fx.home_root, fx.target, now=BEFORE)
    assert _listed(fx) == _matched_from_scratch(fx, BEFORE)
    jobs = _jobs(fx)
    assert {job_url("wstays", 0), job_url("wleaves", 0)} <= jobs and job_url("wnever", 0) not in jobs
    assert len(set(matches.boards[0])) == len(QUIET) + 3

    # One hour after midnight UTC, in a fresh process: ``wleaves-0`` is now outside, ``wstays-0`` still inside.
    stamps_before = _stamps(fx)
    watchlist_reads = _fresh_process(monkeypatch)
    postings.refresh(fx.home_root, fx.target, now=AFTER)
    read = sorted({board for build in matches.boards[1:] for board in build})
    assert read == [], f"a posting left the window: its row is dropped, no board is matched again, not {read}"
    assert watchlist_reads == [], "only the day moved, yet the watched boards were read from the journal"
    assert _stamps(fx) == stamps_before  # every board is still as it was matched
    assert _listed(fx) == _matched_from_scratch(fx, AFTER)
    jobs = _jobs(fx)
    assert job_url("wstays", 0) in jobs and job_url("wleaves", 0) not in jobs and job_url("wnever", 0) not in jobs
    assert {job_url("wleaves", 1), job_url("wnever", 1)} <= jobs  # the boards' other postings stay

    # Later the same UTC day ``wstays-0`` is past the window too. The window is applied once a day (as it was before):
    # it stays listed until the first read of the next day, and nothing is matched.
    builds = len(matches.boards)
    assert set(postings.refresh(fx.home_root, fx.target, now=LATER_SAME_DAY).builds.values()) == {postings.BUILD_UNCHANGED}
    assert len(matches.boards) == builds and job_url("wstays", 0) in _jobs(fx)

    # The next day: it leaves too. The server's read (a ``wait``) is answered with the rows, and nothing was matched.
    watchlist_reads.clear()  # the checks above read the watched boards themselves (``_matched_from_scratch``)
    served = postings.refresh(fx.home_root, fx.target, now=NEXT_DAY, wait=30.0)
    assert [board for build in matches.boards[builds:] for board in build] == [] and watchlist_reads == []
    assert postings.model_status(fx.home_root, fx.target)["last_boards"] == 0
    assert _listed(fx) == _matched_from_scratch(fx, NEXT_DAY) and served.rows == len(_listed(fx))
    assert job_url("wstays", 0) not in _jobs(fx) and job_url("wstays", 1) in _jobs(fx)


def test_a_clock_that_goes_back_a_day_matches_every_board_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A posting the window had left can be inside it again only when the clock goes back: then every board is matched, as before."""

    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _seed(fx)
    matches = _Matches(monkeypatch)
    postings.refresh(fx.home_root, fx.target, now=AFTER)
    assert job_url("wleaves", 0) not in _jobs(fx)

    postings.refresh(fx.home_root, fx.target, now=BEFORE)  # the day before
    assert len({board for build in matches.boards[1:] for board in build}) == len(QUIET) + 3
    assert _listed(fx) == _matched_from_scratch(fx, BEFORE) and job_url("wleaves", 0) in _jobs(fx)


def test_a_posting_whose_index_entry_is_gone_sends_its_board_to_a_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The window pass settles a row only on its board's index entry. Without one, that board (and no other) is matched the long way."""

    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _seed(fx)
    matches = _Matches(monkeypatch)
    postings.refresh(fx.home_root, fx.target, now=BEFORE)
    path = fx.index.path("lever", "wleaves")
    found = path.stat()
    path.unlink()
    monkeypatch.setattr(postings, "_index_files", lambda home_root, real=postings._index_files: {**real(home_root), path.name: (found.st_mtime_ns, found.st_size)})

    postings.refresh(fx.home_root, fx.target, now=AFTER)
    assert sorted({board for build in matches.boards[1:] for board in build}) == ["lever:wleaves"]
    assert job_url("wleaves", 0) not in _jobs(fx) and job_url("wstays", 0) in _jobs(fx)


def test_a_posting_without_a_date_is_never_dropped_by_the_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    undated = lever_job("wundated", 0, title=TITLE_BOTH)
    del undated["createdAt"]
    fx.seed("wundated", [undated, lever_job("wundated", 1, title=TITLE_BOTH, created=LEAVES)], seen_at=days_ago(1))
    matches = _Matches(monkeypatch)
    postings.refresh(fx.home_root, fx.target, now=BEFORE)
    assert {job_url("wundated", 0), job_url("wundated", 1)} <= _jobs(fx)

    postings.refresh(fx.home_root, fx.target, now=AFTER + timedelta(days=400))
    assert [board for build in matches.boards[1:] for board in build] == []
    assert job_url("wundated", 0) in _jobs(fx) and job_url("wundated", 1) not in _jobs(fx)
    assert _listed(fx) == _matched_from_scratch(fx, AFTER + timedelta(days=400))


def test_a_board_whose_index_file_changed_is_still_matched_on_a_new_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _seed(fx)
    matches = _Matches(monkeypatch)
    postings.refresh(fx.home_root, fx.target, now=BEFORE)

    fx.seed("wq01", [lever_job("wq01", n, title=TITLE_BOTH, created=FRESH) for n in range(4)], seen_at=days_ago(0.2), watch=False)
    postings.refresh(fx.home_root, fx.target, now=AFTER)
    assert sorted({board for build in matches.boards[1:] for board in build}) == ["lever:wleaves", "lever:wq01"]
    assert _listed(fx) == _matched_from_scratch(fx, AFTER)
    assert {job_url("wq01", n) for n in range(4)} <= _jobs(fx)

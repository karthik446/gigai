"""0110-9-01: the posting read model is built once, shared, incremental and kept between processes.

On the operator's home (290,000 postings, 10,350 companies) every request
matched the whole index itself, and so did the pipeline's rank lane each turn:
twelve requests were thirteen builds at once and none of them answered. These
are the rules that keep that from coming back, on a small synthetic home (the
operator-sized numbers are ``test_operator_sized_home.py``'s):

1. ONE build for callers that arrive together; the others wait and read it.
2. A caller with a ``wait`` is never held by a large build: it gets the rows
   as stored (``stale``) or ``PostingModelPreparing`` with the progress, and
   :func:`postings.model_status` says the same from memory.
3. INCREMENTAL: after one company's index file changes, that one board is
   matched again and no other.
4. The tag store is compared by what it holds: a reader's open connection (its
   ``-wal`` file) does not make the model look changed.
5. A fresh process does not read the watchlist from the journal to learn that
   nothing changed, and a page's posting text is not read from the index again
   while the board's files are the same.
6. The rank lane's rows come from the boards its unranked rows are on, and
   are the rows the whole-index match gives.
"""

from __future__ import annotations

from pathlib import Path
import threading
import time

import pytest

from gigai.scout import postings
from gigai.scout.find_jobs import company_index, title_query
from gigai.scout.find_jobs.posting_tags import default_store, tag_new_titles

from tests.support.posting_fixtures import NOW, TITLE_BOTH, TITLE_SECOND_ONLY, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job

BOARDS = 6
PER_BOARD = 4


def _seed(fx: PostingsFixture) -> None:
    for board in range(BOARDS):
        slug = f"sf{board:02d}"
        jobs = [lever_job(slug, n, title=TITLE_BOTH if n % 2 else TITLE_SECOND_ONLY) for n in range(PER_BOARD)]
        fx.seed(slug, jobs, seen_at=days_ago(1))


def _refresh(fx: PostingsFixture, **kwargs: object) -> postings.RefreshResult:
    return postings.refresh(fx.home_root, fx.target, now=NOW, **kwargs)  # type: ignore[arg-type]


def _flight(fx: PostingsFixture) -> postings._Flight:
    return postings._flight(fx.home_root, fx.target)


class _Matches:
    """Every build that matches the index while it is open: how many, and the boards each one read."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, hold: threading.Event | None = None) -> None:
        self.boards: list[list[str]] = []
        self.started = threading.Event()
        real = postings._match

        def counting(plan, store, views, facts_of, progress):
            read: list[str] = []
            self.boards.append(read)

            def report(phase: str, done: int, total: int) -> None:
                if progress is not None:
                    progress(phase, done, total)
                if total and not self.started.is_set():
                    self.started.set()
                    if hold is not None:
                        assert hold.wait(30)

            from gigai.scout.find_jobs import index_search

            real_read = index_search.read_indexed_boards

            def reading(boards, **kwargs):
                read.extend(f"{board.provider.value}:{board.board_token}" for board in boards)
                return real_read(boards, **kwargs)

            monkeypatch.setattr(index_search, "read_indexed_boards", reading)
            try:
                return real(plan, store, views, facts_of, report)
            finally:
                monkeypatch.setattr(index_search, "read_indexed_boards", real_read)
                self.started.set()

        monkeypatch.setattr(postings, "_match", counting)


def test_eight_callers_at_once_are_one_build_and_all_read_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _seed(fx)
    matches = _Matches(monkeypatch)
    results: list[object] = []
    barrier = threading.Barrier(8)

    def call() -> None:
        barrier.wait(10)
        try:
            results.append(_refresh(fx))
        except BaseException as exc:  # noqa: BLE001 - the assertion below shows it
            results.append(exc)

    threads = [threading.Thread(target=call) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)

    assert len(results) == 8 and all(isinstance(item, postings.RefreshResult) for item in results), results
    assert len(matches.boards) == 1 and _flight(fx).builds == 1  # ONE build, whoever started it
    kinds = sorted(tuple(sorted(set(item.builds.values()))) for item in results)  # type: ignore[union-attr]
    assert kinds.count((postings.BUILD_FULL,)) == 1 and kinds.count((postings.BUILD_UNCHANGED,)) == 7
    assert {item.rows for item in results} == {BOARDS * PER_BOARD + BOARDS * PER_BOARD // 2}  # type: ignore[union-attr]


def test_a_caller_with_a_wait_is_not_held_by_a_large_build_and_sees_its_progress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _seed(fx)
    monkeypatch.setattr(postings, "SMALL_BUILD_BOARDS", 2)  # six boards are a large build here
    assert postings.model_status(fx.home_root, fx.target)["state"] == postings.STATE_UNKNOWN
    release = threading.Event()
    matches = _Matches(monkeypatch, hold=release)

    started = time.monotonic()
    with pytest.raises(postings.PostingModelPreparing) as caught:
        _refresh(fx, wait=30.0)
    assert time.monotonic() - started < 10  # the build is held open: the caller was answered without it
    assert caught.value.code == "preparing"
    progress = caught.value.progress
    assert progress["state"] == postings.STATE_PREPARING and progress["boards_total"] == BOARDS and progress["builds"] == 1
    assert progress["schema_version"] == postings.STATUS_SCHEMA_VERSION and 0 <= progress["percent"] < 100
    # A second and a third caller during the build: the same answer, at once, and no second build.
    for _ in range(2):
        with pytest.raises(postings.PostingModelPreparing):
            _refresh(fx, wait=0.0)
    assert postings.model_status(fx.home_root, fx.target)["state"] == postings.STATE_PREPARING
    assert len(matches.boards) == 1

    release.set()
    done = _refresh(fx)  # a caller without a wait waits for the build, then reads it
    assert set(done.builds.values()) == {postings.BUILD_UNCHANGED} and done.rows > 0
    status = postings.model_status(fx.home_root, fx.target)
    assert status["state"] == postings.STATE_READY and status["percent"] == 100 and status["builds"] == 1

    # With stored rows, a build that runs is served from them: "stale", never "preparing".
    fx.seed("sf00", [lever_job("sf00", n) for n in range(PER_BOARD + 1)], seen_at=days_ago(0.5), watch=False)
    release.clear()
    monkeypatch.setattr(postings, "SMALL_BUILD_BOARDS", 0)
    matches.started.clear()
    served = _refresh(fx, wait=0.0, force=True)
    assert set(served.builds.values()) == {postings.BUILD_STALE} and served.rows == done.rows
    assert postings.model_status(fx.home_root, fx.target)["state"] == postings.STATE_REFRESHING
    release.set()
    assert _refresh(fx).rows > done.rows


def test_a_small_build_is_waited_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _seed(fx)
    built = _refresh(fx, wait=30.0)  # six boards: under SMALL_BUILD_BOARDS, so the caller gets the rows, not a 202
    assert set(built.builds.values()) <= {postings.BUILD_FULL, postings.BUILD_UNCHANGED} and built.rows > 0
    assert _flight(fx).builds == 1


def test_only_the_board_whose_index_file_changed_is_matched_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _seed(fx)
    matches = _Matches(monkeypatch)
    first = _refresh(fx)
    assert sorted(set(matches.boards[0])) == [f"lever:sf{board:02d}" for board in range(BOARDS)]

    fx.seed("sf03", [lever_job("sf03", n) for n in range(PER_BOARD + 2)], seen_at=days_ago(0.5), watch=False)
    second = _refresh(fx)
    assert set(second.builds.values()) == {postings.BUILD_FULL}
    assert sorted(set(matches.boards[1])) == ["lever:sf03"]  # one board read, the other five left as stored
    assert second.rows == first.rows + 2 * 2 + PER_BOARD // 2  # sf03: every posting now matches both profiles
    store = postings.open_store(fx.home_root, fx.target)
    try:
        rows = store.postings(live=False)
        assert {row.job for row in rows if row.board == "lever:sf03"} == {job_url("sf03", n) for n in range(PER_BOARD + 2)}
        assert len({row.job for row in rows if row.board == "lever:sf01"}) == PER_BOARD  # untouched
        stamps = store.posting_board_stamps(fx.default_profile_id)
    finally:
        store.close()
    assert len(stamps) == BOARDS

    assert set(_refresh(fx).builds.values()) == {postings.BUILD_UNCHANGED} and len(matches.boards) == 2
    # The full rebuild still reads every board.
    _refresh(fx, force=True)
    assert len(set(matches.boards[2])) == BOARDS


def test_an_open_tag_store_connection_does_not_make_the_model_look_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _seed(fx)
    store = default_store(fx.home_root)
    tag_new_titles(store, [TITLE_BOTH, TITLE_SECOND_ONLY])
    store.close()
    matches = _Matches(monkeypatch)
    _refresh(fx)
    assert len(matches.boards) == 1

    # Another reader holds the store open (SQLite keeps a ``-wal`` file while it does), as a second request thread does.
    held = default_store(fx.home_root)
    assert held.count() == 2
    try:
        assert set(_refresh(fx).builds.values()) == {postings.BUILD_UNCHANGED}
    finally:
        held.close()
    assert set(_refresh(fx).builds.values()) == {postings.BUILD_UNCHANGED}
    assert len(matches.boards) == 1

    # A tag that changes is seen: the model is matched again.
    writer = default_store(fx.home_root)
    assert writer.set_model_function(title_query.normalize_title(TITLE_SECOND_ONLY), "security", model="m", prompt_version="p")
    writer.close()
    assert set(_refresh(fx).builds.values()) == {postings.BUILD_FULL} and len(matches.boards) == 2


def test_a_tag_change_right_after_a_build_is_served_as_stored_and_a_companys_update_is_not_held_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _seed(fx)
    store = default_store(fx.home_root)
    tag_new_titles(store, [TITLE_BOTH, TITLE_SECOND_ONLY])
    store.close()
    matches = _Matches(monkeypatch)
    built = _refresh(fx, wait=30.0)
    assert len(matches.boards) == 1

    # A model tags a title: EVERY board would be matched again. A caller with a wait (the server) gets the rows as stored,
    # and no build, until REBUILD_MIN_SECONDS after the last one; a caller without one (the CLI) gets the exact rows.
    writer = default_store(fx.home_root)
    assert writer.set_model_function(title_query.normalize_title(TITLE_SECOND_ONLY), "security", model="m", prompt_version="p")
    writer.close()
    served = _refresh(fx, wait=30.0)
    assert set(served.builds.values()) == {postings.BUILD_STALE} and served.rows == built.rows and len(matches.boards) == 1

    # One company's update is never held back: its board is matched at once (and only it).
    fx.seed("sf02", [lever_job("sf02", n) for n in range(PER_BOARD + 1)], seen_at=days_ago(0.5), watch=False)
    monkeypatch.setattr(postings, "REBUILD_MIN_SECONDS", 0.0)  # the minute has passed
    exact = _refresh(fx, wait=30.0)
    assert set(exact.builds.values()) <= {postings.BUILD_FULL, postings.BUILD_UNCHANGED} and len(matches.boards) == 2
    assert len(set(matches.boards[1])) == BOARDS  # the tag change: every board
    monkeypatch.setattr(postings, "REBUILD_MIN_SECONDS", 3600.0)
    fx.seed("sf04", [lever_job("sf04", n) for n in range(PER_BOARD + 1)], seen_at=days_ago(0.4), watch=False)
    one = _refresh(fx, wait=30.0)
    assert set(one.builds.values()) <= {postings.BUILD_FULL, postings.BUILD_UNCHANGED}
    assert sorted(set(matches.boards[2])) == ["lever:sf04"]


def test_a_fresh_process_reads_neither_the_watchlist_nor_the_index_when_nothing_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _seed(fx)
    built = _refresh(fx)

    # What a new process starts with: nothing kept in memory, the pipeline file as the last process left it.
    postings._BOARDS.clear()
    postings._FLIGHTS.clear()
    postings._TEXTS.clear()

    def no_watchlist(*_args: object, **_kwargs: object):
        raise AssertionError("the watchlist was read from the journal although its tree did not change")

    def no_index(*_args: object, **_kwargs: object):
        raise AssertionError("a company index file was read although nothing changed")

    monkeypatch.setattr(postings, "_watched", no_watchlist)
    monkeypatch.setattr(company_index.CompanyIndex, "read", no_index)
    warm = _refresh(fx)
    assert set(warm.builds.values()) == {postings.BUILD_UNCHANGED} and warm.rows == built.rows
    assert _flight(fx).builds == 0


def test_a_watchlist_that_cannot_be_read_just_now_does_not_throw_the_stored_rows_away(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout.find_jobs import watchlist

    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _seed(fx)
    built = _refresh(fx)
    matches = _Matches(monkeypatch)
    postings._BOARDS.clear()
    store = postings.open_store(fx.home_root, fx.target)
    try:
        kept = store.posting_source("watchlist")
        # A new company is watched (the committed watchlist changes), and the read of it fails once.
        fx.seed("sf90", [lever_job("sf90", 0)], seen_at=days_ago(0.5))
        real = watchlist.list_active

        def failing(*_args: object, **_kwargs: object):
            raise RuntimeError("journal head moved during a snapshot read")

        monkeypatch.setattr(watchlist, "list_active", failing)
        during = postings.refresh(fx.home_root, fx.target, store=store, now=NOW)
        assert during.rows == built.rows and not matches.boards  # nothing matched, nothing dropped
        assert store.posting_source("watchlist") == kept and not postings._BOARDS  # and the failed read is not kept
        monkeypatch.setattr(watchlist, "list_active", real)
        after = postings.refresh(fx.home_root, fx.target, store=store, now=NOW)
        assert after.rows == built.rows + 2 and sorted(set(matches.boards[0])) == ["lever:sf90"]
        assert store.posting_source("watchlist") != kept
    finally:
        store.close()


def test_a_pages_posting_text_is_read_once_while_the_boards_files_are_the_same(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _seed(fx)
    _refresh(fx)
    store = postings.open_store(fx.home_root, fx.target)
    try:
        rows = [row for row in store.postings(profile_id=fx.second_profile_id) if row.board in ("lever:sf01", "lever:sf02")]
    finally:
        store.close()
    reads: list[str] = []
    real = company_index.CompanyIndex.read

    def counting(index, ats, slug):
        reads.append(slug)
        return real(index, ats, slug)

    monkeypatch.setattr(company_index.CompanyIndex, "read", counting)
    first = postings.posting_texts(fx.home_root, rows)
    assert sorted(reads) == ["sf01", "sf02"] and len(first) == 2 * PER_BOARD
    assert postings.posting_texts(fx.home_root, rows) == first and len(reads) == 2  # kept
    assert postings.posting_texts(fx.home_root, rows[:3]) == {row.job: first[row.job] for row in rows[:3]} and len(reads) == 2

    # The board is updated: its text is read again, and it is the new text.
    fx.seed("sf01", [lever_job("sf01", n, text=f"Changed text {n}. Requirements: Python.") for n in range(PER_BOARD)], seen_at=days_ago(0.2), watch=False)
    del reads[:]  # the update read the board's own file
    again = postings.posting_texts(fx.home_root, rows)
    assert reads == ["sf01"]
    assert again[job_url("sf01", 0)].text.startswith("Changed text 0") and again[job_url("sf02", 0)] == first[job_url("sf02", 0)]


def test_the_rank_lanes_rows_are_the_whole_index_matchs_rows_read_from_their_own_boards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _seed(fx)
    refreshed = _refresh(fx)
    view = next(item for item in refreshed.profiles if item.profile_id == fx.second_profile_id)
    whole = {row.normalized_url: row for row in postings.profile_posting_rows(view, fx.home_root, fx.target, NOW)}
    store = postings.open_store(fx.home_root, fx.target)
    try:
        stored = store.postings(profile_id=view.profile_id)
    finally:
        store.close()
    reads: list[str] = []
    real = company_index.CompanyIndex.read
    monkeypatch.setattr(company_index.CompanyIndex, "read", lambda index, ats, slug: (reads.append(slug), real(index, ats, slug))[1])
    some = [row for row in stored if row.board in ("lever:sf04", "lever:sf05")]
    found = {row.normalized_url: row for row in postings.posting_rows(fx.home_root, some)}
    assert sorted(reads) == ["sf04", "sf05"]  # two boards, not the index
    assert found == {job: whole[job] for job in found} and set(found) == {row.job for row in some}

"""SI2 (0.1.11.7): ``sources update`` keeps ``search.sqlite`` current, synthetic boards and fake homes only.

Pins: a missing index is built once at the end of the update (never per board,
``catch_up.search_index`` says ``deferred``); a built one is upserted company by
company (``verify=False``) and verified once at the end; a board that left the
watchlist leaves it; an upsert that raises or says no never fails the update and
the end-of-update refresh makes the index current; the readers beside an upsert
burst are never blocked (WAL). Nothing here touches the network or a real home.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import threading
import time

import pytest

from gigai.scout.find_jobs import search_index, sources_update
from gigai.scout.find_jobs.ats_board_clients import matches_roles
from gigai.scout.find_jobs.company_index import CompanyIndex
from gigai.scout.find_jobs.contracts import ATSProvider
from gigai.scout.find_jobs.search_index import IndexQuery
from gigai.scout.find_jobs.sources_update import board_cache_for_home, run_sources_update, update_sources

from .test_acquire_scale import _board, _config, _limits
from .test_refresh_core import _project
from .test_search_index import BOARDS, make_home, scan, write_board
from .test_sources_update import _Boards

pytestmark = pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 9, 0), reason="needs FTS5")

FAST = _limits(concurrency=1)
TYPED = ("software engineer",)


@pytest.fixture(autouse=True)
def _close_after(tmp_path: Path):
    yield
    search_index.close(tmp_path)


def meta(home: Path) -> dict[str, str]:
    search_index.close(home)
    conn = sqlite3.connect(search_index.search_index_path(home))
    try:
        return dict(conn.execute("SELECT key, value FROM meta").fetchall())
    finally:
        conn.close()


def live_pairs(home: Path) -> set[tuple[str, str]]:
    index = CompanyIndex.for_home(home)
    return {
        (entry.key, posting.posting_id)
        for ats, slug in index.keys()
        if (entry := index.read(ats, slug)) is not None
        for posting in entry.live()
    }


def assert_current_and_exact(home: Path) -> None:
    """The index answers (not stale), its rows are the company files' rows, and the stamp is verified."""

    state = search_index.status(home)
    assert state.available, state
    found = search_index.candidates(home, IndexQuery())
    assert found.available and {(row.board, row.posting_id) for row in found.rows} == live_pairs(home)
    typed = search_index.candidates(home, IndexQuery(titles=TYPED))
    assert [(row.posted, row.url, row.board, row.posting_id) for row in typed.rows if matches_roles(row.title, TYPED)] == scan(home, TYPED, False, None)
    values = meta(home)
    assert values["verified"] == values["stamp"]


class Calls:
    """Counts what an update asks of ``search_index``."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.rebuilds = 0
        self.upserts: list[tuple[str, bool]] = []
        self.removes: list[str] = []
        self.refreshes = 0
        real_rebuild, real_upsert = search_index.rebuild_from_index, search_index.upsert_company
        real_remove, real_refresh = search_index.remove_company, search_index.refresh

        def rebuild(*args, **kwargs):
            self.rebuilds += 1
            return real_rebuild(*args, **kwargs)

        def upsert(home, key, *, verify=True):
            self.upserts.append((key, verify))
            return real_upsert(home, key, verify=verify)

        def remove(home, key, *, verify=True):
            self.removes.append(key)
            return real_remove(home, key, verify=verify)

        def refresh(*args, **kwargs):
            self.refreshes += 1
            return real_refresh(*args, **kwargs)

        monkeypatch.setattr(search_index, "rebuild_from_index", rebuild)
        monkeypatch.setattr(search_index, "upsert_company", upsert)
        monkeypatch.setattr(search_index, "remove_company", remove)
        monkeypatch.setattr(search_index, "refresh", refresh)


def test_a_missing_index_is_built_once_at_the_end_not_per_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = _project(tmp_path)
    boards = _Boards()
    calls = Calls(monkeypatch)
    seen: list[object] = []

    def watch(snapshot: dict[str, object]) -> None:
        seen.append((snapshot.get("catch_up") or {}).get("search_index"))  # type: ignore[attr-defined]

    with boards.client() as client:
        first = run_sources_update(home_root=home, target=target, client=client, limits=FAST).to_json()
        assert first["status"] == "succeeded"
        assert calls.rebuilds == 1 and calls.upserts == [] and calls.refreshes == 0  # three boards, one build
        assert_current_and_exact(home)
        # Company files are there but the index is gone: "deferred" while the boards are asked, built at the end.
        search_index.close(home)
        for suffix in ("", "-wal", "-shm"):
            Path(f"{search_index.search_index_path(home)}{suffix}").unlink(missing_ok=True)
        boards.version = 2
        second = run_sources_update(home_root=home, target=target, client=client, limits=FAST, full_refresh=True, on_progress=watch).to_json()
    assert second["status"] == "succeeded"
    assert calls.rebuilds == 2 and calls.upserts == [] and calls.refreshes == 0
    assert "deferred" in seen  # "building" is published unforced, as the text index's is: the throttle may skip it
    assert (second["catch_up"] or {}).get("search_index") is None  # type: ignore[union-attr]
    assert_current_and_exact(home)


def test_an_update_that_changes_two_boards_leaves_the_index_current(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = _project(tmp_path)
    boards = _Boards()
    with boards.client() as client:
        run_sources_update(home_root=home, target=target, client=client, limits=FAST)
        assert_current_and_exact(home)
        before = live_pairs(home)
        calls = Calls(monkeypatch)
        boards.version = 2
        boards.greenhouse["acme"].append({"id": 13, "title": "Data Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/13", "location": {"name": "Remote"}, "updated_at": "2026-09-22T00:00:00Z"})
        boards.lever["initech"] = [boards.lever["initech"][0]]
        second = run_sources_update(home_root=home, target=target, client=client, limits=FAST, full_refresh=True).to_json()
    assert second["status"] == "succeeded" and second["stores"]["text"]["failures"] == 0  # type: ignore[index]
    assert sorted(calls.upserts) == [("greenhouse:acme", False), ("lever:initech", False)]  # the two changed boards, unverified
    assert calls.rebuilds == 0 and calls.refreshes == 1  # one refresh at the end: it verifies once
    after = live_pairs(home)
    assert after != before and ("greenhouse:acme", "13") in after and ("lever:initech", "lev-2") not in after
    assert_current_and_exact(home)


def test_an_upsert_that_raises_or_refuses_never_fails_the_update_and_the_end_refresh_catches_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home, target = _project(tmp_path)
    boards = _Boards()
    with boards.client() as client:
        run_sources_update(home_root=home, target=target, client=client, limits=FAST)
        boards.version = 2
        boards.greenhouse["acme"].append({"id": 13, "title": "Data Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/13", "location": {"name": "Remote"}, "updated_at": "2026-09-22T00:00:00Z"})
        boards.greenhouse["globex"] = []
        attempts: list[str] = []

        def boom(home_root: Path, key: str, *, verify: bool = True) -> bool:
            attempts.append(key)
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(search_index, "upsert_company", boom)
        started = time.monotonic()
        result = run_sources_update(home_root=home, target=target, client=client, limits=FAST, full_refresh=True).to_json()
        assert time.monotonic() - started < 30
    assert result["status"] == "succeeded"
    assert len(attempts) == 1  # unusable for the rest of the update: no second try, no cost per board
    assert capsys.readouterr().err.count("the search index was not updated") == 1
    assert_current_and_exact(home)  # the end-of-update refresh re-read both changed files
    assert ("greenhouse:acme", "13") in live_pairs(home)

    # The same when the upsert says no instead of raising.
    with boards.client() as client:
        boards.version = 3
        boards.greenhouse["acme"].append({"id": 14, "title": "Staff Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/14", "location": {"name": "Remote"}, "updated_at": "2026-09-23T00:00:00Z"})
        monkeypatch.setattr(search_index, "upsert_company", lambda *_a, **_k: False)
        refused = run_sources_update(home_root=home, target=target, client=client, limits=FAST, full_refresh=True).to_json()
    assert refused["status"] == "succeeded"
    assert_current_and_exact(home)
    assert ("greenhouse:acme", "14") in live_pairs(home)


def test_a_search_index_that_cannot_be_asked_never_fails_the_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = _project(tmp_path)
    boards = _Boards()

    def broken(*_a: object, **_k: object) -> bool:
        raise RuntimeError("no")

    monkeypatch.setattr(search_index, "is_built", broken)
    monkeypatch.setattr(search_index, "rebuild_from_index", broken)
    with boards.client() as client:
        result = run_sources_update(home_root=home, target=target, client=client, limits=FAST).to_json()
    assert result["status"] == "succeeded"
    assert not search_index.search_index_path(home).exists()


def test_the_index_mirrors_the_folder_a_dropped_board_stays_and_a_deleted_file_goes_at_the_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    boards = _Boards()
    watchlist = [_board(ATSProvider.GREENHOUSE, "acme"), _board(ATSProvider.LEVER, "initech", catalog=True)]

    def update(entries):
        with boards.client() as client:
            return update_sources(
                entries, cache=board_cache_for_home(tmp_path), index=CompanyIndex.for_home(tmp_path), client=client,
                config=_config(), limits=FAST, full_refresh=True, home_root=tmp_path,
            ).to_json()

    update(watchlist)
    calls = Calls(monkeypatch)
    # An update never deletes a company file: a board that left the watchlist is still in the folder, so still searchable.
    assert update(watchlist[:1])["status"] == "succeeded" and calls.removes == [] and calls.rebuilds == 0
    assert any(board == "lever:initech" for board, _id in live_pairs(tmp_path))
    assert_current_and_exact(tmp_path)
    # A file deleted outside the update (a hand edit, an import): the end-of-update refresh drops its rows.
    assert CompanyIndex.for_home(tmp_path).delete("lever", "initech")
    assert update(watchlist[:1])["status"] == "succeeded" and calls.rebuilds == 0 and calls.refreshes >= 1
    assert all(board != "lever:initech" for board, _id in live_pairs(tmp_path))
    assert_current_and_exact(tmp_path)


def test_an_other_version_index_is_rebuilt_once_at_the_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = _project(tmp_path)
    boards = _Boards()
    with boards.client() as client:
        run_sources_update(home_root=home, target=target, client=client, limits=FAST)
        search_index.close(home)
        monkeypatch.setattr(search_index, "SCHEMA_VERSION", search_index.SCHEMA_VERSION + 1)
        calls = Calls(monkeypatch)
        boards.version = 2
        boards.greenhouse["acme"].append({"id": 13, "title": "Data Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/13", "location": {"name": "Remote"}, "updated_at": "2026-09-22T00:00:00Z"})
        run_sources_update(home_root=home, target=target, client=client, limits=FAST, full_refresh=True)
    assert calls.rebuilds == 1 and calls.upserts == []
    assert_current_and_exact(home)


def test_readers_are_not_blocked_by_an_upsert_burst(tmp_path: Path) -> None:
    """WAL: a read transaction held open and a thread querying all through a burst of company writes."""

    home = make_home(tmp_path / "burst")
    path = search_index.search_index_path(home)
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("PRAGMA query_only=1")
    holder.execute("BEGIN")
    before = holder.execute("SELECT COUNT(*) FROM p").fetchone()[0]
    reasons: set[object] = set()
    latencies: list[float] = []
    errors: list[BaseException] = []
    stop = threading.Event()

    def reader() -> None:
        try:
            while not stop.is_set():
                began = time.monotonic()
                found = search_index.candidates(home, IndexQuery(titles=TYPED), limit=20)
                latencies.append(time.monotonic() - began)
                reasons.add(None if found.available else found.reason)
        except BaseException as error:  # noqa: BLE001 - the test reports it
            errors.append(error)
        finally:
            search_index.close(home)

    thread = threading.Thread(target=reader)
    thread.start()
    try:
        for round_ in range(1, 6):
            for board in range(len(BOARDS)):
                began = time.monotonic()
                key = write_board(home, board, round_=round_)
                assert search_index.upsert_company(home, key, verify=False)
                assert time.monotonic() - began < 4  # never waits on the readers (busy timeout is 5 s)
        assert holder.execute("SELECT COUNT(*) FROM p").fetchone()[0] == before  # the held reader keeps its snapshot
        holder.execute("ROLLBACK")
        assert search_index.verify(home)  # the one truncating checkpoint, once no reader holds the old snapshot
    finally:
        stop.set()
        thread.join(timeout=20)
        holder.close()
    assert not errors, errors
    assert latencies and max(latencies) < 4
    assert reasons <= {None, search_index.STALE}, reasons  # an answer or "the files moved on": never busy, never damaged
    assert search_index.status(home).available
    search_index.close(home)

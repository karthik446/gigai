"""F1: the full-text index (``text_index.py``), synthetic postings only.

Pins: build from the stored company bodies, add / replace / remove a
company, a keyword that only the description carries, a posting without text
(never returned, counted as unchecked), no Porter stemming, and the rebuild
after a deleted / corrupt / version-bumped file or a SQLite without FTS5.
``rebuild_from_cache`` is the only full build (``is_built``, its
``progress``); a write with ``build_if_missing=False`` never runs it, and no
write rebuilds a built index.

Every test runs twice: with ``contentless_delete`` (SQLite 3.43+) and in the
soft-delete mode an older SQLite gets (Debian 12 ships 3.40, sweep run
36880117927). The second mode is forced through the module flag, so it runs
on any machine; the first is skipped where SQLite cannot do it.
Nothing here touches the network or real data.
"""

from __future__ import annotations

import json
from pathlib import Path
import os
import sqlite3
import subprocess
import sys
import time

import pytest

from gigai.scout.find_jobs import text_index
from gigai.scout.find_jobs.ats_board_clients import BoardCache
from gigai.scout.find_jobs.company_index import CompanyIndex, board_list_url, company_key, refresh_company
from gigai.scout.find_jobs.sources_update import run_sources_update
from gigai.scout.find_jobs.text_index import TextPosting

from .test_acquire_scale import _limits
from .test_refresh_core import _project
from .test_sources_update import _Boards

pytestmark = pytest.mark.skipif(
    not hasattr(sqlite3, "sqlite_version_info") or sqlite3.sqlite_version_info < (3, 9, 0),
    reason="needs FTS5",
)


CONTENTLESS_DELETE = "contentless_delete"
SOFT_DELETE = "soft_delete"


@pytest.fixture(autouse=True, params=[CONTENTLESS_DELETE, SOFT_DELETE])
def mode(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    if request.param == CONTENTLESS_DELETE and sqlite3.sqlite_version_info < (3, 43, 0):
        pytest.skip("this SQLite has no contentless_delete")
    monkeypatch.setattr(text_index, "_CONTENTLESS_DELETE", request.param == CONTENTLESS_DELETE)
    yield request.param
    text_index.close(tmp_path)


def _count_rebuilds(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    rebuilds: list[Path] = []
    real = text_index.rebuild_from_cache

    def counting(home_root: Path, **kwargs) -> text_index.TextIndexStats:
        rebuilds.append(home_root)
        return real(home_root, **kwargs)

    monkeypatch.setattr(text_index, "rebuild_from_cache", counting)
    return rebuilds


def _file_bytes(home: Path) -> int:
    """The index file's size once the WAL (up to ~4 MiB in any mode) is folded back into it."""

    text_index.close(home)
    path = text_index.text_index_path(home)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    return path.stat().st_size


def _raw(home: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(text_index.text_index_path(home))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _ashby_job(job_id: str, title: str, description: str | None) -> dict[str, object]:
    return {
        "id": job_id,
        "title": title,
        "location": "Remote",
        "jobUrl": f"https://jobs.ashbyhq.com/acme/{job_id}",
        "publishedAt": "2026-09-20T00:00:00Z",
        "descriptionPlain": description or "",
    }


def _seed_company(home: Path, ats: str, slug: str, payload: object) -> None:
    cache = BoardCache(home / "cache" / "scout" / "ats-boards")
    cache.store(ats, board_list_url(ats, slug), body=json.dumps(payload).encode(), etag=None, last_modified=None, marker=None)
    refresh_company(CompanyIndex.for_home(home), cache, ats=ats, slug=slug)


def _ids(result: text_index.TextSearchResult) -> list[str]:
    return [hit.posting_id for hit in result.hits]


def _seed_acme(home: Path) -> None:
    _seed_company(
        home,
        "ashby",
        "acme",
        {
            "jobs": [
                _ashby_job("a1", "Backend Engineer", "We run Kubernetes clusters for fintech."),
                _ashby_job("a2", "Product Designer", "Design agentic workflows."),
                _ashby_job("a3", "Office Manager", ""),
            ]
        },
    )
    _seed_company(
        home,
        "greenhouse",
        "globex",
        {
            "jobs": [
                {"id": 11, "title": "Kubernetes Platform Engineer", "absolute_url": "https://boards.greenhouse.io/globex/jobs/11", "updated_at": "2026-09-20T00:00:00Z"},
            ]
        },
    )


def test_build_finds_a_keyword_only_the_description_has(tmp_path: Path) -> None:
    _seed_acme(tmp_path)

    built = text_index.build(tmp_path)

    assert built.available and built.postings == 4 and built.with_text == 2 and built.without_text == 2
    result = text_index.search(tmp_path, "kubernetes")
    # a1 matches in its description only; the Greenhouse "Kubernetes" title has no text, so it is NOT returned.
    assert result.available and _ids(result) == ["a1"] and result.hits[0].company_key == "ashby:acme"
    assert result.unchecked == 2  # a3 (empty description) and Greenhouse 11


def test_search_is_created_on_first_use_and_a_posting_without_text_is_only_counted(tmp_path: Path) -> None:
    _seed_acme(tmp_path)

    result = text_index.search(tmp_path, "Platform")

    assert text_index.text_index_path(tmp_path).exists()
    assert result.hits == () and result.unchecked == 2
    only_globex = text_index.search(tmp_path, "Platform", company_keys=[company_key("greenhouse", "globex")])
    assert only_globex.unchecked == 1


def test_no_stemming_and_phrase_and_prefix_queries(tmp_path: Path) -> None:
    text_index.upsert_company(
        tmp_path,
        "ashby:acme",
        [
            TextPosting("1", "Agent Platform", "Build an agent runtime."),
            TextPosting("2", "Research Lead", "Our agentic systems use tools."),
        ],
    )

    assert _ids(text_index.search(tmp_path, "agentic")) == ["2"]
    assert _ids(text_index.search(tmp_path, "agent")) == ["1"]
    assert _ids(text_index.search(tmp_path, '"agent runtime"')) == ["1"]
    assert sorted(_ids(text_index.search(tmp_path, "agen*"))) == ["1", "2"]
    assert text_index.search(tmp_path, "Ünïcode").hits == ()


def test_title_hits_rank_above_description_hits_and_a_bad_query_is_not_a_crash(tmp_path: Path) -> None:
    text_index.upsert_company(
        tmp_path,
        "ashby:acme",
        [TextPosting("d", "Engineer", "python python python"), TextPosting("t", "Python Engineer", "other words")],
    )

    result = text_index.search(tmp_path, "python")
    assert _ids(result) == ["t", "d"] and result.hits[0].rank <= result.hits[1].rank
    assert text_index.search(tmp_path, 'python AND (').available  # falls back to plain words
    assert text_index.search(tmp_path, "***").error == "bad_query"
    assert text_index.search(tmp_path, "python", limit=1).hits[0].posting_id == "t"


def test_add_replace_and_remove_a_company(tmp_path: Path) -> None:
    key = "ashby:acme"
    assert text_index.upsert_company(tmp_path, key, [TextPosting("1", "Engineer", "rust services"), TextPosting("2", "Analyst", None)])
    assert text_index.upsert_company(tmp_path, "lever:other", [TextPosting("9", "Other", "rust tooling")])
    assert sorted(_ids(text_index.search(tmp_path, "rust"))) == ["1", "9"]
    assert text_index.search(tmp_path, "rust").unchecked == 1

    # replace: 1 changes its text, 2 gains text, 3 is new
    text_index.upsert_company(
        tmp_path, key, [TextPosting("1", "Engineer", "golang services"), TextPosting("2", "Analyst", "rust models"), TextPosting("3", "New", "rust")]
    )
    assert sorted(_ids(text_index.search(tmp_path, "rust"))) == ["2", "3", "9"]
    assert _ids(text_index.search(tmp_path, "golang")) == ["1"]
    assert text_index.search(tmp_path, "rust").unchecked == 0

    assert text_index.remove_company(tmp_path, key)
    assert _ids(text_index.search(tmp_path, "rust")) == ["9"]
    assert text_index.search(tmp_path, "golang").hits == ()
    assert text_index.stats(tmp_path).postings == 1


def test_the_file_records_its_mode_and_a_write_never_rebuilds_a_built_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """The Debian 12 failure: on SQLite < 3.43 a write rebuilt from the cache and dropped what it was given."""

    _seed_acme(tmp_path)
    assert text_index.build(tmp_path).postings == 4
    assert _raw(tmp_path, "SELECT value FROM meta WHERE key='can_delete'") == [("1" if mode == CONTENTLESS_DELETE else "0",)]
    rebuilds = _count_rebuilds(monkeypatch)

    # a company the cache does not hold, and a replace of one it does
    assert text_index.upsert_company(tmp_path, "lever:other", [TextPosting("zz", "Kept", "quasarflux")])
    assert text_index.upsert_company(tmp_path, "ashby:acme", [TextPosting("a1", "Backend Engineer", "nomad clusters")])
    assert _ids(text_index.search(tmp_path, "quasarflux")) == ["zz"]
    assert _ids(text_index.search(tmp_path, "nomad")) == ["a1"]
    assert text_index.search(tmp_path, "kubernetes").hits == ()  # the replaced text is gone
    assert text_index.search(tmp_path, "agentic").hits == ()  # a2 left the company
    assert text_index.remove_company(tmp_path, "ashby:acme")
    assert text_index.search(tmp_path, "nomad").hits == ()
    assert text_index.remove_company(tmp_path, "ashby:never-indexed")

    assert rebuilds == []
    stats = text_index.stats(tmp_path)
    assert (stats.postings, stats.with_text, stats.without_text) == (2, 1, 1)  # zz and Greenhouse 11
    assert stats.garbage == (3 if mode == SOFT_DELETE else 0)  # a1, a2, then the new a1


def test_a_soft_deleted_rowid_is_never_used_again(tmp_path: Path, mode: str) -> None:
    """A reused rowid would still match the words of the posting that had it before."""

    key = "ashby:acme"
    assert text_index.upsert_company(tmp_path, key, [TextPosting("1", "Engineer", "oldwordalpha"), TextPosting("2", "Analyst", "oldwordbeta")])
    assert text_index.remove_company(tmp_path, key)  # the highest rowids are free again
    assert text_index.upsert_company(tmp_path, key, [TextPosting("3", "Writer", "newwordgamma"), TextPosting("4", "Editor", None)])
    assert text_index.upsert_company(tmp_path, "lever:other", [TextPosting("9", "Other", "newworddelta")])

    for stale in ("oldwordalpha", "oldwordbeta", "engineer", "analyst"):
        assert text_index.search(tmp_path, stale).hits == ()
    assert _ids(text_index.search(tmp_path, "newwordgamma")) == ["3"]
    assert _ids(text_index.search(tmp_path, "newworddelta")) == ["9"]
    assert text_index.postings_with_text(tmp_path, key) == frozenset({"3"})
    if mode == SOFT_DELETE:
        assert [row[0] for row in _raw(tmp_path, "SELECT rowid FROM postings ORDER BY rowid")] == [3, 4, 5]
        assert text_index.stats(tmp_path).garbage == 2


def test_an_index_file_from_before_the_garbage_count_still_takes_writes(tmp_path: Path, mode: str) -> None:
    """A 0.1.10.4 file has no ``garbage`` / ``next_rowid`` meta rows (same schema version)."""

    key = "ashby:acme"
    assert text_index.upsert_company(tmp_path, key, [TextPosting("1", "Engineer", "oldwordalpha"), TextPosting("2", "Analyst", "oldwordbeta")])
    text_index.close(tmp_path)
    conn = sqlite3.connect(text_index.text_index_path(tmp_path))
    try:
        conn.execute("DELETE FROM meta WHERE key IN ('garbage', 'next_rowid')")
        conn.commit()
    finally:
        conn.close()

    assert text_index.upsert_company(tmp_path, "lever:other", [TextPosting("9", "Other", "newworddelta")])
    assert text_index.upsert_company(tmp_path, key, [TextPosting("1", "Engineer", "newwordgamma")])
    assert _ids(text_index.search(tmp_path, "newwordgamma")) == ["1"] and _ids(text_index.search(tmp_path, "newworddelta")) == ["9"]
    assert text_index.search(tmp_path, "oldwordalpha").hits == () and text_index.search(tmp_path, "analyst").hits == ()
    assert text_index.stats(tmp_path).garbage == (2 if mode == SOFT_DELETE else 0)


def _churn_postings(turn: int) -> list[TextPosting]:
    return [TextPosting(f"p{n}", f"Engineer {n}", f"turn{turn}word shared words for posting {n} " * 20) for n in range(5)]


def test_1000_replaces_of_one_company_never_rebuild_and_stay_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Writes alone (no idle compaction): zero rebuilds, and the garbage costs a bounded number of bytes."""

    key = "ashby:acme"
    assert text_index.upsert_company(tmp_path, key, _churn_postings(0))
    rebuilds = _count_rebuilds(monkeypatch)
    started = time.perf_counter()
    for turn in range(1, 1001):
        assert text_index.upsert_company(tmp_path, key, _churn_postings(turn))
    elapsed = time.perf_counter() - started

    assert rebuilds == []
    assert sorted(_ids(text_index.search(tmp_path, "turn1000word"))) == ["p0", "p1", "p2", "p3", "p4"]
    assert text_index.search(tmp_path, "turn999word").hits == () and text_index.search(tmp_path, "turn1word").hits == ()
    assert len(text_index.search(tmp_path, "shared").hits) == 5  # 5,000 stale rows carry the word too
    stats = text_index.stats(tmp_path)
    assert stats.postings == 5 and stats.garbage == (5000 if mode == SOFT_DELETE else 0)
    size = _file_bytes(tmp_path)
    with capsys.disabled():
        print(f"\n[text_index churn/{mode}] 1,000 replaces of 5 postings: {elapsed:.2f}s, 0 rebuilds, {size / 1024:.0f} KiB, garbage {stats.garbage}")
    assert size < 2 * 1024 * 1024  # measured: 860 KiB soft delete (5,000 stale rows, ~170 bytes each), 52 KiB otherwise


def test_idle_compaction_bounds_the_garbage_and_runs_rarely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """With the idle hook after every write: a handful of rebuilds for 1,000 replaces, never one per write."""

    _seed_company(tmp_path, "ashby", "acme", {"jobs": [_ashby_job(f"p{n}", f"Engineer {n}", "cachedword text") for n in range(5)]})
    key = "ashby:acme"
    assert text_index.build(tmp_path).with_text == 5
    assert text_index.compact_if_needed(tmp_path) is False
    rebuilds = _count_rebuilds(monkeypatch)
    most_garbage = 0
    for turn in range(1, 1001):
        assert text_index.upsert_company(tmp_path, key, _churn_postings(turn))
        assert len(rebuilds) == ((turn - 1) // 200 if mode == SOFT_DELETE else 0)  # the write itself never rebuilds
        most_garbage = max(most_garbage, text_index.stats(tmp_path).garbage)
        compacted = text_index.compact_if_needed(tmp_path)
        assert compacted is (mode == SOFT_DELETE and turn % 200 == 0)

    if mode == SOFT_DELETE:
        assert len(rebuilds) == 5 and most_garbage == text_index._COMPACT_MIN_GARBAGE
        # compaction refills from the cache: the garbage is gone, the cached text is back
        assert text_index.stats(tmp_path).garbage == 0 and len(text_index.search(tmp_path, "cachedword").hits) == 5
        assert _raw(tmp_path, "SELECT COUNT(*) FROM text") == [(5,)]
    else:
        assert rebuilds == [] and most_garbage == 0
    size = _file_bytes(tmp_path)
    with capsys.disabled():
        print(f"\n[text_index compaction/{mode}] 1,000 replaces with the idle hook: {len(rebuilds)} rebuilds, {size / 1024:.0f} KiB")
    assert size < 1024 * 1024


def test_compaction_needs_enough_garbage_and_more_than_half_and_never_creates_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    assert text_index.compact_if_needed(tmp_path) is False
    assert not text_index.text_index_path(tmp_path).exists()

    monkeypatch.setattr(text_index, "_COMPACT_MIN_GARBAGE", 3)
    big = [TextPosting(str(n), "Engineer", "steady words") for n in range(6)]
    assert text_index.upsert_company(tmp_path, "lever:big", big)
    assert text_index.upsert_company(tmp_path, "ashby:acme", [TextPosting("a", "A", "x"), TextPosting("b", "B", "y")])
    rebuilds = _count_rebuilds(monkeypatch)

    assert text_index.upsert_company(tmp_path, "ashby:acme", [TextPosting("a", "A", "x"), TextPosting("b", "B", "y")])
    assert text_index.compact_if_needed(tmp_path) is False  # 2 garbage rows: under the minimum
    assert text_index.upsert_company(tmp_path, "ashby:acme", [TextPosting("a", "A", "x"), TextPosting("b", "B", "y")])
    assert text_index.compact_if_needed(tmp_path) is False  # 4 garbage rows, 8 live: not more than half
    assert text_index.remove_company(tmp_path, "lever:big")
    assert rebuilds == []
    assert text_index.compact_if_needed(tmp_path) is (mode == SOFT_DELETE)  # 10 garbage rows, 2 live
    assert len(rebuilds) == (1 if mode == SOFT_DELETE else 0)
    assert text_index.stats(tmp_path).garbage == 0


def test_is_built_has_no_side_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = text_index.text_index_path(tmp_path)
    assert text_index.is_built(tmp_path) is False
    assert not path.exists() and not path.parent.exists()  # nothing created, not even the directory

    _seed_acme(tmp_path)
    rebuilds = _count_rebuilds(monkeypatch)
    assert text_index.is_built(tmp_path) is False and not path.exists() and rebuilds == []

    assert text_index.build(tmp_path).available
    assert text_index.is_built(tmp_path) is True
    monkeypatch.setattr(text_index, "SCHEMA_VERSION", text_index.SCHEMA_VERSION + 1)
    assert text_index.is_built(tmp_path) is False  # another version's file needs a rebuild
    monkeypatch.undo()

    text_index.close(tmp_path)
    for suffix in ("-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    garbage = b"this is not a sqlite database" * 100
    path.write_bytes(garbage)
    assert text_index.is_built(tmp_path) is False
    assert path.read_bytes() == garbage  # not repaired, not replaced


def test_a_write_told_not_to_build_leaves_an_unbuilt_index_unbuilt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """0110-028: the first write of an update ran the full build (25 s on 283k postings) on the update thread."""

    _seed_acme(tmp_path)
    rebuilds = _count_rebuilds(monkeypatch)
    new = [TextPosting("a1", "Backend Engineer", "nomad clusters")]

    assert text_index.upsert_company(tmp_path, "ashby:acme", new, build_if_missing=False) is False
    assert text_index.remove_company(tmp_path, "greenhouse:globex", build_if_missing=False) is False
    assert rebuilds == [] and text_index.is_built(tmp_path) is False
    assert text_index.stats(tmp_path).postings == 0  # nothing half-written for the later build to trip on

    assert text_index.rebuild_from_cache(tmp_path).postings == 4  # the caller's one build, later
    assert text_index.is_built(tmp_path) is True and len(rebuilds) == 1
    assert text_index.upsert_company(tmp_path, "ashby:acme", new, build_if_missing=False) is True
    assert text_index.remove_company(tmp_path, "greenhouse:globex", build_if_missing=False) is True
    assert len(rebuilds) == 1 and _ids(text_index.search(tmp_path, "nomad")) == ["a1"]

    # a reset file (deleted under the open connection) is unbuilt again
    path = text_index.text_index_path(tmp_path)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    assert text_index.upsert_company(tmp_path, "ashby:acme", new, build_if_missing=False) is False and len(rebuilds) == 1

    # the default is unchanged from 0.1.10.4: the first write builds from the cache, once
    assert text_index.upsert_company(tmp_path, "ashby:acme", new) is True
    assert len(rebuilds) == 2 and text_index.is_built(tmp_path) is True
    assert text_index.upsert_company(tmp_path, "ashby:acme", new) is True and len(rebuilds) == 2
    assert text_index.stats(tmp_path).postings == 2  # a1 replaced acme's three; Greenhouse 11 came from the cache


def test_a_write_told_not_to_build_still_creates_the_empty_index_of_a_home_with_no_company_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rebuilds = _count_rebuilds(monkeypatch)

    assert text_index.remove_company(tmp_path, "ashby:acme", build_if_missing=False) is True
    assert text_index.is_built(tmp_path) is True and rebuilds == []
    assert text_index.upsert_company(tmp_path, "ashby:acme", [TextPosting("1", "Engineer", "rust")], build_if_missing=False) is True
    assert _ids(text_index.search(tmp_path, "rust")) == ["1"] and rebuilds == []


def test_the_build_reports_progress_per_company_and_a_raising_callback_keeps_the_old_index(tmp_path: Path) -> None:
    _seed_acme(tmp_path)
    seen: list[tuple[int, int]] = []

    built = text_index.rebuild_from_cache(tmp_path, progress=lambda done, total: seen.append((done, total)))

    assert built.postings == 4 and seen == [(0, 2), (1, 2), (2, 2)]
    assert text_index.upsert_company(tmp_path, "lever:other", [TextPosting("zz", "Kept", "quasarflux")])

    def stop(done: int, total: int) -> None:
        if done == 1:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        text_index.rebuild_from_cache(tmp_path, progress=stop)
    assert text_index.is_built(tmp_path) is True
    assert _ids(text_index.search(tmp_path, "quasarflux")) == ["zz"] and _ids(text_index.search(tmp_path, "kubernetes")) == ["a1"]

    calls: list[tuple[int, int]] = []
    assert text_index.compact_if_needed(tmp_path, progress=lambda done, total: calls.append((done, total))) is False
    assert calls == []


def test_an_update_writes_each_changed_company_in_both_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """``sources_update`` gates its per-company writes on ``_SUPPORTS_CONTENTLESS_DELETE``: on in both modes.

    On Debian 12 (SQLite 3.40) that gate was off, so an update wrote no
    company and rebuilt the whole index instead.
    """

    assert text_index._SUPPORTS_CONTENTLESS_DELETE is True
    home, target = _project(tmp_path)
    boards = _Boards()
    fast = _limits(concurrency=1)
    try:
        with boards.client() as client:
            first = run_sources_update(home_root=home, target=target, client=client, limits=fast).to_json()
            assert first["stores"]["text"]["failures"] == 0 and text_index.is_built(home)
            assert _raw(home, "SELECT value FROM meta WHERE key='can_delete'") == [("1" if mode == CONTENTLESS_DELETE else "0",)]
            assert {(hit.company_key, hit.posting_id) for hit in text_index.search(home, "office").hits} == {("lever:initech", "lev-2")}

            rebuilds = _count_rebuilds(monkeypatch)
            boards.version = 2
            boards.lever["initech"] = [
                boards.lever["initech"][0],
                {"id": "lev-3", "text": "UX Designer", "hostedUrl": "https://jobs.lever.co/initech/lev-3", "categories": {"location": "Remote"}, "country": "US", "createdAt": 1758412800000, "descriptionPlain": "Design the kanban flow."},
            ]
            second = run_sources_update(home_root=home, target=target, client=client, limits=fast, full_refresh=True).to_json()

        assert second["stores"]["text"] == {"companies_written": 1, "companies_removed": 0, "failures": 0}
        assert rebuilds == []  # written company by company, not rebuilt
        assert {(hit.company_key, hit.posting_id) for hit in text_index.search(home, "kanban").hits} == {("lever:initech", "lev-3")}
        assert text_index.search(home, "office").hits == ()
        garbage = text_index.stats(home).garbage
        assert garbage >= 2 if mode == SOFT_DELETE else garbage == 0  # at least initech's two replaced rows
    finally:
        text_index.close(home)


def test_the_env_switch_forces_the_soft_delete_mode() -> None:
    code = "from gigai.scout.find_jobs import text_index as t; print(t._CONTENTLESS_DELETE, t._SUPPORTS_CONTENTLESS_DELETE)"
    env = {**os.environ, text_index.SOFT_DELETE_ENV: "1"}
    forced = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    # _SUPPORTS_CONTENTLESS_DELETE is what sources_update.py reads: per-company writes stay on.
    assert forced.stdout.split() == ["False", "True"]


def test_rebuilds_after_delete_corrupt_and_version_bump(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_acme(tmp_path)
    path = text_index.text_index_path(tmp_path)
    assert _ids(text_index.search(tmp_path, "kubernetes")) == ["a1"]

    for suffix in ("", "-wal", "-shm"):  # deleted while a connection is open
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    assert _ids(text_index.search(tmp_path, "kubernetes")) == ["a1"]

    text_index.close(tmp_path)
    for suffix in ("-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    path.write_bytes(b"this is not a sqlite database" * 100)  # corrupt
    assert _ids(text_index.search(tmp_path, "kubernetes")) == ["a1"]

    text_index.close(tmp_path)
    monkeypatch.setattr(text_index, "SCHEMA_VERSION", text_index.SCHEMA_VERSION + 1)  # version bump
    assert _ids(text_index.search(tmp_path, "kubernetes")) == ["a1"]
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(text_index.SCHEMA_VERSION)
    finally:
        conn.close()


def test_a_sqlite_without_fts5_reports_unavailable_and_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(text_index, "_FTS_DDL", "CREATE VIRTUAL TABLE text USING no_such_module(title)")
    monkeypatch.setattr(text_index, "_FTS_DDL_NO_DELETE", "CREATE VIRTUAL TABLE text USING no_such_module(title)")
    _seed_acme(tmp_path)

    result = text_index.search(tmp_path, "kubernetes")
    assert not result.available and result.hits == () and "FTS5" in (result.reason or "")
    assert text_index.upsert_company(tmp_path, "ashby:acme", [TextPosting("1", "A", "b")]) is False
    assert text_index.remove_company(tmp_path, "ashby:acme") is False
    assert not text_index.rebuild_from_cache(tmp_path).available
    assert not text_index.stats(tmp_path).available


def test_the_index_uses_wal_and_does_not_store_the_text(tmp_path: Path) -> None:
    text_index.upsert_company(tmp_path, "ashby:acme", [TextPosting("1", "Engineer", "distinctivewordzebra")])
    text_index.close(tmp_path)

    raw = text_index.text_index_path(tmp_path)
    conn = sqlite3.connect(raw)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("SELECT description FROM text").fetchall() == [(None,)]
    finally:
        conn.close()
    assert text_index.search(tmp_path, "distinctivewordzebra").hits


def test_speed_sanity_on_20k_synthetic_postings(tmp_path: Path, mode: str, capsys: pytest.CaptureFixture[str]) -> None:
    words = ["kubernetes", "python", "fintech", "agentic", "golang", "payments", "ledger", "search", "rust", "frontend"]
    companies = 200
    started = time.perf_counter()
    for company in range(companies):
        postings = [
            TextPosting(
                f"{company}-{n}",
                f"{words[n % 10].title()} Engineer {n}",
                " ".join(words[(n * 7 + k * 3 + company) % 10] for k in range(120)) + (" zebrafish" if n == 5 and company == 7 else ""),
            )
            for n in range(100)
        ]
        assert text_index.upsert_company(tmp_path, f"ashby:c{company}", postings)
    built = time.perf_counter() - started

    started = time.perf_counter()
    result = text_index.search(tmp_path, "kubernetes AND python", limit=50)
    queried = time.perf_counter() - started
    rare = text_index.search(tmp_path, "zebrafish")

    started = time.perf_counter()
    assert text_index.upsert_company(tmp_path, "ashby:c3", [TextPosting("x", "Replaced", "rust")])
    replaced = time.perf_counter() - started
    with capsys.disabled():
        print(f"\n[text_index speed/{mode}] 20,000 postings: build {built:.2f}s, query {queried * 1000:.1f}ms, replace one company {replaced * 1000:.1f}ms")
    assert text_index.stats(tmp_path).postings == 19901
    assert len(result.hits) == 50 and _ids(rare) == ["7-5"]

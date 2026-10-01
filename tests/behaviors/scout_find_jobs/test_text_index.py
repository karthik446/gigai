"""F1: the full-text index (``text_index.py``), synthetic postings only.

Pins: build from the stored company bodies, add / replace / remove a
company, a keyword that only the description carries, a posting without text
(never returned, counted as unchecked), no Porter stemming, and the rebuild
after a deleted / corrupt / version-bumped file or a SQLite without FTS5.
Nothing here touches the network or real data.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time

import pytest

from gigai.scout.find_jobs import text_index
from gigai.scout.find_jobs.ats_board_clients import BoardCache
from gigai.scout.find_jobs.company_index import CompanyIndex, board_list_url, company_key, refresh_company
from gigai.scout.find_jobs.text_index import TextPosting

pytestmark = pytest.mark.skipif(
    not hasattr(sqlite3, "sqlite_version_info") or sqlite3.sqlite_version_info < (3, 9, 0),
    reason="needs FTS5",
)


@pytest.fixture(autouse=True)
def _close_connections(tmp_path: Path):
    yield
    text_index.close(tmp_path)


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


def test_without_contentless_delete_a_replace_rebuilds_from_the_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(text_index, "_SUPPORTS_CONTENTLESS_DELETE", False)
    _seed_acme(tmp_path)

    assert text_index.upsert_company(tmp_path, "ashby:acme", [TextPosting("zz", "Ignored", "x")])  # rebuilt from the cache instead
    assert _ids(text_index.search(tmp_path, "kubernetes")) == ["a1"]
    assert text_index.remove_company(tmp_path, "ashby:acme")
    assert text_index.search(tmp_path, "kubernetes").available


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


def test_speed_sanity_on_20k_synthetic_postings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
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
        print(f"\n[text_index speed] 20,000 postings: build {built:.2f}s, query {queried * 1000:.1f}ms, replace one company {replaced * 1000:.1f}ms")
    assert text_index.stats(tmp_path).postings == 19901
    assert len(result.hits) == 50 and _ids(rare) == ["7-5"]

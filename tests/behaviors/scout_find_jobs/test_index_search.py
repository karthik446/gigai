"""N11-C: a search reads the company index only (``index_search.read_indexed_boards``).

``gigai scout sources update`` fills the board cache and the company index
over a fake transport; the search side then has to hand acquire the very
rows a board fetch would have handed it -- with no HTTP client at all, so
the fake transport's request log cannot grow. An empty index yields no rows
and the 'Run Update sources' status; a stale one still reads what is stored
and says so.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from gigai.scout.find_jobs.ats_board_clients import ATSBoardClients
from gigai.scout.find_jobs.company_index import CompanyIndex
from gigai.scout.find_jobs.contracts import ATSProvider, SourceKind
from gigai.scout.find_jobs.index_search import (
    SOURCES_UPDATE_REQUIRED_CODE,
    index_line,
    read_indexed_boards,
    read_last_search,
)
from gigai.scout.find_jobs.sources_update import board_cache_for_home, update_sources

from tests.behaviors.scout_find_jobs.test_acquire_scale import _board, _config, _limits

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


class _Boards:
    """Greenhouse ``acme`` (ETag + details) and Lever ``initech``; every request is logged."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.version = 1
        self.honor_content = False  # 0110-026d: True = the board answers ?content=true with descriptions (the fill)
        self.greenhouse = [
            {"id": 11, "title": "Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/11", "location": {"name": "Denver, CO"}, "updated_at": "2026-09-20T00:00:00Z", "first_published": "2026-09-20T00:00:00Z"},
            {"id": 12, "title": "Marketing Manager", "absolute_url": "https://boards.greenhouse.io/acme/jobs/12", "location": {"name": "Remote"}, "updated_at": "2026-09-20T00:00:00Z", "first_published": "2026-09-20T00:00:00Z"},
            # 0110-10-14: up since January and edited last week. The window judges the day it went up, so it is too old.
            {"id": 13, "title": "Software Engineer, Old", "absolute_url": "https://boards.greenhouse.io/acme/jobs/13", "location": {"name": "Denver, CO"}, "updated_at": "2026-09-21T00:00:00Z", "first_published": "2026-01-05T00:00:00Z"},
        ]
        self.lever = [
            {"id": "lev-1", "text": "Staff Software Engineer", "hostedUrl": "https://jobs.lever.co/initech/lev-1", "categories": {"location": "Austin, TX"}, "country": "US", "createdAt": 1790000000000, "descriptionPlain": "Build things."},
            {"id": "lev-2", "text": "Software Engineer", "hostedUrl": "https://jobs.lever.co/initech/lev-2", "categories": {"location": "Berlin"}, "country": "DE", "createdAt": 1790000000000, "descriptionPlain": "Build things in Berlin."},
            {"id": "lev-3", "text": "Office Manager", "hostedUrl": "https://jobs.lever.co/initech/lev-3", "categories": {"location": "Austin, TX"}, "country": "US", "createdAt": 1790000000000, "descriptionPlain": "Run the office."},
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(path)
        etag = f'W/"acme-{self.version}"'
        if path == "/v1/boards/acme/jobs":
            if request.headers.get("if-none-match") == etag:
                return httpx.Response(304, headers={"etag": etag})
            if self.honor_content and request.url.params.get("content") == "true":
                jobs = [{**job, "content": f"&lt;p&gt;Build {job['id']} v{self.version}.&lt;/p&gt;"} for job in self.greenhouse]
                return httpx.Response(200, json={"jobs": jobs}, headers={"etag": etag})
            return httpx.Response(200, json={"jobs": self.greenhouse}, headers={"etag": etag})
        for job in self.greenhouse:
            if path == f"/v1/boards/acme/jobs/{job['id']}":
                return httpx.Response(200, json={**job, "content": f"&lt;p&gt;Build {job['id']} v{self.version}.&lt;/p&gt;"})
        if path == "/v0/postings/initech":
            return httpx.Response(200, json=self.lever)
        return httpx.Response(404, json={"error": "no such board"})


def _watchlist() -> list:
    return [_board(ATSProvider.LEVER, "initech", catalog=True), _board(ATSProvider.GREENHOUSE, "acme")]


def _us_config(**changes):
    # Lever's createdAt above is 2026-09-21; the window is the default 60 days.
    return replace(_config(), countries=("US",), **changes)


def _update(home: Path, boards: _Boards, config=None, full_refresh: bool = False):
    with httpx.Client(transport=httpx.MockTransport(boards.handler)) as client:
        return update_sources(
            _watchlist(),
            cache=board_cache_for_home(home),
            index=CompanyIndex.for_home(home),
            client=client,
            config=config if config is not None else _us_config(),
            limits=_limits(concurrency=2),
            full_refresh=full_refresh,
        )


def _search(home: Path, config=None, *, now: datetime = NOW, watchlist=None, **kwargs):
    return read_indexed_boards(
        _watchlist() if watchlist is None else watchlist,
        index=CompanyIndex.for_home(home),
        cache=board_cache_for_home(home),
        config=config if config is not None else _us_config(),
        now=now,
        **kwargs,
    )


def test_a_search_reads_the_rows_a_fetch_would_return_with_zero_board_requests(tmp_path: Path) -> None:
    boards = _Boards()
    _update(tmp_path, boards)
    # What the fetch path itself returns for these boards (against the same
    # cache), to compare the index read with.
    with httpx.Client(transport=httpx.MockTransport(boards.handler)) as client:
        fetched = {
            row.url: row
            for provider, token in (("greenhouse", "acme"), ("lever", "initech"))
            for row in ATSBoardClients().fetch_board(client, provider, token, _us_config(), cache=board_cache_for_home(tmp_path)).rows
        }
    requests_before = list(boards.requests)

    rows, failures, summary = _search(tmp_path)

    assert boards.requests == requests_before, "the search made a board request"
    assert failures == []
    # The user-added board first, then the catalog one; the title, the
    # 60-day window (job 13) and the country rule (lev-2, Berlin) applied.
    assert [row.url for row in rows] == [
        "https://boards.greenhouse.io/acme/jobs/11",
        "https://jobs.lever.co/initech/lev-1",
    ]
    assert rows == [fetched[row.url] for row in rows]
    assert rows[0].text == "Build 11 v1." and rows[1].text == "Build things."
    assert all(row.source_kind is SourceKind.ATS for row in rows)
    assert summary["source"] == "index" and summary["requests"] == 0
    assert summary["total"] == 2 and summary["cached"] == 2 and summary["skipped"] == 0 and summary["fetched"] == 0
    assert summary["listed"] == 6 and summary["prefiltered_out"] == 2 and summary["filtered_out"] == 2
    assert summary["matched"] == 2 and summary["companies_matched"] == 2
    assert summary["without_text"] == 0 and summary["not_cached"] == 0
    assert summary["index"]["status"] == "ready" and summary["index"]["needs_update"] is False
    assert index_line(summary).startswith("scout acquire: read the company index (ready): 2 of 2 companies, 6 postings stored, 2 matched")
    assert "0 board requests" in index_line(summary)


def test_an_empty_index_returns_no_rows_and_says_run_update_sources(tmp_path: Path) -> None:
    rows, failures, summary = _search(tmp_path)

    assert rows == []
    assert [(failure.source_kind, failure.code) for failure in failures] == [(SourceKind.ATS, SOURCES_UPDATE_REQUIRED_CODE)]
    assert failures[0].message == "No company postings are stored on this machine yet. Run Update sources, then search again."
    assert summary["requests"] == 0 and summary["cached"] == 0 and summary["skipped"] == 2
    assert summary["index"]["status"] == "empty" and summary["index"]["needs_update"] is True
    assert summary["index"]["message"] == failures[0].message
    # Nothing was read, so nothing is remembered as searched.
    assert read_last_search(CompanyIndex.for_home(tmp_path)) is None
    assert not (tmp_path / "cache").exists()


def test_an_empty_watchlist_is_not_a_missing_index(tmp_path: Path) -> None:
    rows, failures, summary = _search(tmp_path, watchlist=[])

    assert rows == [] and failures == []
    assert summary["total"] == 0


def test_a_stale_index_is_still_read_and_flagged(tmp_path: Path) -> None:
    boards = _Boards()
    _update(tmp_path, boards)
    requests_before = list(boards.requests)
    later = datetime.now(timezone.utc) + timedelta(hours=30)

    rows, failures, summary = _search(tmp_path, now=later)

    assert boards.requests == requests_before
    assert [row.url for row in rows] == ["https://boards.greenhouse.io/acme/jobs/11", "https://jobs.lever.co/initech/lev-1"]
    assert failures == []
    assert summary["index"]["status"] == "stale" and summary["index"]["needs_update"] is True
    assert summary["index"]["message"] == "The stored company postings are out of date. Run Update sources, then search again."


def test_a_company_that_was_never_indexed_is_skipped_not_fetched(tmp_path: Path) -> None:
    boards = _Boards()
    _update(tmp_path, boards)
    requests_before = list(boards.requests)
    watchlist = [*_watchlist(), _board(ATSProvider.ASHBY, "new-co")]

    rows, failures, summary = _search(tmp_path, watchlist=watchlist)

    assert boards.requests == requests_before
    assert len(rows) == 2 and failures == []
    assert summary["total"] == 3 and summary["cached"] == 2 and summary["skipped"] == 1
    assert summary["index"]["not_indexed"] == 1 and summary["index"]["status"] == "ready"


def test_the_summary_counts_what_is_new_or_changed_since_the_last_search(tmp_path: Path) -> None:
    boards = _Boards()
    _update(tmp_path, boards)
    index = CompanyIndex.for_home(tmp_path)

    _rows, _failures, first = _search(tmp_path, now=datetime.now(timezone.utc))
    assert first["last_search_at"] is None and first["touched_since_last_search"] == 2
    assert read_last_search(index) == first["searched_at"]

    _rows, _failures, second = _search(tmp_path, now=datetime.now(timezone.utc))
    assert second["last_search_at"] == first["searched_at"] and second["touched_since_last_search"] == 0
    assert second["matched"] == 2, "unchanged postings are still returned: acquire's reuse rule decides what to do with them"

    boards.version = 2
    boards.honor_content = True  # a Full refresh redoes the description fill, which brings the new text
    boards.greenhouse[0] = {**boards.greenhouse[0], "updated_at": "2026-09-26T00:00:00Z"}
    _update(tmp_path, boards, full_refresh=True)  # the boards were just checked: force the re-check
    rows, _failures, third = _search(tmp_path, now=datetime.now(timezone.utc))
    assert third["touched_since_last_search"] == 1
    # 0110-10-14: the edit is the news (one posting changed, its new text is read); the day it went up does not move.
    assert rows[0].text == "Build 11 v2." and rows[0].published_at == "2026-09-20T00:00:00Z"
    assert index.read("greenhouse", "acme").postings["11"].updated_at == "2026-09-26T00:00:00Z"  # type: ignore[union-attr]

    # A dry read (the API's preview) does not move the marker.
    before = read_last_search(index)
    _search(tmp_path, now=datetime.now(timezone.utc), remember_search=False)
    assert read_last_search(index) == before


def test_a_title_the_update_did_not_fetch_a_description_for_is_returned_without_text(tmp_path: Path) -> None:
    boards = _Boards()
    _update(tmp_path, boards)
    requests_before = list(boards.requests)

    # The roles changed after the update: job 12's detail was never fetched.
    rows, failures, summary = _search(tmp_path, config=_us_config(roles=("marketing manager",)))

    assert boards.requests == requests_before
    assert failures == []
    assert [row.title for row in rows] == ["Marketing Manager"]
    assert rows[0].text is None
    assert summary["matched"] == 1 and summary["without_text"] == 1


class _Progress:
    def __init__(self) -> None:
        self.planned: list[dict] = []
        self.finished: list[dict] = []

    def boards_planned(self, **kwargs) -> None:
        self.planned.append(kwargs)

    def board_finished(self, **kwargs) -> None:
        self.finished.append(kwargs)


def test_progress_gets_one_line_per_company_with_zero_requests(tmp_path: Path) -> None:
    _update(tmp_path, _Boards())
    progress = _Progress()

    _search(tmp_path, watchlist=[*_watchlist(), _board(ATSProvider.ASHBY, "new-co")], progress=progress)

    assert progress.planned == [{"total": 3, "budget_seconds": None, "rotation": None}]
    lines = [(item["provider"], item["board_token"], item["status"], item["cache"], item["requests"], item["postings"], item["matched"], item["code"]) for item in progress.finished]
    assert lines == [
        ("ashby", "new-co", "skipped", None, 0, 0, 0, SOURCES_UPDATE_REQUIRED_CODE),
        ("greenhouse", "acme", "cached", "index", 0, 3, 1, None),
        ("lever", "initech", "cached", "index", 0, 3, 1, None),
    ]


def test_the_search_module_has_no_http_client() -> None:
    import gigai.scout.find_jobs.index_search as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "import httpx" not in source and "client.get" not in source


@pytest.mark.parametrize("roles", [(), ("no such title",)])
def test_roles_that_match_nothing_return_nothing(tmp_path: Path, roles: tuple[str, ...]) -> None:
    _update(tmp_path, _Boards())

    rows, failures, summary = _search(tmp_path, config=_us_config(roles=roles))

    assert rows == [] and failures == []
    assert summary["prefiltered_out"] == 6 and summary["matched"] == 0

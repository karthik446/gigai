"""0110-026c (S2): a board with no cached body is requested conditionally from the company index's validators.

Fake transport only (``httpx.MockTransport``), no network. The index entry stands in for one imported from a snapshot.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from gigai.scout.find_jobs.ats_board_clients import ATSBoardClients, BoardCache
from gigai.scout.find_jobs.company_index import (
    STATUS_UNTOUCHED,
    STATUS_UPDATED,
    CompanyIndex,
    CompanyIndexEntry,
    IndexedPosting,
    refresh_company,
    slug_from_list_url,
    board_list_url,
)
from gigai.scout.find_jobs.contracts import FindJobsConfig, SourceToggles

IMPORTED_AT = "2026-09-27T10:00:00.000Z"
NOW = "2026-09-28T09:00:00.000Z"
ETAG = '"snap-etag-1"'
LAST_MODIFIED = "Sun, 27 Sep 2026 10:00:00 GMT"


def _config() -> FindJobsConfig:
    return FindJobsConfig(
        roles=("software engineer",),
        merged_queries=("software engineer",),
        location="Denver, CO",
        remote=True,
        published_after=None,
        sources=SourceToggles(exa=False, ats=True, hiringcafe=False),
    )


class _Board:
    """One board list endpoint: ETag/Last-Modified aware, records every request."""

    def __init__(self, provider: str, *, etag: str | None = ETAG, last_modified: str | None = None) -> None:
        self.provider = provider
        self.etag = etag
        self.last_modified = last_modified
        self.requests: list[dict[str, str]] = []
        self.jobs = [
            {"id": 11, "title": "Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/11", "location": {"name": "Denver, CO"}, "updated_at": "2026-09-20T00:00:00Z"}
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append({k.lower(): v for k, v in request.headers.items() if k.lower().startswith("if-")} | {"url": str(request.url)})
        headers = {}
        if self.etag:
            headers["ETag"] = self.etag
        if self.last_modified:
            headers["Last-Modified"] = self.last_modified
        if (self.etag and request.headers.get("if-none-match") == self.etag) or (
            self.last_modified and request.headers.get("if-modified-since") == self.last_modified
        ):
            return httpx.Response(304, headers=headers)
        return httpx.Response(200, headers=headers, content=json.dumps({"jobs": self.jobs}).encode())


def _seed_index(tmp_path: Path, *, etag: str | None = ETAG, last_modified: str | None = None) -> tuple[CompanyIndex, BoardCache]:
    """An index entry as an imported snapshot leaves it (postings + validators), and an EMPTY body cache beside it."""

    index = CompanyIndex(tmp_path / "companies")
    cache = BoardCache(tmp_path / "ats-boards")
    posting = IndexedPosting(
        posting_id="11",
        title="Software Engineer",
        location="Denver, CO",
        url="https://boards.greenhouse.io/acme/jobs/11",
        updated_at="2026-09-20T00:00:00Z",
        content_sha256=None,
        first_seen=IMPORTED_AT,
        last_seen=IMPORTED_AT,
    )
    index.write(
        CompanyIndexEntry(
            company="Acme",
            ats="greenhouse",
            slug="acme",
            checked_at=IMPORTED_AT,
            etag=etag,
            body_sha256="sha256:imported",
            postings={"11": posting},
            last_modified=last_modified,
        )
    )
    return index, cache


def _fetch(board: _Board, cache: BoardCache):
    with httpx.Client(transport=httpx.MockTransport(board.handler)) as client:
        return ATSBoardClients().fetch_board(client, "greenhouse", "acme", _config(), cache=cache)


def test_imported_etag_and_unchanged_board_is_one_304_no_body_entries_untouched(tmp_path: Path) -> None:
    index, cache = _seed_index(tmp_path)
    before = index.read("greenhouse", "acme")
    board = _Board("greenhouse")

    result = _fetch(board, cache)

    assert len(board.requests) == 1
    assert board.requests[0]["if-none-match"] == ETAG
    assert result.stats.requests == 1
    assert result.stats.cache == "hit"
    assert result.rows == ()
    assert not list((tmp_path / "ats-boards").rglob("*.body.gz"))  # no body fetched or stored
    assert cache.lookup("greenhouse", board_list_url("greenhouse", "acme")) is None

    change = refresh_company(index, cache, ats="greenhouse", slug="acme", observed_at=NOW)

    assert change.status == STATUS_UNTOUCHED
    after = index.read("greenhouse", "acme")
    assert after.postings == before.postings
    assert after.body_sha256 == before.body_sha256
    assert (after.etag, after.last_modified) == (ETAG, None)
    assert after.checked_at == NOW


def test_304_with_a_fresh_validator_refreshes_it_in_the_index(tmp_path: Path) -> None:
    index, cache = _seed_index(tmp_path, etag=None, last_modified=LAST_MODIFIED)
    board = _Board("greenhouse", etag=None, last_modified=LAST_MODIFIED)

    _fetch(board, cache)
    assert board.requests[0]["if-modified-since"] == LAST_MODIFIED
    assert "if-none-match" not in board.requests[0]
    assert refresh_company(index, cache, ats="greenhouse", slug="acme", observed_at=NOW).status == STATUS_UNTOUCHED
    assert index.read("greenhouse", "acme").last_modified == LAST_MODIFIED


def test_changed_board_is_a_normal_update_and_stores_the_new_validator(tmp_path: Path) -> None:
    index, cache = _seed_index(tmp_path)
    board = _Board("greenhouse", etag='"snap-etag-2"')
    board.jobs.append(
        {"id": 12, "title": "Software Engineer II", "absolute_url": "https://boards.greenhouse.io/acme/jobs/12", "location": {"name": "Remote"}, "updated_at": "2026-09-27T00:00:00Z"}
    )

    result = _fetch(board, cache)

    assert board.requests[0]["if-none-match"] == ETAG  # still conditional; the board answered 200
    assert result.stats.cache == "miss"
    assert any(row for row in result.rows)
    change = refresh_company(index, cache, ats="greenhouse", slug="acme", observed_at=NOW, details=False)
    assert change.status == STATUS_UPDATED
    assert change.new == ("12",)
    entry = index.read("greenhouse", "acme")
    assert entry.etag == '"snap-etag-2"'
    assert set(entry.postings) == {"11", "12"}


def test_no_validator_stays_unconditional_as_before(tmp_path: Path) -> None:
    index, cache = _seed_index(tmp_path, etag=None, last_modified=None)
    board = _Board("greenhouse", etag=None)

    result = _fetch(board, cache)

    assert board.requests[0] == {"url": board.requests[0]["url"]}  # no If-* header at all
    assert result.stats.cache == "miss"
    assert cache.lookup("greenhouse", board_list_url("greenhouse", "acme")) is not None


def test_no_company_index_stays_unconditional(tmp_path: Path) -> None:
    cache = BoardCache(tmp_path / "ats-boards")  # no sibling companies/ directory
    board = _Board("greenhouse")

    _fetch(board, cache)

    assert not any(k.startswith("if-") for k in board.requests[0])


def test_cached_body_still_wins_over_index_validators(tmp_path: Path) -> None:
    index, cache = _seed_index(tmp_path)
    board = _Board("greenhouse", etag='"cached-etag"')
    _fetch(board, cache)  # 200 stores a body with its own etag
    board.requests.clear()

    result = _fetch(board, cache)

    assert board.requests[0]["if-none-match"] == '"cached-etag"'
    assert result.stats.cache == "hit"
    assert len(result.rows) == 1  # the cached body is read as before


def test_detail_urls_never_take_index_validators(tmp_path: Path) -> None:
    index, _ = _seed_index(tmp_path)
    assert index.validators_for_url("greenhouse", board_list_url("greenhouse", "acme")) == (ETAG, None)
    assert index.validators_for_url("greenhouse", "https://boards-api.greenhouse.io/v1/boards/acme/jobs/11") is None
    assert slug_from_list_url("greenhouse", "https://example.test/other") is None

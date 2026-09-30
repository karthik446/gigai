"""N11-C: the company index behind the real board client (fake transport, no network).

``test_company_index.py`` pins the index on its own. Here the existing
``ATSBoardClients.fetch_board`` makes the (conditional) requests against an
``httpx.MockTransport`` and leaves the bodies in the ``BoardCache``;
``refresh_company`` then reads that cache and must never make a request of
its own: a ``304`` and a ``200`` with the same bytes both leave the company
untouched, and a new body is diffed into new / changed / removed postings.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from gigai.scout.find_jobs.ats_board_clients import ATSBoardClients, BoardCache
from gigai.scout.find_jobs.company_index import (
    STATUS_INDEXED,
    STATUS_UNTOUCHED,
    STATUS_UPDATED,
    CompanyIndex,
    cached_posting_rows,
    refresh_company,
)
from gigai.scout.find_jobs.contracts import FindJobsConfig, SourceToggles, content_hash

T1 = "2026-09-27T10:00:00.000Z"
T2 = "2026-09-27T16:00:00.000Z"
T3 = "2026-09-28T09:00:00.000Z"


def _config() -> FindJobsConfig:
    return FindJobsConfig(
        roles=("software engineer",),
        merged_queries=("software engineer",),
        location="Denver, CO",
        remote=True,
        published_after=None,
        sources=SourceToggles(exa=False, ats=True, hiringcafe=False),
    )


class _Greenhouse:
    """An ETag'd content-free list (``304`` when it matches) plus one detail per job id."""

    def __init__(self) -> None:
        self.version = 1
        self.requests: list[tuple[str, str | None]] = []
        self.jobs = [
            {"id": 11, "title": "Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/11", "location": {"name": "Denver, CO"}, "updated_at": "2026-09-20T00:00:00Z"},
            {"id": 12, "title": "Marketing Manager", "absolute_url": "https://boards.greenhouse.io/acme/jobs/12", "location": {"name": "Remote"}, "updated_at": "2026-09-20T00:00:00Z"},
        ]

    @property
    def etag(self) -> str:
        return f'W/"acme-{self.version}"'

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((path, request.headers.get("if-none-match")))
        if path == "/v1/boards/acme/jobs":
            if request.headers.get("if-none-match") == self.etag:
                return httpx.Response(304, headers={"etag": self.etag})
            return httpx.Response(200, json={"jobs": self.jobs}, headers={"etag": self.etag})
        for job in self.jobs:
            if path == f"/v1/boards/acme/jobs/{job['id']}":
                return httpx.Response(200, json={**job, "content": f"&lt;p&gt;Build {job['id']} v{self.version}.&lt;/p&gt;"})
        return httpx.Response(404, json={"error": "no fixture"})


def _stores(home: Path) -> tuple[CompanyIndex, BoardCache]:
    return CompanyIndex.for_home(home), BoardCache(home / "cache" / "scout" / "ats-boards")


def _fetch(handler, cache: BoardCache, provider: str, token: str):
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return ATSBoardClients().fetch_board(client, provider, token, _config(), cache=cache)


def test_a_304_from_the_board_leaves_the_company_untouched(tmp_path: Path) -> None:
    board = _Greenhouse()
    index, cache = _stores(tmp_path)

    _fetch(board.handler, cache, "greenhouse", "acme")
    fetched = len(board.requests)
    first = refresh_company(index, cache, ats="greenhouse", slug="acme", company="Acme", observed_at=T1)

    assert first.status == STATUS_INDEXED and first.new == ("11", "12")
    # The index read the cache: not one request more than the fetch made
    # (the list + the one detail whose title matches the roles).
    assert fetched == 2 and len(board.requests) == fetched
    entry = index.read("greenhouse", "acme")
    assert entry is not None and entry.etag == 'W/"acme-1"'
    # Every listed title is indexed, not only the one the roles match; the
    # digest exists where the fetch cached a detail.
    assert entry.postings["11"].content_sha256 == content_hash(b"Software Engineer\nBuild 11 v1.")
    assert entry.postings["12"].title == "Marketing Manager" and entry.postings["12"].content_sha256 is None
    before = index.path("greenhouse", "acme").read_text(encoding="utf-8")

    board.requests.clear()
    second = _fetch(board.handler, cache, "greenhouse", "acme")
    assert board.requests == [("/v1/boards/acme/jobs", 'W/"acme-1"')]
    assert second.stats.cache == "hit" and second.stats.requests == 1

    change = refresh_company(index, cache, ats="greenhouse", slug="acme", observed_at=T2)

    assert change.status == STATUS_UNTOUCHED and not change.has_changes and change.live == 2
    assert len(board.requests) == 1
    after = json.loads(index.path("greenhouse", "acme").read_text(encoding="utf-8"))
    assert after["checked_at"] == T2
    assert {**after, "checked_at": T1} == json.loads(before)


def test_a_new_board_body_is_diffed_into_new_changed_and_removed(tmp_path: Path) -> None:
    board = _Greenhouse()
    index, cache = _stores(tmp_path)
    _fetch(board.handler, cache, "greenhouse", "acme")
    refresh_company(index, cache, ats="greenhouse", slug="acme", company="Acme", observed_at=T1)

    board.version = 2
    board.jobs = [
        {**board.jobs[0], "updated_at": "2026-09-27T00:00:00Z"},
        {"id": 13, "title": "Staff Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/13", "location": {"name": "Remote"}, "updated_at": "2026-09-27T00:00:00Z"},
    ]
    board.requests.clear()
    _fetch(board.handler, cache, "greenhouse", "acme")
    fetched = len(board.requests)

    change = refresh_company(index, cache, ats="greenhouse", slug="acme", observed_at=T2)

    assert len(board.requests) == fetched
    assert change.status == STATUS_UPDATED
    assert change.new == ("13",) and change.changed == ("11",) and change.removed == ("12",)
    entry = index.read("greenhouse", "acme")
    assert entry is not None and entry.etag == 'W/"acme-2"' and entry.changed_at == T2
    assert entry.postings["11"].first_seen == T1 and entry.postings["11"].changed_at == T2
    assert entry.postings["11"].content_sha256 == content_hash(b"Software Engineer\nBuild 11 v2.")
    assert entry.postings["13"].first_seen == T2
    assert entry.postings["12"].removed_at == T2
    assert [posting.posting_id for posting in entry.touched_since(T1)] == ["11", "13"]

    # What a search would hand to ranking and assessment: the acquire rows,
    # read back from the cache.
    rows = cached_posting_rows(cache, "greenhouse", "acme", ["11", "13"])
    assert len(board.requests) == fetched
    assert [row.text for row in rows.rows.values()] == ["Build 11 v2.", "Build 13 v2."]
    assert rows.without_text == () and rows.missing == ()


def test_a_200_with_the_same_bytes_leaves_a_lever_company_untouched(tmp_path: Path) -> None:
    requests: list[str] = []
    jobs = [
        {"id": "lev-1", "text": "Software Engineer", "hostedUrl": "https://jobs.lever.co/acme/lev-1", "categories": {"location": "Austin, TX"}, "createdAt": 1758326400000, "descriptionPlain": "Build things."},
        {"id": "lev-2", "text": "Office Manager", "hostedUrl": "https://jobs.lever.co/acme/lev-2", "categories": {"location": "Berlin"}, "createdAt": 1758412800000, "descriptionPlain": "Run the office."},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(200, json=jobs)  # Lever sends no ETag

    index, cache = _stores(tmp_path)
    _fetch(handler, cache, "lever", "acme")
    first = refresh_company(index, cache, ats="lever", slug="acme", company="Acme", observed_at=T1)
    assert first.status == STATUS_INDEXED and first.new == ("lev-1", "lev-2")

    again = _fetch(handler, cache, "lever", "acme")
    assert again.stats.cache == "revalidated"
    change = refresh_company(index, cache, ats="lever", slug="acme", observed_at=T2)

    assert change.status == STATUS_UNTOUCHED
    entry = index.read("lever", "acme")
    assert entry is not None and entry.checked_at == T2
    assert {posting.last_seen for posting in entry.postings.values()} == {T1}

    jobs[1] = {**jobs[1], "descriptionPlain": "Run the office and the kitchen."}
    _fetch(handler, cache, "lever", "acme")
    change = refresh_company(index, cache, ats="lever", slug="acme", observed_at=T3)

    assert change.status == STATUS_UPDATED and change.changed == ("lev-2",) and change.new == () and change.removed == ()
    assert requests == ["/v0/postings/acme"] * 3


def test_a_lever_company_indexed_before_the_text_fix_is_reread_once_and_marked_changed(tmp_path: Path) -> None:
    # uat-bug-046: the cached body is byte-identical, but the postings' text now
    # includes the full HTML description, so an index built by the older parser
    # must not be skipped as "untouched".
    from dataclasses import replace

    filler = "".join(f"<p>Paragraph {n} about how we work together and what we value.</p>" for n in range(30))
    jobs = [
        {
            "id": "lev-1",
            "text": "Software Engineer",
            "hostedUrl": "https://jobs.lever.co/acme/lev-1",
            "categories": {"location": "Austin, TX"},
            "createdAt": 1758326400000,
            "descriptionPlain": "Lead the vision.",
            "lists": [],
            "description": f"<p>Lead the vision.</p>{filler}<ul><li>7+ years of PostgreSQL</li></ul>",
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=jobs)

    index, cache = _stores(tmp_path)
    _fetch(handler, cache, "lever", "acme")
    refresh_company(index, cache, ats="lever", slug="acme", company="Acme", observed_at=T1)
    entry = index.read("lever", "acme")
    assert entry is not None
    raw_digest = cache.lookup("lever", "https://api.lever.co/v0/postings/acme?mode=json").sha256
    assert entry.body_sha256 != raw_digest  # tagged with the text revision

    # What the older parser stored: the raw body digest and the intro-only text hash.
    old_hash = content_hash(b"Software Engineer\nLead the vision.")
    stale = replace(entry, body_sha256=raw_digest, postings={"lev-1": replace(entry.postings["lev-1"], content_sha256=old_hash)})
    index.write(stale)

    change = refresh_company(index, cache, ats="lever", slug="acme", observed_at=T2)
    assert change.status == STATUS_UPDATED and change.changed == ("lev-1",)

    again = refresh_company(index, cache, ats="lever", slug="acme", observed_at=T3)
    assert again.status == STATUS_UNTOUCHED

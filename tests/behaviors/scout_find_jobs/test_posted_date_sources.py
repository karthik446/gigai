"""0110-10-14 (the correction): a board's POSTED date is the day the posting went up, never its last change.

What each provider's public list gives (synthetic payloads shaped after the fields; no request leaves the process):

- Greenhouse: ``first_published`` beside ``updated_at``, on the list and on the job. ``published_at`` is
  ``first_published``; ``updated_at`` stays the index's separate "updated" value. A job with no ``first_published``
  has NO posted date (its ``updated_at`` is never one).
- Lever: ``createdAt`` (epoch ms), the only date in the list.
- Ashby: ``publishedAt``.

And the upgrade: an index file written before ``first_published`` was read holds the list's ``updated_at`` under
``published_at`` for every Greenhouse posting. That date is not read as a posted date, and the NEXT sources update
puts the real one there, also for a board whose body did not change (a ``304``), without a request more than the
update makes anyway and without calling an unchanged posting "changed".
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from gigai.scout.find_jobs.ats_board_clients import ATSBoardClients, BoardCache
from gigai.scout.find_jobs.company_index import (
    STATUS_UNTOUCHED,
    CompanyIndex,
    cached_posting_rows,
    refresh_company,
)
from gigai.scout.find_jobs.contracts import FindJobsConfig, SourceToggles

T1 = "2026-09-27T10:00:00.000Z"
T2 = "2026-10-04T16:00:00.000Z"
T3 = "2026-10-05T09:00:00.000Z"
#: The operator's example: up for two months, edited three days ago.
_FIRST_PUBLISHED = "2026-08-02T09:30:00-04:00"
_UPDATED = "2026-10-01T11:15:00-04:00"


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
    """An ETag'd list (``304`` when it matches) plus one detail per job id. Job 12 has no ``first_published``."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str | None]] = []
        self.jobs: list[dict[str, object]] = [
            {
                "id": 11, "title": "Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/11",
                "location": {"name": "Denver, CO"}, "updated_at": _UPDATED, "first_published": _FIRST_PUBLISHED,
            },
            {
                "id": 12, "title": "Marketing Manager", "absolute_url": "https://boards.greenhouse.io/acme/jobs/12",
                "location": {"name": "Remote"}, "updated_at": _UPDATED, "first_published": None,
            },
        ]

    etag = 'W/"acme-1"'

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((path, request.headers.get("if-none-match")))
        if path == "/v1/boards/acme/jobs":
            if request.headers.get("if-none-match") == self.etag:
                return httpx.Response(304, headers={"etag": self.etag})
            return httpx.Response(200, json={"jobs": self.jobs}, headers={"etag": self.etag})
        for job in self.jobs:
            if path == f"/v1/boards/acme/jobs/{job['id']}":
                return httpx.Response(200, json={**job, "content": f"&lt;p&gt;Build {job['id']}.&lt;/p&gt;"})
        return httpx.Response(404, json={"error": "no fixture"})


def _stores(home: Path) -> tuple[CompanyIndex, BoardCache]:
    return CompanyIndex.for_home(home), BoardCache(home / "cache" / "scout" / "ats-boards")


def _fetch(handler, cache: BoardCache, provider: str, token: str):
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return ATSBoardClients().fetch_board(client, provider, token, _config(), cache=cache)


def _as_stored_before(index: CompanyIndex) -> None:
    """Rewrite acme's index file as the version before this one wrote it: ``published_at`` is the list's ``updated_at``."""

    path = index.path("greenhouse", "acme")
    stored = json.loads(path.read_text(encoding="utf-8"))
    for posting in stored["postings"].values():
        posting.pop("published_kind", None)
        posting["published_at"] = posting["updated_at"]
    path.write_text(json.dumps(stored, separators=(",", ":"), sort_keys=True), encoding="utf-8")


def test_a_greenhouse_postings_date_is_first_published_and_its_last_change_is_kept_beside_it(tmp_path: Path) -> None:
    board = _Greenhouse()
    index, cache = _stores(tmp_path)

    fetched = _fetch(board.handler, cache, "greenhouse", "acme")
    refresh_company(index, cache, ats="greenhouse", slug="acme", company="Acme", observed_at=T1)

    # The acquire row (what a search hands on) and the row read back from the cache: the day the posting went up.
    assert [row.published_at for row in fetched.rows] == [_FIRST_PUBLISHED]
    assert cached_posting_rows(cache, "greenhouse", "acme", ["11"]).rows["11"].published_at == _FIRST_PUBLISHED
    entry = index.read("greenhouse", "acme")
    assert entry is not None
    assert (entry.postings["11"].published_at, entry.postings["11"].updated_at) == (_FIRST_PUBLISHED, _UPDATED)
    # A posting the board gives no first day for has NO posted date: its last change is never one.
    assert (entry.postings["12"].published_at, entry.postings["12"].updated_at) == (None, _UPDATED)


def test_a_greenhouse_job_takes_first_published_from_its_detail_when_the_list_has_none(tmp_path: Path) -> None:
    board = _Greenhouse()
    listed = dict(board.jobs[0])
    del listed["first_published"]
    index, cache = _stores(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/boards/acme/jobs":
            return httpx.Response(200, json={"jobs": [listed]})
        return httpx.Response(200, json={**listed, "first_published": _FIRST_PUBLISHED, "content": "&lt;p&gt;Build.&lt;/p&gt;"})

    fetched = _fetch(handler, cache, "greenhouse", "acme")

    assert [row.published_at for row in fetched.rows] == [_FIRST_PUBLISHED]
    del index


def test_lever_and_ashby_dates_are_the_day_the_posting_went_up(tmp_path: Path) -> None:
    index, cache = _stores(tmp_path)
    lever = [{
        "id": "lev-1", "text": "Software Engineer", "hostedUrl": "https://jobs.lever.co/acme/lev-1", "categories": {"location": "Austin, TX"},
        "descriptionPlain": "Build services.", "createdAt": 1758326400000,
    }]
    ashby = {"jobs": [{
        "id": "ash-1", "title": "Software Engineer", "jobUrl": "https://jobs.ashbyhq.com/acme/ash-1", "location": "Remote",
        "descriptionPlain": "Build services.", "publishedAt": "2026-09-19T12:00:00.000+00:00",
    }]}

    lever_rows = _fetch(lambda request: httpx.Response(200, json=lever, request=request), cache, "lever", "acme")
    ashby_rows = _fetch(lambda request: httpx.Response(200, json=ashby, request=request), cache, "ashby", "acme")
    refresh_company(index, cache, ats="lever", slug="acme", observed_at=T1)
    refresh_company(index, cache, ats="ashby", slug="acme", observed_at=T1)

    assert [row.published_at for row in lever_rows.rows] == ["2025-09-20T00:00:00Z"]
    assert [row.published_at for row in ashby_rows.rows] == ["2026-09-19T12:00:00.000+00:00"]
    for ats, posting_id, published in (("lever", "lev-1", "2025-09-20T00:00:00Z"), ("ashby", "ash-1", "2026-09-19T12:00:00.000+00:00")):
        entry = index.read(ats, "acme")
        assert entry is not None and entry.postings[posting_id].published_at == published
        # Their stored date always was the posting's day: a file written before this version reads the same.
        path = index.path(ats, "acme")
        stored = json.loads(path.read_text(encoding="utf-8"))
        for posting in stored["postings"].values():
            posting.pop("published_kind", None)
        path.write_text(json.dumps(stored), encoding="utf-8")
        again = index.read(ats, "acme")
        assert again is not None and again.postings[posting_id].published_at == published and not again.dates_pending


def test_an_index_from_before_gets_first_published_on_the_next_update_though_the_board_did_not_change(tmp_path: Path) -> None:
    board = _Greenhouse()
    index, cache = _stores(tmp_path)
    _fetch(board.handler, cache, "greenhouse", "acme")
    refresh_company(index, cache, ats="greenhouse", slug="acme", company="Acme", observed_at=T1)
    _as_stored_before(index)

    # Until the update: the stored date is the posting's last change, so it is not a posted date at all.
    before = index.read("greenhouse", "acme")
    assert before is not None and before.dates_pending
    assert (before.postings["11"].published_at, before.postings["11"].updated_at) == (None, _UPDATED)

    # The next sources update: the board answers 304 (nothing changed). One request, the one the update makes anyway.
    board.requests.clear()
    _fetch(board.handler, cache, "greenhouse", "acme")
    assert board.requests == [("/v1/boards/acme/jobs", 'W/"acme-1"')]
    change = refresh_company(index, cache, ats="greenhouse", slug="acme", observed_at=T2)

    assert len(board.requests) == 1
    entry = index.read("greenhouse", "acme")
    assert entry is not None and not entry.dates_pending
    assert (entry.postings["11"].published_at, entry.postings["11"].updated_at) == (_FIRST_PUBLISHED, _UPDATED)
    assert entry.postings["12"].published_at is None
    # Nothing about the postings changed: no posting is new or changed for it, and what was known stays.
    # (So the update's summary, the text index and the tags have nothing to do for this company.)
    assert change.status == STATUS_UNTOUCHED and not change.has_changes and change.live == 2
    assert entry.changed_at is None
    assert (entry.postings["11"].first_seen, entry.postings["11"].changed_at) == (T1, None)
    assert entry.postings["11"].content_sha256 == before.postings["11"].content_sha256 is not None
    assert entry.etag == 'W/"acme-1"'

    # And it is done once: the update after that leaves the company untouched again (job 12 still has no date).
    _fetch(board.handler, cache, "greenhouse", "acme")
    third = refresh_company(index, cache, ats="greenhouse", slug="acme", observed_at=T3)
    assert third.status == STATUS_UNTOUCHED
    stored = json.loads(index.path("greenhouse", "acme").read_text(encoding="utf-8"))
    assert {**stored, "checked_at": T2} == entry.to_json()


def test_a_board_from_before_with_no_cached_body_is_read_in_full_by_the_updates_own_request(tmp_path: Path) -> None:
    """An imported snapshot's board: an index file and validators, no body. A 304 would leave the old date for ever."""

    board = _Greenhouse()
    index, cache = _stores(tmp_path)
    _fetch(board.handler, cache, "greenhouse", "acme")
    refresh_company(index, cache, ats="greenhouse", slug="acme", company="Acme", observed_at=T1)
    list_url = "https://boards-api.greenhouse.io/v1/boards/acme/jobs"
    for path in cache._paths("greenhouse", list_url):
        path.unlink()

    # A file this version wrote: its validators make the request conditional (a 304, nothing downloaded).
    assert index.validators_for_url("greenhouse", list_url) == ('W/"acme-1"', None)
    _as_stored_before(index)
    # A file from before: no validators are offered, so the one list request brings the body.
    assert index.validators_for_url("greenhouse", list_url) is None

    board.requests.clear()
    _fetch(board.handler, cache, "greenhouse", "acme")
    assert [request for request in board.requests if request[0] == "/v1/boards/acme/jobs"] == [("/v1/boards/acme/jobs", None)]
    listed = len(board.requests)
    change = refresh_company(index, cache, ats="greenhouse", slug="acme", observed_at=T2)

    assert len(board.requests) == listed
    entry = index.read("greenhouse", "acme")
    assert entry is not None and not entry.dates_pending and not change.has_changes
    assert (entry.postings["11"].published_at, entry.postings["11"].first_seen) == (_FIRST_PUBLISHED, T1)

"""N11-C: the company-keyed posting index (``company_index.py``).

Operator direction 2026-09-27: sources are refreshed by ``gigai scout sources
update``; a search reads the local index only. These tests pin the index
itself: the schema, change detection per company (new / changed / removed /
``304`` / same body digest), reading a cached board body with the existing
ATS parsers, the rebuild after a delete, and the atomic write.

Nothing here touches the network: bodies are put into a ``BoardCache`` under
``tmp_path`` directly (``test_company_index_fetch.py`` drives the real board
client over a fake transport).
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

import pytest

from gigai.scout.find_jobs import company_index
from gigai.scout.find_jobs.ats_board_clients import BoardCache
from gigai.scout.find_jobs.company_index import (
    COMPANY_INDEX_SCHEMA,
    INDEX_EMPTY,
    INDEX_READY,
    INDEX_STALE,
    REMOVED_RETENTION_DAYS,
    STATUS_INDEXED,
    STATUS_MISSING,
    STATUS_UNREADABLE,
    STATUS_UNTOUCHED,
    STATUS_UPDATED,
    CompanyIndex,
    CompanyIndexEntry,
    CompanyIndexError,
    ObservedPosting,
    UpdateTotals,
    board_list_url,
    body_digest,
    cached_posting_rows,
    index_state,
    observe_company,
    parse_board_body,
    refresh_company,
)
from gigai.scout.find_jobs.contracts import content_hash

T1 = "2026-09-27T10:00:00.000Z"
T2 = "2026-09-27T16:00:00.000Z"
T3 = "2026-09-28T09:00:00.000Z"


def _posting(posting_id: str, *, title: str = "Software Engineer", updated_at: str | None = "2026-09-20T00:00:00Z", digest: str | None = None, location: str = "Denver, CO") -> ObservedPosting:
    return ObservedPosting(
        posting_id=posting_id,
        title=title,
        location=location,
        url=f"https://boards.greenhouse.io/acme/jobs/{posting_id}",
        updated_at=updated_at,
        content_sha256=digest,
    )


def _observe(previous, postings, *, at: str, body: str = "sha256:b1", etag: str | None = 'W/"1"', not_modified: bool = False):
    return observe_company(
        previous,
        company="Acme",
        ats="greenhouse",
        slug="acme",
        observed_at=at,
        observed=None if postings is None else {item.posting_id: item for item in postings},
        body_sha256=body,
        etag=etag,
        not_modified=not_modified,
    )


# --- change detection (pure) ---------------------------------------------


def test_first_observation_marks_every_posting_new() -> None:
    entry, change = _observe(None, [_posting("11"), _posting("12", title="Data Engineer")], at=T1)

    assert change.status == STATUS_INDEXED
    assert change.new == ("11", "12") and change.changed == () and change.removed == ()
    assert change.live == 2
    assert entry.checked_at == T1 and entry.changed_at is None
    assert entry.etag == 'W/"1"' and entry.body_sha256 == "sha256:b1"
    assert {posting.first_seen for posting in entry.postings.values()} == {T1}
    assert {posting.last_seen for posting in entry.postings.values()} == {T1}


def test_a_new_id_gets_first_seen_and_known_ids_keep_theirs() -> None:
    first, _ = _observe(None, [_posting("11")], at=T1)

    entry, change = _observe(first, [_posting("11"), _posting("12")], at=T2, body="sha256:b2")

    assert change.status == STATUS_UPDATED
    assert change.new == ("12",) and change.changed == () and change.removed == ()
    assert entry.postings["12"].first_seen == T2
    assert entry.postings["11"].first_seen == T1 and entry.postings["11"].last_seen == T2
    assert entry.postings["11"].changed_at is None
    assert entry.changed_at == T2 and entry.body_sha256 == "sha256:b2"


def test_a_different_updated_at_is_a_change() -> None:
    first, _ = _observe(None, [_posting("11"), _posting("12")], at=T1)

    entry, change = _observe(first, [_posting("11", updated_at="2026-09-26T00:00:00Z"), _posting("12")], at=T2, body="sha256:b2")

    assert change.changed == ("11",) and change.new == () and change.removed == ()
    assert entry.postings["11"].changed_at == T2 and entry.postings["11"].first_seen == T1
    assert entry.postings["11"].updated_at == "2026-09-26T00:00:00Z"
    assert entry.postings["12"].changed_at is None


def test_a_different_content_digest_is_a_change_when_the_board_sends_no_updated_at() -> None:
    first, _ = _observe(None, [_posting("a", updated_at=None, digest="sha256:one")], at=T1)

    entry, change = _observe(first, [_posting("a", updated_at=None, digest="sha256:two")], at=T2, body="sha256:b2")

    assert change.changed == ("a",)
    assert entry.postings["a"].content_sha256 == "sha256:two" and entry.postings["a"].changed_at == T2


def test_a_different_title_is_a_change() -> None:
    first, _ = _observe(None, [_posting("11")], at=T1)

    _entry, change = _observe(first, [_posting("11", title="Senior Software Engineer")], at=T2, body="sha256:b2")

    assert change.changed == ("11",)


def test_an_unchanged_posting_only_moves_last_seen() -> None:
    first, _ = _observe(None, [_posting("11", digest="sha256:one")], at=T1)

    # The body differs (say, another field or the order) but this posting
    # does not; a new location is refreshed without counting as a change.
    entry, change = _observe(first, [_posting("11", digest="sha256:one", location="Remote")], at=T2, body="sha256:b2")

    assert change.status == STATUS_UPDATED and not change.has_changes
    posting = entry.postings["11"]
    assert posting.first_seen == T1 and posting.last_seen == T2 and posting.changed_at is None
    assert posting.location == "Remote"
    assert entry.changed_at is None and entry.checked_at == T2


def test_a_known_digest_is_kept_when_the_list_carries_no_description() -> None:
    first, _ = _observe(None, [_posting("11", digest="sha256:one")], at=T1)

    entry, change = _observe(first, [_posting("11", digest=None)], at=T2, body="sha256:b2")

    assert not change.has_changes
    assert entry.postings["11"].content_sha256 == "sha256:one"

    # ... but a changed posting never keeps the digest of its old wording.
    entry, change = _observe(entry, [_posting("11", digest=None, updated_at="2026-09-26T00:00:00Z")], at=T3, body="sha256:b3")
    assert change.changed == ("11",)
    assert entry.postings["11"].content_sha256 is None


def test_a_removed_id_gets_removed_at_and_a_returning_id_counts_as_changed() -> None:
    first, _ = _observe(None, [_posting("11"), _posting("12")], at=T1)

    second, change = _observe(first, [_posting("11")], at=T2, body="sha256:b2")

    assert change.removed == ("12",) and change.new == () and change.changed == ()
    assert change.live == 1
    assert second.postings["12"].removed_at == T2 and second.postings["12"].last_seen == T1
    assert [posting.posting_id for posting in second.live()] == ["11"]

    # Still gone at the next check: not counted as removed a second time.
    third, change = _observe(second, [_posting("11")], at=T3, body="sha256:b3")
    assert change.removed == () and third.postings["12"].removed_at == T2

    back, change = _observe(third, [_posting("11"), _posting("12")], at="2026-09-29T09:00:00.000Z", body="sha256:b4")
    assert change.changed == ("12",) and change.new == ()
    assert back.postings["12"].removed_at is None
    assert back.postings["12"].first_seen == T1 and back.postings["12"].changed_at == "2026-09-29T09:00:00.000Z"


def test_removed_postings_are_dropped_after_the_retention_window() -> None:
    first, _ = _observe(None, [_posting("11"), _posting("12")], at="2026-01-01T00:00:00.000Z")
    second, _ = _observe(first, [_posting("11")], at="2026-01-02T00:00:00.000Z", body="sha256:b2")
    assert "12" in second.postings

    inside, _ = _observe(second, [_posting("11"), _posting("13")], at="2026-03-01T00:00:00.000Z", body="sha256:b3")
    assert "12" in inside.postings

    assert REMOVED_RETENTION_DAYS == 90
    after, change = _observe(inside, [_posting("11"), _posting("14")], at="2026-06-01T00:00:00.000Z", body="sha256:b4")
    assert "12" not in after.postings
    # 13 was live until now, so it is removed (and kept); 12's window is over.
    assert change.removed == ("13",) and after.postings["13"].removed_at == "2026-06-01T00:00:00.000Z"


def test_a_304_leaves_the_company_untouched_and_only_checked_at_moves() -> None:
    first, _ = _observe(None, [_posting("11"), _posting("12")], at=T1)

    entry, change = _observe(first, None, at=T2, body=None, etag=None, not_modified=True)

    assert change.status == STATUS_UNTOUCHED and not change.has_changes and change.live == 2
    assert entry.checked_at == T2
    assert {**entry.to_json(), "checked_at": T1} == first.to_json()
    assert entry.postings == first.postings  # last_seen included: nothing about a posting moves


def test_the_same_body_digest_leaves_the_company_untouched() -> None:
    first, _ = _observe(None, [_posting("11")], at=T1)

    # Lever/Ashby send no ETag: a 200 with the same bytes. Even a parsed
    # body handed in is ignored -- the digest already says nothing changed.
    entry, change = _observe(first, [_posting("11", title="Would be a change")], at=T2, body="sha256:b1", etag=None)

    assert change.status == STATUS_UNTOUCHED
    assert {**entry.to_json(), "checked_at": T1} == first.to_json()
    assert entry.etag == 'W/"1"'


def test_a_304_for_a_company_that_is_not_indexed_needs_the_body() -> None:
    with pytest.raises(CompanyIndexError) as caught:
        _observe(None, None, at=T1, not_modified=True)
    assert caught.value.code == "body_required"


def test_touched_since_returns_live_postings_new_or_changed_after_a_search() -> None:
    first, _ = _observe(None, [_posting("11"), _posting("12"), _posting("13")], at=T1)
    second, _ = _observe(first, [_posting("11", updated_at="2026-09-26T00:00:00Z"), _posting("12"), _posting("14")], at=T3, body="sha256:b2")

    assert [posting.posting_id for posting in second.touched_since(T2)] == ["11", "14"]
    assert [posting.posting_id for posting in second.touched_since(T3)] == []
    # No earlier search: every live posting (13 was removed).
    assert [posting.posting_id for posting in second.touched_since(None)] == ["11", "12", "14"]


def test_the_entry_round_trips_as_the_operator_schema() -> None:
    entry, _ = _observe(None, [_posting("11", digest="sha256:one")], at=T1)
    removed, _ = _observe(entry, [], at=T2, body="sha256:b2")

    payload = json.loads(json.dumps(removed.to_json()))

    assert set(payload) == {"schema_version", "company", "ats", "slug", "checked_at", "changed_at", "etag", "last_modified", "body_sha256", "postings"}
    assert payload["schema_version"] == COMPANY_INDEX_SCHEMA
    assert payload["postings"]["11"] == {
        "title": "Software Engineer",
        "location": "Denver, CO",
        "url": "https://boards.greenhouse.io/acme/jobs/11",
        "updated_at": "2026-09-20T00:00:00Z",
        "content_sha256": "sha256:one",
        "first_seen": T1,
        "last_seen": T1,
        "removed_at": T2,
    }
    assert CompanyIndexEntry.from_json(payload) == removed


def test_a_foreign_or_torn_payload_reads_as_not_indexed() -> None:
    entry, _ = _observe(None, [_posting("11")], at=T1)
    good = entry.to_json()

    assert CompanyIndexEntry.from_json(None) is None
    assert CompanyIndexEntry.from_json([]) is None
    assert CompanyIndexEntry.from_json({**good, "schema_version": "scout-company-index:0"}) is None
    assert CompanyIndexEntry.from_json({**good, "ats": "workday"}) is None
    assert CompanyIndexEntry.from_json({**good, "postings": []}) is None
    # One torn posting is dropped; the rest of the file still reads.
    torn = CompanyIndexEntry.from_json({**good, "postings": {**good["postings"], "12": {"title": "No stamps"}}})
    assert torn is not None and sorted(torn.postings) == ["11"]


# --- reading a board body with the existing parsers ----------------------


_GREENHOUSE_JOBS = [
    {"id": 11, "title": "Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/11", "location": {"name": "Denver, CO"}, "updated_at": "2026-09-20T00:00:00Z"},
    {"id": 12, "title": "Marketing Manager", "absolute_url": "https://boards.greenhouse.io/acme/jobs/12", "location": {"name": "Remote"}, "updated_at": "2026-09-21T00:00:00Z"},
    {"id": 13, "title": "No URL"},
]
_LEVER_JOBS = [
    {
        "id": "lev-1",
        "text": "Software Engineer",
        "hostedUrl": "https://jobs.lever.co/acme/lev-1",
        "categories": {"location": "Austin, TX", "allLocations": ["Austin, TX"]},
        "country": "US",
        "createdAt": 1758326400000,
        "descriptionPlain": "Build things.",
    },
    {"id": "lev-2", "text": "Office Manager", "hostedUrl": "https://jobs.lever.co/acme/lev-2", "categories": {"location": "Berlin"}, "country": "DE", "createdAt": 1758412800000, "updatedAt": 1758499200000, "descriptionPlain": "Run the office."},
]
_ASHBY_JOBS = [
    {
        "id": "ash-1",
        "title": "Staff Engineer",
        "jobUrl": "https://jobs.ashbyhq.com/acme/ash-1",
        "location": "San Francisco",
        "address": {"postalAddress": {"addressCountry": "United States"}},
        "publishedAt": "2026-09-19T12:00:00.000+00:00",
        "descriptionPlain": "Lead the platform.",
    },
    {"id": "ash-2", "title": "Recruiter", "jobUrl": "https://jobs.ashbyhq.com/acme/ash-2", "location": "London", "publishedAt": "2026-09-18T12:00:00.000+00:00", "descriptionPlain": "Hire people."},
]


def _body(payload: object) -> bytes:
    return json.dumps(payload).encode("utf-8")


def test_a_greenhouse_list_indexes_every_title_with_updated_at_and_no_digest() -> None:
    observed = parse_board_body("greenhouse", "acme", _body({"jobs": _GREENHOUSE_JOBS}))

    # Both titles, whatever the search roles are; the job without a URL is skipped.
    assert sorted(observed) == ["11", "12"]
    assert observed["12"].title == "Marketing Manager" and observed["12"].location == "Remote"
    assert observed["11"].url == "https://boards.greenhouse.io/acme/jobs/11"
    assert observed["11"].updated_at == "2026-09-20T00:00:00Z" and observed["11"].published_at == "2026-09-20T00:00:00Z"
    # The list has no description: no digest, updated_at is the signal.
    assert observed["11"].content_sha256 is None and observed["11"].countries is None


def test_a_greenhouse_detail_supplies_the_digest_the_acquire_path_computes() -> None:
    seen: list[tuple[str, str | None]] = []

    def lookup(job_id: str, marker: str | None):
        seen.append((job_id, marker))
        return {"content": "&lt;p&gt;Build 11.&lt;/p&gt;"} if job_id == "11" else None

    observed = parse_board_body("greenhouse", "acme", _body({"jobs": _GREENHOUSE_JOBS}), detail_lookup=lookup)

    assert seen == [("11", "2026-09-20T00:00:00Z"), ("12", "2026-09-21T00:00:00Z")]
    assert observed["11"].content_sha256 == content_hash(b"Software Engineer\nBuild 11.")
    assert observed["12"].content_sha256 is None


def test_a_lever_list_indexes_every_posting_with_the_acquire_digest_and_countries() -> None:
    observed = parse_board_body("lever", "acme", _body(_LEVER_JOBS))

    assert sorted(observed) == ["lev-1", "lev-2"]
    first, second = observed["lev-1"], observed["lev-2"]
    assert first.title == "Software Engineer" and first.location == "Austin, TX"
    assert first.content_sha256 == content_hash(b"Software Engineer\nBuild things.")
    assert first.countries == ("US",) and second.countries == ("DE",)
    assert first.published_at == "2025-09-20T00:00:00Z"
    assert first.updated_at is None
    assert second.updated_at == "2025-09-22T00:00:00Z"


def test_an_ashby_list_indexes_every_posting_with_the_acquire_digest_and_countries() -> None:
    observed = parse_board_body("ashby", "acme", _body({"jobs": _ASHBY_JOBS}))

    assert sorted(observed) == ["ash-1", "ash-2"]
    assert observed["ash-1"].content_sha256 == content_hash(b"Staff Engineer\nLead the platform.")
    assert observed["ash-1"].countries == ("US",) and observed["ash-2"].countries is None
    assert observed["ash-1"].published_at == "2026-09-19T12:00:00.000+00:00"
    assert observed["ash-2"].url == "https://jobs.ashbyhq.com/acme/ash-2"


def test_a_posting_without_a_provider_id_is_keyed_by_its_url() -> None:
    job = {key: value for key, value in _ASHBY_JOBS[0].items() if key != "id"}

    observed = parse_board_body("ashby", "acme", _body({"jobs": [job]}))

    assert list(observed) == ["url:https://jobs.ashbyhq.com/acme/ash-1"]


@pytest.mark.parametrize(
    ("ats", "body"),
    [
        ("greenhouse", b"<html>rate limited</html>"),
        ("greenhouse", b'{"jobs": "none"}'),
        ("lever", b'{"jobs": []}'),
        ("ashby", b"[]"),
        ("ashby", b"\xff\xfe"),
    ],
)
def test_a_body_that_is_not_the_providers_list_is_bad_json(ats: str, body: bytes) -> None:
    with pytest.raises(CompanyIndexError) as caught:
        parse_board_body(ats, "acme", body)
    assert caught.value.code == "bad_json"
    assert "rate limited" not in str(caught.value)


def test_an_unknown_provider_is_refused() -> None:
    with pytest.raises(CompanyIndexError) as caught:
        board_list_url("workday", "acme")
    assert caught.value.code == "unsupported_provider"


# --- totals and the state a search reports --------------------------------


def test_update_totals_print_the_operator_summary_line() -> None:
    totals = UpdateTotals()
    first, change_one = _observe(None, [_posting("11"), _posting("12")], at=T1)
    totals.add(change_one)
    _second, change_two = _observe(first, [_posting("11", updated_at="2026-09-26T00:00:00Z"), _posting("13")], at=T2, body="sha256:b2")
    totals.add(change_two)
    _third, change_three = _observe(first, None, at=T2, body="sha256:b1")
    totals.add(change_three)

    assert totals.summary_line() == "2 companies with new postings: 3 new, 1 changed, 1 removed"
    assert totals.to_json() == {
        "companies": 3,
        "indexed": 1,
        "updated": 1,
        "untouched": 1,
        "missing": 0,
        "unreadable": 0,
        "companies_with_new": 2,
        "companies_with_changes": 2,
        "new": 3,
        "changed": 1,
        "removed": 1,
        "live": 6,
    }

    one = UpdateTotals()
    one.add(change_one)
    assert one.summary_line() == "1 company with new postings: 2 new, 0 changed, 0 removed"


def test_index_state_tells_a_search_to_run_update_sources() -> None:
    now = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    fresh, _ = _observe(None, [_posting("11")], at="2026-09-28T09:00:00.000Z")
    old, _ = _observe(None, [_posting("11")], at="2026-09-20T09:00:00.000Z")

    empty = index_state([None, None], now=now)
    assert empty.status == INDEX_EMPTY and empty.needs_update
    assert empty.companies == 2 and empty.indexed == 0 and empty.not_indexed == 2
    assert "Run Update sources" in (empty.message or "")

    stale = index_state([old, None], now=now)
    assert stale.status == INDEX_STALE and stale.needs_update
    assert "Run Update sources" in (stale.message or "")

    # One recent check is enough: a rotation still working through the
    # catalog leaves older companies behind without the index being stale.
    ready = index_state([fresh, old, None], now=now)
    assert ready.status == INDEX_READY and not ready.needs_update and ready.message is None
    assert ready.oldest_checked_at == "2026-09-20T09:00:00.000Z" and ready.newest_checked_at == "2026-09-28T09:00:00.000Z"
    assert ready.to_json()["not_indexed"] == 1

    assert index_state([fresh], now=now, stale_after_hours=1).status == INDEX_STALE
    assert index_state([], now=now).status == INDEX_EMPTY


# --- the store: one file per company, atomic, deletable --------------------


def _stores(home: Path) -> tuple[CompanyIndex, BoardCache]:
    return CompanyIndex.for_home(home), BoardCache(home / "cache" / "scout" / "ats-boards")


def _cache_board(cache: BoardCache, ats: str, slug: str, payload: object, *, etag: str | None = None) -> bytes:
    body = _body(payload)
    cache.store(ats, board_list_url(ats, slug), body=body, etag=etag, last_modified=None, marker=None)
    return body


def _cache_greenhouse_detail(cache: BoardCache, slug: str, job_id: int, *, marker: str, content: str) -> None:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
    cache.store("greenhouse", url, body=_body({"id": job_id, "content": content}), etag=None, last_modified=None, marker=marker)


def test_the_index_is_one_json_file_per_company_under_the_home_cache(tmp_path: Path) -> None:
    index, _cache = _stores(tmp_path)
    entry, _ = _observe(None, [_posting("11")], at=T1)

    path = index.write(entry)

    assert path == tmp_path / "cache" / "scout" / "companies" / "greenhouse:acme.json"
    assert json.loads(path.read_text(encoding="utf-8")) == entry.to_json()
    assert index.read("greenhouse", "acme") == entry
    assert index.read("greenhouse", "other") is None
    assert list(index.keys()) == [("greenhouse", "acme")]


def test_a_slug_can_never_name_a_file_outside_the_index(tmp_path: Path) -> None:
    index, _cache = _stores(tmp_path)

    for slug in ("../../etc/passwd", "a/b", "..", "harrison&star", "1st Formations", "TECLA"):
        path = index.path("lever", slug)
        assert path.parent == index.root
        assert path.name.startswith("lever:") and path.name.endswith(".json")
        assert "/" not in path.name

    entry = observe_company(None, company="A B", ats="lever", slug="a/b", observed_at=T1, observed={}, body_sha256="sha256:b1")[0]
    index.write(entry)
    assert index.read("lever", "a/b") == entry
    assert list(index.keys()) == [("lever", "a/b")]
    assert sorted(item.name for item in tmp_path.rglob("*.json")) == ["lever:a%2Fb.json"]

    for ats, slug in (("workday", "acme"), ("", "acme")):
        with pytest.raises(CompanyIndexError):
            index.path(ats, slug)
    with pytest.raises(CompanyIndexError):
        index.path("lever", "")


def test_a_corrupt_or_foreign_file_reads_as_not_indexed(tmp_path: Path) -> None:
    index, _cache = _stores(tmp_path)
    entry, _ = _observe(None, [_posting("11")], at=T1)
    path = index.write(entry)

    path.write_text("{not json", encoding="utf-8")
    assert index.read("greenhouse", "acme") is None

    # Another company's entry under this company's name is not this company's.
    path.write_text(json.dumps({**entry.to_json(), "slug": "other"}), encoding="utf-8")
    assert index.read("greenhouse", "acme") is None


def test_a_write_replaces_the_file_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    index, _cache = _stores(tmp_path)
    first, _ = _observe(None, [_posting("11")], at=T1)
    path = index.write(first)
    before = path.read_bytes()
    second, _ = _observe(first, [_posting("11"), _posting("12")], at=T2, body="sha256:b2")

    replaced: list[tuple[Path, Path, bytes, bytes]] = []
    real_replace = os.replace

    def recording_replace(source, target):
        # At the moment of the swap the complete new file already exists
        # beside the old one, and the old one is still whole.
        replaced.append((Path(source), Path(target), Path(source).read_bytes(), Path(target).read_bytes()))
        real_replace(source, target)

    monkeypatch.setattr(company_index.os, "replace", recording_replace)
    index.write(second)

    assert len(replaced) == 1
    source, target, new_bytes, old_bytes = replaced[0]
    assert target == path and source.parent == path.parent and source != path
    assert old_bytes == before
    assert json.loads(new_bytes) == second.to_json()
    assert path.read_bytes() == new_bytes
    assert sorted(item.name for item in index.root.iterdir()) == ["greenhouse:acme.json"]


def test_a_failed_write_keeps_the_previous_file_and_leaves_no_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    index, _cache = _stores(tmp_path)
    first, _ = _observe(None, [_posting("11")], at=T1)
    path = index.write(first)
    before = path.read_bytes()
    second, _ = _observe(first, [_posting("11"), _posting("12")], at=T2, body="sha256:b2")

    def failing_replace(source, target):
        raise OSError("disk full")

    monkeypatch.setattr(company_index.os, "replace", failing_replace)
    with pytest.raises(OSError):
        index.write(second)

    assert path.read_bytes() == before
    assert index.read("greenhouse", "acme") == first
    assert sorted(item.name for item in index.root.iterdir()) == ["greenhouse:acme.json"]


def test_refresh_indexes_a_company_from_its_cached_body_without_a_request(tmp_path: Path) -> None:
    index, cache = _stores(tmp_path)
    body = _cache_board(cache, "lever", "acme", _LEVER_JOBS)

    change = refresh_company(index, cache, ats="lever", slug="acme", company="Acme Corp", observed_at=T1)

    assert change.status == STATUS_INDEXED and change.new == ("lev-1", "lev-2") and change.live == 2
    entry = index.read("lever", "acme")
    assert entry is not None
    assert entry.company == "Acme Corp" and entry.checked_at == T1
    assert entry.body_sha256 == body_digest(body) and entry.etag is None
    assert entry.postings["lev-1"].content_sha256 == content_hash(b"Software Engineer\nBuild things.")
    assert entry.postings["lev-1"].first_seen == T1


def test_refresh_with_the_same_cached_body_only_moves_checked_at(tmp_path: Path) -> None:
    index, cache = _stores(tmp_path)
    _cache_board(cache, "ashby", "acme", {"jobs": _ASHBY_JOBS})
    refresh_company(index, cache, ats="ashby", slug="acme", company="Acme", observed_at=T1)
    path = index.path("ashby", "acme")
    before = json.loads(path.read_text(encoding="utf-8"))

    change = refresh_company(index, cache, ats="ashby", slug="acme", observed_at=T2)

    assert change.status == STATUS_UNTOUCHED and not change.has_changes and change.live == 2
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["checked_at"] == T2 and after["company"] == "Acme"
    assert {**after, "checked_at": T1} == before


def test_refresh_reports_new_changed_and_removed_postings_for_a_new_body(tmp_path: Path) -> None:
    index, cache = _stores(tmp_path)
    _cache_board(cache, "greenhouse", "acme", {"jobs": _GREENHOUSE_JOBS}, etag='W/"1"')
    refresh_company(index, cache, ats="greenhouse", slug="acme", company="Acme", observed_at=T1)

    jobs = [
        {**_GREENHOUSE_JOBS[0], "updated_at": "2026-09-27T00:00:00Z"},
        {"id": 14, "title": "Data Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/14", "location": {"name": "Remote"}, "updated_at": "2026-09-27T00:00:00Z"},
    ]
    body = _cache_board(cache, "greenhouse", "acme", {"jobs": jobs}, etag='W/"2"')
    change = refresh_company(index, cache, ats="greenhouse", slug="acme", observed_at=T2)

    assert change.status == STATUS_UPDATED
    assert change.new == ("14",) and change.changed == ("11",) and change.removed == ("12",)
    assert change.to_json() == {"company": "greenhouse:acme", "status": "updated", "new": 1, "changed": 1, "removed": 1, "live": 2}
    entry = index.read("greenhouse", "acme")
    assert entry is not None
    assert entry.etag == 'W/"2"' and entry.body_sha256 == body_digest(body)
    assert entry.checked_at == T2 and entry.changed_at == T2 and entry.company == "Acme"
    assert entry.postings["14"].first_seen == T2
    assert entry.postings["11"].first_seen == T1 and entry.postings["11"].changed_at == T2
    assert entry.postings["12"].removed_at == T2


def test_a_deleted_index_file_is_rebuilt_from_the_cached_body(tmp_path: Path) -> None:
    index, cache = _stores(tmp_path)
    _cache_board(cache, "lever", "acme", _LEVER_JOBS)
    _cache_board(cache, "ashby", "acme", {"jobs": _ASHBY_JOBS})
    for ats in ("lever", "ashby"):
        refresh_company(index, cache, ats=ats, slug="acme", company="Acme", observed_at=T1)
    original = index.read("lever", "acme")
    assert original is not None

    assert index.delete("lever", "acme") is True
    assert index.delete("lever", "acme") is False
    assert index.read("lever", "acme") is None
    assert list(index.keys()) == [("ashby", "acme")]

    change = refresh_company(index, cache, ats="lever", slug="acme", company="Acme", observed_at=T2)

    assert change.status == STATUS_INDEXED and change.new == ("lev-1", "lev-2")
    rebuilt = index.read("lever", "acme")
    assert rebuilt is not None
    assert rebuilt.body_sha256 == original.body_sha256
    # The same postings with the same digests; their history restarts at the rebuild.
    assert {key: (item.title, item.url, item.content_sha256) for key, item in rebuilt.postings.items()} == {
        key: (item.title, item.url, item.content_sha256) for key, item in original.postings.items()
    }
    assert {item.first_seen for item in rebuilt.postings.values()} == {T2}
    assert list(index.keys()) == [("ashby", "acme"), ("lever", "acme")]


def test_the_whole_index_directory_can_be_deleted_and_rebuilt(tmp_path: Path) -> None:
    index, cache = _stores(tmp_path)
    _cache_board(cache, "lever", "acme", _LEVER_JOBS)
    refresh_company(index, cache, ats="lever", slug="acme", observed_at=T1)
    index.write_update_summary({"finished_at": T1})

    for item in index.root.iterdir():
        item.unlink()
    index.root.rmdir()
    assert list(index.keys()) == [] and index.read_update_summary() is None

    assert refresh_company(index, cache, ats="lever", slug="acme", observed_at=T2).status == STATUS_INDEXED
    assert list(index.keys()) == [("lever", "acme")]


def test_refresh_without_a_cached_body_writes_nothing(tmp_path: Path) -> None:
    index, cache = _stores(tmp_path)

    change = refresh_company(index, cache, ats="greenhouse", slug="never-fetched", observed_at=T1)

    assert change.status == STATUS_MISSING and change.code == "not_cached" and change.live == 0
    assert not index.root.exists()


def test_an_unreadable_cached_body_keeps_the_existing_index_file(tmp_path: Path) -> None:
    index, cache = _stores(tmp_path)
    _cache_board(cache, "lever", "acme", _LEVER_JOBS)
    refresh_company(index, cache, ats="lever", slug="acme", observed_at=T1)
    before = index.path("lever", "acme").read_bytes()

    _cache_board(cache, "lever", "acme", {"error": "maintenance"})
    change = refresh_company(index, cache, ats="lever", slug="acme", observed_at=T2)

    assert change.status == STATUS_UNREADABLE and change.code == "bad_json"
    assert index.path("lever", "acme").read_bytes() == before


def test_refresh_reads_a_greenhouse_digest_only_from_a_detail_that_matches_the_list(tmp_path: Path) -> None:
    index, cache = _stores(tmp_path)
    _cache_board(cache, "greenhouse", "acme", {"jobs": _GREENHOUSE_JOBS})
    _cache_greenhouse_detail(cache, "acme", 11, marker="2026-09-20T00:00:00Z", content="&lt;p&gt;Build 11.&lt;/p&gt;")
    # Cached for an older version of job 12: not this posting's wording any more.
    _cache_greenhouse_detail(cache, "acme", 12, marker="2026-09-01T00:00:00Z", content="&lt;p&gt;Old.&lt;/p&gt;")

    refresh_company(index, cache, ats="greenhouse", slug="acme", observed_at=T1)

    entry = index.read("greenhouse", "acme")
    assert entry is not None
    assert entry.postings["11"].content_sha256 == content_hash(b"Software Engineer\nBuild 11.")
    assert entry.postings["12"].content_sha256 is None


def test_cached_posting_rows_are_the_acquire_rows_read_from_the_cache(tmp_path: Path) -> None:
    _index, cache = _stores(tmp_path)
    _cache_board(cache, "lever", "acme", _LEVER_JOBS)
    _cache_board(cache, "greenhouse", "acme", {"jobs": _GREENHOUSE_JOBS})
    _cache_greenhouse_detail(cache, "acme", 11, marker="2026-09-20T00:00:00Z", content="&lt;p&gt;Build 11.&lt;/p&gt;")

    lever = cached_posting_rows(cache, "lever", "acme", ["lev-2", "gone", "lev-2"])
    assert list(lever.rows) == ["lev-2"] and lever.missing == ("gone",) and lever.without_text == ()
    row = lever.rows["lev-2"]
    assert row.title == "Office Manager" and row.text == "Run the office." and row.board_token == "acme"
    assert row.query_key == "ats:lever:acme" and row.countries == ("DE",)
    assert row.content_sha256 == content_hash(b"Office Manager\nRun the office.")

    greenhouse = cached_posting_rows(cache, "greenhouse", "acme", ["11", "12"])
    assert list(greenhouse.rows) == ["11", "12"]
    assert greenhouse.rows["11"].text == "Build 11."
    # No detail in the cache for 12: the row has no description, and says so.
    assert greenhouse.rows["12"].text is None and greenhouse.without_text == ("12",)

    nothing = cached_posting_rows(cache, "ashby", "acme", ["ash-1"])
    assert dict(nothing.rows) == {} and nothing.missing == ("ash-1",)


def test_the_last_update_summary_round_trips_beside_the_company_files(tmp_path: Path) -> None:
    index, _cache = _stores(tmp_path)
    assert index.read_update_summary() is None

    path = index.write_update_summary({"finished_at": T1, "totals": UpdateTotals(companies=2, new=5).to_json()})

    assert path == index.root / "last-update.json"
    summary = index.read_update_summary()
    assert summary is not None and summary["finished_at"] == T1 and summary["totals"]["new"] == 5
    # It is not a company file.
    assert list(index.keys()) == []
    path.write_text("[]", encoding="utf-8")
    assert index.read_update_summary() is None

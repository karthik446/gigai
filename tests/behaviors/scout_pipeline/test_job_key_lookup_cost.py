"""0.1.11.9 PERF1: a list never scans the search index once per job to learn a posting's board. Synthetic only.

A job's records are kept under ONE identity (``find_jobs.job_key``), found from the posting's BOARD. Most URLs name
their board. An employer's own careers address (``https://careers.acme.test/jobs?job=1``) does not, and the search
index was then asked for every posting whose URL holds the address's PATH: ``/jobs`` is in most URLs, so each
lookup built a row for most of the index (3 to 5 s each on a 380k-posting home, 11 of them a ``jobs list``, again in
every process).

Here: the copies fixture's job (seven countries and the US, one Lever board) at the employer's own ``/jobs``
addresses, one copy assessed, one applied to; a second board no role lists, two of its jobs applied to; and an index
padded to 50,000 postings whose URLs hold ``/jobs`` too.

Pinned, on the END outcome:

- the list, ``scout new --peek``, the keys and the canonical order are what they were (literal rows, one key, one
  record file);
- a list in a NEW process (the per-process memory emptied) reads the search index's URLs at most once, and builds
  no index row for it: the rows' own boards answer for the jobs a role lists, and one read answers for all the
  applied jobs no role lists;
- the one read finds what the path-only lookup found: a live posting before a removed one, a host in another case,
  a tracking parameter, a bare address, an address the index does not hold.

No network, no real home; the scripted model of ``pipeline_fixtures``.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import sqlite3
import uuid

import pytest

from gigai.application_events import record_application
from gigai.scout import posting_search, scout_new
from gigai.scout.find_jobs import job_key as keys
from gigai.scout.find_jobs import search_index
from gigai.scout.find_jobs.contracts import normalize_url
from gigai.scout.job_store_layout import job_digest
from gigai.scout.quick_assess import quick_assess_dir
from gigai.workpad import committed_read_cache

from tests.support.copies_fixtures import COUNTRIES, anywhere_profile, copy_job
from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago

NEWEST = NOW - timedelta(hours=3)
SLUG, UNLISTED = "point-example", "kitchen-example"
PADDING = 50_000


def own(slug: str, n: int) -> str:
    """The employer's own address of posting ``n``: its path is ``/jobs``, and nothing in it names the board."""

    return f"https://careers.{slug}.test/jobs?job={n}"


US = own(SLUG, 14)
#: The eight postings of the one job in the canonical order: the US one, then the seven countries, the earliest posted first.
COPIES = [US, *(own(SLUG, n) for n in range(7, 0, -1))]
LATVIA = own(SLUG, 3)
COOKS = [own(UNLISTED, 1), own(UNLISTED, 2)]


def _at_own_address(job: dict[str, object], slug: str, n: int) -> dict[str, object]:
    job["hostedUrl"] = own(slug, n)
    return job


def _pad(home_root: Path, rows: int) -> None:
    """``rows`` more postings in the search index, of boards no company file is kept for; every URL holds ``/jobs``."""

    conn = sqlite3.connect(search_index.search_index_path(home_root))
    try:
        first = conn.execute("SELECT COALESCE(MAX(id), 0) FROM p").fetchone()[0] + 1
        conn.executemany(
            "INSERT INTO p (id, board, company, posting_id, title, title_words, company_words, location, location_words, url, posted,"
            " published_ts, first_seen, changed_at, removed, wm, ckind, us_place, content) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                (
                    first + n, f"greenhouse:pad{n % 500:03d}", f"Pad {n % 500}", str(n), "Line Cook", "line cook", "pad", "Remote", "remote",
                    f"https://job-boards.greenhouse.io/pad{n % 500:03d}/jobs/{n}", "2026-09-01T00:00:00Z", None, "2026-09-01T00:00:00Z", None, 0,
                    "remote", "none", "unclear", None,
                )
                for n in range(rows)
            ),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    fixture = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    jobs = [copy_job(SLUG, n, "Staff Engineer", f"Remote {country}", None, NEWEST) for n, country in enumerate(COUNTRIES, start=1)]
    jobs.append(copy_job(SLUG, 14, "Staff Engineer", "Denver, CO", "US", NEWEST + timedelta(minutes=20)))
    fixture.seed(SLUG, [_at_own_address(job, SLUG, n) for job, n in zip(jobs, [*range(1, 8), 14])], seen_at=days_ago(1))
    # A board no role lists (its titles match no role's): its postings are in the company index and nowhere else.
    cooks = [copy_job(UNLISTED, n, "Line Cook", "Austin, TX", "US", NEWEST, description=f"Cook the line, station {n}.") for n in (1, 2)]
    fixture.seed(UNLISTED, [_at_own_address(job, UNLISTED, n) for n, job in enumerate(cooks, start=1)], seen_at=days_ago(1))
    assert search_index.rebuild_from_index(fixture.home_root).available
    _pad(fixture.home_root, PADDING)
    status = search_index.status(fixture.home_root)
    assert status.available and status.postings >= PADDING + 10
    return fixture


class _IndexReads:
    """What a call asked of the search index's URLs: how many reads, and how many index rows were built for them."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.reads: list[str] = []
        self.rows_built = 0
        build = search_index._row

        def row(found):  # type: ignore[no-untyped-def]
            self.rows_built += 1
            return build(found)

        monkeypatch.setattr(search_index, "_row", row)
        for name in ("rows_with_url_part", "boards_with_url_parts"):
            if hasattr(search_index, name):  # the second one is this packet's
                monkeypatch.setattr(search_index, name, self._counted(name, getattr(search_index, name)))

    def _counted(self, name: str, real):  # type: ignore[no-untyped-def]
        def read(*args, **more):  # type: ignore[no-untyped-def]
            self.reads.append(name)
            return real(*args, **more)

        return read

    def clear(self) -> None:
        self.reads.clear()
        self.rows_built = 0


def _new_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """What one process remembers of the boards is gone: the next read is a new command's."""

    monkeypatch.setattr(keys, "_BOARD_OF", {})
    monkeypatch.setattr(keys, "_NOT_FOUND", {})


def _search(fx: PostingsFixture, profile_id: str, **more: object) -> dict:
    return posting_search.search_postings(fx.home_root, fx.target, now=NOW, limit=200, profile_ids=[profile_id], **more)  # type: ignore[arg-type]


def _apply(fx: PostingsFixture, job: str) -> None:
    with committed_read_cache():
        recorded = record_application(
            resolved=fx.base.gig.resolved,
            data={"external_ref": job, "event_kind": "applied", "occurred_at": "2026-10-02T10:00:00Z", "timezone": "UTC", "operation_key": f"cost-{uuid.uuid4()}"},
            confirm=True,
        )
    assert recorded["status"] == "recorded"


def _stored_files(fx: PostingsFixture) -> list[str]:
    store = quick_assess_dir(fx.home_root, fx.target)
    return sorted(path.relative_to(store).as_posix() for path in store.rglob("*.json"))


def test_a_list_of_jobs_whose_urls_name_no_board_reads_the_index_urls_at_most_once(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    anywhere = anywhere_profile(fx)
    assert all(keys.copies_of(fx.home_root, copy) == tuple(COPIES) for copy in COPIES)
    done = posting_search.assess_these(fx.home_root, fx.target, jobs=[LATVIA], approve=True, include_low_rank=True, now=NOW)
    assert done["assessed"]["assessed"] == 1 and _stored_files(fx) == [f"job/{job_digest(US)}.json"]
    for job in COOKS:
        _apply(fx, job)  # two applied jobs no role lists: no row names their board

    reads = _IndexReads(monkeypatch)
    _new_process(monkeypatch)
    listed = _search(fx, anywhere, us_only=False)
    list_reads, list_rows = list(reads.reads), reads.rows_built

    # THE RESULT, as it was: one row for the eight copies, the canonical posting's, assessed.
    rows = listed["postings"]["rows"]
    assert [(row["job_identity"], row["copies"], row["application"]) for row in rows] == [(US, 8, None)]
    assert rows[0]["state"] != "not_assessed" and listed["counts"]["applied"] == 0
    assert {keys.job_key(fx.home_root, fx.target, copy) for copy in COPIES} == {US}
    assert all(keys.copies_of(fx.home_root, copy) == tuple(COPIES) for copy in COPIES)
    assert _stored_files(fx) == [f"job/{job_digest(US)}.json"]

    # THE COST: one read of the index's URLs for the two applied jobs no role lists, and no index row built for it.
    # Before: one read a job (three and more), each building a row for all 50,000 postings whose URL holds "/jobs".
    assert len(list_reads) <= 1, list_reads
    assert list_rows < 100, list_rows

    # The same for what is new (its own read of the applied jobs and of the stored assessment).
    reads.clear()
    _new_process(monkeypatch)
    new = scout_new.scout_new(fx.home_root, fx.target, profile_id=anywhere, peek=True, assess=False, now=NOW, us_only=False)
    assert [(row["job_identity"], row["copies"], row["state"] != "not_assessed") for row in new["postings"]["rows"]] == [(US, 8, True)]
    assert len(reads.reads) <= 1 and reads.rows_built < 100, (reads.reads, reads.rows_built)


def test_an_application_on_a_copy_still_shows_on_the_jobs_row_with_no_read_of_the_index(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    anywhere = anywhere_profile(fx)
    _apply(fx, LATVIA)
    reads = _IndexReads(monkeypatch)
    _new_process(monkeypatch)

    applied = _search(fx, anywhere, us_only=False, states=["applied"])["postings"]["rows"]

    assert [(row["job_identity"], row["copies"], row["application"]["status"]) for row in applied] == [(US, 8, "applied")]
    assert reads.reads == [] and reads.rows_built == 0  # the applied job is a row of the list: its board is the row's


def _first_board_by_path(home_root: Path, identity: str) -> str | None:
    """The lookup as it was: every row whose URL holds the address's path (its host for a bare one), the first that IS the address."""

    from urllib.parse import urlsplit

    parts = urlsplit(identity)
    bare = parts.path in ("", "/")
    found = search_index.rows_with_url_part(home_root, (parts.hostname or "") if bare else parts.path, fold_case=bare)
    assert found.available
    return next((row.board for row in found.rows if normalize_url(row.url) == identity), None)


def test_one_read_for_many_addresses_names_the_boards_the_path_lookup_named(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = sqlite3.connect(search_index.search_index_path(fx.home_root))
    try:
        first = conn.execute("SELECT MAX(id) FROM p").fetchone()[0] + 1
        extra = [
            # The same address on two boards: the live posting is the answer, though the removed one is newer.
            ("greenhouse:gone", "https://careers.twice.test/jobs?job=1", "2026-09-20T00:00:00Z", 1),
            ("greenhouse:live", "https://careers.twice.test/jobs?job=1", "2026-09-10T00:00:00Z", 0),
            # Two live postings with one address: the newest.
            ("greenhouse:older", "https://careers.pair.test/jobs?job=2", "2026-09-10T00:00:00Z", 0),
            ("greenhouse:newer", "https://careers.pair.test/jobs?job=2", "2026-09-12T00:00:00Z", 0),
            # The stored URL is the board's own spelling: a host in capitals, a tracking parameter, a trailing slash.
            ("greenhouse:spelled", "https://Careers.Spelled.TEST/jobs/?job=3&utm_source=feed", "2026-09-10T00:00:00Z", 0),
            # A bare address.
            ("greenhouse:bare", "https://Bare.Example.TEST/?job=4", "2026-09-10T00:00:00Z", 0),
            # The same path and query at another host is another posting.
            ("greenhouse:elsewhere", "https://careers.elsewhere.test/jobs?job=3", "2026-09-10T00:00:00Z", 0),
        ]
        conn.executemany(
            "INSERT INTO p (id, board, company, posting_id, title, title_words, company_words, location, location_words, url, posted,"
            " published_ts, first_seen, changed_at, removed, wm, ckind, us_place, content) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(first + n, board, "Co", str(n), "T", "t", "co", "", "", url, posted, None, posted, None, removed, "", "none", "unclear", None) for n, (board, url, posted, removed) in enumerate(extra)],
        )
        conn.commit()
    finally:
        conn.close()
    asked = [
        "https://careers.twice.test/jobs?job=1", "https://careers.pair.test/jobs?job=2", "https://careers.spelled.test/jobs?job=3",
        "https://bare.example.test/?job=4", "https://careers.elsewhere.test/jobs?job=3", "https://careers.nowhere.test/jobs?job=9",
        *COOKS, LATVIA,
    ]
    before = {identity: _first_board_by_path(fx.home_root, identity) for identity in asked}
    assert before == {
        asked[0]: "greenhouse:live", asked[1]: "greenhouse:newer", asked[2]: "greenhouse:spelled", asked[3]: "greenhouse:bare",
        asked[4]: "greenhouse:elsewhere", asked[5]: None, COOKS[0]: f"lever:{UNLISTED}", COOKS[1]: f"lever:{UNLISTED}", LATVIA: f"lever:{SLUG}",
    }

    reads = _IndexReads(monkeypatch)
    _new_process(monkeypatch)
    keys.find_boards(fx.home_root, asked)
    assert reads.reads == ["boards_with_url_parts"] and reads.rows_built == 0

    assert {identity: keys._board_of(fx.home_root, identity) for identity in asked} == before
    assert reads.reads == ["boards_with_url_parts"]  # every answer was kept, also the address the index does not hold
    # One address alone is one narrow read, and the same answer.
    _new_process(monkeypatch)
    assert keys._board_of(fx.home_root, asked[0]) == "greenhouse:live" and reads.rows_built == 0
    # What the caller's row says is kept only when that board's company file holds the posting.
    _new_process(monkeypatch)
    reads.clear()
    keys.find_boards(fx.home_root, known={LATVIA: f"lever:{SLUG}", COOKS[0]: f"lever:{SLUG}"})
    assert keys._board_of(fx.home_root, LATVIA) == f"lever:{SLUG}" and keys._board_of(fx.home_root, COOKS[0]) == f"lever:{UNLISTED}"
    assert reads.reads == ["boards_with_url_parts"]  # for the row whose board does not hold it: the index is asked

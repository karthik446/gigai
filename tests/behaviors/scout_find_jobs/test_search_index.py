"""SI1 (0.1.11.7): the local search index (``find_jobs/search_index.py``), synthetic boards only.

Pins: the build from the company files, one company replaced / removed, the
stamp against the company folder, and EXACTNESS: for 16 typed titles x {the
whole-word rule, the strict rule} x five shapes, the index's candidates put
through the rule are the rows a brute-force scan of the same company files
gives, in the same order, and the count path agrees. The safety cases
(damaged files, concurrency) are in ``test_search_index_safety.py``.

Every posting, company and place below is made up. Nothing here touches the
network or a real home.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import re
import sqlite3
import unicodedata

import pytest

from gigai.scout import postings as postings_module
from gigai.scout.find_jobs import search_index
from gigai.scout.find_jobs.ats_board_clients import MATCH_ANY_TITLE_ROLE, matches_roles
from gigai.scout.find_jobs.company_index import CompanyIndex, CompanyIndexEntry, IndexedPosting, company_key
from gigai.scout.find_jobs.contracts import FindJobsConfig, SourceToggles, WorkModePreference
from gigai.scout.find_jobs.filters import country_match, published_too_old
from gigai.scout.find_jobs.index_search import _indexed_row
from gigai.scout.find_jobs.search_index import IndexFilters, IndexQuery, strict_title_match, words_match
from gigai.scout.find_jobs.work_mode import work_mode_fit

pytestmark = pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 9, 0), reason="needs FTS5")

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)

#: (company, ats, slug). Two-letter company words ("AI", "Go"), an accent, the two providers with structured countries.
BOARDS = (
    ("Example AI", "greenhouse", "example-ai"),
    ("Sample Cloud Co", "lever", "sample-cloud"),
    ("Placeholder Labs", "ashby", "placeholder-labs"),
    ("Zürich Robotics GmbH", "greenhouse", "zurich-robotics"),
    ("Go Fast AI", "ashby", "go-fast-ai"),
    ("Maintain Systems", "lever", "maintain-systems"),
)
TITLES = (
    "Senior Software Engineer",
    "Sr. Software Engineer",
    "Sr Systems Engineer",
    "Senior Systems Engineer, Controls",
    "Systems Engineer",
    "Staff Engineer",
    "Staff Software Engineer",
    "Staff Training Engineer",
    "Staff Sales Engineer",
    "Software Engineer, Staff",
    "Engineering Manager",
    "Sr. Engineering Manager, Platform",
    "Director of Software Engineering",
    "Director, Engineering",
    "C++ Engineer",
    "Senior C++ Software Engineer",
    "C# Developer",
    "Sr. C# Developer (.NET)",
    "C Engineer",
    "Ingénieur Logiciel Senior",
    "Développeur C#",
    "Forward Deployed Engineer",
    "Staff AI Engineer",
    "Maintain AI Systems",
    "Machine Learning Engineer",
    "Senior Machine Learning Engineers",
    "Data Engineer",
    "Product Manager",
    "Senior Product Manager - Platform",
    "Head of Engineering",
    "Platform Engineers",
    "Senior Platform Engineer (Remote)",
    "Backend Engineer / API",
    "Principal Engineer",
    "Senior Engineer",
    "Engineer",
    "Software Engineering Intern",
    "Accountant",
)
#: (location, the structured countries a Lever / Ashby board gives for it; ``None`` = the board gave none).
PLACES = (
    ("Remote", ()),
    ("Remote - US", ("US",)),
    ("San Francisco, CA", ("US",)),
    ("Denver, CO - Hybrid", ("US",)),
    ("Denver, CO", None),
    ("London, UK", ("GB",)),
    ("Zürich, Switzerland", ("CH",)),
    ("Montréal, QC, Canada", ("CA",)),
    ("Remote (Germany)", ("DE",)),
    ("Berlin, Germany (On-site)", ("de",)),
    ("EMEA", None),
    ("Europe", None),
    ("", None),
    ("Remote - EST", None),
    ("Anywhere", None),
    ("New York, NY or Remote", ("US", "CA")),
)

TYPED = (
    "senior engineer",
    "senior systems engineer",
    "director of software engineering",
    "forward deployed engineer",
    "staff ai engineer",
    "software engineer",
    "staff engineer",
    "engineering manager",
    "c++ engineer",
    "c# developer",
    "ingénieur logiciel",
    "product manager",
    "head of engineering",
    "platform engineer",
    "sr software engineer",
    "machine learning engineer",
)


def _stamp(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def _board_postings(board: int, *, round_: int = 0) -> dict[str, IndexedPosting]:
    """One board's postings: every title at three places; some undated, some removed, many posted the same instant."""

    _company, ats, slug = BOARDS[board]
    structured = ats in ("lever", "ashby")
    postings: dict[str, IndexedPosting] = {}
    for number, title in enumerate(TITLES):
        for copy in range(3):
            n = number * 3 + copy + round_
            location, countries = PLACES[(number * 7 + board * 3 + copy * 5 + round_) % len(PLACES)]
            posting_id = f"{board}{number:02d}{copy}"
            postings[posting_id] = IndexedPosting(
                posting_id=posting_id,
                title=title,
                location=location,
                url=f"https://jobs.example.test/{slug}/{posting_id}",
                updated_at=None,
                content_sha256=None,
                first_seen=_stamp(1 + (n % 4)),
                last_seen=_stamp(0),
                changed_at=_stamp(1) if n % 5 == 0 else None,
                removed_at=_stamp(0.5) if (n + board) % 11 == 0 else None,
                # Whole days at the same hour: many postings share a posted instant, so the URL decides the order.
                published_at=None if (n + board) % 9 == 0 else ("not a date" if n % 53 == 0 else _stamp((n * 5 + board) % 70)),
                countries=countries if structured and (n + board) % 4 else None,
            )
    return postings


def write_board(home: Path, board: int, *, round_: int = 0) -> str:
    company, ats, slug = BOARDS[board]
    entry = CompanyIndexEntry(
        company=company, ats=ats, slug=slug, checked_at=_stamp(0), etag=None, body_sha256=None,
        postings=_board_postings(board, round_=round_),
    )
    CompanyIndex.for_home(home).write(entry)
    return company_key(ats, slug)


def make_home(tmp_path: Path, *, build: bool = True) -> Path:
    home = tmp_path / "home"
    for board in range(len(BOARDS)):
        write_board(home, board)
    if build:
        built = search_index.rebuild_from_index(home)
        assert built.available, built
    return home


@pytest.fixture
def home(tmp_path: Path):
    path = make_home(tmp_path)
    yield path
    search_index.close(path)


def _config(**values: object) -> FindJobsConfig:
    base: dict[str, object] = dict(
        roles=("engineer",), merged_queries=("engineer",), location=None, remote=True, published_after=None,
        sources=SourceToggles(exa=False, ats=True, hiringcafe=False), countries=("US",), max_age_days=30,
        work_mode=WorkModePreference.REMOTE,
    )
    base.update(values)
    return FindJobsConfig(**base)  # type: ignore[arg-type]


def _test_words(text: str) -> set[str]:
    """The test's own reading of "whole words, case and accents aside" (not the module's function)."""

    plain = "".join(ch for ch in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(ch))
    return set(re.findall(r"[a-z0-9]+", plain))


def scan(home: Path, typed: tuple[str, ...], strict: bool, config: FindJobsConfig | None,
         company_words: tuple[str, ...] = (), location_words: tuple[str, ...] = ()) -> list[tuple[str, str, str, str]]:
    """The truth: the product's own path over the company files, no index. Newest first by posted, then URL."""

    index = CompanyIndex.for_home(home)
    hits = []
    for ats, slug in index.keys():
        entry = index.read(ats, slug)
        if entry is None:
            continue  # a torn file: the product's readers skip it too
        for posting in entry.live():
            if config is not None:
                row = _indexed_row(entry, posting)
                if published_too_old(row, config, now=NOW):
                    continue
                if config.countries and country_match(row.location, config.countries, structured_countries=row.countries) is False:
                    continue
                if not work_mode_fit(row, config).passes:
                    continue
            if not set(company_words) <= _test_words(entry.company) or not set(location_words) <= _test_words(posting.location):
                continue
            if strict:
                hit = any(strict_title_match(posting.title, one) for one in typed)
            else:
                hit = matches_roles(posting.title, typed)
            if hit:
                posted = postings_module.stamp(posting.published_at) or postings_module.stamp(posting.first_seen) or ""
                hits.append((posted, posting.url, entry.key, posting.posting_id))
    hits.sort(reverse=True)
    return hits


def via_index(home: Path, typed: tuple[str, ...], strict: bool, config: FindJobsConfig | None,
              company_words: tuple[str, ...] = (), location_words: tuple[str, ...] = ()):
    """The index's candidates through the rule: (rows in order, the count path's number, how many candidates)."""

    query = IndexQuery(
        titles=typed, strict=strict, company_words=company_words, location_words=location_words,
        filters=None if config is None else IndexFilters.from_config(config, now=NOW),
    )

    def rule(title: str) -> bool:
        if strict:
            return any(strict_title_match(title, one) for one in typed)
        return matches_roles(title, typed)

    found = search_index.candidates(home, query)
    assert found.available, found
    rows = [(row.posted, row.url, row.board, row.posting_id) for row in found.rows if rule(row.title)]
    counted = search_index.title_counts(home, query)
    assert counted.available, counted
    assert sum(count for _title, count in counted.counts) == len(found.rows)
    return rows, sum(count for title, count in counted.counts if rule(title)), len(found.rows)


SHAPES = {
    "all": (None, (), ()),
    "defaults": (_config(), (), ()),
    "defaults+words": (_config(), ("ai",), ("remote",)),
    "remote, not the default country": (_config(countries=("DE", "GB")), (), ()),
    "hybrid in an area, two countries": (_config(work_mode=WorkModePreference.HYBRID, location="Denver, CO", countries=("US", "DE")), (), ()),
    "onsite, no area, any country, fixed date": (
        _config(work_mode=WorkModePreference.ONSITE, countries=(), max_age_days=None, published_after="2026-09-20"), (), ("remote",),
    ),
}


# ---------------------------------------------------------------------------
# Exactness
# ---------------------------------------------------------------------------


def test_index_candidates_through_the_rule_equal_a_scan_of_the_company_files(home: Path) -> None:
    compared = with_rows = narrowed = 0
    for typed in TYPED:
        for strict in (False, True):
            for shape, (config, company_words, location_words) in SHAPES.items():
                expected = scan(home, (typed,), strict, config, company_words, location_words)
                rows, counted, candidates = via_index(home, (typed,), strict, config, company_words, location_words)
                label = f"{typed!r} strict={strict} {shape}"
                assert rows == expected, label
                assert counted == len(expected), label
                compared += 1
                with_rows += bool(expected)
                narrowed += candidates > len(expected)
    assert compared == len(TYPED) * 2 * len(SHAPES) == 192
    # The comparison is not empty on both sides, and the index really is only a narrowing (the rule drops rows).
    assert with_rows >= 110, with_rows
    assert narrowed >= 20, narrowed


def test_the_fixture_holds_the_hard_cases(home: Path) -> None:
    """What the exactness test is worth: each case the spike named has rows on this home."""

    def rows(typed: str, strict: bool = False, **shape: object) -> int:
        return len(scan(home, (typed,), strict, shape.get("config"), shape.get("company", ()), shape.get("location", ())))  # type: ignore[arg-type]

    assert rows("senior engineer", strict=True) < rows("senior engineer")  # seniority words count only when strict
    assert rows("sr software engineer", strict=True) == rows("senior software engineer", strict=True) > 0  # "Sr." is "senior"
    assert rows("c++ engineer") > 0 and rows("c# developer") > 0
    assert rows("c++ engineer") != rows("c engineer")  # "c++" is its own word, not "c"
    for typed, mark in (("c++ engineer", "c++"), ("c# developer", "c#")):
        narrowed = search_index.candidates(home, IndexQuery(titles=(typed,))).rows
        assert narrowed and all(mark in row.title.lower() for row in narrowed), typed  # the index keeps "+" and "#"
    assert rows("ingénieur logiciel") > 0
    assert rows("staff engineer", company=("ai",)) > 0  # a two-letter company word
    assert 0 < rows("staff engineer", company=("go",), location=("zurich",)) < rows("staff engineer", location=("zurich",))  # an accent
    index = CompanyIndex.for_home(home)
    every = [posting for ats, slug in index.keys() for posting in index.read(ats, slug).postings.values()]  # type: ignore[union-attr]
    assert sum(1 for posting in every if posting.removed) > 20
    assert sum(1 for posting in every if postings_module.stamp(posting.published_at) is None) > 20
    assert sum(1 for posting in every if posting.countries is not None) > 100
    assert sum(1 for posting in every if posting.countries == ()) > 5  # structured and empty: a trusted "no country"


def test_several_typed_titles_and_paging(home: Path) -> None:
    typed = ("staff engineer", "product manager", "c# developer")
    for strict in (False, True):
        expected = scan(home, typed, strict, _config())
        rows, counted, _candidates = via_index(home, typed, strict, _config())
        assert rows == expected and counted == len(expected) > 0

    query = IndexQuery(titles=typed, filters=IndexFilters.from_config(_config(), now=NOW))
    whole = search_index.candidates(home, query).rows
    paged = []
    for offset in range(0, len(whole) + 7, 7):
        page = search_index.candidates(home, query, limit=7, offset=offset)
        assert page.available and len(page.rows) <= 7
        paged.extend(page.rows)
    assert tuple(paged) == whole and len(whole) > 14
    assert [(row.posted, row.url) for row in whole] == sorted(((row.posted, row.url) for row in whole), reverse=True)
    assert search_index.candidates(home, query, limit=0).rows == ()


def test_titles_that_ask_for_nothing_and_no_title_at_all(home: Path) -> None:
    live = scan(home, (MATCH_ANY_TITLE_ROLE,), False, None)
    assert len(search_index.candidates(home, IndexQuery()).rows) == len(live)
    assert len(search_index.candidates(home, IndexQuery(titles=(MATCH_ANY_TITLE_ROLE,))).rows) == len(live)
    # "of the" has no word the rule asks for: the rule matches no title, and so does the index.
    nothing = search_index.candidates(home, IndexQuery(titles=("of the",)))
    assert nothing.available and nothing.rows == () and scan(home, ("of the",), False, None) == []
    assert search_index.title_counts(home, IndexQuery(titles=("of the",))).counts == ()
    # ... but beside a real title it changes nothing.
    assert via_index(home, ("of the", "data engineer"), False, None)[0] == scan(home, ("data engineer",), False, None)
    # Words alone (no title): exact, no rule needed.
    words = search_index.candidates(home, IndexQuery(company_words=("Zurich", "robotics"), location_words=("REMOTE",)))
    expected = scan(home, (MATCH_ANY_TITLE_ROLE,), False, None, ("zurich", "robotics"), ("remote",))
    assert [(row.posted, row.url, row.board, row.posting_id) for row in words.rows] == expected and expected
    assert all(row.company == "Zürich Robotics GmbH" and not row.removed for row in words.rows)


def test_removed_postings_are_stored_and_flagged(home: Path) -> None:
    index = CompanyIndex.for_home(home)
    every = [(entry.key, posting) for ats, slug in index.keys() for entry in [index.read(ats, slug)] for posting in entry.postings.values()]  # type: ignore[union-attr]
    found = search_index.candidates(home, IndexQuery(include_removed=True))
    assert len(found.rows) == len(every)
    assert {(row.board, row.posting_id) for row in found.rows if row.removed} == {
        (key, posting.posting_id) for key, posting in every if posting.removed
    }
    state = search_index.status(home)
    assert (state.available, state.postings, state.live, state.boards) == (
        True, len(every), sum(1 for _key, posting in every if not posting.removed), len(BOARDS),
    )


def test_a_row_carries_what_the_page_shows(home: Path) -> None:
    row = search_index.candidates(home, IndexQuery(titles=("accountant",), company_words=("example",)), limit=1).rows[0]
    posting = CompanyIndex.for_home(home).read("greenhouse", "example-ai").postings[row.posting_id]  # type: ignore[union-attr]
    assert (row.board, row.company, row.title, row.location, row.url) == (
        "greenhouse:example-ai", "Example AI", "Accountant", posting.location, posting.url,
    )
    assert row.first_seen == posting.first_seen and row.changed_at == posting.changed_at
    assert row.published_at == postings_module.stamp(posting.published_at)
    assert row.posted == (row.published_at or postings_module.stamp(posting.first_seen))


# ---------------------------------------------------------------------------
# The pure rules
# ---------------------------------------------------------------------------


def test_strict_title_match_asks_for_every_typed_word() -> None:
    assert matches_roles("Staff Software Engineer", ("senior engineer",))
    assert not strict_title_match("Staff Software Engineer", "senior engineer")
    assert strict_title_match("Sr. Software Engineer", "senior engineer")
    assert strict_title_match("Senior Software Engineers", "Sr Engineer")
    assert not strict_title_match("Senior Training Engineer", "senior engineer")  # the rule's deny words still apply
    assert not strict_title_match("Senior C Engineer", "senior c++ engineer")


def test_words_match_is_whole_words_case_and_accents_aside() -> None:
    assert words_match("Example AI", ["ai"]) and words_match("Example AI", ["AI", "example"])
    assert not words_match("Maintain Systems", ["ai"])
    assert words_match("Zürich, Switzerland", ["zurich"]) and words_match("Zurich", ["Zürich"])
    assert words_match("Montréal, QC, Canada", ["montreal qc"])
    assert words_match("anything", []) and not words_match("", ["remote"])


def test_the_copied_stamp_and_folder_reader_are_the_products(home: Path) -> None:
    for value in ("2026-10-01", "2026-10-01T09:30:00Z", "2026-10-01T09:30:00.123+02:00", "2026-10-01T09:30:00", "not a date", "", None):
        assert search_index._stamp(value) == postings_module.stamp(value), value
    # An instant that leaves the calendar in UTC: the product's stamp raises, the index stores the first / last stamp.
    for value, stored in (("0001-01-01T00:00:00+05:00", "0001-01-01T00:00:00.000000Z"), ("9999-12-31T23:59:59-05:00", "9999-12-31T23:59:59.999999Z")):
        with pytest.raises(OverflowError):
            postings_module.stamp(value)
        assert search_index._stamp(value) == stored
    root = CompanyIndex.for_home(home).root
    (root / "last-update.json").write_text("{}", encoding="utf-8")
    assert search_index._folder_files(root) == postings_module._index_files(home) and len(postings_module._index_files(home)) == len(BOARDS)


# ---------------------------------------------------------------------------
# Build and update
# ---------------------------------------------------------------------------


def test_build_writes_one_file_in_place_and_folds_the_wal(tmp_path: Path) -> None:
    home = make_home(tmp_path, build=False)
    path = search_index.search_index_path(home)
    assert path == home / "cache" / "scout" / "search.sqlite"
    assert not search_index.is_built(home) and not path.exists()  # asking never creates the file
    assert search_index.candidates(home, IndexQuery()).reason == search_index.MISSING and not path.exists()
    before = postings_module._index_files(home)

    seen: list[tuple[int, int]] = []
    built = search_index.rebuild_from_index(home, progress=lambda done, total: seen.append((done, total)))
    assert built.available and built.boards == len(BOARDS) and built.postings == len(BOARDS) * len(TITLES) * 3
    assert seen == [(done, len(BOARDS)) for done in range(len(BOARDS) + 1)]
    assert search_index.is_built(home)
    assert postings_module._index_files(home) == before  # the company files are only read
    assert sorted(item.name for item in path.parent.iterdir() if item.name.startswith("search")) in (
        ["search.sqlite", "search.sqlite-shm", "search.sqlite-wal"], ["search.sqlite"],
    )
    wal = Path(f"{path}-wal")
    assert not wal.exists() or wal.stat().st_size == 0  # wal_checkpoint(TRUNCATE)

    # A second build is in place: the same file, never a temp file + os.replace.
    inode = path.stat().st_ino
    assert search_index.rebuild_from_index(home).available and path.stat().st_ino == inode
    search_index.close(home)
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("INSERT INTO ft(ft) VALUES ('integrity-check')").rowcount is not None  # raises when FTS and rows differ
        assert "contentless_delete" not in " ".join(row[0] or "" for row in conn.execute("SELECT sql FROM sqlite_master"))
    finally:
        conn.close()


def test_one_company_replaced_and_removed(home: Path) -> None:
    index = CompanyIndex.for_home(home)
    typed = ("staff engineer", "data engineer")
    before = scan(home, typed, False, _config())
    assert via_index(home, typed, False, _config())[0] == before

    key = write_board(home, 1, round_=3)  # the company file changes first, as `sources update` does
    stale = search_index.candidates(home, IndexQuery(titles=typed))
    assert (stale.available, stale.reason, stale.rows) == (False, search_index.STALE, ())
    assert search_index.status(home).reason == search_index.STALE and search_index.is_built(home)
    assert search_index.upsert_company(home, key) is True
    after = scan(home, typed, False, _config())
    assert after != before and via_index(home, typed, False, _config())[0] == after
    # This company's rows took the ids of its old rows: the old rows' words must be gone from the FTS table.
    for words in (("remote",), ("denver",), ("zurich",), ("london",)):
        got = search_index.candidates(home, IndexQuery(company_words=("sample",), location_words=words)).rows
        assert [(row.posted, row.url, row.board, row.posting_id) for row in got] == scan(
            home, (MATCH_ANY_TITLE_ROLE,), False, None, ("sample",), words
        ), words
    wal = Path(f"{search_index.search_index_path(home)}-wal")
    assert not wal.exists() or wal.stat().st_size == 0

    assert index.delete("ashby", "go-fast-ai")
    assert search_index.candidates(home, IndexQuery()).reason == search_index.STALE
    assert search_index.remove_company(home, "ashby:go-fast-ai") is True
    assert via_index(home, typed, False, None)[0] == scan(home, typed, False, None)
    state = search_index.status(home)
    assert state.available and state.boards == len(BOARDS) - 1
    assert not search_index.candidates(home, IndexQuery(company_words=("go",))).rows

    # Every board again and again: the FTS rows follow the row table through the two triggers.
    for round_ in (5, 6):
        for board in (0, 2, 3):
            assert search_index.upsert_company(home, write_board(home, board, round_=round_))
    for one in TYPED:
        assert via_index(home, (one,), True, _config())[0] == scan(home, (one,), True, _config()), one
    search_index.close(home)
    conn = sqlite3.connect(search_index.search_index_path(home))
    try:
        conn.execute("INSERT INTO ft(ft) VALUES ('integrity-check')")
        assert conn.execute("SELECT COUNT(*) FROM pc WHERE id NOT IN (SELECT id FROM p)").fetchone()[0] == 0
    finally:
        conn.close()


def test_row_ids_freed_by_a_removed_company_carry_no_old_words(home: Path) -> None:
    """The last company file holds the highest row ids. Removed, its ids go to the next company written: the FTS
    table must have dropped the removed rows' words (the delete trigger), or a word search would list wrong rows."""

    index = CompanyIndex.for_home(home)
    assert list(index.keys())[-1] == ("lever", "sample-cloud")
    assert index.delete("lever", "sample-cloud") and search_index.remove_company(home, "lever:sample-cloud")
    assert search_index.upsert_company(home, write_board(home, 0, round_=4))
    for words in (("remote",), ("denver",), ("zurich",), ("london",), ("germany",), ("anywhere",)):
        got = search_index.candidates(home, IndexQuery(location_words=words)).rows
        assert [(row.posted, row.url, row.board, row.posting_id) for row in got] == scan(
            home, (MATCH_ANY_TITLE_ROLE,), False, None, (), words
        ), words
    assert not search_index.candidates(home, IndexQuery(company_words=("sample",))).rows
    for one in TYPED:
        assert via_index(home, (one,), True, None)[0] == scan(home, (one,), True, None), one


def test_refresh_reindexes_every_changed_new_and_gone_company(home: Path) -> None:
    index = CompanyIndex.for_home(home)
    write_board(home, 0, round_=2)
    write_board(home, 4, round_=9)
    index.delete("lever", "maintain-systems")
    index.write(CompanyIndexEntry(company="Brand New Co", ats="greenhouse", slug="brand-new", checked_at=None, etag=None,
                                  body_sha256=None, postings={"n1": _board_postings(0)["0050"]}))
    assert search_index.status(home).reason == search_index.STALE
    assert search_index.refresh(home) is True
    assert search_index.status(home).available
    for one in ("staff engineer", "senior engineer"):
        assert via_index(home, (one,), False, _config())[0] == scan(home, (one,), False, _config())
    assert len(search_index.candidates(home, IndexQuery(company_words=("brand", "new"))).rows) == 1
    assert search_index.refresh(home) is True and search_index.status(home).available  # nothing changed: still current


def test_a_write_never_builds_and_never_creates_the_file(tmp_path: Path) -> None:
    home = make_home(tmp_path, build=False)
    path = search_index.search_index_path(home)
    assert search_index.upsert_company(home, "greenhouse:example-ai") is False
    assert search_index.remove_company(home, "greenhouse:example-ai") is False
    assert search_index.refresh(home) is False
    assert not path.exists()
    assert search_index.upsert_company(home, "not-a-provider:x") is False and search_index.upsert_company(home, "greenhouse:") is False
    assert search_index.rebuild_from_index(home).available
    assert search_index.upsert_company(home, "not-a-provider:x") is False
    assert search_index.upsert_company(home, "greenhouse:never-seen") is True and search_index.status(home).available
    search_index.close(home)


def test_a_company_file_nobody_can_read_is_stamped_and_holds_no_postings(home: Path) -> None:
    root = CompanyIndex.for_home(home).root
    (root / "greenhouse:torn.json").write_text("{ not json", encoding="utf-8")
    (root / "other:thing.json").write_text("{}", encoding="utf-8")
    assert search_index.status(home).reason == search_index.STALE
    assert search_index.refresh(home)
    state = search_index.status(home)
    assert state.available and state.boards == len(BOARDS) + 2
    assert via_index(home, ("staff engineer",), False, None)[0] == scan(home, ("staff engineer",), False, None)
    assert search_index.rebuild_from_index(home).boards == len(BOARDS) + 2


def test_an_empty_home_builds_an_empty_index(tmp_path: Path) -> None:
    home = tmp_path / "empty"
    built = search_index.rebuild_from_index(home)
    assert (built.available, built.postings, built.boards) == (True, 0, 0)
    found = search_index.candidates(home, IndexQuery(titles=("engineer",)))
    assert found.available and found.rows == ()
    write_board(home, 0)
    assert search_index.candidates(home, IndexQuery(titles=("engineer",))).reason == search_index.STALE
    search_index.close(home)


def test_a_sqlite_without_fts5_is_unavailable_not_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = make_home(tmp_path, build=False)
    monkeypatch.setattr(search_index, "_FTS_DDL", search_index._FTS_DDL.replace("USING fts5(", "USING fts5_not_here("))
    built = search_index.rebuild_from_index(home)
    assert (built.available, built.reason) == (False, search_index.NO_FTS5)
    assert not search_index.is_built(home)
    found = search_index.candidates(home, IndexQuery(titles=("engineer",)))
    assert not found.available and found.rows == () and found.reason == search_index.NOT_BUILT
    assert search_index.upsert_company(home, "greenhouse:example-ai") is False
    monkeypatch.undo()
    assert search_index.rebuild_from_index(home).available
    search_index.close(home)


def test_a_failed_build_keeps_the_old_index(home: Path) -> None:
    before = via_index(home, ("staff engineer",), False, None)[0]

    def stop(done: int, _total: int) -> None:
        if done == 3:
            raise RuntimeError("stopped by the caller")

    with pytest.raises(RuntimeError):
        search_index.rebuild_from_index(home, progress=stop)
    assert search_index.status(home).available
    assert via_index(home, ("staff engineer",), False, None)[0] == before


# ---------------------------------------------------------------------------
# Freshness: the folder scan
# ---------------------------------------------------------------------------


def test_the_folder_is_scanned_again_only_when_it_moved(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = CompanyIndex.for_home(home).root
    scans: list[Path] = []
    real = search_index._folder_files

    def counting(folder: Path) -> dict[str, tuple[int, int]]:
        scans.append(folder)
        return real(folder)

    monkeypatch.setattr(search_index, "_folder_files", counting)
    query = IndexQuery(titles=("staff engineer",))
    # A folder written a moment ago is scanned every time: a replace in the same clock tick must not hide.
    os.utime(root)
    assert search_index.candidates(home, query).available and search_index.candidates(home, query).available
    assert len(scans) == 2
    # A folder that has been still for a while is scanned once, then only its own mtime is read.
    old = (NOW - timedelta(days=1)).timestamp()
    os.utime(root, (old, old))
    for _ in range(4):
        assert search_index.candidates(home, query).available
    assert len(scans) == 3
    # The company index replaces a file (temp file + os.replace): the folder moves and the next read sees it.
    write_board(home, 2, round_=1)
    assert search_index.candidates(home, query).reason == search_index.STALE
    assert len(scans) == 4

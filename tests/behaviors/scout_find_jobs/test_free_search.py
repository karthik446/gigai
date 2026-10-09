"""FS1 (0.1.11.7): the free search (``find_jobs/free_search.py``) over synthetic boards only.

Pins:

- EXACTNESS: for 16 typed titles x {all, defaults, defaults + a company word + a location word} the index path, the
  scan path and a brute-force read of the company files give the same rows in the same order and the same total
  (the brute force is ``test_search_index.scan``: the product's own rule and filters, written apart from the module);
- the four titles of the design, the strict rule (every typed word, seniority included) and several titles at once;
- paging: every page of 7 put end to end is the whole list, and ``more`` says whether a row follows;
- an index that cannot answer (missing, damaged, stale, not built, another schema) means the scan, with the same rows;
- a search WRITES NOTHING: the home is byte-identical, with the index and without it, and a search never builds it;
- 0.1.11.8 N1 + N2: the same 48 combinations x {US only: the default, on, off} x {every posting, copies as one row},
  on the index path, the scan path and a brute force written apart: the same jobs in the same order with the same
  copies (the canonical one first), the same total and "Show all" total; the ONE country rule of a request and the
  "unclear location" of a posting it cannot place; the copies of one job as one row (the Point Wild case), only when
  the description is the same and stored.

The labels, the CLI and the route are in ``test_free_search_labels_cli.py`` and ``tests/api_e2e/test_free_search_route.py``.
Every posting, company and place is made up. Nothing here touches the network or a real home.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import hashlib
from pathlib import Path
import sqlite3
import unicodedata

import pytest

from gigai.scout.find_jobs import free_search, search_index
from gigai.scout.find_jobs.ats_board_clients import MATCH_ANY_TITLE_ROLE
from gigai.scout.find_jobs.company_index import CompanyIndex, CompanyIndexEntry, IndexedPosting
from gigai.scout.find_jobs.contracts import FindJobsConfig
from gigai.scout.find_jobs.filters import country_match, published_too_old
from gigai.scout.find_jobs.job_copies import place_of
from gigai.scout.find_jobs.index_search import _indexed_row
from gigai.scout.find_jobs.search_index import strict_title_match
from gigai.scout.find_jobs.work_mode import work_mode_fit
from gigai.scout import postings as postings_module
from gigai.scout.find_jobs.free_search import FreeSearchError, SearchRequest

from tests.behaviors.scout_find_jobs.test_search_index import BOARDS, NOW, TITLES, TYPED, _config, make_home, scan, write_board

pytestmark = pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 9, 0), reason="needs FTS5")

DESIGN_TITLES = ("director of software engineering", "senior systems engineer", "forward deployed engineer", "staff ai engineer")
#: shape -> (show all, company words, location words)
SHAPES = {
    "all": (True, (), ()),
    "defaults": (False, (), ()),
    "defaults+company+location": (False, ("ai",), ("remote",)),
}


@pytest.fixture
def homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Two homes with the same company files: one with the index built, one that never had one. Default filters: remote, US, 30 days."""

    indexed = make_home(tmp_path / "indexed", build=False)
    scanned = make_home(tmp_path / "scanned", build=False)
    # 0.1.11.8: the fixture's postings get descriptions (their digest), so some of a title's three postings are copies.
    _describe(indexed)
    _describe(scanned)
    assert search_index.rebuild_from_index(indexed).available
    monkeypatch.setattr(free_search, "default_config", lambda _home, _target: _config())
    yield indexed, scanned
    search_index.close(indexed)
    search_index.close(scanned)


def _describe(home: Path) -> None:
    """Give the fixture's postings a description digest: of a title's three postings on a board, the first and the
    third share one description, the second has another; every fifth title has none stored (never merged)."""

    index = CompanyIndex.for_home(home)
    for ats, slug in list(index.keys()):
        entry = index.read(ats, slug)
        assert entry is not None
        postings = {}
        for posting_id, posting in entry.postings.items():
            number, copy = int(posting_id[1:3]), int(posting_id[3])
            content = None if number % 5 == 0 else "sha256:" + hashlib.sha256(f"{posting.title}|{copy % 2}".encode()).hexdigest()
            postings[posting_id] = replace(posting, content_sha256=content)
        index.write(replace(entry, postings=postings))


def _everything(home: Path, request: SearchRequest, *, page: int = 200) -> tuple[list[tuple[str, str, str, str]], free_search.SearchPage]:
    """Every row of a search, page after page, as the brute force names a row; and the first page."""

    rows: list[tuple[str, str, str, str]] = []
    first = None
    offset = 0
    while True:
        found = free_search.search(home, replace(request, limit=page, offset=offset), target=home, now=NOW)
        first = first or found
        rows.extend((row.posting.posted, row.posting.url, row.posting.board, row.posting.posting_id) for row in found.rows)
        assert len(found.rows) <= page
        if not found.more:
            return rows, first
        assert len(found.rows) == page, "a page that is followed by a row is full"
        offset += page


def _brute(home: Path, titles: tuple[str, ...], shape: str, config: FindJobsConfig | None = None):
    show_all, company, location = SHAPES[shape]
    return scan(home, titles, True, None if show_all else (config or _config()), company, location)


def _typed(*args: object, **more: object) -> SearchRequest:
    """``SearchRequest.typed`` as the 0.1.11.7 search unless a test says otherwise: every posting its own row, no US-only switch."""

    return SearchRequest.typed(*args, **{"us_only": False, "collapse": False, **more})  # type: ignore[arg-type]


def _request(titles: str, shape: str, **more: object) -> SearchRequest:
    show_all, company, location = SHAPES[shape]
    return _typed(titles, company=company, location=location, show_all=show_all, **more)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# (1) Exactness: index == scan == brute force
# ---------------------------------------------------------------------------


def test_index_path_scan_path_and_brute_force_give_the_same_rows_and_total(homes) -> None:
    indexed, scanned = homes
    compared = with_rows = 0
    for typed in TYPED:
        for shape in SHAPES:
            label = f"{typed!r} {shape}"
            expected = _brute(indexed, (typed,), shape)
            request = _request(typed, shape, count=True)
            by_index, first_index = _everything(indexed, request)
            by_scan, first_scan = _everything(scanned, request)
            assert first_index.source == "index" and first_index.index_reason is None, label
            assert first_scan.source == "scan" and first_scan.index_reason == "missing", label
            assert by_index == expected, label
            assert by_scan == expected, label
            assert first_index.total == first_scan.total == len(expected), label
            everywhere = len(_brute(indexed, (typed,), "all")) if shape == "all" else len(scan(indexed, (typed,), True, None, *SHAPES[shape][1:]))
            assert first_index.total_all == first_scan.total_all == everywhere, label
            assert first_index.rows == first_scan.rows, label  # the whole row, labels included
            compared += 1
            with_rows += bool(expected)
    assert compared == len(TYPED) * len(SHAPES) == 48 and len(TYPED) >= 12
    assert with_rows >= 30, with_rows  # the comparison is not empty on both sides


def test_the_four_titles_of_the_design(homes) -> None:
    indexed, scanned = homes
    for typed in DESIGN_TITLES:
        for shape in SHAPES:
            expected = _brute(indexed, (typed,), shape)
            assert _everything(indexed, _request(typed, shape))[0] == expected, (typed, shape)
            assert _everything(scanned, _request(typed, shape))[0] == expected, (typed, shape)
        assert _brute(indexed, (typed,), "all"), f"{typed!r} has rows on the fixture"
        assert _brute(indexed, (typed,), "defaults"), f"{typed!r} has rows with the default filters"


def test_every_typed_word_counts_and_the_deny_words_still_apply(homes) -> None:
    indexed, scanned = homes
    for home in (indexed, scanned):
        found = free_search.search(home, _typed("senior systems engineer", show_all=True, limit=200), target=home, now=NOW)
        titles = {row.posting.title for row in found.rows}
        # "Sr" is Senior; a plain "Systems Engineer" is not listed (the profile rule would list it).
        assert titles == {"Sr Systems Engineer", "Senior Systems Engineer, Controls"}, titles
        staff = {row.posting.title for row in free_search.search(home, _typed("staff engineer", show_all=True, limit=200), target=home, now=NOW).rows}
        assert "Staff Software Engineer" in staff and "Staff Engineer" in staff
        assert "Staff Training Engineer" not in staff and "Staff Sales Engineer" not in staff
        assert {"C++ Engineer", "Senior C++ Software Engineer"} == {
            row.posting.title for row in free_search.search(home, _typed("c++ engineer", show_all=True, limit=200), target=home, now=NOW).rows
        }


def test_several_titles_and_words_alone(homes) -> None:
    indexed, scanned = homes
    typed = ("senior systems engineer", "staff ai engineer", "c# developer")
    for shape in SHAPES:
        expected = _brute(indexed, typed, shape)
        assert expected
        request = _request("Senior Systems Engineer,  Staff AI Engineer , c# developer,", shape, count=True)
        assert request.titles == ("Senior Systems Engineer", "Staff AI Engineer", "c# developer")
        for home in (indexed, scanned):
            rows, first = _everything(home, request)
            assert rows == expected and first.total == len(expected), shape
    # No title at all: a company word and a location word select by themselves (every title is accepted).
    words_only = _typed(None, company="go fast", location="remote", show_all=True, count=True)
    expected = scan(indexed, (MATCH_ANY_TITLE_ROLE,), False, None, ("go", "fast"), ("remote",))
    by_index, first = _everything(indexed, words_only)
    by_scan, _first = _everything(scanned, words_only)
    assert by_index == by_scan == expected and expected, "a search by words alone has rows, the same on both paths"
    assert {row[2] for row in by_index} == {"ashby:go-fast-ai"} and first.total == len(by_index)


def test_pages_put_end_to_end_are_the_whole_list(homes) -> None:
    indexed, scanned = homes
    request = _request("software engineer", "all")
    expected = _brute(indexed, ("software engineer",), "all")
    assert len(expected) > 30
    for home in (indexed, scanned):
        rows, _first = _everything(home, request, page=7)
        assert rows == expected
        last = free_search.search(home, replace(request, limit=7, offset=len(expected) - 3), target=home, now=NOW)
        assert len(last.rows) == 3 and last.more is False
        exact = free_search.search(home, replace(request, limit=len(expected), offset=0), target=home, now=NOW) if len(expected) <= 200 else None
        assert exact is None or (len(exact.rows) == len(expected) and exact.more is False)
        past = free_search.search(home, replace(request, limit=7, offset=len(expected) + 50), target=home, now=NOW)
        assert past.rows == () and past.more is False


def test_the_total_is_counted_only_when_asked_on_the_index_path(homes) -> None:
    indexed, scanned = homes
    request = _request("staff engineer", "defaults")
    page = free_search.search(indexed, request, target=indexed, now=NOW)
    assert page.source == "index" and page.total is None and page.total_all is None and page.hidden is None
    counted = free_search.search(indexed, replace(request, count=True), target=indexed, now=NOW)
    assert counted.rows == page.rows and counted.total == len(_brute(indexed, ("staff engineer",), "defaults"))
    assert counted.total_all == len(_brute(indexed, ("staff engineer",), "all")) > counted.total
    assert free_search.count(indexed, request, target=indexed, now=NOW) == (counted.total, counted.total_all)
    # The scan knows both already.
    by_scan = free_search.search(scanned, request, target=scanned, now=NOW)
    assert (by_scan.total, by_scan.total_all) == (counted.total, counted.total_all)
    assert free_search.count(scanned, request, target=scanned, now=NOW) == (counted.total, counted.total_all)
    # Show all: nothing is hidden.
    everything = free_search.search(indexed, replace(request, show_all=True, count=True), target=indexed, now=NOW)
    assert everything.total == everything.total_all == counted.total_all and everything.hidden == 0 and everything.filters is None


def test_removed_postings_only_when_asked(homes) -> None:
    indexed, scanned = homes
    for home in (indexed, scanned):
        live = free_search.search(home, _typed("engineer", show_all=True, limit=200, count=True), target=home, now=NOW)
        both = free_search.search(home, _typed("engineer", show_all=True, include_removed=True, limit=200, count=True), target=home, now=NOW)
        assert not any(row.posting.removed for row in live.rows)
        assert both.total is not None and live.total is not None and both.total > live.total
    with_removed = _typed("engineer", show_all=True, include_removed=True)
    assert _everything(indexed, with_removed)[0] == _everything(scanned, with_removed)[0]
    assert any(row.posting.removed for row in free_search.search(indexed, replace(with_removed, limit=200), target=indexed, now=NOW).rows)


# ---------------------------------------------------------------------------
# (4) An index that cannot answer: the scan, with the same rows
# ---------------------------------------------------------------------------


def _damage(home: Path) -> None:
    search_index.close(home)
    path = search_index.search_index_path(home)
    for suffix in ("-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    data = bytearray(path.read_bytes())
    data[:100] = bytes(100)
    path.write_bytes(bytes(data))


def _stale(home: Path) -> None:
    write_board(home, 2, round_=1)  # a company file replaced after the build, and nobody told the index


def _meta(key: str, value: str):
    def change(home: Path) -> None:
        search_index.close(home)
        conn = sqlite3.connect(search_index.search_index_path(home))
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()

    return change


@pytest.mark.parametrize(
    ("reason", "change"),
    [
        ("damaged", _damage),
        ("stale", _stale),
        ("not_built", _meta("built", "0")),
        ("other_schema", _meta("schema_version", "999")),
    ],
)
def test_an_index_that_cannot_answer_means_the_scan_with_the_same_rows(homes, reason: str, change) -> None:
    indexed, _scanned = homes
    assert free_search.search(indexed, _request("staff engineer", "defaults"), target=indexed, now=NOW).source == "index"
    change(indexed)
    index_bytes = search_index.search_index_path(indexed).read_bytes()
    for typed in ("staff engineer", "senior systems engineer", "software engineer"):
        for shape in SHAPES:
            rows, first = _everything(indexed, _request(typed, shape, count=True))
            assert first.source == "scan" and first.index_reason == reason, (typed, shape, first.index_reason)
            assert rows == _brute(indexed, (typed,), shape), (typed, shape)
            assert first.total == len(rows)
    search_index.close(indexed)
    assert search_index.search_index_path(indexed).read_bytes() == index_bytes, "a search never repairs or rebuilds the index"


def test_a_search_never_builds_the_index(homes) -> None:
    _indexed, scanned = homes
    path = search_index.search_index_path(scanned)
    for _ in range(2):
        found = free_search.search(scanned, _request("staff engineer", "defaults", count=True), target=scanned, now=NOW)
        assert found.source == "scan" and found.index_reason == "missing" and found.rows
    assert not path.exists() and not Path(f"{path}-wal").exists() and not Path(f"{path}-shm").exists()
    assert search_index.is_built(scanned) is False


def test_the_index_moving_between_two_reads_of_a_page_is_answered_by_the_scan(homes, monkeypatch: pytest.MonkeyPatch) -> None:
    indexed, _scanned = homes
    real = search_index.candidates
    calls: list[int] = []

    def moving(home, query, *, limit=None, offset=0):
        calls.append(offset)
        found = real(home, query, limit=limit, offset=offset)
        return replace(found, stamp=f"moved-{len(calls)}")

    # Small reads, so one page takes several of them: first with a still index (the rows are the same as in one read).
    monkeypatch.setattr(free_search, "_MIN_CHUNK", 5)
    monkeypatch.setattr(free_search, "_CHUNK_FACTOR", 0)
    request = replace(_request("engineer", "all"), limit=40)
    still = free_search.search(indexed, request, target=indexed, now=NOW)
    assert still.source == "index" and still.more is True
    assert [(row.posting.posted, row.posting.url, row.posting.board, row.posting.posting_id) for row in still.rows] == _brute(indexed, ("engineer",), "all")[:40]
    monkeypatch.setattr(search_index, "candidates", moving)
    found = free_search.search(indexed, request, target=indexed, now=NOW)
    assert found.source == "scan" and found.index_reason == "stale" and len(calls) == 2 * free_search._RESTARTS
    assert [(row.posting.posted, row.posting.url, row.posting.board, row.posting.posting_id) for row in found.rows] == _brute(indexed, ("engineer",), "all")[:40]


# ---------------------------------------------------------------------------
# (3) Writes nothing
# ---------------------------------------------------------------------------


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _searches(home: Path) -> None:
    for typed in ("staff engineer", "senior systems engineer", "accountant", "no such title anywhere"):
        for shape in SHAPES:
            request = _request(typed, shape, count=True)
            free_search.search(home, request, target=home, now=NOW)
            free_search.search(home, replace(request, count=False, limit=3, offset=2), target=home, now=NOW)
            free_search.count(home, request, target=home, now=NOW)


def test_a_search_writes_nothing_when_the_scan_runs(homes) -> None:
    _indexed, scanned = homes
    before = _snapshot(scanned)
    _searches(scanned)
    assert _snapshot(scanned) == before, "the home is byte-identical after the searches (no index, no last-search.json, nothing)"
    assert not (scanned / "cache" / "scout" / "companies" / "last-search.json").exists()


def test_a_search_writes_nothing_when_the_index_answers(homes) -> None:
    indexed, _scanned = homes
    search_index.close(indexed)
    sidecars = {f"cache/scout/search.sqlite{suffix}" for suffix in ("-wal", "-shm")}
    before = {name: data for name, data in _snapshot(indexed).items() if name not in sidecars}
    _searches(indexed)
    # SQLite's own -wal / -shm beside the index come and go with an open connection: every other file, the index
    # file itself included, is byte-identical to what it was BEFORE the first read.
    assert {name: data for name, data in _snapshot(indexed).items() if name not in sidecars} == before
    # And with the connection held, a further round of searches changes no byte at all, the sidecars included.
    held = _snapshot(indexed)
    _searches(indexed)
    assert _snapshot(indexed) == held
    search_index.close(indexed)
    assert {name: data for name, data in _snapshot(indexed).items() if name not in sidecars} == before


# ---------------------------------------------------------------------------
# The request, the default filters, what is printed
# ---------------------------------------------------------------------------


def test_a_request_that_asks_for_nothing_is_refused() -> None:
    for build in (
        lambda: _typed(None),
        lambda: _typed(" , ,"),
        lambda: _typed("of the"),  # filler words only: the rule has nothing to ask for
        lambda: _typed("engineer", company="--"),
        lambda: _typed("engineer", limit=0),
        lambda: _typed("engineer", limit=201),
        lambda: _typed("engineer", offset=-1),
    ):
        with pytest.raises(FreeSearchError) as refused:
            build()
        assert refused.value.code == "invalid_value"
    assert _typed("engineer", company=("Go  Fast", "ai"), location="remote remote").company_words == ("Go", "Fast", "ai")
    assert _typed("engineer", location="remote remote").location_words == ("remote",)


def test_the_default_filters_need_a_readable_config_and_show_all_does_not(tmp_path: Path) -> None:
    home = make_home(tmp_path)
    try:
        with pytest.raises(FreeSearchError) as refused:
            free_search.search(home, _typed("staff engineer"), target=home, now=NOW)
        assert refused.value.code == "config_unavailable" and "--all" in str(refused.value)
        with pytest.raises(FreeSearchError) as refused:
            free_search.search(home, _typed("staff engineer"), now=NOW)
        assert refused.value.code == "config_unavailable"
        found = free_search.search(home, _typed("staff engineer", show_all=True), now=NOW)
        assert found.rows and found.filters is None and found.labelled is False
        assert all(row.labels.profiles == () and row.labels.assessment is None and row.labels.application is None for row in found.rows)
    finally:
        search_index.close(home)


def test_the_json_has_stable_keys_and_the_text_says_what_was_hidden(homes) -> None:
    indexed, _scanned = homes
    request = _request("staff engineer", "defaults", count=True)
    response = free_search.to_json(free_search.search(indexed, replace(request, limit=2), target=indexed, now=NOW))
    assert sorted(response) == [
        "all_scope_text", "checked_at", "counts", "filters", "footer", "index", "labels_read", "order", "postings", "profiles", "query",
        "ranked", "schema_version", "scope_text", "source", "us_only",
    ]
    assert response["schema_version"] == "scout-free-search:1" and response["ranked"] is False and response["order"] == "newest_posted"
    assert sorted(response["counts"]) == ["hidden", "more", "shown", "total", "total_all"]  # type: ignore[arg-type]
    assert sorted(response["query"]) == ["all", "collapse", "company", "count", "include_removed", "limit", "location", "offset", "titles", "us_only"]  # type: ignore[arg-type]
    assert response["filters"]["text"] == "remote, US, last 30 days"  # type: ignore[index]
    row = response["postings"]["rows"][0]  # type: ignore[index]
    assert sorted(row) == [
        "application", "assessment", "company", "company_key", "company_name", "company_slug", "copies", "first_seen", "job_identity", "job_url", "location",
        "location_unclear", "locations", "locations_text", "members", "posted", "profiles", "published_at", "removed", "title",
    ]
    # 0.1.11.8: the keys a row gained are additive; a row with no other copy is one copy at its one location.
    assert row["copies"] == 1 and row["locations"] == [row["location"]] and row["locations_text"] == row["location"]
    assert row["members"] == [{"job_identity": row["job_identity"], "job_url": row["job_url"], "location": row["location"], "posted": row["posted"]}]
    assert not {"rank", "rank_score", "fit", "score"} & set(row), "no rank and no fit number"
    counts = response["counts"]
    assert counts["hidden"] == counts["total_all"] - counts["total"] > 0  # type: ignore[index,operator]
    assert response["footer"] == ["Not ranked. Save as a profile to rank.", f"Show all {counts['total_all']:,} (any place, any date): --all"]  # type: ignore[index]
    text = free_search.render_page(response) + "\n" + free_search.render_total(response)
    assert 'Showing 1-2, newest first: "staff engineer" (remote, US, last 30 days).' in text
    assert f"{counts['total']} postings match. More: --offset 2" in text  # type: ignore[index]
    assert "Not ranked. Save as a profile to rank." in text and f"Show all {counts['total_all']} (any place, any date): --all" in text  # type: ignore[index]
    # Not counted: the page alone says nothing about a total, and the footer has no "Show all".
    page = free_search.to_json(free_search.search(indexed, replace(request, count=False), target=indexed, now=NOW))
    assert page["counts"]["total"] is None and page["footer"] == ["Not ranked. Save as a profile to rank."]  # type: ignore[index]
    # Show all: nothing is hidden, so nothing more to show.
    everything = free_search.to_json(free_search.search(indexed, replace(request, show_all=True), target=indexed, now=NOW))
    assert everything["filters"] is None and everything["footer"] == ["Not ranked. Save as a profile to rank."]
    assert "(any place, any date)" in free_search.render_page(everything)


def test_a_title_with_no_match_is_a_sentence(homes) -> None:
    indexed, scanned = homes
    for home in (indexed, scanned):
        nothing = free_search.to_json(free_search.search(home, _typed("underwater basket weaver", count=True), target=home, now=NOW))
        assert nothing["postings"]["rows"] == [] and nothing["counts"]["total"] == 0  # type: ignore[index]
        text = free_search.render_page(nothing)
        assert text.splitlines()[-1] == 'No stored posting matches "underwater basket weaver" (remote, US, last 30 days).'
        assert "Not ranked" not in free_search.render_total(nothing)
    # A title the default filters hide entirely says where the rows are.
    assert "Accountant" in TITLES and BOARDS
    hidden = free_search.to_json(
        free_search.search(indexed, _typed("accountant", location="zürich", count=True), target=indexed, now=NOW)
    )
    if hidden["counts"]["total"] == 0 and hidden["counts"]["total_all"]:  # type: ignore[index]
        assert f"Show all {hidden['counts']['total_all']} (any place, any date): --all" in free_search.render_total(hidden)  # type: ignore[index]


# ---------------------------------------------------------------------------
# 0.1.11.8 N1 + N2: US only, and the copies of one job as one row
# ---------------------------------------------------------------------------

Row = tuple[str, str, str, str]


def _test_fold(text: str) -> str:
    """The test's own reading of "case, spaces and punctuation aside; + and # stay" (not the module's function)."""

    kept = "".join(ch if ch.isalnum() or ch in "+#" else " " for ch in unicodedata.normalize("NFKC", text).casefold())
    return " ".join(kept.split())


def _truth(
    home: Path, titles: tuple[str, ...], shape: str, *, us_only: bool | None, collapse: bool, config: FindJobsConfig | None = None,
    include_removed: bool = False,
) -> tuple[list[list[Row]], int]:
    """The rows of a search as rows of copies (the canonical one first), and the "Show all" total. Written apart from the module.

    The ONE country rule: US only ON = every posting but one clearly outside the US (``place_of``, tested by its own
    table); OFF = the config's countries, or none with Show all. The default is ON when the config's countries hold
    the US. "Show all" keeps the US rule and drops the window and the work mode. Copies: the same board, company,
    title and description digest (a posting with none is alone). The canonical one: in the US first, then the
    earliest posted (undated last), then the posting id; a row stands where its canonical posting stands.
    """

    show_all, company_words, location_words = SHAPES[shape]
    config = config or _config()
    us = ("US" in {code.upper() for code in config.countries}) if us_only is None else us_only
    index = CompanyIndex.for_home(home)
    hits: list[tuple[Row, object, bool]] = []
    everywhere: set[object] = set()
    for ats, slug in index.keys():
        entry = index.read(ats, slug)
        if entry is None:
            continue
        for posting in entry.postings.values():
            if posting.removed and not include_removed:
                continue
            if not any(strict_title_match(posting.title, one) for one in titles):
                continue
            if not set(company_words) <= _words_of(entry.company) or not set(location_words) <= _words_of(posting.location):
                continue
            row = _indexed_row(entry, posting)
            place = place_of(posting.location, posting.countries)
            alone = (entry.key, posting.posting_id)
            key: object = alone
            if collapse and posting.content_sha256:
                key = (entry.key, _test_fold(entry.company), _test_fold(posting.title), posting.content_sha256, bool(posting.removed))
            if us and place == "other":
                continue
            everywhere.add(key)
            if not us and not show_all and config.countries and country_match(row.location, config.countries, structured_countries=row.countries) is False:
                continue
            if not show_all and (published_too_old(row, config, now=NOW) or not work_mode_fit(row, config).passes):
                continue
            posted = postings_module.stamp(posting.published_at) or postings_module.stamp(posting.first_seen) or ""
            hits.append(((posted, posting.url, entry.key, posting.posting_id), key, place == "us"))
    groups: dict[object, list[tuple[Row, bool]]] = {}
    for row, key, in_the_us in hits:
        groups.setdefault(key, []).append((row, in_the_us))
    rows = [
        [row for row, _us in sorted(group, key=lambda item: (not item[1], item[0][0] == "", item[0][0], item[0][3]))]
        for group in groups.values()
    ]
    rows.sort(key=lambda group: group[0], reverse=True)
    return rows, len(everywhere)


def _words_of(text: str) -> set[str]:
    import re

    plain = "".join(ch for ch in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(ch))
    return set(re.findall(r"[a-z0-9]+", plain))


def _groups(home: Path, request: SearchRequest, *, page: int = 200) -> tuple[list[list[Row]], free_search.SearchPage]:
    """Every row of a search with its copies, page after page; and the first page."""

    groups: list[list[Row]] = []
    first = None
    offset = 0
    while True:
        found = free_search.search(home, replace(request, limit=page, offset=offset), target=home, now=NOW)
        first = first or found
        for row in found.rows:
            assert row.copies[0][0] == row.posting, "a row is its canonical posting, the first of its copies"
            groups.append([(copy.posted, copy.url, copy.board, copy.posting_id) for copy, _identity in row.copies])
        if not found.more:
            return groups, first
        assert len(found.rows) == page, "a page that is followed by a row is full of ROWS (jobs), whatever their copies"
        offset += page


@pytest.mark.parametrize("page", [200, 4], ids=["one_page", "pages_of_4"])
def test_us_only_and_copies_are_the_same_on_the_index_the_scan_and_a_brute_force(homes, page: int) -> None:
    indexed, scanned = homes
    compared = merged = narrowed = 0
    for typed in TYPED:
        for shape in SHAPES:
            for us_only in (None, True, False):
                for collapse in (False, True):
                    label = f"{typed!r} {shape} us_only={us_only} collapse={collapse}"
                    expected, everywhere = _truth(indexed, (typed,), shape, us_only=us_only, collapse=collapse)
                    request = _request(typed, shape, count=True, us_only=us_only, collapse=collapse)
                    by_index, first_index = _groups(indexed, request, page=page)
                    by_scan, first_scan = _groups(scanned, request, page=page)
                    assert first_index.source == "index" and first_scan.source == "scan", label
                    assert by_index == expected, label
                    assert by_scan == expected, label
                    assert first_index.total == first_scan.total == len(expected), label
                    assert first_index.total_all == first_scan.total_all == everywhere, label
                    assert first_index.rows == first_scan.rows, label
                    assert free_search.count(indexed, request, target=indexed, now=NOW) == (len(expected), everywhere), label
                    assert first_index.us_only is (True if us_only is None else us_only) and first_index.us_only_default is True, label
                    compared += 1
                    merged += any(len(group) > 1 for group in expected)
                    narrowed += us_only is not False and shape == "all" and len(expected) < len(_truth(indexed, (typed,), shape, us_only=False, collapse=collapse)[0])
    assert compared == len(TYPED) * len(SHAPES) * 6 == 288
    assert merged >= 25, merged  # the fixture has copies to merge
    assert narrowed >= 20, narrowed  # and US only narrows a Show all search


def _digest(title: str, description: str | None) -> str | None:
    return None if description is None else "sha256:" + hashlib.sha256(f"{title}\n{description}".encode()).hexdigest()


def _write_company(home: Path, company: str, ats: str, slug: str, jobs: list[tuple[str, str, float, bool, str | None]]) -> str:
    """One synthetic board: ``(title, location, posted days ago, removed, description)`` per posting, ids in the order given.

    ``description`` ``None``: the index holds none for the posting. The digest is of the title AS WRITTEN and the text.
    """

    def at(days: float) -> str:
        return (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")

    postings = {}
    for number, (title, location, days, removed, description) in enumerate(jobs):
        posting_id = f"{slug}-{number:02d}"
        postings[posting_id] = IndexedPosting(
            posting_id=posting_id, title=title, location=location, url=f"https://jobs.example.test/{slug}/{number}", updated_at=None,
            content_sha256=_digest(title, description), first_seen=at(days), last_seen=at(0), changed_at=None,
            removed_at=at(0.5) if removed else None, published_at=at(days), countries=None,
        )
    entry = CompanyIndexEntry(company=company, ats=ats, slug=slug, checked_at=at(0), etag=None, body_sha256=None, postings=postings)
    CompanyIndex.for_home(home).write(entry)
    return entry.key


COUNTRIES = ("Estonia", "Lithuania", "Latvia", "Bulgaria", "Romania", "Ukraine", "Poland")
ROLE = "Run the model platform."  # the description the copies of the one job share


@pytest.fixture
def copies_homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point Wild's case, made up: one job posted once per country, beside what must NOT merge into it."""

    def fill(home: Path) -> None:
        _write_company(home, "Point Example", "ashby", "point-example", [
            # 0-6: ONE job, posted once per country (Estonia the newest, Poland the earliest).
            *[("Senior MLOps Engineer", f"Remote {country}", 2 + number, False, ROLE) for number, country in enumerate(COUNTRIES)],
            ("Senior MLOps Engineer", "Remote Portugal", 3, False, "Run the DATA platform."),  # 7: another description (another team)
            ("Senior MLOps Engineer", "Remote Greece", 3.5, False, None),  # 8: no stored description: never merged
            ("Senior MLOps Engineer", "Remote Cyprus", 3.6, False, None),  # 9: nor with another one that has none
            ("Senior MLOps Engineer", "Remote Spain", 1, True, ROLE),  # 10: a removed copy never joins the live ones
            ("Senior MLOps Engineer II", "Remote Estonia", 3, False, ROLE),  # 11: another title
            ("MLOps Engineer", "Berlin, Germany", 4, False, ROLE),  # 12, 13: two cities of one country: one row, both listed
            ("MLOps Engineer", "Munich, Germany", 5, False, ROLE),
            ("MLOps Engineer", "Austin, TX", 40, False, ROLE),  # 14: the same job in the US, older than the default 30 days
            ("MLOps Engineer", "Remote", 7, False, ROLE),  # 15: nobody says where
            ("MLOps Engineer", "", 8, False, ROLE),  # 16
            ("MLOps Engineer", "Springfield Campus", 9, False, ROLE),  # 17
            ("MLOps Engineer", "EMEA", 9.5, False, ROLE),  # 18
        ])
        # Another company with the very same title and description: never merged with the first.
        _write_company(home, "Other Example", "greenhouse", "other-example", [
            ("Senior MLOps Engineer", "Remote Estonia", 2.5, False, ROLE), ("Senior MLOps Engineer", "Remote - US", 12, False, ROLE),
        ])

    indexed, scanned = tmp_path / "indexed" / "home", tmp_path / "scanned" / "home"
    fill(indexed)
    fill(scanned)
    assert search_index.rebuild_from_index(indexed).available
    monkeypatch.setattr(free_search, "default_config", lambda _home, _target: _config())
    yield indexed, scanned
    search_index.close(indexed)
    search_index.close(scanned)


def _json(home: Path, *args: object, **more: object) -> dict:
    return free_search.to_json(free_search.search(home, SearchRequest.typed(*args, **more), target=home, now=NOW))  # type: ignore[arg-type]


def _point(n: int) -> str:
    return f"https://jobs.example.test/point-example/{n}"


def test_the_same_job_posted_once_per_country_is_one_row(copies_homes) -> None:
    for home in copies_homes:
        # Every posting its own row: the 0.1.11.7 list (US only off, Show all, so no place is left out).
        before = _json(home, "senior mlops engineer", show_all=True, us_only=False, collapse=False, count=True)
        mine = [row for row in before["postings"]["rows"] if row["company_key"] == "ashby:point-example" and row["title"] == "Senior MLOps Engineer"]
        assert len(mine) == 10 and before["counts"]["total"] == 13, "seven countries, three more postings of the title; one more title; the other company's two"

        after = _json(home, "senior mlops engineer", show_all=True, us_only=False, count=True)
        rows = after["postings"]["rows"]
        assert after["query"]["collapse"] is True and after["counts"]["total"] == len(rows) == 6
        ours = {row["job_url"]: row for row in rows if row["company_key"] == "ashby:point-example"}
        # The seven countries are ONE row: one canonical job (no US posting, so the EARLIEST posted: Poland), which
        # lists every location, the canonical one first.
        one = ours[_point(6)]
        assert one["copies"] == 7 and one["location"] == "Remote Poland" and one["title"] == "Senior MLOps Engineer"
        assert one["locations"] == [f"Remote {country}" for country in reversed(COUNTRIES)]
        assert one["locations_text"] == "Remote: Poland, Ukraine, Romania +4"
        assert [member["job_url"] for member in one["members"]] == [_point(n) for n in (6, 5, 4, 3, 2, 1, 0)]
        assert one["members"][0]["job_identity"] == one["job_identity"] and all(member["location"] for member in one["members"])
        # Conservative: another description is another row; a posting with no stored description is never merged,
        # not even with another one that has none; another title is another row.
        assert {url: row["copies"] for url, row in ours.items()} == {_point(6): 7, _point(7): 1, _point(8): 1, _point(9): 1, _point(11): 1}
        # Another company with the same title and description is its own row, and ITS canonical job is its US posting.
        (other,) = [row for row in rows if row["company_key"] == "greenhouse:other-example"]
        assert other["copies"] == 2 and other["location"] == "Remote - US" and other["locations_text"] == "Remote: US, Estonia"
        assert other["job_url"] == "https://jobs.example.test/other-example/1", "the US posting, though the Estonia one is newer"
        # Rows stand where their canonical jobs stand, newest first.
        posted = [row["posted"] for row in rows]
        assert posted == sorted(posted, reverse=True)
        text = free_search.render_page(after)
        assert "Senior MLOps Engineer  (Remote: Poland, Ukraine, Romania +4) [7 copies]" in text

        # A removed copy is never merged with the live ones: asked for, it is a row of its own.
        with_removed = _json(home, "senior mlops engineer", show_all=True, us_only=False, include_removed=True, count=True)
        gone = [row for row in with_removed["postings"]["rows"] if row["removed"]]
        assert len(gone) == 1 and gone[0]["copies"] == 1 and gone[0]["location"] == "Remote Spain" and with_removed["counts"]["total"] == 7

        # Two cities of ONE country merge too, and every location is listed.
        cities = _json(home, "mlops engineer", location="germany", show_all=True, us_only=False, count=True)
        (row,) = cities["postings"]["rows"]
        assert row["copies"] == 2 and row["locations"] == ["Munich, Germany", "Berlin, Germany"] and row["locations_text"] == "Munich, Germany; Berlin, Germany"

        # A job with a US posting is THAT posting, however late it was posted, and it lists the others.
        (job,) = [item for item in _json(home, "mlops engineer", show_all=True, us_only=False, limit=200)["postings"]["rows"] if item["title"] == "MLOps Engineer"]
        assert job["job_url"] == _point(14) and job["location"] == "Austin, TX" and job["copies"] == 7 and job["location_unclear"] is False
        assert job["locations"][0] == "Austin, TX" and set(job["locations"]) == {"Austin, TX", "Berlin, Germany", "Munich, Germany", "Remote", "Springfield Campus", "EMEA"}


def test_a_page_is_a_page_of_jobs_and_the_counts_are_jobs(copies_homes) -> None:
    indexed, scanned = copies_homes
    request = SearchRequest.typed("mlops engineer, senior mlops engineer", show_all=True, us_only=False, limit=1)
    pages = {}
    for home in (indexed, scanned):
        seen = []
        for offset in range(0, 9):
            found = free_search.search(home, replace(request, offset=offset), target=home, now=NOW)
            seen.append([(row.posting.url, len(row.copies)) for row in found.rows])
            assert found.more is (offset < 6) and len(found.rows) == (1 if offset < 7 else 0)
        pages[home] = seen
    assert pages[indexed] == pages[scanned]
    # Seven jobs (twenty postings), one per page of 1, each where its canonical posting stands: newest first.
    assert pages[indexed][:7] == [
        [(_point(7), 1)], [(_point(11), 1)], [(_point(8), 1)], [(_point(9), 1)], [(_point(6), 7)],
        [("https://jobs.example.test/other-example/1", 2)], [(_point(14), 7)],
    ]
    for home in (indexed, scanned):
        counted = free_search.search(home, replace(request, count=True), target=home, now=NOW)
        assert counted.total == counted.total_all == 7
        each = free_search.search(home, replace(request, count=True, collapse=False, limit=200), target=home, now=NOW)
        assert each.total == len(each.rows) == 20


def test_us_only_is_by_the_postings_location_and_one_country_rule_decides(copies_homes, monkeypatch: pytest.MonkeyPatch) -> None:
    def places(home: Path, **more: object) -> dict[str, bool]:
        found = _json(home, "mlops engineer, senior mlops engineer", collapse=False, limit=200, **more)
        return {row["location"]: row["location_unclear"] for row in found["postings"]["rows"]}

    us = {"Austin, TX": False, "Remote - US": False}
    # Nobody says where: KEPT, and the row says so ("unclear location"). Never hidden.
    unclear = {"Remote": True, "": True, "Springfield Campus": True}
    abroad = {f"Remote {country}" for country in (*COUNTRIES, "Portugal", "Greece", "Cyprus")} | {"Berlin, Germany", "Munich, Germany", "EMEA"}
    for home in copies_homes:
        # A US setup: on by default. With Show all it STILL applies; only turning it off shows the other countries.
        assert places(home, show_all=True) == {**us, **unclear}
        assert places(home, show_all=True, us_only=True) == {**us, **unclear}
        wide = places(home, show_all=True, us_only=False)
        assert set(wide) == set(us) | set(unclear) | abroad and {place for place, flag in wide.items() if flag} == set(unclear)
        on = _json(home, "mlops engineer", show_all=True)
        assert on["us_only"] == {"on": True, "default": True, "rule": free_search.US_ONLY_RULE} and on["query"]["us_only"] is True
        assert on["filters"] is None and on["scope_text"] == "US only, any date"
        assert _json(home, "mlops engineer", show_all=True, us_only=False)["scope_text"] == "any place, any date"
        # Not by its board: the two companies both have US and non-US postings, and each keeps only its US ones.
        kept = _json(home, "senior mlops engineer", show_all=True, collapse=False)["postings"]["rows"]
        assert {(row["company_key"], row["location"]) for row in kept} == {("greenhouse:other-example", "Remote - US")}
        # The terminal says the label where US only kept the row, and not where the switch is off.
        assert "(Remote) [unclear location]" in free_search.render_page(_json(home, "mlops engineer", show_all=True, collapse=False))
        assert "unclear location" not in free_search.render_page(_json(home, "mlops engineer", show_all=True, collapse=False, us_only=False))
        # With the copies as one row: the job's US posting is the row, so the row is not "unclear".
        (job,) = [row for row in _json(home, "mlops engineer", show_all=True, limit=200)["postings"]["rows"] if row["title"] == "MLOps Engineer"]
        assert job["location"] == "Austin, TX" and job["copies"] == 4 and job["location_unclear"] is False

    indexed, scanned = copies_homes
    # The default filters (remote, US, 30 days) already hold the US: US only on does not apply it twice, and says "US" once.
    for us_only in (None, True, False):
        found = _json(indexed, "mlops engineer", us_only=us_only, count=True)
        assert found["filters"]["countries"] == ["US"] and found["filters"]["text"] == "remote, US, last 30 days"
        assert _json(scanned, "mlops engineer", us_only=us_only, count=True)["counts"] == found["counts"]
    # "Show all N" counts under the SAME US rule: the number is what switching Show all on then lists.
    narrow = _json(indexed, "mlops engineer", collapse=False, count=True)
    assert narrow["counts"]["hidden"] >= 1, "at least the posting older than 30 days"
    assert narrow["footer"][-1] == f"Show all {narrow['counts']['total_all']} (US only, any date): --all"
    assert narrow["counts"]["total_all"] == _json(indexed, "mlops engineer", show_all=True, collapse=False, count=True)["counts"]["total"]
    wide = _json(indexed, "mlops engineer", us_only=False, collapse=False, count=True)
    assert wide["footer"][-1] == f"Show all {wide['counts']['total_all']} (any place, any date): --all"
    assert wide["counts"]["total_all"] == _json(indexed, "mlops engineer", show_all=True, us_only=False, collapse=False, count=True)["counts"]["total"] > narrow["counts"]["total_all"]

    # A setup whose countries do not hold the US: OFF by default, and its own countries decide ...
    monkeypatch.setattr(free_search, "default_config", lambda _home, _target: _config(countries=("DE",), work_mode=_config().work_mode.__class__("any")))
    for home in copies_homes:
        german = _json(home, "mlops engineer", collapse=False)
        assert german["us_only"]["on"] is False and german["us_only"]["default"] is False and german["filters"]["countries"] == ["DE"]
        assert {row["location"] for row in german["postings"]["rows"]} == {"Berlin, Germany", "Munich, Germany", "", "Springfield Campus"}
        # ... until US only is asked for: then the US rule ALONE decides (one rule: never "DE and US", never "DE or US").
        asked = _json(home, "mlops engineer", collapse=False, us_only=True)
        assert asked["filters"]["countries"] == ["US"] and {row["location"] for row in asked["postings"]["rows"]} == {"Remote", "Remote - US", "", "Springfield Campus"}
        assert set(places(home, show_all=True)) == set(us) | set(unclear) | abroad  # Show all, no US default: any country

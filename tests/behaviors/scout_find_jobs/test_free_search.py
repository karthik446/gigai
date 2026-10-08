"""FS1 (0.1.11.7): the free search (``find_jobs/free_search.py``) over synthetic boards only.

Pins:

- EXACTNESS: for 16 typed titles x {all, defaults, defaults + a company word + a location word} the index path, the
  scan path and a brute-force read of the company files give the same rows in the same order and the same total
  (the brute force is ``test_search_index.scan``: the product's own rule and filters, written apart from the module);
- the four titles of the design, the strict rule (every typed word, seniority included) and several titles at once;
- paging: every page of 7 put end to end is the whole list, and ``more`` says whether a row follows;
- an index that cannot answer (missing, damaged, stale, not built, another schema) means the scan, with the same rows;
- a search WRITES NOTHING: the home is byte-identical, with the index and without it, and a search never builds it.

The labels, the CLI and the route are in ``test_free_search_labels_cli.py`` and ``tests/api_e2e/test_free_search_route.py``.
Every posting, company and place is made up. Nothing here touches the network or a real home.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest

from gigai.scout.find_jobs import free_search, search_index
from gigai.scout.find_jobs.ats_board_clients import MATCH_ANY_TITLE_ROLE
from gigai.scout.find_jobs.contracts import FindJobsConfig
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

    indexed = make_home(tmp_path / "indexed")
    scanned = make_home(tmp_path / "scanned", build=False)
    monkeypatch.setattr(free_search, "default_config", lambda _home, _target: _config())
    yield indexed, scanned
    search_index.close(indexed)
    search_index.close(scanned)


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


def _request(titles: str, shape: str, **more: object) -> SearchRequest:
    show_all, company, location = SHAPES[shape]
    return SearchRequest.typed(titles, company=company, location=location, show_all=show_all, **more)  # type: ignore[arg-type]


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
        found = free_search.search(home, SearchRequest.typed("senior systems engineer", show_all=True, limit=200), target=home, now=NOW)
        titles = {row.posting.title for row in found.rows}
        # "Sr" is Senior; a plain "Systems Engineer" is not listed (the profile rule would list it).
        assert titles == {"Sr Systems Engineer", "Senior Systems Engineer, Controls"}, titles
        staff = {row.posting.title for row in free_search.search(home, SearchRequest.typed("staff engineer", show_all=True, limit=200), target=home, now=NOW).rows}
        assert "Staff Software Engineer" in staff and "Staff Engineer" in staff
        assert "Staff Training Engineer" not in staff and "Staff Sales Engineer" not in staff
        assert {"C++ Engineer", "Senior C++ Software Engineer"} == {
            row.posting.title for row in free_search.search(home, SearchRequest.typed("c++ engineer", show_all=True, limit=200), target=home, now=NOW).rows
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
    words_only = SearchRequest.typed(None, company="go fast", location="remote", show_all=True, count=True)
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
        live = free_search.search(home, SearchRequest.typed("engineer", show_all=True, limit=200, count=True), target=home, now=NOW)
        both = free_search.search(home, SearchRequest.typed("engineer", show_all=True, include_removed=True, limit=200, count=True), target=home, now=NOW)
        assert not any(row.posting.removed for row in live.rows)
        assert both.total is not None and live.total is not None and both.total > live.total
    with_removed = SearchRequest.typed("engineer", show_all=True, include_removed=True)
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
        lambda: SearchRequest.typed(None),
        lambda: SearchRequest.typed(" , ,"),
        lambda: SearchRequest.typed("of the"),  # filler words only: the rule has nothing to ask for
        lambda: SearchRequest.typed("engineer", company="--"),
        lambda: SearchRequest.typed("engineer", limit=0),
        lambda: SearchRequest.typed("engineer", limit=201),
        lambda: SearchRequest.typed("engineer", offset=-1),
    ):
        with pytest.raises(FreeSearchError) as refused:
            build()
        assert refused.value.code == "invalid_value"
    assert SearchRequest.typed("engineer", company=("Go  Fast", "ai"), location="remote remote").company_words == ("Go", "Fast", "ai")
    assert SearchRequest.typed("engineer", location="remote remote").location_words == ("remote",)


def test_the_default_filters_need_a_readable_config_and_show_all_does_not(tmp_path: Path) -> None:
    home = make_home(tmp_path)
    try:
        with pytest.raises(FreeSearchError) as refused:
            free_search.search(home, SearchRequest.typed("staff engineer"), target=home, now=NOW)
        assert refused.value.code == "config_unavailable" and "--all" in str(refused.value)
        with pytest.raises(FreeSearchError) as refused:
            free_search.search(home, SearchRequest.typed("staff engineer"), now=NOW)
        assert refused.value.code == "config_unavailable"
        found = free_search.search(home, SearchRequest.typed("staff engineer", show_all=True), now=NOW)
        assert found.rows and found.filters is None and found.labelled is False
        assert all(row.labels.profiles == () and row.labels.assessment is None and row.labels.application is None for row in found.rows)
    finally:
        search_index.close(home)


def test_the_json_has_stable_keys_and_the_text_says_what_was_hidden(homes) -> None:
    indexed, _scanned = homes
    request = _request("staff engineer", "defaults", count=True)
    response = free_search.to_json(free_search.search(indexed, replace(request, limit=2), target=indexed, now=NOW))
    assert sorted(response) == [
        "checked_at", "counts", "filters", "footer", "index", "labels_read", "order", "postings", "profiles", "query", "ranked",
        "schema_version", "source",
    ]
    assert response["schema_version"] == "scout-free-search:1" and response["ranked"] is False and response["order"] == "newest_posted"
    assert sorted(response["counts"]) == ["hidden", "more", "shown", "total", "total_all"]  # type: ignore[arg-type]
    assert sorted(response["query"]) == ["all", "company", "count", "include_removed", "limit", "location", "offset", "titles"]  # type: ignore[arg-type]
    assert response["filters"]["text"] == "remote, US, last 30 days"  # type: ignore[index]
    row = response["postings"]["rows"][0]  # type: ignore[index]
    assert sorted(row) == [
        "application", "assessment", "company", "company_key", "company_name", "company_slug", "first_seen", "job_identity", "job_url", "location", "posted", "profiles",
        "published_at", "removed", "title",
    ]
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
        nothing = free_search.to_json(free_search.search(home, SearchRequest.typed("underwater basket weaver", count=True), target=home, now=NOW))
        assert nothing["postings"]["rows"] == [] and nothing["counts"]["total"] == 0  # type: ignore[index]
        text = free_search.render_page(nothing)
        assert text.splitlines()[-1] == 'No stored posting matches "underwater basket weaver" (remote, US, last 30 days).'
        assert "Not ranked" not in free_search.render_total(nothing)
    # A title the default filters hide entirely says where the rows are.
    assert "Accountant" in TITLES and BOARDS
    hidden = free_search.to_json(
        free_search.search(indexed, SearchRequest.typed("accountant", location="zürich", count=True), target=indexed, now=NOW)
    )
    if hidden["counts"]["total"] == 0 and hidden["counts"]["total_all"]:  # type: ignore[index]
        assert f"Show all {hidden['counts']['total_all']} (any place, any date): --all" in free_search.render_total(hidden)  # type: ignore[index]

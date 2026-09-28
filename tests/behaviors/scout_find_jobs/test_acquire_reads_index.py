"""N11-C: a find-jobs acquire pass reads the company index and asks no board.

``acquire_node(..., boards_from="index")`` is what the production binding
passes. The watchlist's rows come from the index ``gigai scout sources
update`` wrote; the HTTP client handed to acquire logs every request it
sees and the board client fails the test if it is called at all, so "zero
board requests during a search" is asserted, not assumed. Sealing, the
NEW/UNCHANGED outcome and the workpad's cleanliness are the real ones (a
managed workpad, the real journal import).
"""

from __future__ import annotations

from dataclasses import replace
import inspect
from pathlib import Path

import httpx
import pytest

from gigai.scout.find_jobs import bindings
from gigai.scout.find_jobs.ats_board_clients import ATSBoardClients
from gigai.scout.find_jobs.company_index import CompanyIndex
from gigai.scout.find_jobs.contracts import (
    ATSProvider,
    FindJobsContractError,
    PostingRow,
    RowOutcome,
    SourceKind,
    SourceToggles,
)
from gigai.scout.find_jobs.market_acquisition import (
    BOARDS_FROM_INDEX,
    SOURCES_UPDATE_REQUIRED_CODE,
    AcquireAllSourcesFailedError,
    acquire_node,
)
from gigai.scout.find_jobs.progress import read_progress
from gigai.scout.find_jobs.sources_update import board_cache_for_home, update_sources

from tests.behaviors.scout_find_jobs.test_acquire_scale import (
    _Exa,
    _Watchlist,
    _board,
    _config,
    _input,
    _limits,
    _managed,
    _real_context,
)
from tests.support.workpad_assertions import assert_managed_workpad_clean

JOBS = [
    {"id": 11, "title": "Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/11", "location": {"name": "Denver, CO"}, "updated_at": "2026-09-20T00:00:00Z"},
    {"id": 12, "title": "Marketing Manager", "absolute_url": "https://boards.greenhouse.io/acme/jobs/12", "location": {"name": "Remote"}, "updated_at": "2026-09-20T00:00:00Z"},
    {"id": 13, "title": "Senior Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/13", "location": {"name": "Denver, CO"}, "updated_at": "2026-09-21T00:00:00Z"},
]


def _board_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/boards/acme/jobs":
        return httpx.Response(200, json={"jobs": JOBS}, headers={"etag": 'W/"acme"'})
    for job in JOBS:
        if path == f"/v1/boards/acme/jobs/{job['id']}":
            return httpx.Response(200, json={**job, "content": f"&lt;p&gt;Build {job['id']}.&lt;/p&gt;"})
    return httpx.Response(404, json={})


class _SearchClient:
    """The client a search is handed: it must never be used for a board."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        return httpx.Response(500, json={"error": "a search must not fetch"})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


class _NoBoardClient:
    def fetch_board(self, *args, **kwargs):
        raise AssertionError("a search called the board client")

    def list_board(self, *args, **kwargs):
        raise AssertionError("a search called the board client")


class _ExaRows:
    def search(self, client, config, *, home_root=None):
        url = "https://jobs.lever.co/example/9"
        return (
            PostingRow(
                url=url, normalized_url=url, provider=ATSProvider.LEVER, board_token=None, company="Example",
                title="Software Engineer", location="Denver, CO", published_at="2026-09-22T00:00:00Z",
                content_sha256="sha256:" + "e" * 64, source_kind=SourceKind.EXA, query_key="software engineer",
            ),
        )


def _boards() -> list:
    return [_board(ATSProvider.GREENHOUSE, "acme")]


def _update_sources(home: Path) -> None:
    with httpx.Client(transport=httpx.MockTransport(_board_handler)) as client:
        result = update_sources(
            _boards(), cache=board_cache_for_home(home), index=CompanyIndex.for_home(home), client=client,
            config=_config(), limits=_limits(concurrency=1),
        )
    assert result.status == "succeeded", result.to_json()


def _search(substrate, search: _SearchClient, *, n: int, exa=None, boards=None):
    home, target, workpad, gig_id = substrate
    with search.client() as client:
        return acquire_node(
            _real_context(home, target, workpad, gig_id, key=f"index-search-{n}", run_id=f"run_{n:02d}"),
            _input(),
            http_client=client,
            exa=exa if exa is not None else _Exa(),
            ats=_NoBoardClient(),
            watchlist=_Watchlist(_boards() if boards is None else boards),
            home_root=home,
            target=target,
            limits=_limits(concurrency=1),
            boards_from=BOARDS_FROM_INDEX,
        )


def test_a_search_reads_the_index_and_makes_zero_board_requests(tmp_path: Path) -> None:
    substrate = _managed(tmp_path)
    home, _target, workpad, _gig_id = substrate
    _update_sources(home)
    search = _SearchClient()

    first = _search(substrate, search, n=1)

    assert search.requests == [], "the search made a request"
    assert [row.posting.title for row in first.rows] == ["Software Engineer", "Senior Software Engineer"]
    assert [row.posting.text for row in first.rows] == ["Build 11.", "Build 13."]
    assert {row.outcome for row in first.rows} == {RowOutcome.NEW}
    assert first.failures == ()
    boards = read_progress(workpad / "runs" / "run_01").boards
    assert boards["status"] == "done" and boards["source"] == "index"
    assert boards["requests"] == 0 and boards["fetched"] == 0
    assert boards["total"] == 1 and boards["cached"] == 1 and boards["skipped"] == 0
    assert boards["listed"] == 3 and boards["prefiltered_out"] == 1 and boards["matched"] == 2
    assert boards["touched_since_last_search"] == 2
    assert boards["index"]["status"] == "ready" and boards["index"]["needs_update"] is False
    assert_managed_workpad_clean(workpad)

    # The reuse rule is the sealed batches' own: the same postings again are UNCHANGED.
    second = _search(substrate, search, n=2)

    assert search.requests == []
    assert [row.posting.content_sha256 for row in second.rows] == [row.posting.content_sha256 for row in first.rows]
    assert {row.outcome for row in second.rows} == {RowOutcome.UNCHANGED}
    assert read_progress(workpad / "runs" / "run_02").boards["touched_since_last_search"] == 0
    assert_managed_workpad_clean(workpad)
    # The index and the cache are under the home, never in the workpad.
    assert not list(workpad.rglob("greenhouse:acme.json"))


def test_a_search_with_nothing_stored_fails_with_run_update_sources(tmp_path: Path) -> None:
    substrate = _managed(tmp_path)
    _home, _target, workpad, _gig_id = substrate
    search = _SearchClient()

    with pytest.raises(AcquireAllSourcesFailedError) as caught:
        _search(substrate, search, n=1)

    assert search.requests == [], "an empty index must not trigger a fetch"
    assert caught.value.code == SOURCES_UPDATE_REQUIRED_CODE
    assert str(caught.value) == "No company postings are stored on this machine yet. Run Update sources, then search again."
    boards = read_progress(workpad / "runs" / "run_01").boards
    assert boards["index"]["status"] == "empty" and boards["index"]["needs_update"] is True
    assert boards["index"]["message"] == str(caught.value)
    assert boards["requests"] == 0
    assert_managed_workpad_clean(workpad)


def test_a_search_with_nothing_stored_still_returns_what_exa_found(tmp_path: Path) -> None:
    home, target, workpad, gig_id = _managed(tmp_path)
    search = _SearchClient()
    with search.client() as client:
        out = acquire_node(
            _real_context(home, target, workpad, gig_id, key="index-search-exa", run_id="run_01"),
            _input(replace(_config(), sources=SourceToggles(exa=True, ats=True, hiringcafe=False))),
            http_client=client, exa=_ExaRows(), ats=_NoBoardClient(), watchlist=_Watchlist(_boards()),
            home_root=home, target=target, limits=_limits(concurrency=1), boards_from=BOARDS_FROM_INDEX,
        )

    assert search.requests == []
    assert [row.posting.source_kind for row in out.rows] == [SourceKind.EXA]
    assert [(failure.source_kind, failure.code) for failure in out.failures] == [(SourceKind.ATS, SOURCES_UPDATE_REQUIRED_CODE)]
    assert read_progress(workpad / "runs" / "run_01").boards["index"]["needs_update"] is True


def test_a_company_added_after_the_update_is_not_fetched_by_the_search(tmp_path: Path) -> None:
    substrate = _managed(tmp_path)
    home, _target, workpad, _gig_id = substrate
    _update_sources(home)
    search = _SearchClient()

    out = _search(substrate, search, n=1, boards=[*_boards(), _board(ATSProvider.LEVER, "added-later")])

    assert search.requests == []
    assert len(out.rows) == 2 and out.failures == ()
    boards = read_progress(workpad / "runs" / "run_01").boards
    assert boards["total"] == 2 and boards["cached"] == 1 and boards["skipped"] == 1
    assert boards["index"]["not_indexed"] == 1


def test_the_production_binding_reads_the_index_and_so_does_a_caller_that_names_no_source() -> None:
    # The fetch is only for a caller that asks for it by name; every way a
    # search starts is pinned in test_find_jobs_entrypoints_read_index.py.
    assert inspect.signature(acquire_node).parameters["boards_from"].default == BOARDS_FROM_INDEX
    source = inspect.getsource(bindings)
    assert "boards_from=BOARDS_FROM_INDEX" in source
    assert bindings.BOARDS_FROM_INDEX == "index"


def test_an_unknown_board_source_is_refused(tmp_path: Path) -> None:
    with pytest.raises(FindJobsContractError):
        acquire_node(
            None, _input(), http_client=None, exa=_Exa(), ats=_NoBoardClient(), watchlist=_Watchlist([]),  # type: ignore[arg-type]
            boards_from="network",
        )


# --- option E: only the companies Exa found in this run, not indexed yet, at most 20 ---


class _ExaFinds:
    """Exa rows on Lever boards (one posting each), in a fixed order."""

    def __init__(self, tokens: list[str], *, also_acme: bool = True) -> None:
        self.tokens = tokens
        self.also_acme = also_acme

    def search(self, client, config, *, home_root=None):
        rows = []
        if self.also_acme:
            url = "https://boards.greenhouse.io/acme/jobs/11"
            rows.append(
                PostingRow(
                    url=url, normalized_url=url, provider=ATSProvider.GREENHOUSE, board_token="acme", company="acme",
                    title="Software Engineer", location="Denver, CO", published_at="2026-09-20T00:00:00Z",
                    content_sha256=None, source_kind=SourceKind.EXA, query_key="software engineer",
                )
            )
        for token in self.tokens:
            url = f"https://jobs.lever.co/{token}/job-1"
            rows.append(
                PostingRow(
                    url=url, normalized_url=url, provider=ATSProvider.LEVER, board_token=token, company=token,
                    title="Software Engineer", location="Denver, CO", published_at="2026-09-22T00:00:00Z",
                    content_sha256=None, source_kind=SourceKind.EXA, query_key="software engineer",
                )
            )
        return tuple(rows)


class _NewCompanies:
    """The transport of a search: it serves the Lever boards Exa found; ``dead`` ones 404."""

    def __init__(self, *, dead: frozenset[str] = frozenset()) -> None:
        self.requests: list[str] = []
        self.dead = dead

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(f"{request.url.host}{request.url.path}")
        token = request.url.path.rsplit("/", 1)[-1]
        if request.url.host != "api.lever.co" or token in self.dead:
            return httpx.Response(404, json={})
        return httpx.Response(
            200,
            json=[
                {"id": "job-1", "text": "Software Engineer", "hostedUrl": f"https://jobs.lever.co/{token}/job-1", "categories": {"location": "Denver, CO"}, "country": "US", "createdAt": 1790000000000, "descriptionPlain": f"Build things at {token}."},
                {"id": "job-2", "text": "Recruiter", "hostedUrl": f"https://jobs.lever.co/{token}/job-2", "categories": {"location": "Denver, CO"}, "country": "US", "createdAt": 1790000000000, "descriptionPlain": "Hire people."},
            ],
        )


class _GrowingWatchlist:
    """Like the journaled watchlist: a company Exa finds is on it from then on."""

    def __init__(self, boards) -> None:
        self.boards = {board.watchlist_id: board for board in boards}

    def add_to_watchlist(self, entry):
        return self.boards.setdefault(entry.watchlist_id, entry)

    def active_entries(self):
        return tuple(self.boards.values())


def _search_with_exa(substrate, transport: _NewCompanies, exa, *, n: int, watchlist=None):
    home, target, workpad, gig_id = substrate
    with httpx.Client(transport=httpx.MockTransport(transport.handler)) as client:
        return acquire_node(
            _real_context(home, target, workpad, gig_id, key=f"exa-new-{n}", run_id=f"run_{n:02d}"),
            _input(replace(_config(), sources=SourceToggles(exa=True, ats=True, hiringcafe=False))),
            http_client=client, exa=exa, ats=ATSBoardClients(),
            watchlist=watchlist if watchlist is not None else _GrowingWatchlist(_boards()),
            home_root=home, target=target, limits=_limits(concurrency=2), boards_from=BOARDS_FROM_INDEX,
        )


def test_only_companies_exa_found_in_this_run_are_fetched_at_most_20_and_indexed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    substrate = _managed(tmp_path)
    home, _target, workpad, _gig_id = substrate
    _update_sources(home)
    tokens = [f"x{n:02d}" for n in range(25)]
    transport = _NewCompanies()
    watchlist = _GrowingWatchlist(_boards())

    first = _search_with_exa(substrate, transport, _ExaFinds(tokens), n=1, watchlist=watchlist)

    # acme is in the index: not one request for it. The first 20 companies
    # Exa found are fetched, one request each; the other 5 wait.
    assert sorted(transport.requests) == [f"api.lever.co/v0/postings/x{n:02d}" for n in range(20)]
    exa_new = read_progress(workpad / "runs" / "run_01").boards["exa_new"]
    assert exa_new == {"cap": 20, "found": 25, "fetched": 20, "failed": 0, "waiting": 5, "requests": 20}
    boards = read_progress(workpad / "runs" / "run_01").boards
    assert boards["source"] == "index" and boards["requests"] == 20 and boards["fetched"] == 20
    # acme was read from the index; the 25 new companies were not in it.
    assert boards["total"] == 26 and boards["cached"] == 1 and boards["skipped"] == 25
    # The cap is in words: 20 is companies, not requests.
    log = capsys.readouterr().err
    assert "20 board requests, " in log
    assert "fetched 20 new companies found by Exa (cap 20 companies per search), 5 more wait for the next Update sources" in log

    index = CompanyIndex.for_home(home)
    assert sorted(slug for ats, slug in index.keys() if ats == "lever") == tokens[:20]
    # Every listed posting of a fetched company is indexed, not only the matching title.
    assert sorted(index.read("lever", "x00").postings) == ["job-1", "job-2"]

    by_url = {row.posting.normalized_url: row.posting for row in first.rows}
    assert by_url["https://boards.greenhouse.io/acme/jobs/11"].text == "Build 11."
    for token in tokens[:20]:
        assert by_url[f"https://jobs.lever.co/{token}/job-1"].text == f"Build things at {token}."
    # The five that wait are listed from Exa alone, without a description yet.
    for token in tokens[20:]:
        assert by_url[f"https://jobs.lever.co/{token}/job-1"].text is None
    assert first.failures == ()
    assert_managed_workpad_clean(workpad)

    # The next search finds the 20 in the index and fetches only the 5 that waited.
    transport.requests.clear()
    second = _search_with_exa(substrate, transport, _ExaFinds(tokens), n=2, watchlist=watchlist)

    assert sorted(transport.requests) == [f"api.lever.co/v0/postings/x{n:02d}" for n in range(20, 25)]
    again = read_progress(workpad / "runs" / "run_02").boards
    assert again["exa_new"] == {"cap": 20, "found": 5, "fetched": 5, "failed": 0, "waiting": 0, "requests": 5}
    assert again["total"] == 26 and again["cached"] == 21 and again["skipped"] == 5
    texts = {row.posting.normalized_url: row.posting.text for row in second.rows}
    assert all(texts[f"https://jobs.lever.co/{token}/job-1"] == f"Build things at {token}." for token in tokens)
    assert_managed_workpad_clean(workpad)


def test_a_new_company_whose_board_does_not_answer_is_one_failure_row(tmp_path: Path) -> None:
    substrate = _managed(tmp_path)
    home, _target, workpad, _gig_id = substrate
    _update_sources(home)
    transport = _NewCompanies(dead=frozenset({"gone"}))

    out = _search_with_exa(substrate, transport, _ExaFinds(["alive", "gone"]), n=1)

    assert sorted(transport.requests) == ["api.lever.co/v0/postings/alive", "api.lever.co/v0/postings/gone"]
    assert read_progress(workpad / "runs" / "run_01").boards["exa_new"] == {
        "cap": 20, "found": 2, "fetched": 1, "failed": 1, "waiting": 0, "requests": 2,
    }
    assert [(failure.source_kind, failure.query_key) for failure in out.failures] == [(SourceKind.ATS, "gone")]
    assert CompanyIndex.for_home(home).read("lever", "gone") is None
    assert CompanyIndex.for_home(home).read("lever", "alive") is not None


def test_without_exa_finds_a_search_makes_no_request_at_all(tmp_path: Path) -> None:
    substrate = _managed(tmp_path)
    home, _target, workpad, _gig_id = substrate
    _update_sources(home)
    transport = _NewCompanies()

    out = _search_with_exa(substrate, transport, _ExaFinds([], also_acme=True), n=1)

    assert transport.requests == []
    assert read_progress(workpad / "runs" / "run_01").boards["exa_new"] == {
        "cap": 20, "found": 0, "fetched": 0, "failed": 0, "waiting": 0, "requests": 0,
    }
    assert [row.posting.text for row in out.rows] == ["Build 11.", "Build 13."]

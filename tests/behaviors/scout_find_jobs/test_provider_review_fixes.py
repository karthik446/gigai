"""0.1.11.8 review fixes, lane A (providers / URLs / feed parsing): B1, B2, S4, S5, S8, N3, N4.

0.1.11.12 (Workday P6): a pasted Workday job URL, or a stored one with no description, is ONE cxs detail GET addressed
by the URL's own path (never the list's POST loop), behind the sources update's site-level robots gate.

Synthetic fixtures only; every request goes to an ``httpx.MockTransport``.
"""

from __future__ import annotations

import json

import httpx
import pytest

from gigai.scout.find_jobs import ats_feeds, providers, robots_guard
from gigai.scout.find_jobs.assess_contracts import AssessJobInput
from gigai.scout.find_jobs.ats_board_clients import ATSBoardClients, BoardCache, fetch_feed_board
from gigai.scout.find_jobs.company_index import (
    STATUS_INDEXED,
    STATUS_UNREADABLE,
    STATUS_UPDATED,
    CompanyIndex,
    board_list_url,
    parse_board_body,
    refresh_company,
)
from gigai.scout.find_jobs.contracts import FindJobsConfig, FindJobsContractError, SourceToggles, parse_board_url
from gigai.scout.find_jobs.job_input import PostingTextUnavailable, fetch_missing_description, resolve_job


def _config() -> FindJobsConfig:
    return FindJobsConfig(
        roles=("software engineer",),
        merged_queries=("software engineer",),
        location=None,
        remote=True,
        published_after=None,
        sources=SourceToggles(exa=False, ats=True, hiringcafe=False),
    )


def _rippling_item(n: int) -> dict:
    uid = f"00000000-0000-4000-8000-{n:012d}"
    return {
        "id": uid,
        "name": f"Software Engineer {n}",
        "url": f"https://ats.rippling.com/acme-jobs/jobs/{uid}",
        "locations": [{"name": "Denver", "countryCode": "US"}],
    }


# --- B1: one posting, one detail request ------------------------------------------------------------------------


def test_rippling_description_lookup_is_one_detail_request() -> None:
    items = [_rippling_item(n) for n in range(300)]
    target = items[123]
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == f"/api/v1/board/acme-jobs/jobs/{target['id']}":
            return httpx.Response(
                200, json={"uuid": target["id"], "name": target["name"], "description": {"company": "<p>About.</p>", "role": "<p>Build it.</p>"}}
            )
        if request.url.path == "/api/v2/board/acme-jobs/jobs":
            return httpx.Response(200, json={"items": items, "totalItems": 300})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    found = fetch_missing_description(client, provider="rippling", token="acme-jobs", posting_id=target["id"], url=target["url"])
    assert found.text == "About.\nBuild it."
    assert found.title == target["name"]
    assert len(calls) == 1 and calls[0].endswith(target["id"])


def test_rippling_list_path_makes_no_detail_request() -> None:
    items = [_rippling_item(n) for n in range(40)]
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"items": items, "totalItems": 40}) if "/api/v2/" in request.url.path else httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    rows = ATSBoardClients().list_board(client, "rippling", "acme-jobs", _config())
    assert len(rows) == 40 and calls == ["/api/v2/board/acme-jobs/jobs"]


def test_rippling_detail_that_is_gone_reads_as_removed() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    with pytest.raises(PostingTextUnavailable) as caught:
        fetch_missing_description(client, provider="rippling", token="acme-jobs", posting_id="abc", url="https://ats.rippling.com/acme-jobs/jobs/abc")
    assert caught.value.reason == "posting_removed"


# --- B2 / N4: URLs ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://apply.workable.com/j/198558616F", None),
        ("https://apply.workable.com/j/198558616F/", None),
        ("https://apply.workable.com/api/v1/widget/accounts/acme", None),
        ("https://apply.workable.com/acme/j/198558616F/", ("workable", "acme")),
        ("https://apply.workable.com/acme/", ("workable", "acme")),
        ("https://ats.rippling.com/en-GB/acme/jobs", None),
        ("https://ats.rippling.com/acme/jobs", ("rippling", "acme")),
        ("https://ats.rippling.com/ab/jobs", ("rippling", "ab")),
    ],
)
def test_vendor_routes_and_locales_name_no_board(url: str, expected: tuple[str, str] | None) -> None:
    assert parse_board_url(url) == expected


def test_exa_result_on_a_workable_job_page_is_not_a_board() -> None:
    from gigai.scout.find_jobs.exa_client import _row_from_result

    assert _row_from_result({"url": "https://apply.workable.com/j/198558616F", "title": "Engineer"}, "q") is None


# --- N3: host match -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("jobs.lever.co", "lever"),
        ("lever.co", "lever"),
        ("job-boards.greenhouse.io", "greenhouse"),
        ("acme.breezy.hr", "breezy"),
        ("jobs.gem.com", "gem"),
        ("jobs.stratagem.com", None),
        ("clever.com", None),
        ("www.clever.com", None),
        ("notlever.co", None),
    ],
)
def test_source_from_host_matches_whole_labels(host: str, expected: str | None) -> None:
    assert providers.source_from_host(host) == expected


# --- S4: Breezy job-page description ---------------------------------------------------------------------------


def _breezy_page(ld: object | None) -> str:
    block = f'<script type="application/ld+json">{json.dumps(ld)}</script>' if ld is not None else ""
    return f"<html><head><title>Role</title>{block}</head><body><nav>menu</nav></body></html>"


def test_breezy_description_comes_from_the_job_page_json_ld(monkeypatch) -> None:
    ld = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Platform Engineer", "description": "<p>Build things.</p><ul><li>Python</li></ul>"}
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, text=_breezy_page(ld), headers={"content-type": "text/html; charset=utf-8"})

    _allow_public(monkeypatch)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    url = "https://acme.breezy.hr/p/abc123-platform-engineer"
    found = fetch_missing_description(client, provider="breezy", token="acme", posting_id="abc123", url=url)
    assert "Build things." in found.text and "Python" in found.text and "<p>" not in found.text
    assert found.title == "Platform Engineer" and found.fetch_kind == "ats_board"
    assert asked == [url]


def test_breezy_page_without_json_ld_description_is_no_text(monkeypatch) -> None:
    _allow_public(monkeypatch)
    for page in (_breezy_page(None), _breezy_page({"@type": "JobPosting", "title": "X"})):
        client = httpx.Client(transport=httpx.MockTransport(lambda request, page=page: httpx.Response(200, text=page)))
        with pytest.raises(PostingTextUnavailable) as caught:
            fetch_missing_description(client, provider="breezy", token="acme", posting_id="abc", url="https://acme.breezy.hr/p/abc-x")
        assert caught.value.reason == "no_text"


def test_jobposting_is_found_in_a_graph_or_list() -> None:
    page = _breezy_page({"@graph": [{"@type": "Organization"}, {"@type": ["JobPosting"], "title": "T", "description": "<b>D</b>"}]})
    assert ats_feeds.jobposting_from_page(page) == ("T", "<b>D</b>")


def _allow_public(monkeypatch) -> None:
    """The stored-URL guard resolves a name; the test transport answers, so let the synthetic host pass it."""

    from gigai.scout.find_jobs import job_input

    def fake_read(client, url):
        response = client.get(url)
        if response.status_code != 200:
            raise job_input._FetchFailure("http_error", "acme.breezy.hr", f"HTTP {response.status_code}", response.status_code)
        return response.content, response.charset_encoding

    monkeypatch.setattr(job_input, "_read_capped_stored", fake_read)


# --- S5: a board whose rows all fail to parse is unreadable -------------------------------------------------------------


def _workable_job(n: int, key: str = "url") -> dict:
    return {"title": f"Engineer {n}", "shortcode": f"S{n}", key: f"https://apply.workable.com/j/S{n}", "published_on": "2026-05-29"}


def test_body_that_lists_jobs_none_of_which_parse_is_bad_json() -> None:
    body = json.dumps({"jobs": [_workable_job(n, "job_url") for n in range(5)]}).encode()
    with pytest.raises(Exception) as caught:
        parse_board_body("workable", "acme", body)
    assert getattr(caught.value, "code", None) == "bad_json"
    assert parse_board_body("workable", "acme", json.dumps({"jobs": []}).encode()) == {}


def _index_board(tmp_path, jobs: list[dict]):
    cache, index = BoardCache(tmp_path / "boards"), CompanyIndex(tmp_path / "companies")
    url = board_list_url("workable", "acme")

    def put(payload_jobs: list[dict]):
        cache.store("workable", url, body=json.dumps({"jobs": payload_jobs}).encode(), etag=None, last_modified=None, marker=None)
        return refresh_company(index, cache, ats="workable", slug="acme")

    return put(jobs), put, index


def test_renamed_url_field_leaves_the_indexed_board_alone(tmp_path) -> None:
    first, put, index = _index_board(tmp_path, [_workable_job(n) for n in range(5)])
    assert first.status == STATUS_INDEXED and first.live == 5
    before = index.path("workable", "acme").read_bytes()
    change = put([_workable_job(n, "job_url") for n in range(5)])
    assert change.status == STATUS_UNREADABLE and change.code == "bad_json"
    assert index.path("workable", "acme").read_bytes() == before  # nothing written, the 5 postings stay live


def test_a_big_board_that_loses_ninety_percent_to_skipped_rows_is_unreadable(tmp_path) -> None:
    first, put, index = _index_board(tmp_path, [_workable_job(n) for n in range(30)])
    assert first.live == 30
    before = index.path("workable", "acme").read_bytes()
    drifted = [_workable_job(0)] + [_workable_job(n, "job_url") for n in range(1, 30)]
    assert put(drifted).status == STATUS_UNREADABLE
    assert index.path("workable", "acme").read_bytes() == before
    # A real shrink (rows gone, none skipped) is still an update.
    assert put([_workable_job(n) for n in range(3)]).status == STATUS_UPDATED


# --- S8: Rippling pages ---------------------------------------------------------------------------------------------


def _list_calls(calls: list[str]) -> list[str]:
    return [call for call in calls if '/api/v2/' in call]  # the details a title match asks are the update gate's, not paging


def _paged_handler(total: int, per_page: int, calls: list[str]):
    items = [_rippling_item(n) for n in range(total)]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        page = int(request.url.params.get("page", "0"))
        chunk = items[page * per_page : (page + 1) * per_page]
        return httpx.Response(200, json={"items": chunk, "page": page, "pageSize": per_page, "totalItems": total}, headers={"etag": f'"p{page}"'})

    return handler


def test_a_2085_posting_rippling_board_is_read_in_all_its_pages(tmp_path) -> None:
    calls: list[str] = []
    client = httpx.Client(transport=httpx.MockTransport(_paged_handler(2085, 500, calls)))
    cache = BoardCache(tmp_path / "boards")
    result = fetch_feed_board(client, "rippling", "acme-jobs", _config(), cache=cache)
    assert len(result.rows) == 2085
    assert [c.split("page=")[1].split("&")[0] for c in _list_calls(calls)] == ["0", "1", "2", "3", "4"]
    # the stored body is the whole board, so the index sees 2,085 postings
    entry = cache.lookup("rippling", providers.list_url("rippling", "acme-jobs"))
    assert len(parse_board_body("rippling", "acme-jobs", entry.body)) == 2085


def test_a_board_that_fits_one_page_makes_one_request() -> None:
    calls: list[str] = []
    client = httpx.Client(transport=httpx.MockTransport(_paged_handler(40, 1000, calls)))
    assert len(fetch_feed_board(client, "rippling", "acme-jobs", _config()).rows) == 40
    assert len(_list_calls(calls)) == 1


def test_paging_stops_at_five_pages() -> None:
    calls: list[str] = []
    client = httpx.Client(transport=httpx.MockTransport(_paged_handler(9000, 1000, calls)))
    assert len(fetch_feed_board(client, "rippling", "acme-jobs", _config()).rows) == 5000
    assert len(_list_calls(calls)) == ats_feeds.MAX_LIST_PAGES == 5


def test_a_failed_later_page_fails_the_board_and_drops_validators(tmp_path) -> None:
    calls: list[str] = []
    inner = _paged_handler(2085, 500, calls)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500) if request.url.params.get("page") == "2" else inner(request)

    cache = BoardCache(tmp_path / "boards")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(Exception):
        fetch_feed_board(client, "rippling", "acme-jobs", _config(), cache=cache)
    entry = cache.lookup("rippling", providers.list_url("rippling", "acme-jobs"))
    assert entry is not None and entry.etag is None


# --- Workday (0.1.11.12 P6): one posting, one cxs detail GET, behind the site gate ---------------------------------

WD_TOKEN = "nvidia.wd5.NVIDIAExternalCareerSite"
WD_PATH = "/job/Santa-Clara/Staff-Engineer_JR1990000"
WD_URL = f"https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite{WD_PATH}"
WD_DETAIL_PATH = f"/wday/cxs/nvidia/NVIDIAExternalCareerSite{WD_PATH}"
WD_LIST_PATH = "/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs"


def _workday_handler(calls: list[str], *, robots: str | None = None):
    """The tenant: its robots file (when given), the cxs detail of ``WD_PATH``, a list that must never be asked, a page shell."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots) if robots is not None else httpx.Response(404)
        if request.url.path == WD_DETAIL_PATH:
            return httpx.Response(200, json={"jobPostingInfo": {"title": "Staff Engineer", "jobDescription": "<p>Build GPUs.</p><p>Ship CUDA.</p>", "jobReqId": "JR1990000"}})
        if request.url.path == WD_LIST_PATH:
            raise AssertionError("the board's list was asked for one posting")
        return httpx.Response(200, text="<html><body><div id='app'></div></body></html>")  # the public page: a script shell

    return handler


def test_a_workday_posting_with_no_description_is_one_cxs_detail_get_by_its_stored_url() -> None:
    calls: list[str] = []
    client = httpx.Client(transport=httpx.MockTransport(_workday_handler(calls)))
    found = fetch_missing_description(client, provider="workday", token=WD_TOKEN, posting_id="JR1990000", url=WD_URL)
    assert found.text == "Build GPUs.\nShip CUDA." and found.title == "Staff Engineer" and found.fetch_kind == "ats_board"
    assert calls == [f"GET {WD_DETAIL_PATH}"], "more than the one detail request, or the list"

    # A locale segment before the site names the same posting; a URL of another board (or no posting) asks nothing.
    calls.clear()
    assert fetch_missing_description(client, provider="workday", token=WD_TOKEN, posting_id="JR1990000", url=WD_URL.replace("/NVIDIAExternalCareerSite/", "/en-US/NVIDIAExternalCareerSite/")).text == "Build GPUs.\nShip CUDA."
    assert calls == [f"GET {WD_DETAIL_PATH}"]
    calls.clear()
    for other in ("https://nvidia.wd5.myworkdayjobs.com/OtherSite" + WD_PATH, "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"):
        with pytest.raises(PostingTextUnavailable) as caught:
            fetch_missing_description(client, provider="workday", token=WD_TOKEN, posting_id="JR1990000", url=other)
        assert caught.value.reason == "board_refused", other
    assert calls == []


def test_a_workday_detail_that_is_gone_reads_as_removed() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    with pytest.raises(PostingTextUnavailable) as caught:
        fetch_missing_description(client, provider="workday", token=WD_TOKEN, posting_id="JR1990000", url=WD_URL)
    assert caught.value.reason == "posting_removed"


def test_a_pasted_workday_job_url_resolves_by_one_cxs_detail_get_not_the_list() -> None:
    calls: list[str] = []
    client = httpx.Client(transport=httpx.MockTransport(_workday_handler(calls)))
    resolved = resolve_job(AssessJobInput(job_url=WD_URL), client=client)
    assert resolved.fetch_kind == "ats_board" and resolved.text == "Build GPUs.\nShip CUDA." and resolved.title == "Staff Engineer"
    # The public page (a script shell, as for every board) and then ONE detail GET; the list's POST loop never runs.
    assert calls == [f"GET /NVIDIAExternalCareerSite{WD_PATH}", f"GET {WD_DETAIL_PATH}"]


def test_a_tenant_whose_robots_disallow_its_site_fails_a_pasted_workday_url_with_no_cxs_request(monkeypatch: pytest.MonkeyPatch) -> None:
    # Zoom's shape: ``Disallow: /Zoom/`` on the site's own pages; the sources update's site-level gate applies here too.
    # The client is the single-posting paths' own (``job_fetch_client``): the per-URL hook installed, the shared guard on.
    monkeypatch.setenv(robots_guard.ROBOTS_ENV, "1")
    guard = robots_guard.RobotsGuard()
    monkeypatch.setattr(robots_guard, "_SHARED", guard)
    calls: list[str] = []
    client = robots_guard.install(httpx.Client(transport=httpx.MockTransport(_workday_handler(calls, robots="User-agent: *\nDisallow: /NVIDIAExternalCareerSite/\n"))), guard)

    with pytest.raises(PostingTextUnavailable) as caught:
        fetch_missing_description(client, provider="workday", token=WD_TOKEN, posting_id="JR1990000", url=WD_URL)
    assert caught.value.reason == "board_refused" and robots_guard.ROBOTS_DISALLOWED in str(caught.value)
    assert calls == ["GET /robots.txt"], "a cxs request was made for a disallowed site"

    calls.clear()
    with pytest.raises(FindJobsContractError) as refused:
        resolve_job(AssessJobInput(job_url=WD_URL), client=client)
    assert refused.value.code == "job_fetch_failed" and f"workday board ({robots_guard.ROBOTS_DISALLOWED})" in str(refused.value)
    assert calls == [], "the page or a cxs path was asked for a disallowed site (the file is held for the hour)"

    # The hook on the client does not count twice: the site gate's client paces, the hook already did.
    assert getattr(client, "paces_hosts", False) is True

    # The tenant's other sites are not the block: a Disallow elsewhere leaves this board's detail readable.
    calls.clear()
    monkeypatch.setattr(robots_guard, "_SHARED", robots_guard.RobotsGuard())
    client = httpx.Client(transport=httpx.MockTransport(_workday_handler(calls, robots="User-agent: *\nDisallow: /InternalSite/\n")))
    assert fetch_missing_description(client, provider="workday", token=WD_TOKEN, posting_id="JR1990000", url=WD_URL).text == "Build GPUs.\nShip CUDA."
    assert calls == ["GET /robots.txt", f"GET {WD_DETAIL_PATH}"]

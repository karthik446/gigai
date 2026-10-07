"""0.1.11.4 7b: the company's own page for a posting is down while the board still lists the job. Synthetic only.

The operator's case: a Greenhouse board hands out the company's own careers route as the posting's URL
(``https://www.<company>.com/careers/job-title/?gh_jid=<id>``). The board's job endpoint answers 200; the company
page answers 404. A fake transport (``httpx.MockTransport``) stands in for the board AND the company site and counts
every request; no request leaves the process. The END outcomes, on the job read (``GET /api/jobs``):

- company page 404 / 410, board 200: ``state: open``, ``company_page: down``, the plain sentence, the board link;
  nothing is closed, ``removed_at`` stays null, the posting stays listed;
- company page 200: ``ok``, no sentence, the board link is still offered;
- company page 500 / 429 / a redirect / a timeout: ``unknown``, no sentence, the board link is still offered;
- a URL on the board's own host: no board link and no extra request;
- at most the one liveness request and one company-page request per job per hour (in the process and across one);
- never in a batch ("assess these"), never with the check switched off, never for a job with no stored row.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import threading

import httpx
import pytest

from gigai.scout import postings
from gigai.scout.posting_search import assess_these, search_postings

from tests.support.fake_dns import PUBLIC_V4, install_fake_dns
from tests.support.greenhouse_fixtures import gh_detail, gh_job, gh_url, posting_text, seed_greenhouse
from tests.support.posting_fixtures import NOW, TITLE_BOTH, PostingsFixture, build_postings_fixture, days_ago

try:  # the red run (HEAD has no company-page check): the end-outcome tests then fail on what the job read said
    from gigai.scout.find_jobs import posting_live
except ImportError:  # pragma: no cover - only at a commit before the liveness check
    posting_live = None  # type: ignore[assignment]

_UPDATED = "2026-10-02T09:00:00Z"
TOKEN, JOB_ID = "examplesecurity", 8236603
COMPANY_PAGE = f"https://www.example-co.com/careers/job-title/?gh_jid={JOB_ID}"
BOARD_API = f"boards-api.greenhouse.io/v1/boards/{TOKEN}/jobs/{JOB_ID}"
BOARD_URL = f"https://job-boards.greenhouse.io/{TOKEN}/jobs/{JOB_ID}"
PAGE_REQUEST = "www.example-co.com/careers/job-title/"
SENTENCE = "The company page for this job is down; the job is still open on the board"


class _Web:
    """The board's job endpoint and the company's site, counted. ``page``: what the company page answers (a status or ``"timeout"``)."""

    def __init__(self, page: object = 404) -> None:
        self.requests: list[str] = []
        self.methods: list[str] = []
        self.targets: list[str] = []  # where each company-page request was sent (S2: the one resolved address)
        self.page = page
        self.board = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        # S2: the company page is requested at its vetted ADDRESS; the ``Host`` header is the name the site is asked as.
        host = request.headers.get("host", request.url.host)
        self.requests.append(f"{host}{request.url.path}")
        self.methods.append(request.method)
        if host != "boards-api.greenhouse.io":
            self.targets.append(request.url.host)
        if host == "boards-api.greenhouse.io":
            token, job_id = request.url.path.strip("/").split("/")[2], int(request.url.path.rstrip("/").split("/")[-1])
            if self.board != 200:
                return httpx.Response(self.board, json={"error": "Job not found"})
            return httpx.Response(200, json=gh_detail(gh_job(token, job_id, TITLE_BOTH), posting_text(job_id)))
        if self.page == "timeout":
            raise httpx.ReadTimeout("the company site did not answer in time", request=request)
        if self.page in (301, 302):
            return httpx.Response(int(self.page), headers={"location": "https://www.example-co.com/careers/"})  # type: ignore[call-overload]
        return httpx.Response(int(self.page), text="<html>Not Found</html>" if self.page != 200 else "<html>Staff Engineer</html>")  # type: ignore[call-overload]

    def install(self, monkeypatch: pytest.MonkeyPatch) -> "_Web":
        if posting_live is None:
            return self
        monkeypatch.setenv(posting_live.LIVENESS_ENV, "1")
        monkeypatch.setenv("GIGAI_SCOUT_ATS_MIN_INTERVAL_SECONDS", "0")
        monkeypatch.setattr(posting_live, "liveness_client", lambda: httpx.Client(transport=httpx.MockTransport(self.handler), follow_redirects=False))
        posting_live.reset_memory()
        self.dns = install_fake_dns(monkeypatch, {"www.example-co.com": [PUBLIC_V4]})  # no name lookup leaves the process
        return self


def _seed(fx: PostingsFixture) -> str:
    """One posting stored under the company's own URL, and one under the board's; returns the first one's job identity."""

    own = {**gh_job(TOKEN, JOB_ID, TITLE_BOTH, updated_at=_UPDATED), "absolute_url": COMPANY_PAGE}
    seed_greenhouse(fx, TOKEN, [own], seen_at=days_ago(1), details={JOB_ID: (posting_text(JOB_ID), _UPDATED)})
    seed_greenhouse(fx, "acme", [gh_job("acme", 471, TITLE_BOTH, updated_at=_UPDATED)], seen_at=days_ago(1), details={471: (posting_text(471), _UPDATED)})
    postings.refresh(fx.home_root, fx.target, now=NOW)
    job = next(row for row in _listed(fx) if "example-co.com" in row)
    return job


def _listed(fx: PostingsFixture, *, removed: bool = False) -> set[str]:
    found = search_postings(fx.home_root, fx.target, removed=removed, now=NOW)
    return {row["job_identity"] for row in found["postings"]["rows"]}  # type: ignore[index]


def _rows(fx: PostingsFixture, url: str):
    store = postings.open_store(fx.home_root, fx.target)
    try:
        return store.postings(jobs={url}, live=False)
    finally:
        store.close()


def _forget(fx: PostingsFixture) -> None:
    """A new process an hour later: nothing in memory, no cache file."""

    posting_live.reset_memory()
    for path in (fx.home_root / "cache" / "scout" / "liveness").rglob("*.json"):
        path.unlink()


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend, serve

    monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    job = _seed(fx)
    web = _Web().install(monkeypatch)
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}", timeout=120.0)
    try:
        yield fx, web, client, job
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(10)


# --- the operator's case, on the end outcome ----------------------------------------------------------------


def test_the_company_page_404s_and_the_board_lists_the_job_the_read_says_open_down_and_links_the_board(served) -> None:
    fx, web, client, job = served

    read = client.get("/api/jobs", params={"url": job})

    assert read.status_code == 200, read.text
    live = read.json()["liveness"]
    assert live["state"] == "open", live
    assert live.get("company_page") == "down", live
    assert live.get("company_page_note") == SENTENCE
    assert live.get("board_url") == BOARD_URL
    assert (live["closed_at"], live["note"]) == (None, None)
    # The stored URL is kept as it is: the board link is a second one, never a replacement.
    assert read.json()["posting"]["source_url"] == COMPANY_PAGE
    # Nothing is closed or removed.
    assert read.json()["posting"]["removed_at"] is None
    assert all(row.removed_at is None for row in _rows(fx, job)) and job in _listed(fx) and job not in _listed(fx, removed=True)
    # One liveness request (the board, by the index token) and one GET of the company page.
    assert web.requests == [BOARD_API, PAGE_REQUEST] and web.methods == ["GET", "GET"]
    # S2: the page was asked at the one address its name resolved to, after one lookup.
    assert web.targets == [PUBLIC_V4] and web.dns.calls == ["www.example-co.com"]

    # The page opened again, and again in a new process, within the hour: no request at all.
    assert client.get("/api/jobs", params={"url": job}).json()["liveness"]["company_page"] == "down"
    posting_live.reset_memory()
    again = client.get("/api/jobs", params={"url": job}).json()["liveness"]
    assert (again["state"], again["company_page"], again["board_url"]) == ("open", "down", BOARD_URL)
    assert web.requests == [BOARD_API, PAGE_REQUEST], "more than one liveness request and one company-page request within the hour"


@pytest.mark.parametrize("status", [404, 410])
def test_404_and_410_of_the_company_page_are_down_and_never_close_or_remove_the_posting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    job = _seed(fx)
    web = _Web(status).install(monkeypatch)

    found = posting_live.job_liveness(fx.home_root, fx.target, job, company_page=True, now=NOW)

    assert (found.state, found.company_page, found.board_url, found.closed_at) == ("open", "down", BOARD_URL, None)
    assert found.company_page_note == SENTENCE and found.note is None
    assert all(row.removed_at is None for row in _rows(fx, job)) and job in _listed(fx)
    assert web.requests == [BOARD_API, PAGE_REQUEST]
    # Past the hour: one of each again, and no more.
    later = NOW + timedelta(hours=1, minutes=1)
    posting_live.reset_memory()
    assert posting_live.job_liveness(fx.home_root, fx.target, job, company_page=True, now=later).company_page == "down"
    assert posting_live.job_liveness(fx.home_root, fx.target, job, company_page=True, now=later).company_page == "down"
    assert web.requests == [BOARD_API, PAGE_REQUEST] * 2


def test_a_company_page_that_answers_200_says_nothing_and_the_board_link_is_still_offered(served) -> None:
    fx, web, client, job = served
    web.page = 200

    live = client.get("/api/jobs", params={"url": job}).json()["liveness"]

    assert (live["state"], live["company_page"], live["company_page_note"], live["board_url"]) == ("open", "ok", None, BOARD_URL)
    client.get("/api/jobs", params={"url": job})
    assert web.requests == [BOARD_API, PAGE_REQUEST]


@pytest.mark.parametrize("answer", [500, 503, 429, 403, 302, "timeout"])
def test_a_company_page_that_fails_any_other_way_is_unknown_says_nothing_and_is_not_asked_again_within_the_hour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: object
) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    job = _seed(fx)
    web = _Web(answer).install(monkeypatch)

    found = posting_live.job_liveness(fx.home_root, fx.target, job, company_page=True, now=NOW)

    assert (found.state, found.company_page, found.company_page_note, found.board_url) == ("open", "unknown", None, BOARD_URL)
    assert all(row.removed_at is None for row in _rows(fx, job))
    posting_live.job_liveness(fx.home_root, fx.target, job, company_page=True, now=NOW + timedelta(minutes=30))
    posting_live.reset_memory()  # a new process
    posting_live.job_liveness(fx.home_root, fx.target, job, company_page=True, now=NOW + timedelta(minutes=45))
    assert web.requests == [BOARD_API, PAGE_REQUEST], "a company site that did not answer was asked again within the hour"


def test_a_posting_stored_under_the_boards_own_url_has_no_board_link_and_makes_no_extra_request(served) -> None:
    fx, web, client, _job = served
    on_board = gh_url("acme", 471)

    live = client.get("/api/jobs", params={"url": on_board}).json()["liveness"]

    assert (live["state"], live["company_page"], live["company_page_note"], live["board_url"]) == ("open", "unknown", None, None)
    assert web.requests == ["boards-api.greenhouse.io/v1/boards/acme/jobs/471"], "a URL on the board's own host was asked a second time"


def test_the_company_page_is_never_asked_when_the_board_does_not_say_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    job = _seed(fx)
    web = _Web().install(monkeypatch)
    web.board = 500

    unknown = posting_live.job_liveness(fx.home_root, fx.target, job, company_page=True, now=NOW)
    assert (unknown.state, unknown.company_page, unknown.board_url) == ("unknown", "unknown", BOARD_URL)  # the link needs no request
    assert web.requests == [BOARD_API]

    # The board says the job is gone: closed, as before; no board link (it would be dead) and no company-page request.
    _forget(fx)
    web.requests.clear()
    web.board = 404
    closed = posting_live.job_liveness(fx.home_root, fx.target, job, company_page=True, now=NOW)
    assert (closed.state, closed.company_page, closed.company_page_note, closed.board_url) == ("closed", "unknown", None, None)
    assert web.requests == [BOARD_API]


def test_the_kill_switch_and_the_fixture_transport_make_no_request_and_the_board_link_is_still_built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    job = _seed(fx)
    web = _Web().install(monkeypatch)

    monkeypatch.setenv(posting_live.LIVENESS_ENV, "0")
    off = posting_live.job_liveness(fx.home_root, fx.target, job, company_page=True, now=NOW)
    assert (off.state, off.company_page, off.board_url) == ("unknown", "unknown", BOARD_URL) and web.requests == []
    # The board answered open earlier (kept); the switch goes off: the company page is still not asked.
    monkeypatch.setenv(posting_live.LIVENESS_ENV, "1")
    assert posting_live.job_liveness(fx.home_root, fx.target, job, now=NOW).state == "open" and web.requests == [BOARD_API]
    monkeypatch.setenv(posting_live.LIVENESS_ENV, "0")
    kept = posting_live.job_liveness(fx.home_root, fx.target, job, company_page=True, now=NOW)
    assert (kept.state, kept.company_page) == ("open", "unknown") and web.requests == [BOARD_API], "the kill switch did not stop the company-page request"
    monkeypatch.setenv(posting_live.LIVENESS_ENV, "1")
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    assert posting_live.job_liveness(fx.home_root, fx.target, job, company_page=True, now=NOW).company_page == "unknown"
    assert web.requests == [BOARD_API]


def test_a_batch_never_asks_a_company_page_and_the_actions_after_the_page_reuse_its_answer(served) -> None:
    fx, web, client, job = served

    done = assess_these(fx.home_root, fx.target, jobs=[job, gh_url("acme", 471)], approve=True, now=NOW)

    assert done["assessed"]["assessed"] == 2  # type: ignore[index]
    assert sorted(web.requests) == sorted([BOARD_API, "boards-api.greenhouse.io/v1/boards/acme/jobs/471"]), "a batch asked a company site"
    # Without the page being opened nothing is said of the company page (and nothing is asked).
    assert posting_live.job_liveness(fx.home_root, fx.target, job, now=NOW).company_page == "unknown"
    # The page opens: the one company-page request. Mark applied after it: no request, never refused, no note.
    web.requests.clear()
    assert client.get("/api/jobs", params={"url": job}).json()["liveness"]["company_page"] == "down"
    applied = client.post("/api/applications", json={"normalized_url": job, "event_kind": "applied"})
    assert applied.status_code == 201 and "posting_note" not in applied.json()
    assert web.requests == [PAGE_REQUEST]
    assert all(row.removed_at is None for row in _rows(fx, job))


def test_a_company_site_address_with_no_stored_row_has_no_board_link_and_makes_no_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    web = _Web().install(monkeypatch)

    found = posting_live.job_liveness(fx.home_root, fx.target, "https://www.example-co.com/careers/job-title/?gh_jid=77", company_page=True, now=NOW)

    assert (found.state, found.company_page, found.board_url) == ("unknown", "unknown", None) and web.requests == []


# --- the pieces ---------------------------------------------------------------------------------------------


def test_the_board_address_is_built_from_the_provider_the_index_token_and_the_posting_id() -> None:
    build = posting_live.board_job_url
    assert build("greenhouse", "examplesecurity", "8236603") == "https://job-boards.greenhouse.io/examplesecurity/jobs/8236603"
    assert build("lever", "acme", "0a1b2c3d-1111-2222-3333-444455556666") == "https://jobs.lever.co/acme/0a1b2c3d-1111-2222-3333-444455556666"
    assert build("ashby", "Acme Labs", "0a1b2c3d-1111-2222-3333-444455556666") == "https://jobs.ashbyhq.com/Acme%20Labs/0a1b2c3d-1111-2222-3333-444455556666"
    for provider, token, posting_id in [
        ("greenhouse", "acme", None), ("greenhouse", "", "5"), ("greenhouse", "acme", "five"), ("lever", "acme", "a/../b"),
        ("lever", "acme", "a?b=c"), ("workday", "acme", "5"), (None, "acme", "5"),
    ]:
        assert build(provider, token, posting_id) is None, (provider, token, posting_id)


def test_a_company_site_is_any_web_host_that_is_not_a_boards() -> None:
    site = posting_live.is_company_site
    assert site(COMPANY_PAGE) and site("https://careers.example-co.com/jobs/5")
    for url in (
        "https://boards.greenhouse.io/acme/jobs/5", "https://job-boards.greenhouse.io/acme/jobs/5", "https://job-boards.eu.greenhouse.io/acme/jobs/5",
        "https://jobs.lever.co/acme/abc", "https://jobs.eu.lever.co/acme/abc", "https://jobs.ashbyhq.com/acme/abc", "text:abc", "", "ftp://example-co.com/x",
    ):
        assert not site(url), url


@pytest.mark.parametrize("url", [
    "http://www.example-co.com/careers/?gh_jid=5",  # not https
    "https://127.0.0.1/careers/?gh_jid=5", "https://[::1]/careers/?gh_jid=5", "https://localhost/careers/?gh_jid=5",
    "https://intranet/careers/?gh_jid=5", "https://jobs.corp.internal/careers/?gh_jid=5", "https://www.example-co.com:8443/careers/?gh_jid=5",
    "https://user:secret@www.example-co.com/careers/?gh_jid=5",
])
def test_only_a_plain_https_address_at_a_public_name_is_ever_asked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    """The stored URL is the board's text, written by strangers: an address on this machine or network is never requested."""

    web = _Web().install(monkeypatch)

    assert posting_live.check_company_page(tmp_path, url=url, now=NOW) == "unknown"
    assert web.requests == []

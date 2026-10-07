"""0.1.11.4 7b: the company's own page for a job is down, the board still lists the job: the page says so and links the board.

A REAL Scout server (`serve()` with the real backend and the built `ui/dist`, in this process, on a free port) on a
synthetic home, and a real browser; no route of the page is stubbed. The public board API AND the companies' sites are
one fake transport (`httpx.MockTransport`) that counts every request: nothing leaves the process.

The operator's case: Greenhouse hands out the company's own careers route as the posting's URL
(`https://www.<company>.com/careers/job-title/?gh_jid=<id>`); the board's job endpoint answers 200, the company page 404.
Pinned, on the job's page:

- company page 404: "The company page for this job is down; the job is still open on the board", the board link
  ("Open on the job board") as the primary action and "Open posting" still on the stored URL; no closed banner; the
  job stays in the Jobs list; one request to the board and one to the company page, none on a reload;
- company page 200: no sentence; "Open posting" and the second link, both secondary;
- company page 500: no sentence; the second link is still offered;
- a posting stored under the board's own URL: no second link, and no request beyond the board's.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import json
import os
from pathlib import Path
import threading
from urllib.parse import quote
import urllib.request

import pytest

from tests.ui.conftest import _ui_session
from tests.ui.evidence import shot
from tests.ui.support import tid

pytestmark = pytest.mark.ui

DOWN_BANNER, CLOSED_BANNER, ROW = '[data-role="company-page-down"]', '[data-role="posting-closed"]', tid("job-row")
ACTIONS = ".job-page .job-actions"
BOARD_LINK, POSTING_LINK = '[data-role="open-on-board"]', '[data-role="open-posting"]'
SENTENCE = "The company page for this job is down; the job is still open on the board"
_UPDATED = "2026-10-02T09:00:00Z"
TOKEN = "examplesecurity"
#: posting id -> (the company's own URL the board hands out, what that page answers)
COMPANY_PAGES = {
    8236603: ("https://www.example-co.com/careers/job-title/?gh_jid=8236603", 404),
    8236604: ("https://www.example-co.com/careers/platform-engineer/?gh_jid=8236604", 200),
    8236605: ("https://www.example-co.com/careers/data-engineer/?gh_jid=8236605", 500),
}
API = f"boards-api.greenhouse.io/v1/boards/{TOKEN}/jobs/"


def _on_board(job_id: int) -> str:
    return f"https://job-boards.greenhouse.io/{TOKEN}/jobs/{job_id}"


@dataclass
class CompanyServer:
    """What `conftest._ui_session` needs of a server (`url`, `pid`, `log_path`), plus the home and the request log."""

    url: str
    pid: int
    log_path: str | None
    fx: object
    requests: list[str]
    jobs: dict[int, str]  # posting id -> the job's identity (its page's address)
    plain: str  # a posting stored under the board's own URL


@pytest.fixture
def company_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[CompanyServer]:
    import httpx

    from gigai.scout import postings
    from gigai.scout.find_jobs import posting_live
    from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend, serve
    from gigai.scout.find_jobs.contracts import normalize_url
    from tests.support.greenhouse_fixtures import gh_detail, gh_job, gh_url, posting_text, seed_greenhouse
    from tests.support.posting_fixtures import NOW, TITLE_BOTH, build_postings_fixture, days_ago

    monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")
    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
    monkeypatch.setenv("GIGAI_SCOUT_ATS_MIN_INTERVAL_SECONDS", "0")
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    seed_greenhouse(
        fx, TOKEN, [{**gh_job(TOKEN, n, TITLE_BOTH, updated_at=_UPDATED), "absolute_url": url} for n, (url, _status) in COMPANY_PAGES.items()],
        seen_at=days_ago(1), details={n: (posting_text(n), _UPDATED) for n in COMPANY_PAGES},
    )
    seed_greenhouse(fx, "acme", [gh_job("acme", 501, TITLE_BOTH, updated_at=_UPDATED)], seen_at=days_ago(1), details={501: (posting_text(501), _UPDATED)})
    postings.refresh(fx.home_root, fx.target, now=NOW)
    requests: list[str] = []
    pages = {httpx.URL(url).path: status for url, status in COMPANY_PAGES.values()}

    def web(request: httpx.Request) -> httpx.Response:
        host = request.headers.get("host", request.url.host)  # S2: the company page is requested at its vetted address
        requests.append(f"{host}{request.url.path}")
        if host == "boards-api.greenhouse.io":
            token, job_id = request.url.path.strip("/").split("/")[2], int(request.url.path.rstrip("/").split("/")[-1])
            return httpx.Response(200, json=gh_detail(gh_job(token, job_id, TITLE_BOTH), posting_text(job_id)))
        status = pages[request.url.path]
        return httpx.Response(status, text="<html>Not Found</html>" if status != 200 else "<html>The job</html>")

    monkeypatch.setenv(posting_live.LIVENESS_ENV, "1")  # the suite switches the check off; this flow is about it
    monkeypatch.setattr(posting_live, "liveness_client", lambda: httpx.Client(transport=httpx.MockTransport(web), follow_redirects=False))
    posting_live.reset_memory()
    from tests.support.fake_dns import PUBLIC_V4, install_fake_dns

    install_fake_dns(monkeypatch, {"www.example-co.com": [PUBLIC_V4]})  # S2: the company page's name is resolved once; no lookup leaves the process
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        yield CompanyServer(
            f"http://127.0.0.1:{server.server_address[1]}", os.getpid(), None, fx, requests,
            {n: normalize_url(url) for n, (url, _status) in COMPANY_PAGES.items()}, gh_url("acme", 501),
        )
    finally:
        server.shutdown()
        server.server_close()
        serving.join(10)


@pytest.fixture
def company_ui(request: pytest.FixtureRequest, ui_browser, company_home: CompanyServer, ui_artifacts: Path):
    with _ui_session(request, ui_browser, company_home, ui_artifacts) as session:
        yield session


def _save_preferences(home: CompanyServer) -> None:
    """The onboarding's own save (`PUT /api/setup`), so the page opens on Jobs and not on the wizard. AFTER the postings are stored."""

    def ask(path: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(home.url + path, data=data, method="GET" if body is None else "PUT", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read() or b"{}")

    default = next(item for item in ask("/api/profiles")["profiles"] if item["profile_id"] == home.fx.default_profile_id)  # type: ignore[attr-defined]
    ask("/api/setup", {
        "roles": list(default["titles"]), "titles_to_avoid": [], "countries": ["US"], "work_mode": "any", "city": None,
        "visa_sponsorship_required": False, "exclude_companies": [], "watch_companies": [], "company_stage_size": None,
        "industries_include": [], "industries_exclude": [], "must_have_stack": [], "dealbreaker_stack": [], "cadence_days": 7,
        "budget_usd_per_session": 0.5,
    })


def _job_page(ui, url: str) -> dict:
    """Open the job's page by its address and wait for its job read (`GET /api/jobs`); returns what the server said of its liveness."""

    with ui.page.expect_response(lambda response: "/api/jobs?url=" in response.url) as read:
        ui.goto(f"/#/jobs/{quote(url, safe='')}")
    ui.page.wait_for_selector(".job-page .job-title")
    return read.value.json().get("liveness") or {}


def _links(ui) -> list[tuple[str, str, str]]:
    """The links of the page's action row, in order: (words, address, "primary" | "secondary")."""

    return [
        (" ".join(text.split()), href, "secondary" if "secondary" in classes.split() else "primary")
        for text, href, classes in ui.page.locator(f"{ACTIONS} a").evaluate_all(
            "(links) => links.map((link) => [link.textContent || '', link.getAttribute('href') || '', link.className || ''])"
        )
    ]


def test_a_dead_company_page_for_an_open_job_says_so_and_puts_the_board_link_first(company_ui, company_home: CompanyServer) -> None:
    ui, home = company_ui, company_home
    _save_preferences(home)
    dead, (stored, _status) = home.jobs[8236603], COMPANY_PAGES[8236603]

    # The list: all four postings, live. Opening the list asks nobody anything.
    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.settle()
    assert ui.job_rows() == 4 and home.requests == []

    live = _job_page(ui, dead)
    assert (live.get("state"), live.get("company_page"), live.get("board_url")) == ("open", "down", _on_board(8236603)), live
    ui.page.wait_for_selector(DOWN_BANNER)
    ui.settle()
    banner = ui.page.locator(DOWN_BANNER)
    assert " ".join((banner.inner_text() or "").split()) == f"{SENTENCE}. Open on the job board ↗"
    link = banner.locator(BOARD_LINK)
    assert link.get_attribute("href") == _on_board(8236603) and link.get_attribute("target") == "_blank"
    assert "noopener" in (link.get_attribute("rel") or "")
    # Both links in the action row: the board's first and primary, the stored URL kept as it is.
    assert _links(ui) == [("Open on the job board ↗", _on_board(8236603), "primary"), ("Open posting ↗", stored, "secondary")]
    # Never closed: no closed banner, and the job is still in the list.
    assert ui.page.locator(CLOSED_BANNER).count() == 0
    assert home.requests == [API + "8236603", "www.example-co.com/careers/job-title/"], "more than one liveness and one company-page request"
    shot(ui, "job-page-company-page-down")

    with ui.page.expect_response(lambda response: "/api/jobs?url=" in response.url):
        ui.reload()
    ui.page.wait_for_selector(DOWN_BANNER)
    ui.settle()
    assert home.requests == [API + "8236603", "www.example-co.com/careers/job-title/"], "a reload within the hour asked again"

    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.settle()
    assert ui.job_rows() == 4 and ui.page.locator(f"{ROW}.removed").count() == 0
    assert ui.server_json("/api/postings?removed=1")["postings"]["rows"] == [], "a dead company page removed a posting"
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests


def test_a_company_page_that_answers_or_fails_says_nothing_and_a_board_address_gets_no_second_link(company_ui, company_home: CompanyServer) -> None:
    ui, home = company_ui, company_home
    _save_preferences(home)

    # Company page 200: no sentence; the stored URL first, the board link offered second.
    fine = _job_page(ui, home.jobs[8236604])
    assert (fine.get("state"), fine.get("company_page"), fine.get("board_url")) == ("open", "ok", _on_board(8236604)), fine
    ui.page.wait_for_selector(f"{ACTIONS} {BOARD_LINK}")
    ui.settle()
    assert ui.page.locator(DOWN_BANNER).count() == 0
    assert _links(ui) == [("Open posting ↗", COMPANY_PAGES[8236604][0], "secondary"), ("Open on the job board ↗", _on_board(8236604), "secondary")]
    shot(ui, "job-page-company-page-ok-second-link")

    # Company page 500: says nothing about the job. No sentence; the second link is still offered.
    failing = _job_page(ui, home.jobs[8236605])
    assert (failing.get("state"), failing.get("company_page"), failing.get("board_url")) == ("open", "unknown", _on_board(8236605)), failing
    ui.page.wait_for_selector(f"{ACTIONS} {BOARD_LINK}")
    ui.settle()
    assert ui.page.locator(DOWN_BANNER).count() == 0
    assert _links(ui) == [("Open posting ↗", COMPANY_PAGES[8236605][0], "secondary"), ("Open on the job board ↗", _on_board(8236605), "secondary")]

    # A posting stored under the board's own URL: one link, and only the board was asked.
    home.requests.clear()
    plain = _job_page(ui, home.plain)
    assert (plain.get("state"), plain.get("company_page"), plain.get("board_url")) == ("open", "unknown", None), plain
    ui.page.wait_for_selector(f"{ACTIONS} {POSTING_LINK}")
    ui.settle()
    assert ui.page.locator(f".job-page {BOARD_LINK}").count() == 0 and ui.page.locator(DOWN_BANNER).count() == 0
    assert [(words, kind) for words, _href, kind in _links(ui)] == [("Open posting ↗", "secondary")]
    assert home.requests == ["boards-api.greenhouse.io/v1/boards/acme/jobs/501"], "a URL on the board's own host was asked a second time"
    ui.assert_clean()

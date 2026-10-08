"""0.1.11.4 R1: a posting its board no longer lists says "This posting is closed" on its page and "Closed" in the list.

A REAL Scout server (`serve()` with the real backend and the built `ui/dist`, in this process, on a free port) on a
synthetic home of two Greenhouse postings, and a real browser; no route of the page is stubbed. The public board API
is a fake transport (`httpx.MockTransport`) that counts every request: nothing leaves the process.

The operator's case: the board answers 404 for one posting that Scout still lists as live. Pinned:

- the Jobs list shows both postings before either page is opened (opening the list asks no board anything);
- opening the dead posting's page shows the closed banner in plain words with the link to the posting, after ONE
  request to the board; the open posting's page shows no banner;
- back on the list the dead posting is gone; under "Removed" it is listed, dimmed, with a "Closed" chip and its
  checkbox off;
- its page opened again (a reload) shows the banner and asks the board nothing: the row already says it.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import json
import os
from pathlib import Path
import threading
from urllib.parse import quote, urlsplit
import urllib.request

import pytest

from tests.ui.conftest import _ui_session
from tests.ui.evidence import shot
from tests.ui.support import tid

pytestmark = pytest.mark.ui

BANNER, ROW = '[data-role="posting-closed"]', tid("job-row")
_UPDATED = "2026-10-02T09:00:00Z"
SLASHED = "https://boards.greenhouse.io/acme/jobs/503/?gh_jid=503"  # stored with a slash before the query (0.1.11.4 UI1 item 8)
BARE = "https://boards.greenhouse.io/acme/jobs/503?gh_jid=503"


@dataclass
class ClosedServer:
    """What `conftest._ui_session` needs of a server (`url`, `pid`, `log_path`), plus the home and the board's request log."""

    url: str
    pid: int
    log_path: str | None
    fx: object
    requests: list[str]
    dead: str
    alive: str


@pytest.fixture
def closed_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ClosedServer]:
    import httpx

    from gigai.scout import postings
    from gigai.scout.find_jobs import posting_live
    from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend, serve
    from tests.support.greenhouse_fixtures import gh_detail, gh_job, gh_url, posting_text, seed_greenhouse
    from tests.support.posting_fixtures import NOW, TITLE_BOTH, build_postings_fixture, days_ago

    monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")
    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    seed_greenhouse(
        fx, "acme", [gh_job("acme", n, TITLE_BOTH, updated_at=_UPDATED) for n in (501, 502)] + [{**gh_job("acme", 503, TITLE_BOTH, updated_at=_UPDATED), "absolute_url": SLASHED}],
        seen_at=days_ago(1), details={n: (posting_text(n), _UPDATED) for n in (501, 502, 503)},
    )
    postings.refresh(fx.home_root, fx.target, now=NOW)
    requests: list[str] = []

    def board(request: httpx.Request) -> httpx.Response:
        requests.append(f"{request.url.host}{request.url.path}")
        job_id = int(request.url.path.rstrip("/").split("/")[-1])
        if job_id == 502:
            return httpx.Response(404, json={"error": "Job not found"})
        return httpx.Response(200, json=gh_detail(gh_job("acme", job_id, TITLE_BOTH), posting_text(job_id)))

    monkeypatch.setenv(posting_live.LIVENESS_ENV, "1")  # the suite switches the check off; this flow is about it
    monkeypatch.setattr(posting_live, "liveness_client", lambda: httpx.Client(transport=httpx.MockTransport(board), follow_redirects=False))
    posting_live.reset_memory()
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        yield ClosedServer(f"http://127.0.0.1:{server.server_address[1]}", os.getpid(), None, fx, requests, gh_url("acme", 502), gh_url("acme", 501))
    finally:
        server.shutdown()
        server.server_close()
        serving.join(10)


@pytest.fixture
def closed_ui(request: pytest.FixtureRequest, ui_browser, closed_home: ClosedServer, ui_artifacts: Path):
    with _ui_session(request, ui_browser, closed_home, ui_artifacts) as session:
        yield session


def _save_preferences(home: ClosedServer) -> None:
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
    return read.value.json()["liveness"]


def _listed(ui) -> list[str]:
    return [href or "" for href in ui.page.locator(f"{ROW} a.posting-title").evaluate_all("(links) => links.map((link) => link.getAttribute('href'))")]


def test_a_closed_posting_says_so_on_its_page_and_is_a_closed_chip_under_removed(closed_ui, closed_home: ClosedServer) -> None:
    ui, home = closed_ui, closed_home
    _save_preferences(home)
    board = "boards-api.greenhouse.io/v1/boards/acme/jobs/"

    # The list as the operator saw it: both postings, live. Opening the list asks no board anything.
    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.settle()
    assert ui.job_rows() == 3 and ui.page.locator(f"{ROW}.removed").count() == 0
    assert home.requests == []

    # The open posting's page: one request, no banner.
    assert _job_page(ui, home.alive)["state"] == "open"
    ui.settle()
    assert home.requests == [board + "501"]
    assert ui.page.locator(BANNER).count() == 0

    # The dead posting's page: the banner in plain words, with the link to the posting, after one request.
    assert _job_page(ui, home.dead)["state"] == "closed"
    ui.page.wait_for_selector(BANNER)
    ui.settle()
    banner = " ".join((ui.page.locator(BANNER).inner_text() or "").split())
    assert banner == "This posting is closed. Its board no longer lists it. Open the posting to confirm"
    link = ui.page.locator(f'{BANNER} [data-role="posting-closed-link"]')
    assert link.get_attribute("href") == home.dead and link.get_attribute("target") == "_blank"
    assert "noopener" in (link.get_attribute("rel") or "")
    assert ui.page.locator(BANNER).get_attribute("data-since")
    assert home.requests == [board + "501", board + "502"], "more than one request for a posting"
    shot(ui, "job-page-closed-banner")

    # The list: the dead posting is gone by default.
    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.page.wait_for_function("(selector) => document.querySelectorAll(selector).length === 2", arg=ROW)
    ui.settle()
    assert len(_listed(ui)) == 2 and quote(home.dead, safe="") not in "".join(_listed(ui))

    # Under "Removed": listed, dimmed, a "Closed" chip, its checkbox off.
    ui.page.click('[data-role="state-filter"] [data-state="removed"]')
    ui.page.wait_for_selector(f"{ROW}.removed")
    ui.settle()
    assert ui.page.locator(ROW).count() == 1
    chip = ui.page.locator(f'{ROW}.removed [data-kind="removed"]')
    assert (chip.text_content() or "").strip() == "Closed" and "no longer lists" in (chip.get_attribute("title") or "")
    assert ui.page.locator(f"{ROW}.removed input.posting-select").is_disabled()
    shot(ui, "jobs-removed-closed-chip")

    # Its page again, by its address: the banner, and the board is not asked (the row already says it).
    assert _job_page(ui, home.dead)["state"] == "closed"
    ui.page.wait_for_selector(BANNER)
    with ui.page.expect_response(lambda response: "/api/jobs?url=" in response.url):
        ui.reload()
    ui.page.wait_for_selector(BANNER)
    ui.settle()
    assert home.requests == [board + "501", board + "502"], "the page asked the board about a posting already removed"
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests



def test_assessing_one_closed_posting_says_plainly_to_skip_it_and_makes_no_model_call(closed_ui, closed_home: ClosedServer) -> None:
    """R1b (a): the page's Assess on a posting its board dropped answers 409 `posting_closed`; the page says it in words."""

    ui, home = closed_ui, closed_home
    _save_preferences(home)
    assert _job_page(ui, home.dead)["state"] == "closed"
    ui.page.wait_for_selector(BANNER)
    ui.settle()
    with ui.page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == "/api/assess") as answered:
        ui.page.locator(".job-page button", has_text="Assess").first.click()
    assert answered.value.status == 409 and answered.value.json()["error"]["code"] == "posting_closed"
    ui.page.wait_for_selector(".job-page .field-error")
    text = " ".join((ui.page.locator(".job-page .field-error").first.inner_text() or "").split())
    assert text == "This job is closed: nothing to apply to. Skip it.", text
    assert "posting_closed" not in ui.page.locator(".job-page").inner_text()
    assert ui.server_json("/api/assessments").get("items") == [], "an assessment was stored for a closed posting (a model call was made)"
    shot(ui, "assess-closed-plain-message")
    assert ui.problems() and all("409" in problem for problem in ui.problems()), ui.problems()  # the refusal is the only one
    ui.network.console_errors.clear()
    ui.network.http_errors.clear()


def test_a_page_route_without_the_slash_resolves_the_stored_address_and_an_unknown_one_says_so(closed_ui, closed_home: ClosedServer) -> None:
    """Item 8: the stored address has a slash before the query, the route has none: same job page. Unknown: a plain page with a link back."""

    ui, home = closed_ui, closed_home
    _save_preferences(home)

    def stored_with_a_slash(route) -> None:
        if route.request.method != "GET":
            route.continue_()
            return
        body = route.fetch().json()
        for row in (body.get("postings") or {}).get("rows", []):
            if row.get("job_identity") == BARE:
                row["job_identity"] = SLASHED
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    ui.page.route("**/api/postings?*", stored_with_a_slash)
    for form in (SLASHED, BARE):
        ui.goto("/#/jobs/" + quote(form, safe=""))
        ui.page.wait_for_selector(".job-page .job-title")
        ui.settle()
        assert ui.page.evaluate("location.hash").startswith("#/jobs/https"), "the page left its route"
        assert ui.page.locator(".job-page [data-role='no-stored-posting']").count() == 0, form
    shot(ui, "address-without-slash-opens")

    unknown = "https://boards.greenhouse.io/acme/jobs/909?gh_jid=909"
    ui.goto("/#/jobs/" + quote(unknown, safe=""))
    ui.page.locator(".panel h2", has_text="We have no stored posting at this address").wait_for()
    ui.settle()
    assert ui.page.evaluate("location.hash") == "#/jobs/" + quote(unknown, safe=""), "a silent redirect"
    back = ui.page.locator("[data-role='back-to-jobs']")
    assert (back.get_attribute("href") or "") == "#/jobs" and "Jobs list" in (back.text_content() or "")
    shot(ui, "address-unknown-plain-page")
    ui.page.unroute_all()
    # 0.1.11.7 FS2: an address no list holds is asked ONCE of the by-address job read (the company index may hold it).
    # Nothing holds this one: that read answers 404, and it is the only problem of the page.
    assert ui.network.http_errors == ["HTTP 404 GET /api/jobs"], ui.network.http_errors
    ui.network.http_errors.clear()
    ui.network.console_errors[:] = [line for line in ui.network.console_errors if "404" not in line]
    ui.assert_clean()

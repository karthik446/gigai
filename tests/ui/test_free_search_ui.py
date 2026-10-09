"""0.1.11.7 FS2: "Search all jobs" on the Jobs page, and "Save this search as a profile".

Real Chromium against a REAL server of its own (`GET /api/search`, the built search index), nothing stubbed but
`GET /api/setup` (the fixture home was never through the setup interview). The home is synthetic
(`tests/support/posting_fixtures.py`: two active profiles, made-up Lever boards, a scripted model): no request leaves
the machine and nothing of the person's is read.

The postings, all titled for the search "staff ai engineer" unless said:

- `acme-health` (watched): five "Staff AI Engineer" (both profiles hold them) and one "Staff Engineer" (the second
  profile alone holds it);
- `quiet-harbor` (NOT watched, so no profile's list holds its postings): 52 recent ones, and 4 posted long ago, which
  the default profile's posted window hides.

Pinned:

- the page comes first and the total after it (the count request is held: the rows are there, "Counting…" is said, no
  total; released, the total line appears), newest first, with the labels (profiles, assessment, application);
- the search names no profile, whichever profile chip is on;
- "Show all N (US only, any date)" changes the count and the switch (0.1.11.8: the fixture is a US setup, so US only
  is on and Show all keeps it); "Load more" asks for the next 50 and adds them;
- "Assess · 1 model call · as <default profile>" asks first, then posts the DEFAULT profile's id (another profile is
  the selected one), one model call; "Mark applied" posts the job alone;
- "Save this search as a profile" opens the new-profile form with the typed titles filled in and creates nothing;
  created from there, the profile has those titles;
- a result opens its job page: a posting no profile holds and nothing assessed (built from its search row), one held
  by another profile than the selected one, one held by both;
- the same posting opened BY ITS ADDRESS (a reload, a pasted link, no search row): the list has no row for it, so the
  page asks the by-address job read (`GET /api/jobs?url=`), which answers it from the company index
  (`posting.fetch_kind == "company_index"`), and opens from that; a reload shows the same page. (An address nothing
  holds still says so: tests/ui/test_job_address_routes.py.)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import re
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlsplit

import pytest

from tests.support.pipeline_fixtures import set_pipeline_enabled
from tests.support.posting_fixtures import SECOND_LABEL, TITLE_BOTH, TITLE_SECOND_ONLY, build_postings_fixture, job_url, lever_job
from tests.ui import support
from tests.ui.conftest import _ui_session

pytestmark = pytest.mark.ui
UI_ORDER = 64  # a server and a home of its own: nothing of the shared home is read or written

WATCHED, UNWATCHED = "acme-health", "quiet-harbor"
RECENT, OLD = 52, 4  # quiet-harbor's postings inside and outside the default posted window
TYPED = "staff ai engineer"

PANEL = '[data-testid="free-search"]'
ROW = f'{PANEL} [data-testid="search-row"]'
TOTAL = f'{PANEL} [data-testid="free-search-total"]'
NOT_FOUND = "We have no stored posting at this address"


@pytest.fixture(autouse=True)
def _scratch_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HOME is a folder of this test: nothing of the person's is read or written."""

    home = support.refuse_real_home(tmp_path / "person")
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("GIGAI_HOME", "GIGAI_SCOUT_PIPELINE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The synthetic home with its search index built, and the real server on it."""

    from gigai.scout.find_jobs import search_index
    from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend, serve

    for name, value in {"GIGAI_SCOUT_AUTO_REFRESH": "0", "GIGAI_SCOUT_MODEL_TAGS": "0", "GIGAI_SCOUT_SNAPSHOT": "0", "GIGAI_SCOUT_POSTING_LIVENESS": "0"}.items():
        monkeypatch.setenv(name, value)
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    set_pipeline_enabled(fx.home_root, fx.target, False)  # no background step: the one model call is the button's
    now = datetime.now(UTC)
    seen = now - timedelta(minutes=30)
    fx.seed(
        WATCHED,
        [lever_job(WATCHED, n, title=TITLE_BOTH, created=now - timedelta(hours=n)) for n in range(1, 6)]
        + [lever_job(WATCHED, 6, title=TITLE_SECOND_ONLY, created=now - timedelta(hours=6))],
        seen_at=seen,
    )
    fx.seed(
        UNWATCHED,
        [lever_job(UNWATCHED, n, title=TITLE_BOTH, created=now - timedelta(hours=10 + n)) for n in range(1, RECENT + 1)]
        + [lever_job(UNWATCHED, RECENT + n, title=TITLE_BOTH, created=now - timedelta(days=400 + n)) for n in range(1, OLD + 1)],
        seen_at=seen, watch=False,
    )
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert search_index.rebuild_from_index(fx.home_root).available, "the search index of the fixture home was not built"
        yield fx, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)
        search_index.close(fx.home_root)


@pytest.fixture
def page(request: pytest.FixtureRequest, ui_browser, ui_artifacts: Path, served, tmp_path: Path):  # noqa: ANN001
    """`ui` on this test's own server, with every `GET /api/search` the page sends kept (`ui.searches`)."""

    fx, url = served
    log = tmp_path / "server.log"
    log.write_text("", encoding="utf-8")
    own = SimpleNamespace(url=url, pid=os.getpid(), log_path=str(log))
    with _ui_session(request, ui_browser, own, ui_artifacts) as session:
        session.fx = fx
        prefill = _refused(session, "/api/setup").get("prefill") or {}
        session.page.route("**/api/setup", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"prefs": prefill})))
        session.searches = []
        session.page.on("request", lambda sent: urlsplit(sent.url).path == "/api/search" and session.searches.append(parse_qs(urlsplit(sent.url).query)))
        session.profiles = {item["profile_id"]: item for item in session.server_json("/api/profiles")["profiles"]}
        session.default_label = session.profiles[fx.default_profile_id]["label"]
        assert session.profiles[fx.default_profile_id]["is_default"] and session.profiles[fx.second_profile_id]["label"] == SECOND_LABEL
        # The lists of the two profiles exist before the page asks (the first read builds the read model).
        assert len(session.server_json("/api/postings?limit=50")["postings"]["rows"]) == 6
        yield session


def _refused(ui, path: str) -> dict:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(ui.server.url + path, timeout=60) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        return json.loads(error.read()).get("error") or {}


def _search(ui, title: str, *, wait: bool = True) -> None:
    ui.page.locator(f'{PANEL} [data-testid="free-search-title"]').fill(title)
    ui.page.locator(f'{PANEL} [data-testid="free-search-submit"]').click()
    if wait:
        ui.page.locator(TOTAL).wait_for()
        ui.settle()


def _open_jobs(ui) -> None:
    ui.goto("/#/jobs")
    ui.page.locator(PANEL).wait_for()
    ui.wait_for_jobs_list()
    ui.settle()


def _rows(ui) -> list[dict]:
    return ui.page.evaluate(
        """(selector) => Array.from(document.querySelectorAll(selector)).map((row) => ({
            job: row.getAttribute('data-job'),
            title: row.querySelector('.posting-title').textContent,
            company: row.querySelector('[data-role="search-company"]').textContent,
            location: row.querySelector('[data-role="search-location"]').textContent,
            at: (row.querySelector('[data-role="posted"]') || {getAttribute: () => null}).getAttribute('data-at'),
            labels: Array.from(row.querySelectorAll('[data-testid="search-label"]')).map((label) => label.textContent),
            actions: Array.from(row.querySelectorAll('.posting-actions a, .posting-actions button')).map((item) => item.textContent),
        }))""",
        ROW,
    )


def _total(ui) -> int:
    return int(ui.page.locator(TOTAL).get_attribute("data-total"))


def _select(ui, profile_id: str) -> None:
    changed = ui.server_json("/api/profiles/selection", {"profile_id": profile_id})
    assert changed["selected_profile_id"] == profile_id


def test_the_page_comes_first_then_the_total_with_labels_show_all_and_load_more(page) -> None:  # noqa: ANN001
    ui, fx = page, page.fx
    applied = job_url(WATCHED, 2)
    ui.server_json("/api/applications", {"job_identity": applied, "event_kind": "applied"})
    assessed = ui.server_json("/api/assess", {"job": {"job_url": job_url(WATCHED, 3)}, "resume": {"profile_id": fx.default_profile_id}, "origin": "job_page"})
    assert assessed["result"]["verdict"]
    _open_jobs(ui)
    # A profile chip is on: the search below is not that profile's list.
    ui.page.locator('[data-role="profile-filter-chip"]', has_text=SECOND_LABEL).click()
    ui.settle()
    assert ui.page.locator(f'{PANEL} [data-role="free-search-hint"]').count() == 1 and ui.page.locator(ROW).count() == 0

    # The count is held back: the page is there without it.
    held: list = []
    ui.page.route(re.compile(r"/api/search\?.*count=1"), lambda route: held.append(route))
    _search(ui, TYPED, wait=False)
    ui.page.locator(ROW).first.wait_for()
    ui.page.locator(f'{PANEL} [data-role="free-search-counting"]').wait_for()
    assert ui.page.locator(TOTAL).count() == 0, "the total is shown before it was counted"
    assert ui.page.locator(ROW).count() == 50
    shown = ui.page.locator(f'{PANEL} [data-role="free-search-shown"]').text_content()
    assert shown.startswith(f'Showing 1-50, newest first: "{TYPED}" (') and "last" in shown, shown
    for _ in range(100):
        if held:
            break
        ui.page.wait_for_timeout(50)
    assert len(held) == 1, "one count request follows the page"
    held.pop().continue_()
    ui.page.locator(TOTAL).wait_for()
    ui.page.unroute(re.compile(r"/api/search\?.*count=1"))
    ui.settle()

    # Two requests, the page then the count; neither names a profile.
    assert [sorted(query) for query in ui.searches] == [["limit", "title"], ["count", "limit", "title"]], ui.searches
    assert ui.searches[0] == {"title": [TYPED], "limit": ["50"]} and ui.searches[1] == {"title": [TYPED], "limit": ["1"], "count": ["1"]}
    total = 5 + RECENT
    assert _total(ui) == total
    scope = shown[shown.index("(") : shown.rindex(")") + 1]
    assert ui.page.locator(TOTAL).text_content() == f'{total} postings match "{TYPED}" {scope}.'
    assert scope[1:-1] in ui.page.locator(f'{PANEL} [data-role="free-search-show-all-label"]').text_content(), "the switch names the default filters"

    # Newest first, with company, title, location and the date; never a rank.
    rows = _rows(ui)
    assert [row["at"] for row in rows] == sorted((row["at"] for row in rows), reverse=True) and all(row["at"] for row in rows)
    assert [row["job"] for row in rows[:5]] == [job_url(WATCHED, n) for n in range(1, 6)]
    assert rows[0]["title"] == TITLE_BOTH and rows[0]["company"] and rows[0]["location"] == "Remote - United States"
    assert ui.page.locator(f"{PANEL} [data-testid='rank-chip'], {PANEL} [data-testid='fit-chip']").count() == 0
    by_job = {row["job"]: row for row in rows}
    both = [f"in: {ui.default_label}", f"in: {SECOND_LABEL}"]
    assert by_job[job_url(WATCHED, 1)]["labels"] == both
    assert by_job[applied]["labels"][:2] == both and by_job[applied]["labels"][2].startswith("Applied")
    assert by_job[job_url(WATCHED, 3)]["labels"][:2] == both and by_job[job_url(WATCHED, 3)]["labels"][2].startswith("assessed: ")
    assert by_job[job_url(UNWATCHED, 1)]["labels"] == [], "a posting no profile holds has no label"
    assert TITLE_SECOND_ONLY not in {row["title"] for row in rows}, "every typed word must be in the title"
    assert ui.page.locator(f'{PANEL} [data-role="free-search-not-ranked"]').text_content() == "Not ranked. Save as a profile to rank."

    # What the default filters hid, and Show all.
    hidden = ui.page.locator(f'{PANEL} [data-testid="free-search-show-hidden"]')
    assert hidden.text_content() == f"Show all {total + OLD} (US only, any date)"
    ui.searches.clear()
    hidden.click()
    ui.page.locator(f'{TOTAL}[data-total="{total + OLD}"]').wait_for()
    ui.settle()
    assert ui.page.locator(f'{PANEL} [data-testid="free-search-show-all"]').is_checked()
    assert ui.searches[0] == {"title": [TYPED], "all": ["1"], "limit": ["50"]}
    assert ui.page.locator(TOTAL).text_content() == f'{total + OLD} postings match "{TYPED}" (US only, any date).'
    assert ui.page.locator(f'{PANEL} [data-testid="free-search-show-hidden"]').count() == 0

    # Load more: the next 50, under the ones shown.
    first_page = [row["job"] for row in _rows(ui)]
    assert len(first_page) == 50
    ui.searches.clear()
    ui.page.locator(f'{PANEL} [data-testid="free-search-more"]').click()
    ui.page.wait_for_function("(arg) => document.querySelectorAll(arg.row).length === arg.count", arg={"row": ROW, "count": total + OLD})
    ui.settle()
    assert ui.searches == [{"title": [TYPED], "all": ["1"], "limit": ["50"], "offset": ["50"]}]
    jobs = [row["job"] for row in _rows(ui)]
    assert jobs[:50] == first_page and len(set(jobs)) == total + OLD
    assert set(jobs[-OLD:]) == {job_url(UNWATCHED, RECENT + n) for n in range(1, OLD + 1)}, "the oldest are last"
    assert ui.page.locator(f'{PANEL} [data-testid="free-search-more"]').count() == 0
    assert all("profile_id" not in query for query in ui.searches)
    ui.assert_clean()


def test_assess_is_as_the_default_profile_and_mark_applied_is_the_jobs(page) -> None:  # noqa: ANN001
    ui, fx = page, page.fx
    _select(ui, fx.second_profile_id)  # the selected profile is NOT the default one
    fx.base.model.assess_prompts.clear()
    _open_jobs(ui)
    _search(ui, TYPED)
    job = job_url(WATCHED, 4)
    row = ui.page.locator(f'{ROW}[data-job="{job}"]')
    button = row.locator('[data-action="search-assess"]')
    assert button.text_content() == f"Assess · 1 model call · as {ui.default_label}"

    # The question first: nothing is sent before its Assess.
    ui.step("asked")
    button.click()
    ask = row.locator('[data-role="search-assess-ask"]')
    ask.wait_for()
    assert ui.default_label in ask.text_content() and "One model call" in ask.text_content()
    assert ui.writes_after("asked") == [] and fx.base.model.assess_prompts == []
    row.locator('[data-action="search-assess-cancel"]').click()
    assert ask.count() == 0 and ui.writes_after("asked") == []

    button.click()
    with ui.page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == "/api/assess", timeout=60_000) as made:
        row.locator('[data-action="search-assess-confirm"]').click()
    answer = made.value
    assert answer.status in (200, 201), answer.text()
    sent = answer.request.post_data_json
    assert sent == {"job": {"job_url": job}, "resume": {"profile_id": fx.default_profile_id}, "origin": "job_page"}
    assert answer.json()["resume"]["profile_id"] == fx.default_profile_id
    ui.settle()
    assert len(fx.base.model.assess_prompts) == 1, "one model call"
    # The END outcome, on the server: the default profile has the assessment, the selected one has none.
    stored = {profile: [item["job"]["job_identity"] for item in ui.server_json(f"/api/assessments?profile_id={profile}")["items"]] for profile in (fx.default_profile_id, fx.second_profile_id)}
    assert stored == {fx.default_profile_id: [job], fx.second_profile_id: []}
    labels = row.locator('[data-testid="search-label"][data-kind="assessment"]')
    labels.wait_for()
    assert labels.text_content().startswith("assessed: ")
    assert row.locator('[data-action="search-assess"]').count() == 0, "the default profile has assessed it: its page re-assesses"
    assert ui.writes_after("asked") == ["POST /api/assess"]

    # Mark applied: the job alone.
    other = job_url(UNWATCHED, 2)
    row = ui.page.locator(f'{ROW}[data-job="{other}"]')
    ui.step("apply")
    with ui.page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == "/api/applications") as recorded:
        row.locator('[data-action="search-apply"]').click()
    assert recorded.value.status == 201, recorded.value.text()
    assert recorded.value.request.post_data_json == {"job_identity": other, "event_kind": "applied"}
    badge = row.locator('[data-testid="search-label"][data-kind="application"]')
    badge.wait_for()
    assert badge.get_attribute("data-status") == "applied" and row.locator('[data-action="search-apply"]').count() == 0
    ui.settle()
    recorded_for = ui.server_json(f"/api/search?title={quote(TYPED)}&company=quiet&count=1")["postings"]["rows"]
    assert {row["job_identity"]: row["application"] and row["application"]["status"] for row in recorded_for if row["application"]} == {other: "applied"}
    ui.assert_clean()


def test_save_this_search_as_a_profile_opens_the_form_filled_in(page) -> None:  # noqa: ANN001
    ui = page
    typed = "Staff AI Engineer, Staff Engineer"
    _open_jobs(ui)
    _search(ui, typed)
    assert _total(ui) == 5 + RECENT + 1
    scope = ui.page.locator(f'{PANEL} [data-role="free-search-shown"]').text_content()
    scope = scope[scope.index("(") + 1 : scope.rindex(")")]
    ui.step("save")
    ui.page.locator(f'{PANEL} [data-testid="save-as-profile"]').click()
    form = ui.page.locator('[data-testid="new-profile-form"]')
    form.wait_for()
    ui.settle()
    assert urlsplit(ui.page.url).fragment == "/settings"
    assert form.locator("#new-profile-label").input_value() == "Staff AI Engineer"
    assert [text.rstrip("×").strip() for text in form.locator(".tag-removable").all_text_contents()] == ["Staff AI Engineer", "Staff Engineer"]
    note = form.locator('[data-testid="profile-from-search"]').text_content()
    assert "The profile matches by the profile rule, which can list more than this search." in note
    assert f"The profile starts with this search's settings: {scope}." in note
    assert ui.page.evaluate("() => { const box = document.getElementById('new-profile-label').getBoundingClientRect(); return box.top >= 0 && box.bottom <= window.innerHeight; }"), "the form is not in view"
    assert ui.writes_after("save") == [], "opening the form creates nothing"
    assert len(ui.server_json("/api/profiles")["profiles"]) == 2

    # Created from there (its resume chosen in the form): the profile has the typed titles.
    form.locator("button", has_text="Choose existing").click()
    choice = form.locator(".wz-file-item").first
    choice.wait_for()
    choice.click()
    with ui.page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == "/api/profiles") as created:
        form.locator('button[type="submit"]').click()
    assert created.value.status == 201, created.value.text()
    body = created.value.request.post_data_json
    assert body["label"] == "Staff AI Engineer" and body["titles"] == ["Staff AI Engineer", "Staff Engineer"] and "search_settings" not in body
    ui.settle()
    made = created.value.json()["profile"]
    default = ui.server_json("/api/profiles")["default_search_settings"]
    assert made["titles"] == ["Staff AI Engineer", "Staff Engineer"] and made["search_settings"] == default, "it starts with the search's settings"
    assert ui.page.locator('[data-testid="profile-from-search"]').count() == 0

    # Show all: the form says the profile still starts with the default profile's settings.
    _open_jobs(ui)
    assert ui.page.locator(TOTAL).count() == 1, "the results are kept while the tab lives"
    ui.page.locator(f'{PANEL} [data-testid="free-search-show-all"]').check()
    ui.page.locator(f'{TOTAL}[data-total="{5 + RECENT + 1 + OLD}"]').wait_for()
    ui.settle()
    ui.page.locator(f'{PANEL} [data-testid="save-as-profile"]').click()
    ui.page.locator('[data-testid="new-profile-form"]').wait_for()
    assert "This search showed every posting (Show all)." in ui.page.locator('[data-role="profile-from-search-settings"]').text_content()
    ui.settle()
    ui.assert_clean()


def _opens(ui, job: str, *, title: str) -> int:
    """Click the result's title: its job page is shown in full, never the not-found panel. The status of its by-address read."""

    with ui.page.expect_response(lambda response: urlsplit(response.url).path == "/api/jobs") as by_address:
        ui.page.locator(f'{ROW}[data-job="{job}"] [data-action="open-search-job"]').click()
    status = by_address.value.status
    ui.page.wait_for_function(
        "(words) => !!document.querySelector('.job-page .job-title') || Array.from(document.querySelectorAll('.panel h2')).some((h) => h.textContent.includes(words))",
        arg=NOT_FOUND,
    )
    ui.settle()
    assert ui.page.locator(".panel h2", has_text=NOT_FOUND).count() == 0, f"the page says '{NOT_FOUND}' for {job}"
    ui.wait_for_job_page()
    ui.settle()
    assert urlsplit(ui.page.url).fragment == "/jobs/" + quote(job, safe="")
    assert ui.page.locator(".job-page .job-title").text_content() == title
    assert ui.page.locator('.job-page [data-role="job-state"] [data-event="applied"]').count() == 1, "Mark applied is offered"
    assert ui.page.locator('.job-page [data-role="open-posting"]').get_attribute("href") == job
    return status


def _back(ui) -> None:
    ui.page.locator(".back-link").click()
    ui.page.locator(TOTAL).wait_for()  # the results are kept: no second search
    ui.settle()


def test_a_result_opens_its_job_page(page) -> None:  # noqa: ANN001
    ui, fx = page, page.fx
    _select(ui, fx.default_profile_id)
    _open_jobs(ui)
    _search(ui, "Staff AI Engineer, Staff Engineer")
    searched = len(ui.searches)

    # 1. No profile holds it and nothing is assessed: the page is built from the search row.
    unheld = job_url(UNWATCHED, 3)
    assert ui.server_json(f"/api/postings?job={quote(unheld, safe='')}&limit=1")["postings"]["rows"] == [], "no list holds it"
    assert _opens(ui, unheld, title=TITLE_BOTH) == 200, "the by-address job read knows a posting only the company index holds"
    assert "Remote - United States" in ui.page.locator(".job-page .job-sub").text_content()
    assert ui.page.locator('.job-page [data-role="posted"]').count() == 1, "the posting's date, from the search row"
    assert ui.page.locator('.job-page [data-role="job-chip"]').get_attribute("data-fit")  # not assessed: a chip, and the Assess action
    # Its Assess says whose assessment it will be: the page's profile (the selected one, here the default).
    assert ui.page.locator(".job-page .callout.info button", has_text="Assess").all_text_contents() == [f"Assess · 1 model call · as {ui.default_label}"]
    _back(ui)

    # 2. Held by ANOTHER profile than the selected one (the default is selected; only the second holds it).
    second_only = job_url(WATCHED, 6)
    held_by = {item["profile_id"] for item in ui.server_json(f"/api/postings?job={quote(second_only, safe='')}&limit=1")["postings"]["rows"][0]["profiles"]}
    assert held_by == {fx.second_profile_id}
    assert _opens(ui, second_only, title=TITLE_SECOND_ONLY) == 200
    assert ui.server_json("/api/profiles")["selected_profile_id"] == fx.default_profile_id, "opening a search result switches no profile"
    _back(ui)

    # 3. Held by both.
    assert _opens(ui, job_url(WATCHED, 1), title=TITLE_BOTH) == 200
    _back(ui)
    assert len(ui.searches) == searched, "coming back searched again"

    ui.assert_clean()


def _by_address(ui, job: str) -> tuple[int, dict]:
    """`GET /api/jobs?url=` for the job, asked of the server directly: its status and its answer."""

    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"{ui.server.url}/api/jobs?url={quote(job, safe='')}", timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _cold_load(ui, job: str) -> list[str]:
    """A fresh load of the job's address (no row from an earlier page); the by-address reads the page sent."""

    asked: list[str] = []
    ui.page.on("request", lambda sent: urlsplit(sent.url).path == "/api/jobs" and asked.append(parse_qs(urlsplit(sent.url).query)["url"][0]))
    ui.page.goto("about:blank")
    ui.goto("/#/jobs/" + quote(job, safe=""))
    ui.page.wait_for_function(
        "(words) => !!document.querySelector('.job-page .job-title') || Array.from(document.querySelectorAll('.panel h2')).some((h) => h.textContent.includes(words))",
        arg=NOT_FOUND,
    )
    ui.page.wait_for_timeout(500)
    ui.settle()
    return asked


def test_an_unheld_posting_opened_by_its_address_opens_from_the_by_address_read(page) -> None:  # noqa: ANN001
    """The reload / pasted-link case: no search row, so the page is built from the by-address job read (FB1's fallback)."""

    ui = page
    unheld = job_url(UNWATCHED, 3)
    status, answer = _by_address(ui, unheld)
    assert status == 200, f"GET /api/jobs?url= answers {status} for a posting only the company index holds"
    assert answer["posting"]["fetch_kind"] == "company_index", "the marker the page builds its row on (freeSearchModel.COMPANY_INDEX_KIND)"
    assert ui.server_json(f"/api/postings?job={quote(unheld, safe='')}&limit=1")["postings"]["rows"] == [], "no list holds it"
    asked = _cold_load(ui, unheld)
    assert ui.page.locator(".panel h2", has_text=NOT_FOUND).count() == 0, f"the page says '{NOT_FOUND}' on a cold load"
    ui.wait_for_job_page()
    assert ui.page.locator(".job-page .job-title").text_content() == TITLE_BOTH
    assert "Remote - United States" in ui.page.locator(".job-page .job-sub").text_content()
    assert ui.page.locator('.job-page [data-role="posted"]').count() == 1
    assert ui.page.locator('.job-page [data-role="job-state"] [data-event="applied"]').count() == 1, "Mark applied is offered"
    assert ui.page.locator(".job-page .callout.info button", has_text="Assess").count() == 1
    assert ui.page.locator('.job-page [data-role="open-posting"]').get_attribute("href") == unheld
    assert set(asked) == {unheld} and len(asked) <= 2, f"the lookup and the page's own read: {asked}"
    # A reload shows the same page.
    ui.reload()
    ui.wait_for_job_page()
    ui.settle()
    assert ui.page.locator(".job-page .job-title").text_content() == TITLE_BOTH
    ui.assert_clean()


def test_no_match_a_refused_search_and_no_default_filters(page) -> None:  # noqa: ANN001
    ui, fx = page, page.fx
    _open_jobs(ui)
    submit = ui.page.locator(f'{PANEL} [data-testid="free-search-submit"]')
    assert submit.is_disabled(), "nothing typed: nothing to search"

    _search(ui, "harbor pilot", wait=False)
    empty = ui.page.locator(f'{PANEL} [data-role="free-search-empty"]')
    empty.wait_for()
    ui.settle()
    assert empty.text_content().startswith('No stored posting matches "harbor pilot" (')
    assert ui.page.locator(ROW).count() == 0 and ui.page.locator(f'{PANEL} [data-role="free-search-not-ranked"]').count() == 0

    # No readable find-jobs.json: 409 config_unavailable says to use Show all, and Show all answers.
    config = next(path for path in Path(fx.target).rglob("find-jobs.json"))
    kept = config.read_bytes()
    config.write_text("{ not json", encoding="utf-8")
    try:
        _search(ui, TYPED, wait=False)
        error = ui.page.locator(f'{PANEL} [data-testid="free-search-error"]')
        error.wait_for()
        assert error.get_attribute("data-code") == "config_unavailable" and "Show all" in error.text_content()
        assert ui.network.http_errors == ["HTTP 409 GET /api/search"]
        ui.network.http_errors.clear()
        ui.network.console_errors[:] = [line for line in ui.network.console_errors if "409" not in line]
        error.locator('[data-action="free-search-use-show-all"]').click()
        ui.page.locator(f'{TOTAL}[data-total="{5 + RECENT + OLD}"]').wait_for()
        ui.settle()
    finally:
        config.write_bytes(kept)
    ui.assert_clean()

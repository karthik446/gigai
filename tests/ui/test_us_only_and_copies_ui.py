"""0.1.11.8 N1 + N2: the "US only" box and the row of copies, in "Search all jobs" and on the Jobs list.

0.1.11.8 N3: the two are the Jobs page's tabs ("Search" at `#/jobs/search`, "Your jobs" at `#/jobs`), and each rule
is one short line with the whole text behind a "?" (tests/ui/test_jobs_tabs_ui.py pins the tabs and the "?").

Real Chromium against a REAL server of its own (`GET /api/search`, `GET /api/postings`, the built search index),
nothing stubbed but `GET /api/setup` (the fixture home was never through the setup interview). The home is synthetic
(`tests/support/copies_fixtures.py` on `posting_fixtures.py`: made-up Lever boards, a scripted model): no request
leaves the machine and nothing of the person's is read.

The postings: `copies_fixtures`' fourteen (at `point-example` "Staff Engineer" once per country, seven countries,
ONE job, none in the US; "Staff Engineer, Payments" in two US cities, one job; and what never merges: the same title
with another description, the same description under a title written another way, "Staff Engineer" at
`other-example`, a posting that says "Remote" alone) and, at `acme-health`, "Staff AI Engineer" in three US cities
(one job, in both of the fixture's profiles' lists). A third profile has no country setting, so the Jobs list holds
the postings of every country. Seventeen postings, eight jobs.

Pinned:

- Search all jobs: the box is ticked in this US setup before anything is typed; untouched it sends nothing; Show
  all does NOT change it (the scope says "US only, any date"); the posting that says "Remote" alone is LISTED and
  labelled "unclear location"; unticked, the request says `us_only=0` and the seven postings abroad are ONE row,
  its canonical job (the earliest posted), which says where they are ("Remote: Poland, Ukraine, Romania +4") and
  how many;
- that row's Mark applied is ONE request, for the row's posting; a row of copies' Assess asks once and is ONE model
  call, and the row then offers neither again;
- the Jobs list: the box is ticked, the list is the jobs not clearly abroad and says how many postings are left out;
  unticked, the address says `us=0`, the list is every job with the copies as one row, and a reload keeps it;
  "Assess these" asks about JOBS (8), not postings (17), and sends what the box says.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.support.copies_fixtures import ONE_JOB, OTHER, SLUG, anywhere_profile, copy_job, seed_copies
from tests.support.pipeline_fixtures import set_pipeline_enabled
from tests.support.posting_fixtures import TITLE_BOTH, build_postings_fixture, job_url
from tests.ui import support
from tests.ui.conftest import _ui_session

pytestmark = pytest.mark.ui
UI_ORDER = 65  # a server and a home of its own: nothing of the shared home is read or written

WATCHED = "acme-health"
PANEL = '[data-testid="free-search"]'
ROW = f'{PANEL} [data-testid="search-row"]'
TOTAL = f'{PANEL} [data-testid="free-search-total"]'
SEARCH_BOX = f'{PANEL} [data-testid="free-search-us-only"]'
LIST_BOX = '[data-testid="jobs-us-only"]'
LIST_ROW = '[data-testid="jobs-list"] [data-testid="job-row"]'
ABROAD = "Remote: Poland, Ukraine, Romania +4"
POSTINGS, JOBS, NOT_ABROAD = 17, 8, 5  # with acme-health's three; the jobs not clearly outside the US


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
    """The synthetic home with its search index built, and the real server on it (in this process)."""

    from gigai.scout.find_jobs import search_index
    from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend, serve

    for name, value in {"GIGAI_SCOUT_AUTO_REFRESH": "0", "GIGAI_SCOUT_MODEL_TAGS": "0", "GIGAI_SCOUT_SNAPSHOT": "0", "GIGAI_SCOUT_POSTING_LIVENESS": "0"}.items():
        monkeypatch.setenv(name, value)
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    set_pipeline_enabled(fx.home_root, fx.target, False)  # no background step: the one model call is the button's
    now = datetime.now(UTC)
    seen = now - timedelta(minutes=30)
    seed_copies(fx, seen_at=seen, newest=now - timedelta(hours=2))
    fx.seed(
        WATCHED,
        [copy_job(WATCHED, n, TITLE_BOTH, place, "US", now - timedelta(hours=1)) for n, place in enumerate(("Remote - United States", "Austin, TX", "Denver, CO"), start=1)],
        seen_at=seen,
    )
    anywhere = anywhere_profile(fx)
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert search_index.rebuild_from_index(fx.home_root).available, "the search index of the fixture home was not built"
        yield fx, f"http://127.0.0.1:{server.server_address[1]}", anywhere
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)
        search_index.close(fx.home_root)


@pytest.fixture
def page(request: pytest.FixtureRequest, ui_browser, ui_artifacts: Path, served, tmp_path: Path):  # noqa: ANN001
    """`ui` on this test's own server, with every `GET /api/search` and `GET /api/postings` the page sends kept."""

    fx, url, anywhere = served
    log = tmp_path / "server.log"
    log.write_text("", encoding="utf-8")
    own = SimpleNamespace(url=url, pid=os.getpid(), log_path=str(log))
    with _ui_session(request, ui_browser, own, ui_artifacts) as session:
        session.fx, session.anywhere = fx, anywhere
        prefill = _refused(session, "/api/setup").get("prefill") or {}
        session.page.route("**/api/setup", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"prefs": prefill})))
        session.searches, session.lists = [], []

        def keep(sent) -> None:  # noqa: ANN001
            parts = urlsplit(sent.url)
            if sent.method == "GET" and parts.path == "/api/search":
                session.searches.append(parse_qs(parts.query))
            elif sent.method == "GET" and parts.path == "/api/postings":
                session.lists.append(parse_qs(parts.query))

        session.page.on("request", keep)
        session.profiles = {item["profile_id"]: item for item in session.server_json("/api/profiles")["profiles"]}
        session.default_label = session.profiles[fx.default_profile_id]["label"]
        # The lists exist before the page asks (the first read builds the read model): every posting of the homes.
        first = session.server_json("/api/postings?limit=50&us_only=0&collapse=0")
        assert first["counts"]["matched"] == POSTINGS, first["counts"]
        yield session


def _refused(ui, path: str) -> dict:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(ui.server.url + path, timeout=60) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        return json.loads(error.read()).get("error") or {}


def _open_jobs(ui, route: str = "/#/jobs") -> None:
    ui.goto(route)
    ui.wait_for_jobs_list()
    ui.settle()


def _open_search(ui) -> None:
    """The Search tab, once it knows the setup's US-only default (the page's list read says it)."""

    ui.goto("/#/jobs/search")
    ui.page.locator(PANEL).wait_for()
    ui.page.wait_for_function("(box) => document.querySelector(box).checked", arg=SEARCH_BOX)
    ui.settle()


def _help(ui, name: str) -> str:
    """The whole text behind the "?" named `name`: opened, read, closed again."""

    tip = ui.page.locator(f'[data-testid="help-tip"][data-help="{name}"]')
    tip.locator('[data-action="help-tip"]').click()
    said = tip.locator('[data-role="help-tip-body"]').text_content()
    ui.page.keyboard.press("Escape")
    tip.locator('[data-role="help-tip-body"]').wait_for(state="detached")
    return said


def _search(ui, title: str) -> None:
    ui.page.locator(f'{PANEL} [data-testid="free-search-title"]').fill(title)
    ui.page.locator(f'{PANEL} [data-testid="free-search-submit"]').click()
    ui.page.locator(TOTAL).wait_for()
    ui.settle()


def _total(ui, expected: int) -> None:
    ui.page.locator(f'{TOTAL}[data-total="{expected}"]').wait_for()
    ui.settle()


def _search_rows(ui) -> dict[str, dict]:
    rows = ui.page.evaluate(
        """(selector) => Array.from(document.querySelectorAll(selector)).map((row) => ({
            job: row.getAttribute('data-job'),
            copies: Number(row.getAttribute('data-copies')),
            title: row.querySelector('.posting-title').textContent,
            location: row.querySelector('[data-role="search-location"]').textContent,
            tag: (row.querySelector('[data-role="search-copies"]') || {textContent: null}).textContent,
            labels: Array.from(row.querySelectorAll('[data-testid="search-label"]')).map((label) => label.textContent),
            actions: Array.from(row.querySelectorAll('.posting-actions a, .posting-actions button')).map((item) => item.textContent),
        }))""",
        ROW,
    )
    return {row["job"]: row for row in rows}


def _list_rows(ui) -> list[dict]:
    return ui.page.evaluate(
        """(selector) => Array.from(document.querySelectorAll(selector)).map((row) => ({
            copies: Number(row.getAttribute('data-copies')),
            title: row.querySelector('.posting-title').textContent,
            detail: (row.querySelector('[data-role="job-detail"]') || {textContent: ''}).textContent,
            tag: (row.querySelector('[data-role="job-copies"]') || {textContent: null}).textContent,
        }))""",
        LIST_ROW,
    )


def test_us_only_and_the_row_of_copies_in_search_all_jobs(page) -> None:  # noqa: ANN001
    ui, fx = page, page.fx
    fx.base.model.assess_prompts.clear()
    _open_search(ui)
    # Before anything is typed: the box is ticked (this setup's countries hold the US); one short line under it, and
    # the rule behind its "?".
    box = ui.page.locator(SEARCH_BOX)
    assert box.is_checked()
    assert ui.page.locator(f'{PANEL} [data-role="free-search-rules"] [data-role="us-only-short"]').text_content() == "Hides postings clearly outside the US; unclear places stay listed."
    rules = _help(ui, "search-us-only")
    assert "US only hides a posting only when every place its location names is clearly outside the US." in rules
    assert 'is labelled "unclear location"' in rules and "still applies with Show all until you turn it off" in rules

    # Untouched, the box sends nothing: the server applies the default. The jobs not clearly abroad: five rows.
    _search(ui, "staff engineer, staff ai engineer")
    assert all("us_only" not in query for query in ui.searches), ui.searches
    _total(ui, NOT_ABROAD)
    # One row per job: the short line under the count, the rule behind its "?".
    assert ui.page.locator(f'{PANEL} [data-role="copies-short"]').text_content() == "One row per job: the same job posted in several places is one row."
    rules = _help(ui, "search-copies")
    assert "the same description posted more than once (only the location differs) is one row" in rules
    assert "its US posting when it has one, else the earliest posted" in rules
    us = _search_rows(ui)
    assert set(us) == {job_url(SLUG, 9), job_url(SLUG, 10), job_url(SLUG, 13), job_url(OTHER, 1), job_url(WATCHED, 3)}
    assert all("Estonia" not in row["location"] for row in us.values())
    # Two US cities are one row: its canonical job (the earlier posted), with both places and how many postings.
    assert us[job_url(SLUG, 9)]["copies"] == 2 and us[job_url(SLUG, 9)]["location"] == "Austin, TX; Remote - United States" and us[job_url(SLUG, 9)]["tag"] == "2 postings"
    # "Remote" alone: nobody says where. It is listed, and labelled; no placed row has the label.
    assert us[job_url(SLUG, 13)]["location"] == "Remote" and us[job_url(SLUG, 13)]["labels"][0] == "unclear location"
    assert [job for job, row in us.items() if "unclear location" in row["labels"]] == [job_url(SLUG, 13)]

    # Show all drops the window and the work mode, NOT US only.
    ui.searches.clear()
    ui.page.locator(f'{PANEL} [data-testid="free-search-show-all"]').check()
    ui.page.locator(f'{TOTAL}').wait_for()
    ui.settle()
    assert ui.searches[0].get("all") == ["1"] and "us_only" not in ui.searches[0]
    assert box.is_checked() and len(_search_rows(ui)) == NOT_ABROAD
    assert ui.page.locator(TOTAL).text_content().endswith("(US only, any date).")

    # Unticked: every country, and the seven postings abroad are ONE row.
    ui.searches.clear()
    box.uncheck()
    _total(ui, JOBS)
    assert ui.searches[0].get("us_only") == ["0"] and ui.searches[0].get("all") == ["1"]
    assert ui.page.locator(TOTAL).text_content().endswith("(any place, any date).")
    rows = _search_rows(ui)
    one = rows[job_url(SLUG, 7)]  # the canonical job: no US posting, so the earliest posted (Poland)
    assert len(rows) == JOBS and one["copies"] == 7 and one["title"] == "Staff Engineer" and one["location"] == ABROAD and one["tag"] == "7 postings"
    assert not any(job_url(SLUG, n) in rows for n in ONE_JOB[:-1]), "the other six are not rows"
    # Never merged: the same title at another company, with another description, or written another way.
    assert rows[job_url(OTHER, 1)]["copies"] == 1 and rows[job_url(SLUG, 12)]["copies"] == 1 and rows[job_url(SLUG, 11)]["copies"] == 1
    assert not any("unclear location" in row["labels"] for row in rows.values()), "the label is said only where US only kept the row"

    # Mark applied on the row of copies: ONE request, for the row's posting.
    row = ui.page.locator(f'{ROW}[data-job="{job_url(SLUG, 7)}"]')
    ui.step("apply")
    with ui.page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == "/api/applications") as recorded:
        row.locator('[data-action="search-apply"]').click()
    assert recorded.value.status == 201 and recorded.value.request.post_data_json == {"job_identity": job_url(SLUG, 7), "event_kind": "applied"}
    row.locator('[data-testid="search-label"][data-kind="application"]').wait_for()
    ui.settle()
    assert row.locator('[data-action="search-apply"]').count() == 0 and ui.writes_after("apply") == ["POST /api/applications"]
    # The END outcome, on the server: the row is applied (one application, on the row's posting), still one row.
    again = ui.server_json("/api/search?title=staff+engineer&all=1&us_only=0&count=1")
    served = {item["job_identity"]: item for item in again["postings"]["rows"]}
    assert served[job_url(SLUG, 7)]["application"]["status"] == "applied" and served[job_url(SLUG, 7)]["copies"] == 7
    assert sum(1 for item in again["postings"]["rows"] if item["application"]) == 1

    # Assess on a row of three copies: asked once, ONE model call, as the default profile, for the row's posting.
    job = job_url(WATCHED, 3)
    row = ui.page.locator(f'{ROW}[data-job="{job}"]')
    assert rows[job]["copies"] == 3 and rows[job]["location"] == "Denver, CO; Austin, TX; Remote - United States"
    ui.step("assess")
    row.locator('[data-action="search-assess"]').click()
    row.locator('[data-role="search-assess-ask"]').wait_for()
    assert ui.writes_after("assess") == [] and fx.base.model.assess_prompts == []
    with ui.page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == "/api/assess", timeout=60_000) as made:
        row.locator('[data-action="search-assess-confirm"]').click()
    assert made.value.status in (200, 201), made.value.text()
    assert made.value.request.post_data_json == {"job": {"job_url": job}, "resume": {"profile_id": fx.default_profile_id}, "origin": "job_page"}
    row.locator('[data-testid="search-label"][data-kind="assessment"]').wait_for()
    ui.settle()
    assert len(fx.base.model.assess_prompts) == 1, "one model call for the job, not one per copy"
    assert row.locator('[data-action="search-assess"]').count() == 0 and ui.writes_after("assess") == ["POST /api/assess"]
    ui.assert_clean()


def test_us_only_and_the_row_of_copies_on_the_jobs_list(page) -> None:  # noqa: ANN001
    ui = page
    _open_jobs(ui)
    box = ui.page.locator(LIST_BOX)
    # This US setup: ticked, untouched (the request says nothing of it), and the list is the jobs not clearly abroad.
    assert box.is_checked() and all("us_only" not in query for query in ui.lists), ui.lists
    rows = _list_rows(ui)
    assert sorted((row["title"], row["copies"]) for row in rows) == [
        ("Staff AI Engineer", 3), ("Staff Engineer", 1), ("Staff Engineer, Payments", 2), ("Staff Engineer, Platform", 1), ("Staff Engineer, Search", 1),
    ]
    assert not any("Estonia" in row["detail"] for row in rows)
    assert ui.page.locator('[data-role="jobs-list-rules"] [data-role="us-only-short"]').text_content() == "Hides postings clearly outside the US; unclear places stay listed."
    assert "US only hides a posting only when every place its location names is clearly outside the US." in _help(ui, "list-us-only")
    assert "the same description posted more than once (only the location differs) is one row" in _help(ui, "list-copies")
    assert ui.page.locator('[data-role="us-only-left-out"]').text_content().strip() == "9 outside the US are left out."
    assert ui.page.locator('[data-role="postings-count"]').text_content().startswith("Showing 1-5 of 5 postings"), "the count is rows"
    # The posting that says "Remote" alone is listed, with the label; no other row has it.
    unclear = ui.page.locator(f'{LIST_ROW} [data-testid="unclear-location"]')
    assert unclear.count() == 1 and unclear.text_content() == "unclear location"
    assert ui.page.locator(f'{LIST_ROW}:has([data-testid="unclear-location"]) .posting-title').text_content() == "Staff Engineer, Platform"

    # Unticked: the address says so, the request says us_only=0, and the seven postings abroad are ONE more row.
    ui.lists.clear()
    box.uncheck()
    ui.page.wait_for_function("(arg) => document.querySelectorAll(arg.row).length === arg.count", arg={"row": LIST_ROW, "count": JOBS})
    ui.settle()
    assert "us=0" in ui.page.evaluate("() => window.location.hash") and ui.lists[-1].get("us_only") == ["0"]
    listed = _list_rows(ui)
    (one,) = [row for row in listed if row["copies"] == 7]
    assert one["title"] == "Staff Engineer" and ABROAD in one["detail"] and one["tag"] == "7 postings"
    # Never merged: the same title with another description, at another company, or written another way.
    assert sorted(row["title"] for row in listed if row["copies"] == 1 and row["title"].casefold().rstrip(".") in ("staff engineer", "staff  engineer")) == ["STAFF  Engineer.", "Staff Engineer", "Staff Engineer"]
    assert ui.page.locator('[data-role="us-only-left-out"]').count() == 0 and not box.is_checked() and unclear.count() == 0
    assert ui.page.locator('[data-role="postings-count"]').text_content().startswith("Showing 1-8 of 8 postings"), "the count is rows"

    # "Assess these" by the filter asks about the JOBS the list shows, and says what the box says.
    ui.step("ask")
    with ui.page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == "/api/postings/assess", timeout=60_000) as asked:
        ui.page.locator('[data-testid="assess-these"]').click()
    assert asked.value.status == 200 and asked.value.request.post_data_json == {"us_only": False}
    answer = asked.value.json()
    assert answer["status"] == "ask" and answer["counts"]["selected"] == JOBS, "eight jobs, not seventeen postings"
    ui.page.keyboard.press("Escape")
    ui.settle()

    # A reload keeps the view's switch (it is in the address), and no profile was changed by it.
    ui.lists.clear()
    ui.page.reload()
    ui.wait_for_jobs_list()
    ui.page.wait_for_function("(arg) => document.querySelectorAll(arg.row).length === arg.count", arg={"row": LIST_ROW, "count": JOBS})
    ui.settle()
    assert not ui.page.locator(LIST_BOX).is_checked() and ui.lists[-1].get("us_only") == ["0"]
    anywhere = next(item for item in ui.server_json("/api/profiles")["profiles"] if item["profile_id"] == ui.anywhere)
    assert not (anywhere.get("search_settings") or {}).get("countries"), "the profile still has no country setting"

    # Ticked again: the jobs not clearly abroad.
    ui.page.locator(LIST_BOX).check()
    ui.page.wait_for_function("(arg) => document.querySelectorAll(arg.row).length === arg.count", arg={"row": LIST_ROW, "count": NOT_ABROAD})
    ui.settle()
    assert "us=1" in ui.page.evaluate("() => window.location.hash") and ui.lists[-1].get("us_only") == ["1"]

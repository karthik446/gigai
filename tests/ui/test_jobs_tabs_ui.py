"""0.1.11.8 N3: the Jobs page has two tabs, "Your jobs" and "Search", and one search box at a time.

Real Chromium against a REAL server of its own (`GET /api/search`, `GET /api/postings`, the built search index),
nothing stubbed but `GET /api/setup` (the fixture home was never through the setup interview). The home is synthetic
(`tests/support/posting_fixtures.py`: two active profiles, made-up Lever boards, a scripted model): no request leaves
the machine and nothing of the person's is read.

The postings: at `acme-health` (watched) five "Staff AI Engineer", in both profiles' lists; at `quiet-harbor` (not
watched, so in no list) six more, which only the search finds.

Pinned:

- `#/jobs` is "Your jobs": the list, its filters and its own search box, and NO "Search all jobs" box; the "Search"
  tab (`#/jobs/search`) is Search all jobs alone, without the list or its box. A click on a tab changes the address;
  role=tablist / tab / tabpanel with aria-selected; the arrow keys move between the two;
- a reload on Search stays on Search (the box is there, the results are gone: they live in memory); the browser's
  Back and Forward switch tabs; `#/jobs?us=0` still opens the list with its switch, and "Your jobs" leads back to
  the list as it was left;
- a search result opens its job page, and both ways back (the browser's Back, the page's own "← Search") return to
  the Search tab with the results still there and no second search; a job opened from the list goes back to the list;
- each long help text is one short line and a "?": it opens and closes (a second press, Escape with the focus back
  on the button, a click elsewhere), by the keyboard too, and holds the whole rule; closed, the rule is not on the
  page;
- on a phone-wide screen the two tabs are on one line inside the screen.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlsplit

import pytest

from tests.support.pipeline_fixtures import set_pipeline_enabled
from tests.support.posting_fixtures import TITLE_BOTH, build_postings_fixture, job_url, lever_job
from tests.ui import support
from tests.ui.conftest import _ui_session

pytestmark = pytest.mark.ui
UI_ORDER = 66  # a server and a home of its own: nothing of the shared home is read or written

WATCHED, UNWATCHED = "acme-health", "quiet-harbor"
HELD, UNHELD = 5, 6
TYPED = "staff ai engineer"

TABS = '[data-testid="jobs-tabs"]'
TAB_YOURS = '[data-testid="jobs-tab-yours"]'
TAB_SEARCH = '[data-testid="jobs-tab-search"]'
PANEL = '[data-testid="free-search"]'
SEARCH_BOX = f'{PANEL} [data-testid="free-search-title"]'
LIST_BOX = "#jobs-filter-search"
LIST = '[data-testid="jobs-list"]'
ROW = f'{PANEL} [data-testid="search-row"]'
TOTAL = f'{PANEL} [data-testid="free-search-total"]'
US_ONLY_RULE = "US only hides a posting only when every place its location names is clearly outside the US."
COPIES_RULE = "the same description posted more than once (only the location differs) is one row"


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
    set_pipeline_enabled(fx.home_root, fx.target, False)
    now = datetime.now(UTC)
    seen = now - timedelta(minutes=30)
    fx.seed(WATCHED, [lever_job(WATCHED, n, title=TITLE_BOTH, created=now - timedelta(hours=n)) for n in range(1, HELD + 1)], seen_at=seen)
    fx.seed(UNWATCHED, [lever_job(UNWATCHED, n, title=TITLE_BOTH, created=now - timedelta(hours=10 + n)) for n in range(1, UNHELD + 1)], seen_at=seen, watch=False)
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
        # The lists of the two profiles exist before the page asks (the first read builds the read model).
        assert len(session.server_json("/api/postings?limit=50")["postings"]["rows"]) == HELD
        yield session


def _refused(ui, path: str) -> dict:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(ui.server.url + path, timeout=60) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        return json.loads(error.read()).get("error") or {}


def _fragment(ui) -> str:
    return urlsplit(ui.page.url).fragment


def _selected(ui) -> dict[str, str | None]:
    return {name: ui.page.locator(tab).get_attribute("aria-selected") for name, tab in (("yours", TAB_YOURS), ("search", TAB_SEARCH))}


def _on_yours(ui) -> None:
    """The "Your jobs" tab is shown: the list and its own box, and no Search all jobs box."""

    ui.page.locator(LIST).wait_for()
    ui.wait_for_jobs_list()
    ui.settle()
    assert _selected(ui) == {"yours": "true", "search": "false"}
    assert ui.page.locator(LIST_BOX).is_visible() and ui.page.locator(PANEL).count() == 0 and ui.page.locator(SEARCH_BOX).count() == 0


def _on_search(ui) -> None:
    """The "Search" tab is shown: Search all jobs, and neither the list nor its box."""

    ui.page.locator(PANEL).wait_for()
    ui.settle()
    assert _selected(ui) == {"yours": "false", "search": "true"}
    assert ui.page.locator(SEARCH_BOX).is_visible() and ui.page.locator(LIST_BOX).count() == 0 and ui.page.locator(LIST).count() == 0
    assert _fragment(ui).startswith("/jobs/search")


def _search(ui, title: str) -> None:
    ui.page.locator(SEARCH_BOX).fill(title)
    ui.page.locator(f'{PANEL} [data-testid="free-search-submit"]').click()
    ui.page.locator(TOTAL).wait_for()
    ui.settle()


def test_two_tabs_one_search_box_and_the_tab_is_the_address(page) -> None:  # noqa: ANN001
    ui = page
    # The default route: Your jobs, with the list's own box and no Search all jobs box.
    ui.goto("/#/jobs")
    _on_yours(ui)
    tabs = ui.page.locator(TABS)
    assert tabs.get_attribute("role") == "tablist" and tabs.locator('[role="tab"]').all_text_contents() == ["Your jobs", "Search"]
    assert ui.page.locator('input[type="search"]:visible').count() == 1, "one search box at a time"
    panel = ui.page.locator('[role="tabpanel"]')
    assert panel.count() == 1 and panel.get_attribute("aria-labelledby") == ui.page.locator(TAB_YOURS).get_attribute("id")
    assert ui.page.locator(TAB_YOURS).get_attribute("aria-controls") == panel.get_attribute("id")

    # A click on Search: the address says so, and the page is Search all jobs alone.
    ui.page.locator(TAB_SEARCH).click()
    _on_search(ui)
    assert _fragment(ui) == "/jobs/search"
    panel = ui.page.locator('[role="tabpanel"]')
    assert panel.count() == 1 and panel.get_attribute("aria-labelledby") == ui.page.locator(TAB_SEARCH).get_attribute("id")
    assert ui.page.locator(f"{PANEL} input[type='search']:visible").count() == ui.page.locator('input[type="search"]:visible').count() == 3, "the title, company and location of the one search"
    assert ui.page.locator(f'{PANEL} [data-testid="free-search-us-only"]').is_checked(), "the setup's default, read by the list this page keeps"
    assert ui.page.locator(f'{PANEL} [data-testid="save-as-profile"]').count() == 0, "Save as a profile comes with the results"

    # Searched, then reloaded: still the Search tab, the box is there, the results are gone (they live in memory).
    _search(ui, TYPED)
    assert int(ui.page.locator(TOTAL).get_attribute("data-total")) == HELD + UNHELD and ui.page.locator(ROW).count() == HELD + UNHELD
    assert ui.page.locator(f'{PANEL} [data-testid="save-as-profile"]').is_visible(), "Save as a profile stays on the Search tab"
    ui.reload()
    _on_search(ui)
    assert _fragment(ui) == "/jobs/search" and ui.page.locator(ROW).count() == 0 and ui.page.locator(TOTAL).count() == 0
    assert ui.page.locator(SEARCH_BOX).input_value() == ""
    ui.page.wait_for_function("(box) => document.querySelector(box).checked", arg=f'{PANEL} [data-testid="free-search-us-only"]')

    # Back and Forward switch tabs.
    ui.page.locator(TAB_YOURS).click()
    _on_yours(ui)
    assert _fragment(ui).split("?")[0] == "/jobs"
    ui.page.go_back()
    _on_search(ui)
    ui.page.go_forward()
    _on_yours(ui)
    ui.page.go_back()
    _on_search(ui)

    # The keyboard: the selected tab alone is in the Tab order; an arrow moves to the other tab and opens it.
    assert ui.page.locator(TAB_SEARCH).get_attribute("tabindex") == "0" and ui.page.locator(TAB_YOURS).get_attribute("tabindex") == "-1"
    ui.page.locator(TAB_SEARCH).focus()
    ui.page.keyboard.press("ArrowLeft")
    _on_yours(ui)
    assert ui.page.evaluate("() => document.activeElement.getAttribute('data-testid')") == "jobs-tab-yours"
    ui.page.keyboard.press("ArrowRight")
    _on_search(ui)
    assert ui.page.evaluate("() => document.activeElement.getAttribute('data-testid')") == "jobs-tab-search"

    # The list's own address still works, and "Your jobs" leads back to the list as it was left.
    ui.goto("/#/jobs?us=0")
    _on_yours(ui)
    assert not ui.page.locator('[data-testid="jobs-us-only"]').is_checked()
    ui.page.locator(TAB_SEARCH).click()
    _on_search(ui)
    assert ui.page.locator(TAB_YOURS).get_attribute("href") == "#/jobs?us=0"
    ui.page.locator(TAB_YOURS).click()
    _on_yours(ui)
    assert _fragment(ui) == "/jobs?us=0" and not ui.page.locator('[data-testid="jobs-us-only"]').is_checked()

    # A phone-wide screen: the two tabs are on one line, inside the screen.
    ui.page.set_viewport_size({"width": 390, "height": 800})
    boxes = [ui.page.locator(tab).bounding_box() for tab in (TAB_YOURS, TAB_SEARCH)]
    assert all(box and box["x"] >= 0 and box["x"] + box["width"] <= 390 for box in boxes), boxes
    assert abs(boxes[0]["y"] - boxes[1]["y"]) < 1 and boxes[0]["x"] + boxes[0]["width"] <= boxes[1]["x"] + 1, boxes
    ui.assert_clean()


def test_a_search_result_opens_and_back_returns_to_search_with_its_results(page) -> None:  # noqa: ANN001
    ui = page
    ui.goto("/#/jobs/search")
    _on_search(ui)
    _search(ui, TYPED)
    searched = len(ui.searches)
    jobs = ui.page.evaluate("(row) => Array.from(document.querySelectorAll(row)).map((item) => item.getAttribute('data-job'))", ROW)
    assert len(jobs) == HELD + UNHELD

    def opened(job: str) -> None:
        ui.page.locator(f'{ROW}[data-job="{job}"] [data-action="open-search-job"]').click()
        ui.wait_for_job_page()
        ui.settle()
        assert _fragment(ui) == "/jobs/" + quote(job, safe=""), "a job page's address is the job's, not the Search tab's"

    def back_on_search() -> None:
        ui.page.locator(TOTAL).wait_for()  # the results are kept: no second search
        _on_search(ui)
        assert ui.page.evaluate("(row) => Array.from(document.querySelectorAll(row)).map((item) => item.getAttribute('data-job'))", ROW) == jobs
        assert ui.page.locator(SEARCH_BOX).input_value() == TYPED and len(ui.searches) == searched, "coming back searched again"

    # A posting no list holds, then the browser's Back.
    opened(job_url(UNWATCHED, 2))
    ui.page.go_back()
    back_on_search()
    # A posting both lists hold, then the page's own way back: it names the tab the job was opened from.
    opened(job_url(WATCHED, 1))
    link = ui.page.locator(".back-link")
    assert link.text_content().strip() == "← Search" and link.get_attribute("href") == "#/jobs/search"
    link.click()
    back_on_search()

    # A job opened from the list goes back to the list.
    ui.page.locator(TAB_YOURS).click()
    _on_yours(ui)
    ui.page.locator(f'{LIST} [data-action="open-job"]').first.click()
    ui.wait_for_job_page()
    ui.settle()
    link = ui.page.locator(".back-link")
    assert link.text_content().strip() == "← Jobs" and link.get_attribute("href") == "#/jobs"
    link.click()
    _on_yours(ui)
    ui.assert_clean()


def _tip(ui, name: str):  # noqa: ANN202
    return ui.page.locator(f'[data-testid="help-tip"][data-help="{name}"]')


def _open(tip) -> str:  # noqa: ANN001
    """The tip is open: its text, with the button saying so and naming the box it opened."""

    body = tip.locator('[data-role="help-tip-body"]')
    body.wait_for()
    button = tip.locator('[data-action="help-tip"]')
    assert button.get_attribute("aria-expanded") == "true" and button.get_attribute("aria-controls") == body.get_attribute("id")
    return body.text_content()


def _closed(tip) -> None:  # noqa: ANN001
    tip.locator('[data-role="help-tip-body"]').wait_for(state="detached")
    button = tip.locator('[data-action="help-tip"]')
    assert button.get_attribute("aria-expanded") == "false" and button.get_attribute("aria-controls") is None


def test_the_help_is_one_short_line_and_a_question_mark_that_opens_and_closes(page) -> None:  # noqa: ANN001
    ui = page
    ui.goto("/#/jobs/search")
    _on_search(ui)
    # The short lines; the rules themselves are not on the page until a "?" is opened.
    assert ui.page.locator(f'{PANEL} [data-role="search-short"]').text_content() == "Searches every stored posting, newest first. A comma separates titles."
    assert ui.page.locator(f'{PANEL} [data-role="us-only-short"]').text_content() == "Hides postings clearly outside the US; unclear places stay listed."
    text = ui.page.locator(PANEL).text_content()
    assert US_ONLY_RULE not in text and "every word of a typed title" not in text and "still applies with Show all" not in text

    # A click opens it, a second click closes it.
    rules = _tip(ui, "search-rules")
    button = rules.locator('[data-action="help-tip"]')
    assert button.get_attribute("aria-label") == "About this search"
    _closed(rules)
    button.click()
    said = _open(rules)
    for words in ("every word of a typed title must be in the posting's title", "whole words", "until you turn on Show all", "Not ranked. Save as a role to rank.", "a search stores nothing"):
        assert words in said, words
    button.click()
    _closed(rules)

    # By the keyboard: Enter opens it, Escape closes it and the focus is back on the button.
    us_only = _tip(ui, "search-us-only")
    us_only.locator('[data-action="help-tip"]').focus()
    ui.page.keyboard.press("Enter")
    said = _open(us_only)
    assert US_ONLY_RULE in said and 'is labelled "unclear location"' in said and "still applies with Show all until you turn it off" in said
    ui.page.keyboard.press("Escape")
    _closed(us_only)
    assert ui.page.evaluate("() => document.activeElement.getAttribute('data-action')") == "help-tip"
    # A click elsewhere closes it; opening it ticked or unticked nothing.
    us_only.locator('[data-action="help-tip"]').click()
    _open(us_only)
    ui.page.locator(SEARCH_BOX).click()
    _closed(us_only)
    assert ui.page.locator(f'{PANEL} [data-testid="free-search-us-only"]').is_checked()

    # One row per job: a short line under the results' count, the rule behind its "?".
    assert ui.page.locator(f'{PANEL} [data-role="copies-short"]').count() == 0
    _search(ui, TYPED)
    assert ui.page.locator(f'{PANEL} [data-role="copies-short"]').text_content() == "One row per job: the same job posted in several places is one row."
    copies = _tip(ui, "search-copies")
    copies.locator('[data-action="help-tip"]').click()
    said = _open(copies)
    assert COPIES_RULE in said and "its US posting when it has one, else the earliest posted" in said and "The counts are rows." in said
    ui.page.keyboard.press("Escape")
    _closed(copies)

    # Your jobs: the same two short lines under its filters, each with its "?".
    ui.page.locator(TAB_YOURS).click()
    _on_yours(ui)
    line = ui.page.locator('[data-role="jobs-list-rules"]')
    assert line.locator('[data-role="us-only-short"]').text_content() == "Hides postings clearly outside the US; unclear places stay listed."
    assert US_ONLY_RULE not in line.text_content() and COPIES_RULE not in line.text_content()
    us_only = _tip(ui, "list-us-only")
    us_only.locator('[data-action="help-tip"]').click()
    assert US_ONLY_RULE in _open(us_only)
    # Another "?" pressed: the first closes.
    copies = _tip(ui, "list-copies")
    copies.locator('[data-action="help-tip"]').click()
    said = _open(copies)
    _closed(us_only)
    assert COPIES_RULE in said and "its US posting when it has one, else the earliest posted" in said
    copies.locator('[data-action="help-tip"]').click()
    _closed(copies)
    ui.assert_clean()

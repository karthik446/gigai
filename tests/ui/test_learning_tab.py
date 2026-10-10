"""0.1.11.10 Part A slice 1: the Learning pathways tab (#/learning), the READ side (the list).

A real Chromium against a real Scout server on a small synthetic home (``tests.support.scout_profile_fixtures.
build_gig_with_resume``: no model, no pipeline, nothing of this suite's shared home). Pathways are SYNTHETIC: one
``done`` course imported with ``learning_store.import_course`` (the same helper ``tests/api_e2e/test_learning_journey.
py`` uses, on a course folder built in ``tmp_path`` by ``make_course``), one ``queued`` and one ``failed`` request.

Pinned:
- the tab is a top-level nav link next to Jobs/Assessments/Applications/Past runs and opens #/learning;
- the request form (Role input, "Generate course") is always on the page (Part B, G6: see test_learning_request.py
  for the dialog and a full generation; this file stays to the list);
- each row shows the role as typed, the requested date, its status in plain text (Ready / Queued / Failed), its cost
  line (or "cost not recorded" for the queued one, which has none), lessons and size for the done one, and a source
  line: "Imported from <imported_from>" for an imported row, "Requested" for a requested one. A failed row's status
  is "Failed" with the error text on its own line below it (plain text, no icons);
- "Open course" is on the done row only, opens the course's url in a NEW TAB (target=_blank, rel=noopener), and that
  tab loads the real course page (200, its title);
- an empty home (no pathways at all) shows "No courses yet" and the CLI import line, no rows;
- `ui.assert_clean()`: no console error, no HTTP >= 400.
"""

from __future__ import annotations

import dataclasses
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from gigai.scout import learning_store

from tests.behaviors.scout_learning.test_learning_store import make_course
from tests.support.scout_profile_fixtures import build_gig_with_resume
from tests.ui.conftest import _ui_session

pytestmark = pytest.mark.ui

NAV = '[data-nav="learning"]'
PANEL = '[data-role="learning"]'
EMPTY = '[data-role="learning-empty"]'
ROW = '[data-testid="learning-row"]'
OPEN = '[data-action="open-course"]'
SOURCE = '[data-role="learning-source"]'
ERROR = '[data-role="learning-error"]'
ROLE_INPUT = "#learning-role"
GENERATE = '[data-action="learning-generate"]'
SHOT_DIR = Path("/Users/kar/orca/workspaces/gigai/orchestrator/research/role-packet/out/ui-shots")

EMPTY_NOTE = "No courses yet"
IMPORT_COMMAND = 'gigai scout learning import <folder> --role "<role>"'


@pytest.fixture
def learning_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    """A real server on a small synthetic gig (no model, no pipeline, no postings)."""

    from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend, serve

    monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")
    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
    gig = build_gig_with_resume(tmp_path)
    server = serve(backend=ScoutFindJobsBackend(home_root=gig.home_root, target=gig.target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(home=gig.home_root, target=gig.target, tmp=tmp_path, url=f"http://127.0.0.1:{server.server_address[1]}", pid=0, log_path=None)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)


@pytest.fixture
def learning_ui(request: pytest.FixtureRequest, ui_browser, learning_home: SimpleNamespace, ui_artifacts: Path):
    with _ui_session(request, ui_browser, learning_home, ui_artifacts) as session:
        # GET /api/setup fulfilled from the 404's own prefill: this fixture home was never through the interview.
        import urllib.error
        import urllib.request

        try:
            with urllib.request.urlopen(f"{learning_home.url}/api/setup", timeout=60) as response:
                prefill = json.loads(response.read()).get("prefill") or {}
        except urllib.error.HTTPError as error:
            prefill = (json.loads(error.read()).get("error") or {}).get("prefill") or {}
        session.page.route(
            "**/api/setup",
            lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"prefs": prefill})),
        )
        yield session


def _text(ui, selector: str) -> str:
    return " ".join((ui.page.locator(selector).first.text_content() or "").split())


def _seed_pathways(home: SimpleNamespace) -> dict[str, str]:
    """One done (imported), one queued, one failed pathway; returns their ids."""

    cost = {"cli_model_calls": 43, "worker_minutes": 45, "web_fetches": 430, "web_searches": 12, "note": None}
    source = make_course(home.tmp / "mlops-course", lessons=4)
    done = learning_store.import_course(home.home, home.target, source, "MLOps engineer", cost).pathway
    queued = learning_store.create_request(home.home, home.target, "Forward deployed engineer")
    failed = learning_store.create_request(home.home, home.target, "Product manager")
    with learning_store.write_lock(home.home, home.target):
        failed = learning_store.write_pathway(
            home.home, home.target, dataclasses.replace(failed, status="failed", error="the model target was not available", revision=2),
        )
    return {"done": done.id, "queued": queued.id, "failed": failed.id}


def test_the_tab_is_a_top_level_nav_link_next_to_the_others(learning_ui) -> None:
    ui = learning_ui
    ui.goto("/#/jobs")
    ui.settle()
    assert ui.page.locator(NAV).is_visible()
    assert _text(ui, NAV) == "Learning pathways"

    ui.page.locator(NAV).click()
    ui.page.wait_for_url("**/#/learning")
    ui.page.locator(PANEL).wait_for()
    assert ui.page.locator(NAV).get_attribute("aria-current") == "page"
    assert ui.page.locator(f'{PANEL} h2').first.text_content().strip() == "Learning pathways"
    ui.assert_clean()


def test_an_empty_home_says_no_courses_yet_and_how_to_import_one(learning_ui) -> None:
    ui = learning_ui
    ui.goto("/#/learning")
    ui.page.locator(EMPTY).wait_for()
    assert _text(ui, f"{EMPTY} p:first-of-type") == EMPTY_NOTE
    assert _text(ui, f"{EMPTY} p:last-of-type") == f"To import a finished course: {IMPORT_COMMAND}"
    assert ui.page.locator(ROW).count() == 0
    # Part B (G6): the request form is always there, even with no courses yet.
    assert ui.page.locator(ROLE_INPUT).is_visible()
    assert ui.page.locator(GENERATE).is_disabled()
    ui.assert_clean()


def test_rows_show_role_date_status_cost_lessons_size_and_source_and_open_course_opens_the_real_page(learning_ui, learning_home: SimpleNamespace) -> None:
    ui = learning_ui
    ids = _seed_pathways(learning_home)
    ui.goto("/#/learning")
    ui.page.locator(ROW).first.wait_for()
    assert ui.page.locator(ROW).count() == 3

    done_row = ui.page.locator(f'{ROW}[data-pathway-id="{ids["done"]}"]')
    assert "MLOps engineer" in (done_row.locator("strong").first.text_content() or "")
    assert _text(ui, f'{ROW}[data-pathway-id="{ids["done"]}"] [data-role="learning-status"]') == "Ready"
    assert _text(ui, f'{ROW}[data-pathway-id="{ids["done"]}"] [data-role="learning-cost"]') == "43 model calls, 430 page fetches, 12 web searches, about 45 minutes"
    assert _text(ui, f'{ROW}[data-pathway-id="{ids["done"]}"] [data-role="learning-lessons"]') == "4 lessons, 0.0 MB"
    # 0.1.11.10 Part A slice 2: GET /api/learning/pathways' row now carries `source`/`imported_from` too
    # (find_jobs/api/learning.py's pathway_summary) -- an imported row's source line names the imported folder.
    assert _text(ui, f'{ROW}[data-pathway-id="{ids["done"]}"] {SOURCE}') == "Imported from mlops-course"
    assert done_row.locator(ERROR).count() == 0
    open_link = done_row.locator(OPEN)
    assert open_link.is_visible()
    assert open_link.get_attribute("target") == "_blank"
    assert open_link.get_attribute("rel") == "noopener"
    href = open_link.get_attribute("href")
    assert href == f"/learning/{ids['done']}/index.html"

    queued_row = ui.page.locator(f'{ROW}[data-pathway-id="{ids["queued"]}"]')
    assert _text(ui, f'{ROW}[data-pathway-id="{ids["queued"]}"] [data-role="learning-status"]') == "Queued"
    assert _text(ui, f'{ROW}[data-pathway-id="{ids["queued"]}"] [data-role="learning-cost"]') == "cost not recorded"
    assert queued_row.locator('[data-role="learning-lessons"]').count() == 0
    assert _text(ui, f'{ROW}[data-pathway-id="{ids["queued"]}"] {SOURCE}') == "Requested"
    assert queued_row.locator(ERROR).count() == 0
    assert queued_row.locator(OPEN).count() == 0

    # 0.1.11.10 Part A slice 2: the list row now carries `error` too; a failed row's status stays plain "Failed" and
    # the error text shows on its own line below it.
    failed_row = ui.page.locator(f'{ROW}[data-pathway-id="{ids["failed"]}"]')
    assert _text(ui, f'{ROW}[data-pathway-id="{ids["failed"]}"] [data-role="learning-status"]') == "Failed"
    assert _text(ui, f'{ROW}[data-pathway-id="{ids["failed"]}"] {SOURCE}') == "Requested"
    assert _text(ui, f'{ROW}[data-pathway-id="{ids["failed"]}"] {ERROR}') == "the model target was not available"
    assert failed_row.locator(OPEN).count() == 0

    with ui.page.context.expect_page() as new_page_info:
        open_link.click()
    new_page = new_page_info.value
    new_page.wait_for_load_state("load")
    assert new_page.url.endswith(f"/learning/{ids['done']}/index.html")
    assert new_page.title() == "Synthetic course"
    new_page.close()

    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    ui.page.screenshot(path=str(SHOT_DIR / "learning-tab.png"))
    ui.assert_clean()

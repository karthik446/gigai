"""0.1.11.10 shipped lessons, slices 4b and 5b: the one button of a shipped course's row, "Build my pathway".

A real Chromium against a real Scout server on a small synthetic home (the ``learning_home`` of
``test_learning_tab.py``). The shipped course is SYNTHETIC (the widget course with the catalog of
``test_learning_pathway_local.py``, written into ``tmp_path`` and standing for the package's shipped folder); the
stored postings are a synthetic board, 'widgetco'; the master resume is two made-up lines. The model is a
scripted port (the user's own target raises): no real model call.

The resume is always the reference, so the build always compares it with the course. Pinned:
- a shipped row whose page is not built has ONE button, "Build my pathway", and no link to a page that is not
  there; a row that is not a shipped course has no such button;
- pressing it asks the server what the build takes and sends (one POST, nothing sent to a model) and opens the
  consent dialog: what the page is, the consent line (it reads the resume and sends lesson titles and resume lines
  to the model), the estimate, and the buttons "Not now" and "Build". "Not now" closes it: no second POST, no model
  call, nothing built;
- "Build" starts the build (one POST, 202); while it runs the row says "Comparing your resume with the lessons…",
  the button cannot be pressed, and the tab polls; then the row says "Your pathway page was built on <day>", the
  button reads "Rebuild my pathway", and "Open your pathway page" opens the real page in a new tab with what the
  resume shows (served under the course's policy: no console error, no failed request);
- a page built by another GigAI version says why it is out of date in one quiet line, and a rebuild clears it;
- a build that does not finish (the model answers twice with something unusable) says a plain line on the row,
  with no HTTP error, and the row stays as it was.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import pytest

from gigai.scout import (
    learning_corpus, learning_course, learning_job, learning_pathway_build, learning_shipped_store, learning_store, master_store, proposal_execution,
)
from gigai.scout.find_jobs import free_search, search_index
from gigai.scout.find_jobs.search_index import IndexFilters
from gigai.scout.master_store import MasterRevision

from tests.behaviors.scout_learning.test_learning_corpus import seed_board
from tests.behaviors.scout_learning.test_learning_pathway_local import KEY, ROLE, WIDGETCO, make_catalog
from tests.behaviors.scout_learning.test_learning_render_shipped import shipped_course, shipped_paths, write_data
from tests.behaviors.scout_learning.test_learning_store import make_course
from tests.ui.test_learning_tab import LOCAL_PAGE, OPEN, ROW, learning_home, learning_ui  # noqa: F401 - the two are fixtures

pytestmark = [pytest.mark.ui, pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 9, 0), reason="needs FTS5")]

SHIPPED = f'{ROW}[data-pathway-id="{KEY}"]'
BUILD = f'{SHIPPED} [data-action="learning-build-page"]'
OPEN_PAGE = f'{SHIPPED} [data-action="open-pathway-page"]'
BUILDING = f'{SHIPPED} [data-role="learning-building"]'
STALE = f'{SHIPPED} [data-role="learning-local-page-stale"]'
BUILD_ERROR = f'{SHIPPED} [data-role="learning-build-error"]'
DIALOG = '[data-testid="learning-pathway-dialog"]'
APPROVE = '[data-action="learning-pathway-approve"]'
NOT_NOW = '[data-action="learning-pathway-cancel"]'
BUILD_PATH = f"/api/learning/shipped/{KEY}/pathway"
SHOT_DIR = Path("build/ui-artifacts/ui-shots")  # inside the repo's build folder, never in another project's

#: A made-up master: one experience line, a skills list.
MASTER = (("e-0a1b2c", "Ran the Widgetflow line for three widget plants"), ("s-112233", "Gearbox, Widgetflow, Sprocket"))
REVISION = MasterRevision(1, "rev-0a1b2c3d", None, "operator", "2026-10-01T00:00:00Z", "sha256:" + "ab" * 32)
ANSWER = {
    "markers": {
        "lesson-alpha/lesson-alpha-first": {"marker": "KNOWN", "evidence": {"id": "e-0a1b2c", "line": "Ran the Widgetflow line"}, "missing": None},
        "lesson-beta/lesson-beta-first": {
            "marker": "SOME", "evidence": {"id": "s-112233", "line": "Gearbox"}, "missing": "The line names Gearbox, not the first part of Beta.",
        },
    }
}


def _text(ui, selector: str) -> str:
    return " ".join((ui.page.locator(selector).first.text_content() or "").split())


class Port:
    """The scripted model of a build: ``answers`` in order, each held until ``gate`` is set."""

    def __init__(self) -> None:
        self.answers: list[Any] = []
        self.prompts: list[str] = []
        self.gate = threading.Event()
        self.gate.set()
        self.asked = threading.Event()

    def ask(self, prompt: str, *, items: int = 1, timeout_seconds: float | None = None) -> str:  # noqa: ARG002
        self.prompts.append(prompt)
        self.asked.set()
        assert self.gate.wait(60), "the test never let the answer go"
        found = self.answers.pop(0)
        return found if isinstance(found, str) else json.dumps(found)

    def invalid_output(self) -> None:
        return None


@pytest.fixture
def shipped_home(learning_home: SimpleNamespace, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    """The tab's home with one synthetic board stored and indexed, the synthetic course as the shipped folder, a US
    setup, a synthetic master, the user's own model target raising and a scripted model in its place (``port``)."""

    home = learning_home
    seed_board(home.home, "widgetco", WIDGETCO)
    assert search_index.rebuild_from_index(home.home).available

    monkeypatch.setattr(free_search, "default_config", lambda _home, _target: SimpleNamespace(countries=("US",)))
    monkeypatch.setattr(IndexFilters, "from_config", classmethod(lambda cls, _config, now=None: IndexFilters(countries=("US",), work_mode="remote", area="Remote")))

    def no_model(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("no real model is called here")

    monkeypatch.setattr(proposal_execution, "resolve_model_adapter", no_model)
    monkeypatch.setattr(learning_corpus.MeteredRoleModel, "__init__", no_model)
    monkeypatch.setattr(learning_course, "read_master_lines", lambda _home, _target: MASTER)
    monkeypatch.setattr(master_store, "master_revisions", lambda **_kwargs: [REVISION])
    learning_pathway_build._MASTER_REFS.clear()

    package = home.tmp / "shipped-package"
    course = shipped_course()
    for module in course["modules"]:
        for lesson in module["concepts"]:
            lesson.pop("source", None)  # the export takes it out; the shipped schema does not hold it
    folder = write_data(package / KEY, course, shipped_paths())
    (folder / "catalog.json").write_text(json.dumps(make_catalog(course), sort_keys=True), encoding="utf-8")
    monkeypatch.setattr(learning_shipped_store, "package_shipped_dir", lambda: package)

    port = Port()
    monkeypatch.setattr(learning_pathway_build, "_model_port", lambda: port)
    starts: list[str] = []
    real_start = learning_pathway_build.start

    def counted(home_root: Path, target: Path, shipped: learning_shipped_store.ShippedCourse, **kwargs: object) -> None:
        starts.append(shipped.role_key)
        real_start(home_root, target, shipped, **kwargs)

    monkeypatch.setattr(learning_pathway_build, "start", counted)
    home.port, home.starts = port, starts
    try:
        yield home
    finally:
        port.gate.set()
        learning_job.wait_for_jobs(timeout=60)
        search_index.close(home.home)


def _open_dialog(ui) -> None:
    with ui.page.expect_response(lambda response: urlsplit(response.url).path == BUILD_PATH) as asked:
        ui.page.locator(BUILD).click()
    assert asked.value.status == 200 and asked.value.request.post_data_json == {}
    ui.page.locator(DIALOG).wait_for()


def _approve(ui) -> None:
    with ui.page.expect_response(lambda response: urlsplit(response.url).path == BUILD_PATH) as started:
        ui.page.locator(APPROVE).click()
    assert started.value.status == 202 and started.value.request.post_data_json == {"approve": True}
    ui.page.locator(DIALOG).wait_for(state="detached")


def test_the_button_asks_first_and_build_compares_the_resume_and_the_row_follows(learning_ui, shipped_home: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    ui, home = learning_ui, shipped_home
    own = learning_store.import_course(home.home, home.target, make_course(home.tmp / "own-course", lessons=3), "Gadget engineer", None).pathway
    ui.goto("/#/learning")
    ui.page.locator(SHIPPED).wait_for()

    # Not built: one button, no link to a page that is not there; the user's own row has no such button.
    assert _text(ui, f"{SHIPPED} {LOCAL_PAGE}") == "Your pathway page is not built yet"
    assert _text(ui, BUILD) == "Build my pathway" and ui.page.locator(f"{SHIPPED} button").count() == 1
    assert ui.page.locator(OPEN_PAGE).count() == 0 and ui.page.locator(STALE).count() == 0
    assert ui.page.locator(f'{ROW}[data-pathway-id="{own.id}"] [data-action="learning-build-page"]').count() == 0

    # Pressed: the consent dialog, and nothing is sent to a model.
    _open_dialog(ui)
    assert _text(ui, f"{DIALOG} h2") == f"Build your pathway page for '{ROLE}'?"
    assert _text(ui, f'{DIALOG} [data-role="learning-pathway-sends"]') == learning_pathway_build.CONSENT
    estimate = _text(ui, f'{DIALOG} [data-role="learning-pathway-estimate"]')
    assert estimate.startswith("Estimate: about 1 model call") and "tokens, about" in estimate and estimate.endswith("seconds.")
    assert "stays here" in _text(ui, f'{DIALOG} [data-role="learning-pathway-page"]')
    assert [(button.text_content() or "").strip() for button in ui.page.locator(f"{DIALOG} button").all()] == ["Not now", "Build"]
    assert ui.page.locator(f"{DIALOG} input, {DIALOG} select").count() == 0, "the dialog offers no option: the resume is always compared"
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    ui.page.screenshot(path=str(SHOT_DIR / "learning-tab-pathway-dialog.png"))

    # "Not now": nothing starts.
    ui.page.locator(NOT_NOW).click()
    ui.page.locator(DIALOG).wait_for(state="detached")
    assert home.starts == [] and home.port.prompts == []
    assert learning_shipped_store.pathway_page(home.home, home.target, KEY) is None
    assert _text(ui, f"{SHIPPED} {LOCAL_PAGE}") == "Your pathway page is not built yet" and ui.page.locator(BUILD).is_enabled()

    # "Build": the build runs; the row says so and the button cannot be pressed again.
    home.port.answers.append(ANSWER)
    home.port.gate.clear()
    _open_dialog(ui)
    _approve(ui)
    ui.page.locator(BUILDING).wait_for()
    assert home.port.asked.wait(60)
    ui.page.wait_for_function("selector => document.querySelector(selector).textContent.includes('Comparing')", arg=BUILDING)
    assert _text(ui, BUILDING) == "Comparing your resume with the lessons…" and ui.page.locator(BUILD).is_disabled()
    assert ui.page.locator(OPEN_PAGE).count() == 0 and home.starts == [KEY]
    ui.page.screenshot(path=str(SHOT_DIR / "learning-tab-pathway-building.png"))
    home.port.gate.set()

    # Built: the row follows the server.
    ui.page.locator(OPEN_PAGE).wait_for(timeout=30000)
    assert ui.page.locator(BUILDING).count() == 0 and len(home.port.prompts) == 1
    assert _text(ui, f"{SHIPPED} {LOCAL_PAGE}").startswith("Your pathway page was built on ")
    assert _text(ui, BUILD) == "Rebuild my pathway" and ui.page.locator(BUILD).is_enabled()
    assert ui.page.locator(f"{SHIPPED} button").count() == 1 and ui.page.locator(STALE).count() == 0 and ui.page.locator(BUILD_ERROR).count() == 0
    link = ui.page.locator(OPEN_PAGE)
    assert _text(ui, OPEN_PAGE) == "Open your pathway page"
    assert link.get_attribute("href") == f"/learning/shipped/{KEY}/pathway.html"
    assert link.get_attribute("target") == "_blank" and link.get_attribute("rel") == "noopener"
    open_site = ui.page.locator(f"{SHIPPED} {OPEN}")
    assert open_site.get_attribute("href") == f"https://scout.edgelab.one/learn/pathways/{KEY}/" and _text(ui, f"{SHIPPED} {OPEN}") == "Open on scout.edgelab.one"

    # The link opens the real page, under the course's policy, with what the resume shows.
    with ui.page.context.expect_page() as opened:
        link.click()
    page = opened.value
    console: list[str] = []
    failed: list[str] = []
    page.on("console", lambda message: console.append(f"{message.type}: {message.text}") if message.type == "error" else None)
    page.on("response", lambda response: failed.append(f"{response.status} {response.url}") if response.status >= 400 else None)
    page.wait_for_load_state("load")
    page.reload()
    page.wait_for_load_state("load")
    assert page.url.endswith(f"/learning/shipped/{KEY}/pathway.html")
    assert (page.locator("h1").first.text_content() or "").strip() == f"Your pathway: {ROLE}"
    shown = page.locator("main").text_content() or ""
    assert "What your resume shows" in shown and "Ran the Widgetflow line" in shown and "Production evidence" in shown
    assert page.locator("html").get_attribute("data-theme") == "light", "the course's theme script ran"
    page.screenshot(path=str(SHOT_DIR / "learning-pathway-page.png"), full_page=True)
    # The lessons are on the learning center: a link goes there in a new tab (not followed here), and no lesson page is local.
    lesson_link = page.locator(f'main a[href="https://scout.edgelab.one/learn/lessons/lesson-alpha/"]').first
    assert lesson_link.get_attribute("target") == "_blank" and lesson_link.get_attribute("rel") == "noopener"
    assert page.locator('a[href$=".html"]:not([href="pathway.html"])').count() == 0 and "site version 2026.10.1" in (page.locator("footer").text_content() or "")
    assert console == [] and failed == [], (console, failed)
    page.close()
    ui.page.screenshot(path=str(SHOT_DIR / "learning-tab-pathway-built.png"))

    # Built by another GigAI version: one quiet line says why, and a rebuild (the same dialog, the same build) clears it.
    with monkeypatch.context() as patched:
        patched.setattr(learning_shipped_store, "gigai_version", lambda: "99.0.0")
        ui.reload()
        ui.page.locator(STALE).wait_for()
        assert _text(ui, STALE) == "It is out of date: GigAI was updated since. Rebuild it to bring it up to date."
        assert _text(ui, f"{SHIPPED} {LOCAL_PAGE}").startswith("Your pathway page was built on ") and ui.page.locator(OPEN_PAGE).count() == 1
        ui.page.screenshot(path=str(SHOT_DIR / "learning-tab-pathway-stale.png"))
        home.port.answers.append(ANSWER)
        _open_dialog(ui)
        _approve(ui)
        ui.page.locator(STALE).wait_for(state="detached", timeout=30000)
        assert home.starts == [KEY, KEY] and len(home.port.prompts) == 2 and _text(ui, BUILD) == "Rebuild my pathway"
    ui.assert_clean()


def test_a_build_that_does_not_finish_says_a_plain_line_and_the_row_stays_as_it_was(learning_ui, shipped_home: SimpleNamespace) -> None:  # noqa: F811
    ui, home = learning_ui, shipped_home
    home.port.answers.extend(["this is not json", "nor is this"])
    ui.goto("/#/learning")
    ui.page.locator(SHIPPED).wait_for()
    _open_dialog(ui)
    _approve(ui)
    ui.page.locator(BUILD_ERROR).wait_for(timeout=30000)
    assert _text(ui, BUILD_ERROR) == learning_pathway_build._MESSAGES["markers_invalid"]
    assert _text(ui, f"{SHIPPED} {LOCAL_PAGE}") == "Your pathway page is not built yet" and _text(ui, BUILD) == "Build my pathway"
    assert ui.page.locator(BUILD).is_enabled() and ui.page.locator(OPEN_PAGE).count() == 0 and ui.page.locator(BUILDING).count() == 0
    assert len(home.port.prompts) == 2 and learning_shipped_store.pathway_page(home.home, home.target, KEY) is None
    ui.assert_clean()  # a build that did not finish is a state of the row, not an HTTP error

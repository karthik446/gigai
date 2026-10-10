"""0.1.11.10 Part B (G6): the Learning pathways tab's REQUEST side -- the form, the estimate dialog, a running
course with Stop, Resume for a failed/interrupted one, and the 409 one course at a time.

Two fixtures, same shape as the assess dialog's tests:
- ``dialog_ui`` (route-mocked, like ``test_assess_dialog_running.py``): a real server for the list and the ask, but
  the generation itself is never reached -- a test that only needs the dialog's text and "Not now"/409 behaviour
  does not need a real, scripted course.
- ``generate_ui`` (``test_assess_all_dialog.py``'s shape: ``serve`` with the real backend, in this process, on a
  synthetic home with seeded boards): a REAL generation through the real server, using the test seam G5 built
  (``learning_job._test_script`` reads ``GIGAI_SCOUT_LEARNING_TEST_SCRIPT`` while both of ``bindings.py``'s test
  seams are on). No model is called, no network, no name is resolved.

Pinned:
(a) typing a role and pressing "Generate course" asks the server and shows the dialog with its title, the resume
    sentence, the sends sentence, the stops sentence and the estimate numbers; "Not now" closes it and starts
    nothing (no pathway is stored, no second POST);
(b) approving starts a real course: the row shows Running with its progress line and counters, Stop cancels it,
    and the row ends in a terminal state;
(c) a full scripted run ends Ready with an "Open course" link that loads the real course index;
(d) a failed row shows its error text and Resume goes on with it to Ready;
(e) a second request while one runs answers 409 ``learning_running`` and the form shows that message plainly;
(f) the empty state still reads right (covered in ``test_learning_tab.py``; not repeated here).
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from gigai.scout import learning_job, learning_store
from gigai.scout.find_jobs import search_index

from tests.behaviors.scout_learning.test_learning_course import lesson_answer, lesson_id
from tests.behaviors.scout_learning.test_learning_generate_cli import corpus_answers, course_answer, seed_boards
from tests.support.scout_profile_fixtures import build_gig_with_resume
from tests.ui.conftest import _ui_session
from tests.ui.support import tid

pytestmark = [pytest.mark.ui, pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 9, 0), reason="needs FTS5")]

PATHWAYS = "/api/learning/pathways"
ROLE_INPUT = "#learning-role"
GENERATE = '[data-action="learning-generate"]'
DIALOG = tid("learning-request-dialog")
ROW = '[data-testid="learning-row"]'
SHOT_DIR = Path("/Users/kar/orca/workspaces/gigai/orchestrator/research/role-packet/out/ui-shots")
ROLE = "MLOps engineer"


def shot(ui, name: str) -> None:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    ui.page.screenshot(path=str(SHOT_DIR / f"{name}.png"))


# ---------------------------------------------------------------------------------------------------------------
# dialog_ui: the list and the ask are real; the generation route is intercepted (no real course is ever built)
# ---------------------------------------------------------------------------------------------------------------


@pytest.fixture
def dialog_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
def dialog_ui(request: pytest.FixtureRequest, ui_browser, dialog_home, ui_artifacts: Path):
    with _ui_session(request, ui_browser, dialog_home, ui_artifacts) as session:
        import urllib.error
        import urllib.request

        try:
            with urllib.request.urlopen(f"{dialog_home.url}/api/setup", timeout=60) as response:
                prefill = json.loads(response.read()).get("prefill") or {}
        except urllib.error.HTTPError as error:
            prefill = (json.loads(error.read()).get("error") or {}).get("prefill") or {}
        session.page.route(
            "**/api/setup",
            lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"prefs": prefill})),
        )
        yield session


def test_generate_asks_shows_the_dialog_and_not_now_starts_nothing(dialog_ui, dialog_home) -> None:
    ui = dialog_ui
    ui.goto("/#/learning")
    ui.page.locator(ROLE_INPUT).wait_for()
    assert ui.page.locator(GENERATE).is_disabled()

    ui.page.locator(ROLE_INPUT).fill(f"  {ROLE}  ")
    assert ui.page.locator(GENERATE).is_enabled()
    shot(ui, "g6-form")
    with ui.page.expect_response(lambda r: r.request.method == "POST" and urlsplit(r.url).path == PATHWAYS) as asked:
        ui.page.click(GENERATE)
    assert asked.value.status == 200 and asked.value.request.post_data_json == {"role_text": ROLE}
    dialog = ui.page.locator(DIALOG)
    dialog.wait_for()

    estimate = asked.value.json()["estimate"]
    caps = asked.value.json()["caps"]
    assert estimate["basis"] == "none", "a fresh home has no recorded learning calls"
    assert ui.page.locator("#learning-request-title").text_content() == f"Generate a course for '{ROLE}'?"
    text = dialog.text_content() or ""
    assert "higher-usage action than an assessment" in text
    assert f"about {estimate['calls']} model call" in text
    assert f"about {estimate['fetches']} page fetch" in text and "public documentation sites" in text
    assert f"about {estimate['minutes']} minute" in text and "no recorded course yet to estimate tokens or time from" in text
    assert "sentences from job postings stored on your machine" in text
    assert "uses your resume to mark what you already know" in text
    assert "contact lines are removed first" in text
    assert f"after {caps['calls']} model calls or {caps['fetches']} page fetches" in text
    assert dialog.locator("input[type=checkbox]").count() == 0, "no checkbox is added to the resume sentence"
    approve = dialog.locator('[data-action="learning-request-approve"]')
    assert (approve.text_content() or "").strip() == f"Generate (about {estimate['calls']} calls)"
    shot(ui, "g6-dialog")

    dialog.locator('[data-action="learning-request-cancel"]').click()
    dialog.wait_for(state="detached")
    assert learning_store.list_pathways(dialog_home.home, dialog_home.target) == []
    ui.assert_clean()


def test_a_second_request_while_one_runs_shows_the_409_message_plainly(generate_ui) -> None:
    ui = generate_ui
    ui.goto("/#/learning")
    ui.page.locator(ROLE_INPUT).fill(ROLE)
    ui.page.click(GENERATE)
    dialog = ui.page.locator(DIALOG)
    dialog.wait_for()
    with ui.page.expect_response(lambda r: r.request.method == "POST" and urlsplit(r.url).path == PATHWAYS) as started:
        dialog.locator('[data-action="learning-request-approve"]').click()
    dialog.wait_for(state="detached")
    assert started.value.status == 202
    ui.page.locator(f'{ROW}[data-pathway-id="{started.value.json()["pathway"]["id"]}"]').wait_for()

    ui.page.locator(ROLE_INPUT).fill("Platform engineer")
    with ui.page.expect_response(lambda r: r.request.method == "POST" and urlsplit(r.url).path == PATHWAYS) as asked:
        ui.page.click(GENERATE)
    assert asked.value.status == 200, "the ask itself (no approve) never 409s: it is the estimate, nothing started"
    dialog.wait_for()
    with ui.page.expect_response(lambda r: r.request.method == "POST" and urlsplit(r.url).path == PATHWAYS) as blocked:
        dialog.locator('[data-action="learning-request-approve"]').click()
    assert blocked.value.status == 409 and blocked.value.json()["error"]["code"] == "learning_running"
    error = dialog.locator(".callout.danger")
    error.wait_for()
    assert "being generated for this project now" in (error.text_content() or "")
    assert ui.problems() and all("409" in problem for problem in ui.problems())
    ui.network.console_errors.clear()
    ui.network.http_errors.clear()
    dialog.locator('[data-action="learning-request-cancel"]').click()
    dialog.wait_for(state="detached")
    ui.assert_clean()


# ---------------------------------------------------------------------------------------------------------------
# generate_ui: a REAL generation through the real server (G5's test seam), no model call, no network
# ---------------------------------------------------------------------------------------------------------------


def write_script(path: Path, *, delay_seconds: float) -> None:
    titles, vocabulary, follow_up = corpus_answers()
    course = course_answer()
    rules: list[dict[str, object]] = [
        {
            "when": ["You are writing one MODULE", f"LESSON id={lesson_id(module, 1)} "],
            "answer": {"module_intro": "Two sentences. About the module.", "lessons": [lesson_answer(lesson_id(module, n)) for n in (1, 2, 3, 4)]},
        }
        for module in range(1, 8)
    ]
    rules += [
        {"when": ["FOLLOW-UP:"], "answer": follow_up},
        {"when": ["You are naming what a set of real job postings"], "answer": vocabulary},
        {"when": ["You are planning a COURSE"], "answer": course},
        {"when": ["You are turning one job role"], "answer": titles},
    ]
    pages = {
        source["url"]: f'<!doctype html><html><head><title>{lesson["title"]} page</title></head><body><h1 id="top">{lesson["title"]}</h1>'
        f'<h2 id="setup">Setup</h2><p>{"A sentence about this lesson. " * 20}</p></body></html>'
        for module in course["modules"] for lesson in module["lessons"] for source in lesson["sources"]  # type: ignore[union-attr]
    }
    path.write_text(json.dumps({"delay_seconds": delay_seconds, "answers": rules, "pages": pages}), encoding="utf-8")


@pytest.fixture
def generate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend, serve

    monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")
    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_MODEL", "1")
    gig = build_gig_with_resume(tmp_path)
    seed_boards(gig.home_root)
    search_index.close(gig.home_root)
    write_script(tmp_path / "learning-script.json", delay_seconds=0.1)
    monkeypatch.setenv("GIGAI_SCOUT_LEARNING_TEST_SCRIPT", str(tmp_path / "learning-script.json"))
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
def generate_ui(request: pytest.FixtureRequest, ui_browser, generate_home, ui_artifacts: Path):
    with _ui_session(request, ui_browser, generate_home, ui_artifacts) as session:
        import urllib.error
        import urllib.request

        try:
            with urllib.request.urlopen(f"{generate_home.url}/api/setup", timeout=60) as response:
                prefill = json.loads(response.read()).get("prefill") or {}
        except urllib.error.HTTPError as error:
            prefill = (json.loads(error.read()).get("error") or {}).get("prefill") or {}
        session.page.route(
            "**/api/setup",
            lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"prefs": prefill})),
        )
        yield session


def test_approving_runs_a_real_course_shows_progress_and_stop_cancels_it(generate_ui) -> None:
    ui = generate_ui
    ui.goto("/#/learning")
    ui.page.locator(ROLE_INPUT).fill(ROLE)
    ui.page.click(GENERATE)
    dialog = ui.page.locator(DIALOG)
    dialog.wait_for()
    with ui.page.expect_response(lambda r: r.request.method == "POST" and urlsplit(r.url).path == PATHWAYS) as started:
        dialog.locator('[data-action="learning-request-approve"]').click()
    assert started.value.status == 202
    dialog.wait_for(state="detached")
    row = ui.page.locator(f'{ROW}[data-pathway-id="{started.value.json()["pathway"]["id"]}"]')
    row.wait_for()

    # Running with progress lines and counters, then Stop.
    ui.page.locator(f'{ROW} [data-role="learning-status"]:has-text("Running")').wait_for(timeout=60_000)
    ui.page.locator(f'{ROW} [data-role="learning-progress"]').wait_for()
    counters = row.locator('[data-role="learning-progress-counters"]')
    counters.wait_for()
    assert "model call" in (counters.text_content() or "") and "page fetch" in (counters.text_content() or "")
    shot(ui, "g6-running")

    stop_button = row.locator('[data-action="learning-stop"]')
    stop_button.wait_for()
    with ui.page.expect_response(lambda r: r.request.method == "POST" and "/cancel" in urlsplit(r.url).path) as cancelled:
        stop_button.click()
    assert cancelled.value.status == 200
    row.locator('[data-role="learning-stopping"]').wait_for()
    row.locator(f'[data-role="learning-status"]:has-text("Failed"), [data-role="learning-status"]:has-text("Ready")').wait_for(timeout=60_000)
    ui.assert_clean()


def test_a_full_scripted_run_ends_ready_and_open_course_loads_the_real_index(generate_ui) -> None:
    ui = generate_ui
    ui.goto("/#/learning")
    ui.page.locator(ROLE_INPUT).fill(ROLE)
    ui.page.click(GENERATE)
    dialog = ui.page.locator(DIALOG)
    dialog.wait_for()
    with ui.page.expect_response(lambda r: r.request.method == "POST" and urlsplit(r.url).path == PATHWAYS) as started:
        dialog.locator('[data-action="learning-request-approve"]').click()
    dialog.wait_for(state="detached")
    pathway_id = started.value.json()["pathway"]["id"]
    row = ui.page.locator(f'{ROW}[data-pathway-id="{pathway_id}"]')
    row.wait_for()

    ready = row.locator('[data-role="learning-status"]:has-text("Ready")')
    ready.wait_for(timeout=120_000)
    open_link = row.locator('[data-action="open-course"]')
    assert open_link.get_attribute("target") == "_blank"
    assert (row.locator('[data-role="learning-lessons"]').text_content() or "").strip()
    shot(ui, "g6-ready")

    with ui.page.context.expect_page() as new_page_info:
        open_link.click()
    new_page = new_page_info.value
    new_page.wait_for_load_state("load")
    assert new_page.url.endswith(f"/learning/{pathway_id}/index.html")
    assert ROLE in new_page.title() or new_page.title()
    new_page.close()
    ui.assert_clean()


def test_a_failed_row_shows_its_error_and_resume_finishes_it(generate_ui, generate_home) -> None:
    ui = generate_ui
    # A job cancelled almost immediately: the record ends failed/cancelled with little written, same as
    # test_learning_generate_journey.py's second course.
    pathway = learning_job.start(generate_home.home, generate_home.target, "ML platform engineer")
    learning_job.cancel(generate_home.home, generate_home.target, pathway.id)

    def _terminal() -> str:
        return learning_store.get_pathway(generate_home.home, generate_home.target, pathway.id).status  # type: ignore[union-attr]

    import time

    deadline = time.monotonic() + 60
    while _terminal() not in ("failed", "interrupted") and time.monotonic() < deadline:
        time.sleep(0.1)
    assert _terminal() == "failed"

    ui.goto("/#/learning")
    row = ui.page.locator(f'{ROW}[data-pathway-id="{pathway.id}"]')
    row.wait_for()
    assert (row.locator('[data-role="learning-status"]').text_content() or "") == "Failed"
    error_text = row.locator('[data-role="learning-error"]')
    error_text.wait_for()
    assert (error_text.text_content() or "").strip()
    shot(ui, "g6-failed")

    resume_button = row.locator('[data-action="learning-resume"]')
    resume_button.wait_for()
    with ui.page.expect_response(lambda r: r.request.method == "POST" and "/resume" in urlsplit(r.url).path) as resumed:
        resume_button.click()
    assert resumed.value.status == 202
    ready = row.locator('[data-role="learning-status"]:has-text("Ready")')
    ready.wait_for(timeout=120_000)
    assert row.locator('[data-action="open-course"]').count() == 1
    ui.assert_clean()

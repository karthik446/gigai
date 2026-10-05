"""0.1.11 UINOTICE: the job page's model notice line, under the verdict.

Real server and real page; the assessment the page reads (`GET /api/assessments`) is laid over with the notice the
API serves for a model GigAI's accuracy results are not for (`model_notice`: text + `link {label, path}`, from
`evaluated_models`), and once without it. Pinned:
- with a notice: ONE muted line right under the verdict wording, the server's sentence, then the link under its
  words ("GigAI's accuracy results"), to the docs page: not a bare path;
- without: no such line at all;
- the page writes nothing and the Jobs list chips are not touched.

Synthetic home only. With `GIGAI_UI_EVIDENCE` set, a picture of the page with its text is written beside the others.
"""

from __future__ import annotations

import json
from urllib.parse import quote

import pytest

from gigai.scout.evaluated_models import model_notice
from tests.ui.job_resume_fixtures import evidence_folder, shot
from tests.ui.support import INTERACTIVE_WALL_SECONDS

pytestmark = pytest.mark.ui

PAGE = ".job-page"
NOTE = f'{PAGE} [data-role="requirements-note"]'
NOTICE = f'{PAGE} [data-role="model-notice"]'
LINK = f'{NOTICE} [data-role="model-notice-link"]'


def _lay(ui, demo, notice: dict | None, note: str | None = None) -> None:
    def answer(route) -> None:
        if route.request.method != "GET":
            route.continue_()
            return
        body = route.fetch().json()
        for item in body.get("items", []):
            if item.get("job", {}).get("job_identity") == demo.hero_job:
                item.pop("model_notice", None)
                item.pop("requirements_note", None)
                if note is not None:
                    item["requirements_note"] = note
                if notice is not None:
                    item["model_notice"] = notice
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    if ui.page.url != "about:blank":
        ui.settle()
        ui.page.goto("about:blank")
    ui.page.unroute_all()
    ui.page.route("**/api/assessments*", answer)


def _open(ui, demo) -> None:
    ui.goto("/#/jobs/" + quote(demo.hero_job, safe=""))
    ui.wait_for_job_page()
    ui.page.locator(f'{PAGE} [data-role="verdict-wording"]').wait_for()
    ui.settle()


def test_the_job_page_shows_the_model_notice_under_the_verdict_and_nothing_without_one(ui, scout_server) -> None:
    demo = scout_server.demo
    served = model_notice("claude_cli", "claude-haiku-4-5")
    assert served is not None
    notice = served.to_json()

    _lay(ui, demo, notice)
    _open(ui, demo)
    ui.step("shown")
    line = ui.page.locator(NOTICE)
    assert line.count() == 1
    assert (line.get_attribute("class") or "").split() == ["muted", "small"]
    link = ui.page.locator(LINK)
    assert (link.text_content() or "").strip() == "GigAI's accuracy results"
    assert link.get_attribute("href") == "https://karthik446.github.io/gigai/latest/scout/accuracy-0-1-11/"
    assert (line.inner_text() or "").strip() == f"{notice['text']} GigAI's accuracy results"
    assert "scout/accuracy" not in (line.inner_text() or ""), "the path is never shown, only its words"
    below = ui.page.evaluate(
        "() => { const a = document.querySelector('.job-page [data-role=\"verdict-wording\"]'); const b = document.querySelector('.job-page [data-role=\"model-notice\"]');"
        " return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING); }"
    )
    assert below, "the notice line follows the verdict wording"
    assert ui.writes_after("start") == []
    ui.wall_budget("open a job page with a model notice (small home)", INTERACTIVE_WALL_SECONDS * 4, "start", "shown")
    shot(ui, evidence_folder(), "uinotice-1-job-page-with-the-notice")

    _lay(ui, demo, None)
    _open(ui, demo)
    assert ui.page.locator(NOTICE).count() == 0 and ui.page.locator(LINK).count() == 0
    assert ui.page.locator(f'{PAGE} [data-role="verdict-wording"]').count() == 1


def test_the_job_page_shows_the_requirements_note_by_the_requirements_table_and_nothing_without_one(ui, scout_server) -> None:
    demo = scout_server.demo
    note = "Only 1 requirement was read from this posting. Open the posting to check."

    _lay(ui, demo, None, note)
    _open(ui, demo)
    ui.step("note")
    line = ui.page.locator(NOTE)
    assert line.count() == 1
    assert (line.get_attribute("class") or "").split() == ["muted", "small"]  # the model notice line's style
    assert (line.inner_text() or "").strip() == note
    assert ui.page.locator(NOTICE).count() == 0, "no model notice was served: only the one note shows"
    before_table = ui.page.evaluate(
        "() => { const n = document.querySelector('.job-page [data-role=\"requirements-note\"]'); const t = document.querySelector('.job-page .matrix-table');"
        " return Boolean(t) && Boolean(n.compareDocumentPosition(t) & Node.DOCUMENT_POSITION_FOLLOWING); }"
    )
    assert before_table, "the note line sits just above the requirements table"
    assert ui.writes_after("start") == []
    shot(ui, evidence_folder(), "uinotice-2-job-page-with-the-requirements-note")

    _lay(ui, demo, None, None)
    _open(ui, demo)
    assert ui.page.locator(NOTE).count() == 0

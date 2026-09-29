"""uat-bug-027: the job page puts open questions first, the requirement table below (uat-bug-045: always open).

The UI is JSX with no JS test runner, so this reads the source (as the other
source pins here do) and checks the built ``ui/dist`` bundle agrees with it.
Pinned:

* the job page renders ``AssessmentBody`` with ``questionsFirst``;
* in that layout the questions section precedes the requirement table, the
  table sits in a plain, always-visible section (no ``<details>``, no toggle;
  uat-bug-045) and carries no answer boxes, and no questions section renders without open questions;
* answering is unchanged: the same drafts controller, one Re-assess/Tailor
  ``RequirementActions``, no new API call;
* ``ui/dist`` carries the new layout and ``index.html`` names bundles that exist.
"""

from __future__ import annotations

import re
from pathlib import Path

from gigai.scout.find_jobs.api import static as static_module

UI = Path(static_module.__file__).resolve().parents[2] / "ui"
UI_SRC = UI / "src"


def _body() -> str:
    return (UI_SRC / "components" / "AssessmentBody.jsx").read_text(encoding="utf-8")


def _first_layout() -> str:
    body = _body()
    start = body.index("if (questionsFirst) {")
    return body[start : body.index("  return (\n    <>\n      {showVerdict", start)]


def test_the_job_page_asks_for_the_questions_first_layout() -> None:
    page = (UI_SRC / "views" / "JobPage.jsx").read_text(encoding="utf-8")
    assert re.search(r"<AssessmentBody[^>]*?\bquestionsFirst\b", page, re.S)
    assert "controller={answerDrafts}" in page and "postAnswer" not in page


def test_questions_render_before_the_always_open_requirements_table() -> None:
    layout = _first_layout()
    assert layout.index('data-role="questions-section"') < layout.index('data-role="requirements-section"')
    assert layout.index("{table(false)}") > layout.index('data-role="requirements-section"')
    # uat-bug-045: no disclosure around the requirements, in the layout or anywhere in the body.
    assert "<details" not in layout and "<summary" not in layout and "requirements-details" not in layout
    assert "<details" not in _body() and "requirements-details" not in _body()
    assert "<h3>Requirements (" in layout
    # No open questions, no questions section: it renders only when there are some.
    assert "{hasQuestions ? (" in layout and layout.index("{hasQuestions ? (") < layout.index('data-role="questions-section"')


def test_the_reference_table_has_no_answer_boxes() -> None:
    body = _body()
    assert "questionsByRow.get(row.requirement) || []" in body and "withBoxes ?" in body
    assert "table(false)" in _first_layout() and "table(true)" in body  # other callers keep N5


def test_answering_still_goes_through_the_drafts_controller() -> None:
    layout = _first_layout()
    assert "answers.setDraft" in layout and "answers.valueFor" in layout and "{actions}" in layout
    assert "<RequirementActions" in _body() and "answers.reassess" in _body()


def test_dist_agrees_with_src() -> None:
    dist = UI / "dist"
    html = (dist / "index.html").read_text(encoding="utf-8")
    bundles = re.findall(r'/assets/([\w.-]+\.js)', html)
    assert bundles and all((dist / "assets" / name).is_file() for name in bundles)
    js = "".join((dist / "assets" / name).read_text(encoding="utf-8") for name in bundles)
    assert "questions-section" in js and "requirements-section" in js and "requirements-details" not in js


def _code(path: Path) -> str:
    return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("//"))


def test_no_other_disclosure_wraps_the_job_page_requirements() -> None:
    assert "<details" not in _code(UI_SRC / "views" / "JobPage.jsx")
    assert "requirements-details" not in (UI_SRC / "styles.css").read_text(encoding="utf-8")


def test_tailoring_status_sits_next_to_the_button_uat_bug_043() -> None:
    actions = _code(UI_SRC / "components" / "RequirementActions.jsx")
    buttons = actions[actions.index('className="req-actions-buttons"') : actions.index('<ul className="action-help">')]
    # inside the button row, after the Tailor button: a role=status with a spinner
    assert buttons.index('name="tailor"') < buttons.index("<TailorStatus")
    status = actions[actions.index("function TailorStatus") : actions.index("function Help")]
    assert 'role="status"' in status and 'className="spinner"' in status and "status.text" in status
    assert 'data-action="tailor-jump"' in status  # the jump link once it finished

    page = _code(UI_SRC / "views" / "JobPage.jsx")
    fn = page[page.index("function tailorStatusFor") : page.index("export default function JobPage")]
    assert "`Tailoring ${who}… ${tailored.elapsed}s`" in fn and "with ${modelName}" in fn  # "Tailoring with Codex… 12s"
    assert 'tailored.outcome === "done"' in fn and 'tailored.outcome === "error"' in fn
    assert "getElementById(\"tailored-resume\")" in fn and "scrollIntoView" in fn
    assert "status: tailorStatus" in page and "default_model_target" in page and "20" not in fn.replace("2026", "")

    panel = _code(UI_SRC / "components" / "TailoredResumePanel.jsx")
    assert 'id="tailored-resume"' in panel and "tailor-progress" not in panel  # one status, by the button
    assert 'setOutcome("done")' in panel and 'setOutcome("error")' in panel
    # scrolls into view when a run FINISHES (was: when it started, off the button)
    assert "wasTailoring.current && !tailoring" in panel and "scrollIntoView" in panel

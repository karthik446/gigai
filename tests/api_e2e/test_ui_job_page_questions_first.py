"""uat-bug-027: the job page puts open questions first, the requirement table below (uat-bug-045: always open).

The UI is JSX with no JS test runner, so this reads the source (as the other
source pins here do) and checks the built ``ui/dist`` bundle agrees with it.
Pinned:

* the job page renders ``AssessmentBody`` with ``questionsFirst``;
* in that layout the questions section precedes the requirement table, the
  table sits in a plain, always-visible section (no ``<details>``, no toggle;
  uat-bug-045) and carries no answer boxes, and no questions section renders without open questions;
* answering is unchanged: the same drafts controller, one Re-assess
  ``RequirementActions`` (0.1.11: the Tailor action went with the tailor
  call), no new API call;
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
    # 0110-10-03: the heading comes from the model ("Requirements (16)", "+N not shown" past the server's bound).
    assert "<h3>{requirementsHeading(assessment)}</h3>" in layout
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
    page = _code(UI_SRC / "views" / "JobPage.jsx")
    # 0110-10-13: the one disclosure the page may have is the short "what one assessment sends" note under the Assess action;
    # nothing else (above all nothing around the requirements) is collapsible.
    assert page.count("<details") == page.count('data-role="assess-sends"'), "a disclosure other than the assess-sends note"
    assert "requirements-details" not in (UI_SRC / "styles.css").read_text(encoding="utf-8")


def test_the_tailoring_status_went_with_the_tailor_call() -> None:
    """uat-bug-043 put the tailoring status next to the Tailor button. 0.1.11 has neither: no model writes a resume."""

    actions = _code(UI_SRC / "components" / "RequirementActions.jsx")
    assert "TailorStatus" not in actions and 'name="tailor"' not in actions and "tailor-jump" not in actions
    assert actions.count("<Action ") == 1 and 'name="reassess"' in actions  # ONE action
    page = _code(UI_SRC / "views" / "JobPage.jsx")
    assert "tailorStatusFor" not in page and "tailorGate" not in page and "postTailoredResume(" not in page
    assert "<JobResumePanel" in page and "<ApplyPanel" in page and "<SuggestionsPanel" in page
    panel = _code(UI_SRC / "components" / "JobResumePanel.jsx")
    assert 'id="job-resume"' in panel and "postTailoredResume(" not in panel
    # Every refresh is a button that says what it costs; none runs by itself.
    model = (UI_SRC / "jobResumeModel.js").read_text(encoding="utf-8")
    assert 'export const REPICK_LABEL = "Re-pick · no model call";' in model and 'export const REASSESS_LABEL = "Re-assess · 1 model call";' in model
    assert "postTailoredResume" not in (UI_SRC / "api.js").read_text(encoding="utf-8").replace("postTailoredResumePdf", "")

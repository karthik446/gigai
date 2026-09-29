"""uat-bug-027: the job page puts open questions first, the requirement table below, collapsed.

The UI is JSX with no JS test runner, so this reads the source (as the other
source pins here do) and checks the built ``ui/dist`` bundle agrees with it.
Pinned:

* the job page renders ``AssessmentBody`` with ``questionsFirst``;
* in that layout the questions section precedes the requirement table, the
  table sits in a ``<details>`` without ``open`` (collapsed) and carries no
  answer boxes, and no questions section renders without open questions;
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


def test_questions_render_before_the_collapsed_requirements_table() -> None:
    layout = _first_layout()
    assert layout.index('data-role="questions-section"') < layout.index('data-role="requirements-details"')
    assert layout.index("{table(false)}") > layout.index("<details")
    details_tag = re.search(r"<details[^>]*>", layout).group(0)
    assert "open" not in details_tag.replace("data-role", "")  # collapsed by default
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
    assert "questions-section" in js and "requirements-details" in js

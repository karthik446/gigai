"""0110-10-03: what a requirement weighs, in the UI (``jobModel.js``, ``postingsModel.js``), under node.

What is pinned:

* a ``list_item`` row reads "One of a list" and sorts after the must-haves, before a bonus;
* ``minorGapText``: the bonus and one-of-a-list rows that are not met, in one line ("1 minor gap: Helm");
* ``requirementsHeading``: "Requirements (16)", and "+N not shown" when the server kept only its bound;
* the Jobs grid's score line carries the server's ``minor_gap_text`` after its own words;
* the assessment body draws both (the gap line, the heading) from the model, not from a count of its own.

LOUD skip when ``node`` is missing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

_SCRIPT = """
const job = await import(process.argv[1] + "/jobModel.js");
const postings = await import(process.argv[1] + "/postingsModel.js");
const row = (requirement, cls, status) => ({ requirement, class: cls, status, resume_evidence: [] });
const helm = {
  verdict: "matched_above_threshold",
  matrix: [row("8+ years", "hard", "met"), row("Python or Go", "askable", "met"), row("Docker", "list_item", "met"), row("Helm", "list_item", "unclear")],
};
const many = {
  matrix: [row("Rust", "nice_to_have", "unclear"), row("Helm", "list_item", "unclear"), row("Istio", "list_item", "unmet"), row("Kafka", "askable", "unclear"), row("Go", "nice_to_have", "unmet")],
  rows_not_shown: 5,
};
console.log(JSON.stringify({
  labels: [job.requirementStatusLabel(row("Helm", "list_item", "unclear")), job.classLabel("list_item")],
  helm: [job.minorGaps(helm), job.minorGapText(helm), job.requirementsHeading(helm)],
  many: [job.minorGaps(many), job.minorGapText(many), job.requirementsHeading(many)],
  none: [job.minorGapText({ matrix: [row("Python", "hard", "met")] }), job.minorGapText(null), job.requirementsHeading(null)],
  summary: job.requirementSummary(many, 5).shown.map((item) => item.requirement),
  score: [
    postings.scoreText({ score_text: "Matched · 3 of 4 requirements · rank 80", minor_gap_text: "1 minor gap: Helm" }),
    postings.scoreText({ score_text: "Matched · 4 of 4 requirements · rank 80", minor_gap_text: null }),
    postings.scoreText({ score_text: "rank 80 · not assessed" }),
  ],
}));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD SKIP: node is not installed; the UI model tests need it")
    result = subprocess.run([node, "--input-type=module", "-e", _SCRIPT, str(SRC)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_a_list_item_reads_one_of_a_list(out: dict) -> None:
    assert out["labels"] == ["One of a list: Unclear", "One of a list"]
    # Must-haves first, then one-of-a-list, then bonuses (unmet and unclear before met inside a class).
    assert out["summary"] == ["Kafka", "Istio", "Helm", "Go", "Rust"]


def test_the_minor_gap_line_and_the_requirements_heading(out: dict) -> None:
    assert out["helm"] == [["Helm"], "1 minor gap: Helm", "Requirements (4)"]
    # A must-have that is not met is no minor gap; more than three are named as "+N more".
    assert out["many"] == [["Rust", "Helm", "Istio", "Go"], "4 minor gaps: Rust, Helm, Istio +1 more", "Requirements (5, +5 not shown)"]
    assert out["none"] == [None, None, "Requirements (0)"]


def test_the_grid_score_line_says_the_gap(out: dict) -> None:
    assert out["score"] == [
        "Matched · 3 of 4 requirements · rank 80 · 1 minor gap: Helm",
        "Matched · 4 of 4 requirements · rank 80",
        "rank 80 · not assessed",
    ]


def test_the_assessment_body_draws_both_from_the_model() -> None:
    body = (SRC / "components" / "AssessmentBody.jsx").read_text(encoding="utf-8")
    # 0110-10-12: minorGapLine is minorGapText for a match and nothing under any other verdict.
    assert "minorGapLine(assessment)" in body and 'data-role="minor-gaps"' in body
    assert "<h3>{requirementsHeading(assessment)}</h3>" in body
    assert "assessment.matrix.length" not in body

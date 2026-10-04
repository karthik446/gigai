"""0.1.10.9 master P7 (UI part): the "resume changed" marker of a stored assessment.

``jobStateModel.js`` / ``postingsModel.js`` are pure JavaScript, run under the
system ``node`` (a LOUD skip without it). Synthetic fixtures only.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.assessment_basis import BASIS_STALE_REASONS
from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

NODE_SCRIPT = """
import * as model from __STATE__;
import * as grid from __GRID__;
const input = JSON.parse(process.argv[1]);
process.stdout.write(JSON.stringify({
  reasons: model.BASIS_STALE_REASONS,
  notes: input.jobs.map((job) => model.staleAssessmentNote(job)),
  chips: input.jobs.map((job) => model.olderSettingsChip(job)),
  count: model.olderSettingsCount(input.jobs),
  rows: input.rows.map((row) => grid.rowChips(row)),
}));
"""

SERVED = {"state": "needs_answers", "since": None, "next_events": []}
_STATE = {"job_state": {**SERVED, "assessment_stale": {"reason": "resume_changed"}}, "basis_stale": True, "basis_stale_reason": "resume_changed"}
_NEW = {"change": "new_line", "question_id": "tooling:helm", "question": "Have you written Helm charts?"}
_OTHER = {"change": "new_line", "question_id": "tooling:istio", "question": "Have you run Istio?"}
_LINE = {"change": "line_changed", "requirement": "Terraform modules"}
JOBS = [
    {"quick": {**_STATE, "basis_stale_resume": [_NEW]}},  # 0 a new line names its open question
    {"quick": {**_STATE, "basis_stale_resume": [_LINE, _NEW, _OTHER]}},  # 1 two questions, and a quoted line gone
    {"quick": {**_STATE, "basis_stale_resume": [_LINE]}},  # 2 a line it quoted is gone
    {"quick": {**_STATE, "basis_stale_resume": [{"change": "new_line", "question_id": "tooling:argo"}]}},  # 3 no question words: the id
    {"quick": {**_STATE}},  # 4 no list
    {"row": {"jobState": {**SERVED, "assessment_stale": {"reason": "resume_changed"}}}},  # 5 a grid row alone
    # 6: re-assessed on this page: the response replaced job.quick, the row was read before it.
    {"quick": {"basis_stale": False, "prompt_version": "assess-prompt-v8"}, "row": {"jobState": {**SERVED, "assessment_stale": {"reason": "resume_changed"}}}},
]
CHANGED = "Your resume changed since this assessment: re-assess"
ROWS = [{"state": "needs_answers", "stale_reason": "resume_changed"}]


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD SKIP: node is not on PATH; the UI model cannot run")
    script = NODE_SCRIPT.replace("__STATE__", json.dumps((UI_SRC / "jobStateModel.js").as_uri())).replace(
        "__GRID__", json.dumps((UI_SRC / "postingsModel.js").as_uri())
    )
    proc = subprocess.run([node, "--input-type=module", "-e", script, json.dumps({"jobs": JOBS, "rows": ROWS})], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_ui_knows_resume_changed(out: dict) -> None:
    assert tuple(out["reasons"]) == BASIS_STALE_REASONS and "resume_changed" in out["reasons"]


def test_the_note_says_what_changed_for_this_assessment(out: dict) -> None:
    notes = out["notes"]
    assert notes[0] == "A new line of your resume may answer: Have you written Helm charts?: re-assess"
    assert notes[1] == "A new line of your resume may answer: Have you written Helm charts? (and 1 more): re-assess"
    assert notes[2] == "A resume line this assessment used has changed: re-assess"
    assert notes[3] == "A new line of your resume may answer: tooling:argo: re-assess"
    assert notes[4] == CHANGED and notes[5] == CHANGED
    assert notes[6] is None, "an assessment made on this page is current"


def test_the_card_marker_says_resume_changed(out: dict) -> None:
    chips = out["chips"]
    assert [chip and chip["label"] for chip in chips] == ["Resume changed"] * 6 + [None]
    assert [chip["title"] for chip in chips[:6]] == out["notes"][:6]
    assert out["count"] == 6


def test_the_grid_row_says_resume_changed(out: dict) -> None:
    stale = [chip for chip in out["rows"][0] if chip["kind"] == "stale"]
    assert [(chip["label"], chip["title"]) for chip in stale] == [("Stale: resume changed", "resume_changed")]

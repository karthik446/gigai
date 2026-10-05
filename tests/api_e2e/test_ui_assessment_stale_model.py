"""Ledger 32 (UI part): the re-assess marker and the posting_incomplete label.

``jobStateModel.js`` / ``display.js`` are pure JavaScript, run under the
system ``node`` (a LOUD skip without it). Synthetic fixtures only; no
browser hand-check exists for the rendered pieces, so those are checked
statically.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

NODE_SCRIPT = """
import * as model from __STATE__;
import * as display from __DISPLAY__;
const input = JSON.parse(process.argv[1]);
process.stdout.write(JSON.stringify({
  notes: input.jobs.map((job) => model.staleAssessmentNote(job)),
  stale: input.jobs.map((job) => model.assessmentStaleFor(job)),
  label: display.notAssessedReasonLabel("posting_incomplete"),
  detail: display.notAssessedReasonDetail("posting_incomplete"),
  existing: display.notAssessedReasonLabel("over_cap"),
}));
"""

NOTE = "Posting text changed since this assessment: re-assess"
SERVED = {"state": "matched", "since": None, "next_events": ["applied"]}
STALE = {**SERVED, "assessment_stale": {"reason": "posting_changed"}}


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD SKIP: node is not on PATH; the UI model cannot run")
    script = NODE_SCRIPT.replace("__STATE__", json.dumps((UI_SRC / "jobStateModel.js").as_uri())).replace(
        "__DISPLAY__", json.dumps((UI_SRC / "display.js").as_uri())
    )
    jobs = [
        {"row": {"jobState": STALE}},  # run row, stale
        {"row": {"jobState": SERVED}},  # run row, not stale
        {"quick": {"job_state": STALE}},  # assessments list / quick item, stale
        {"quick": {"job_state": SERVED}},
        {"quick": {}, "row": None},  # no served state at all
    ]
    proc = subprocess.run([node, "--input-type=module", "-e", script, json.dumps({"jobs": jobs})], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_marker_is_present_iff_assessment_stale_is_present(out: dict) -> None:
    assert out["notes"] == [NOTE, None, NOTE, None, None]
    assert out["stale"][0] == {"reason": "posting_changed"}
    assert out["stale"][1] is None


def test_posting_incomplete_has_its_label(out: dict) -> None:
    assert out["label"] == "Posting text looks incomplete: open the posting"
    assert out["detail"] != "posting_incomplete"
    assert out["existing"] == "Not fully assessed"


def test_marker_and_label_are_wired_into_the_views() -> None:
    page = (UI_SRC / "views" / "JobPage.jsx").read_text()
    # 0110-10-12: the note, and the page's ONE Re-assess is told the assessment is old (it was a second button in the note).
    assert "staleAssessmentNote(job)" in page and "stale: staleReasonWords(job)," in page and 'label="Re-assess"' not in page
    card = (UI_SRC / "components" / "PostingCard.jsx").read_text()
    assert "staleAssessmentNote({ row })" in card
    board = (UI_SRC / "components" / "PostingsBoard.jsx").read_text()
    assert "notAssessedReasonLabel(reason)" in board

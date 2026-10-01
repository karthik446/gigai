"""0110-039 (UI part): the "assessed with older settings" marker and the "Assess all new" count split.

``jobStateModel.js`` / ``assessAllModel.js`` are pure JavaScript, run under the
system ``node`` (a LOUD skip without it). Synthetic fixtures only; the rendered
pieces are checked statically (no browser hand-check exists for them).
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
import * as all from __ALL__;
const input = JSON.parse(process.argv[1]);
process.stdout.write(JSON.stringify({
  reasons: model.BASIS_STALE_REASONS,
  notes: input.jobs.map((job) => model.staleAssessmentNote(job)),
  chips: input.jobs.map((job) => model.olderSettingsChip(job)),
  count: model.olderSettingsCount(input.jobs),
  lines: [0, 1, 3].map((count) => model.olderSettingsLine(count)),
  labels: input.plans.map((plan) => all.assessAllButtonLabel(plan)),
  staleLines: input.plans.map((plan) => all.staleLine(plan)),
  planLines: input.plans.map((plan) => all.planLine(plan)),
}));
"""

OLDER = "Assessed with older settings: re-assess"
BANK = "Your story bank changed since this assessment: re-assess"
POSTING = "Posting text changed since this assessment: re-assess"
SERVED = {"state": "matched", "since": None, "next_events": ["applied"]}


def _stale(reason: str) -> dict:
    return {**SERVED, "assessment_stale": {"reason": reason}}


JOBS = [
    {"quick": {"job_state": _stale("older_prompt"), "basis_stale": True, "basis_stale_reason": "older_prompt"}},  # 0 the list's item
    {"row": {"jobState": _stale("settings_changed")}},  # 1 a run row
    {"quick": {"job_state": _stale("story_bank_changed")}},  # 2
    {"row": {"jobState": _stale("posting_changed")}},  # 3 ledger 32, as before
    {"quick": {"job_state": SERVED, "basis_stale": False}},  # 4 current
    # 5: re-assessed on this page: the POST /api/assess response replaced job.quick, the row was read before it.
    {"quick": {"basis_stale": False, "prompt_version": "assess-prompt-v5"}, "row": {"jobState": _stale("older_prompt")}},
    # 6: re-assessed after an answer: POST /api/answers' `reassessed` carries the basis and no flag.
    {"quick": {"prompt_version": "assess-prompt-v5"}, "row": {"jobState": _stale("settings_changed")}},
    # 7: a fresh item does not hide a changed posting text.
    {"quick": {"basis_stale": False, "prompt_version": "assess-prompt-v5"}, "row": {"jobState": _stale("posting_changed")}},
    {"quick": {}, "row": None},  # 8 nothing served
]
PLANS = [
    {"count": 5, "new_count": 5, "stale_count": 0, "model_target": "codex_cli", "concurrency": 4},
    {"count": 7, "new_count": 4, "stale_count": 3, "model_target": "codex_cli", "concurrency": 4},
    {"count": 1, "new_count": 0, "stale_count": 1, "model_target": "codex_cli", "concurrency": 4},
    {"count": 5, "model_target": "codex_cli", "concurrency": 4},  # a server from before the split
    None,
]


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD SKIP: node is not on PATH; the UI model cannot run")
    script = NODE_SCRIPT.replace("__STATE__", json.dumps((UI_SRC / "jobStateModel.js").as_uri())).replace(
        "__ALL__", json.dumps((UI_SRC / "assessAllModel.js").as_uri())
    )
    payload = json.dumps({"jobs": JOBS, "plans": PLANS})
    proc = subprocess.run([node, "--input-type=module", "-e", script, payload], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_ui_knows_the_servers_reasons(out: dict) -> None:
    assert tuple(out["reasons"]) == BASIS_STALE_REASONS


def test_the_note_names_older_settings_and_keeps_the_posting_changed_words(out: dict) -> None:
    assert out["notes"] == [OLDER, OLDER, BANK, POSTING, None, None, None, POSTING, None]


def test_the_card_marker_is_for_older_settings_only(out: dict) -> None:
    chip = {"label": "Older settings", "title": OLDER}
    assert out["chips"] == [chip, chip, {"label": "Older settings", "title": BANK}, None, None, None, None, None, None]
    assert out["count"] == 3
    assert out["lines"] == [
        "",
        "1 assessed with older settings: open it to re-assess it.",
        "3 assessed with older settings: open one to re-assess it.",
    ]


def test_assess_all_new_counts_the_stale_ones_as_such(out: dict) -> None:
    assert out["labels"] == [
        "Assess all new (5)",
        "Assess all (4 new, 3 with older settings)",
        "Assess all (0 new, 1 with older settings)",
        "Assess all new (5)",
        "Assess all new (0)",
    ]
    assert out["staleLines"] == [
        "",
        "3 were assessed with older settings and are assessed again.",
        "1 was assessed with older settings and is assessed again.",
        "",
        "",
    ]
    assert out["planLines"][1].startswith("7 postings, one Codex call each"), "the plan line counts every call"


def test_the_marker_is_wired_into_the_views() -> None:
    page = (UI_SRC / "views" / "JobPage.jsx").read_text()
    assert "staleAssessmentNote(job)" in page and 'label="Re-assess"' in page, "the job page: the note and the one-click Re-assess"
    card = (UI_SRC / "components" / "JobCard.jsx").read_text()
    assert "olderSettingsChip(job)" in card and 'data-role="assessment-older-settings"' in card
    tab = (UI_SRC / "views" / "AssessmentsView.jsx").read_text()
    assert "olderSettingsLine(olderSettingsCount(jobs))" in tab and 'data-role="assessments-older-settings"' in tab
    jobs = (UI_SRC / "views" / "FindJobsView.jsx").read_text()
    assert "assessAllButtonLabel(assessAllPlan)" in jobs and "staleLine(assessAllPlan)" in jobs


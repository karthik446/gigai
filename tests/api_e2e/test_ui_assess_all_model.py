"""uat-bug-042: the words and state of "Assess all new" and "All new postings", run under node.

``assessAllModel.js`` is plain JavaScript, so this runs it under the system
``node`` and asserts on the JSON the script prints. LOUD skip when ``node``
is not on PATH.

Pinned: the estimate line ("406 postings, one Codex call each, about 72 min
at 4 at a time.") and, with no measured time, count and K only -- never a
made-up minute figure; where the estimate came from; the per-target privacy
line (a hosted target keeps "sends your resume to <provider>"); the
dialog's starting choice ("all" for Ollama / Codex / Claude, the saved
number for OpenRouter); the button label; the job's progress lines; the
header's live counts (the newest run only); the poller (reads while a job
runs, stops when it ends, drops answers for another run). The JS constants
equal the Python ones, and the dialog, the Jobs page and the built bundle
use them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs import assess_all
from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.find_jobs.contracts import ASSESS_ALL, ASSESS_ALL_CEILING, SELECTION_CAP_MAXIMUM

UI = Path(static_module.__file__).resolve().parents[2] / "ui"
UI_SRC = UI / "src"

SCRIPT = """
import * as m from MODEL_URL;

const out = {};
const measured = { count: 406, model_target: "codex_cli", concurrency: 4, per_call_seconds: 42.0, per_call_source: "run", per_call_samples: 10, estimate_minutes: 72 };
const unmeasured = { count: 1, model_target: "claude_cli", concurrency: 4, per_call_seconds: null, per_call_source: null, per_call_samples: 0, estimate_minutes: null };
const earlier = { ...measured, per_call_source: "assess_all", per_call_samples: 1, per_call_seconds: 30.4 };
out.plan = [m.planLine(measured), m.planLine(unmeasured), m.planLine(null)];
out.source = [m.estimateSourceLine(measured), m.estimateSourceLine(unmeasured), m.estimateSourceLine(earlier)];
out.privacy = ["ollama_local", "codex_cli", "claude_cli", "openrouter_api"].map(m.privacyLine);
out.defaults = [
  m.defaultSelectionCap(10, "codex_cli"),
  m.defaultSelectionCap(10, "claude_cli"),
  m.defaultSelectionCap(10, "ollama_local"),
  m.defaultSelectionCap(25, "openrouter_api"),
  m.defaultSelectionCap("all", "openrouter_api"),
  m.defaultSelectionCap("all", "codex_cli"),
];
out.valid = ["all", 1, 50, 0, 51, "ALL", 2.5, null].map(m.capValid);
out.help = [m.fullAssessmentsHelp("all", "codex_cli"), m.fullAssessmentsHelp(10, "codex_cli")];
out.button = [m.assessAllButtonLabel(measured), m.assessAllButtonLabel(null)];
out.jobs = [
  m.jobLine({ status: "running", text: "12 of 406 assessed, 1 failed", concurrency: 4 }),
  m.jobLine({ status: "cancelled", text: "3 of 9 assessed" }),
  m.jobLine({ status: "interrupted", text: "3 of 9 assessed" }),
  m.jobLine({ status: "complete", text: "9 of 9 assessed" }),
  m.jobLine({ status: "failed", text: "0 of 9 assessed", reason: "model_target_unavailable" }),
  m.jobLine(null),
];
out.skip = ["run_not_finished", "no_profile", "other"].map(m.skipReasonText);
const lastRun = { run_id: "r1", counts: { found: 500, new: 416, assessed: 10, matched: 2 } };
out.counts = [
  m.withLiveCounts(lastRun, "r1", { assessed: 50, matched: 9, needs_answers: 30 }),
  m.withLiveCounts(lastRun, "r0", { assessed: 50, matched: 9, needs_answers: 30 }),
  m.withLiveCounts(lastRun, "r1", null),
];
out.constants = { all: m.ASSESS_ALL, k: m.ASSESS_CONCURRENCY, ceiling: m.ASSESS_ALL_CEILING, max: m.SELECTION_CAP_MAX };

// the poller: reads while a job runs; one onEnd when it ends
{
  const posts = [];
  const seen = [];
  let ended = 0;
  const answers = [
    { run_id: "r1", job: { status: "running", assessed: 1 } },
    { run_id: "r1", job: { status: "running", assessed: 2 } },
    { run_id: "r1", job: { status: "complete", assessed: 3 } },
  ];
  const timers = [];
  const poll = m.createAssessAllPoll({
    runId: "r1",
    post: (runId, body) => { posts.push([runId, body]); return Promise.resolve(answers.shift()); },
    onResponse: (r) => seen.push(r.job.status),
    onEnd: () => { ended += 1; },
    schedule: (fn) => { timers.push(fn); return timers.length; },
    cancel: () => {},
  });
  await poll.start();
  while (timers.length) { await timers.shift()(); }
  out.poll = { posts, seen, ended };
}
// another run shown now: answers are dropped
{
  const seen = [];
  const poll = m.createAssessAllPoll({
    runId: "r1",
    post: () => Promise.resolve({ job: { status: "running" } }),
    onResponse: (r) => seen.push(r),
    isCurrent: () => false,
    schedule: () => 1,
    cancel: () => {},
  });
  await poll.read();
  out.dropped = seen.length;
}
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; the Assess all new model was not run")
    script = SCRIPT.replace("MODEL_URL", json.dumps((UI_SRC / "assessAllModel.js").resolve().as_uri()))
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False
    )
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


def test_the_estimate_line_says_count_model_minutes_and_k(out: dict) -> None:
    assert out["plan"] == [
        "406 postings, one Codex call each, about 72 min at 4 at a time.",
        "1 posting, one Claude call each, 4 at a time.",
        "",
    ]


def test_the_estimate_says_where_it_came_from_and_never_makes_one_up(out: dict) -> None:
    assert out["source"] == [
        "Estimated from 10 assessments this run made on this machine: about 42 s per call.",
        "No time estimate: no assessment on this run has been timed yet.",
        'Estimated from 1 earlier "Assess all new" call on this machine: about 30 s per call.',
    ]


def test_a_hosted_target_keeps_its_sends_your_resume_line(out: dict) -> None:
    ollama, codex, claude, openrouter = out["privacy"]
    assert ollama == "Runs locally through Ollama. Nothing leaves this machine."
    assert codex == "Uses the Codex CLI, which sends your resume to OpenAI."
    assert claude.startswith("Uses the Claude Code CLI, which sends your resume to Anthropic.")
    assert openrouter.startswith("Sends your resume to OpenRouter's API")


def test_all_new_is_the_default_for_local_targets_and_a_number_for_openrouter(out: dict) -> None:
    assert out["defaults"] == ["all", "all", "all", 25, 10, "all"]
    assert out["valid"] == [True, True, True, False, False, False, False, False]
    all_help, number_help = out["help"]
    assert all_help == (
        "Every matching posting is ranked, then every new one is assessed in full: one Codex call each, "
        "4 at a time (at most 500). How long that takes depends on how many are new."
    )
    assert number_help == (
        "Every matching posting is ranked. This many of the top-ranked ones are then assessed in full; "
        "you can assess the rest one at a time with Assess."
    )


def test_the_button_and_the_progress_lines(out: dict) -> None:
    assert out["button"] == ["Assess all new (406)", "Assess all new (0)"]
    assert out["jobs"] == [
        "Assessing: 12 of 406 assessed, 1 failed (4 at a time)",
        "Cancelled: 3 of 9 assessed. What finished is kept; Assess all new picks up the rest.",
        "Stopped when Scout stopped: 3 of 9 assessed. Assess all new picks up the rest.",
        "Done: 9 of 9 assessed.",
        "Stopped: 0 of 9 assessed (model_target_unavailable).",
        "",
    ]
    assert out["skip"] == ["Wait for the run to finish, then assess the rest.", "Select a profile to assess this run's postings.", ""]


def test_the_header_counts_follow_the_job_for_the_newest_run_only(out: dict) -> None:
    live, other, none = out["counts"]
    assert live["counts"] == {"found": 500, "new": 416, "assessed": 50, "matched": 9}
    assert other["counts"]["assessed"] == 10 and none["counts"]["assessed"] == 10


def test_the_poller_reads_while_a_job_runs_and_ends_once(out: dict) -> None:
    assert out["poll"]["posts"] == [["r1", {"start": True}], ["r1", {}], ["r1", {}]]
    assert out["poll"]["seen"] == ["running", "running", "complete"] and out["poll"]["ended"] == 1
    assert out["dropped"] == 0


def test_the_js_constants_are_the_python_ones(out: dict) -> None:
    assert out["constants"] == {
        "all": ASSESS_ALL,
        "k": assess_all.ASSESS_CONCURRENCY,
        "ceiling": ASSESS_ALL_CEILING,
        "max": SELECTION_CAP_MAXIMUM,
    }


def test_the_dialog_offers_all_new_postings() -> None:
    dialog = (UI_SRC / "components" / "RunConfirmDialog.jsx").read_text(encoding="utf-8")
    assert "RunConfirmDialog({ config, onConfirm, onCancel, submitting, error, initialKeywords })" in dialog
    assert '<option value={ASSESS_ALL}>All new postings</option>' in dialog
    assert "Top-ranked only (1-{SELECTION_CAP_MAX})" in dialog
    assert "defaultSelectionCap(config.default_assess_cap, config.default_model_target)" in dialog
    assert "capValid(selectionCap)" in dialog and "fullAssessmentsHelp(selectionCap, modelTarget)" in dialog
    assert "onConfirm({ selectionCap, modelTarget, keywords })" in dialog  # "all" goes to POST /api/run as it is


def test_the_jobs_page_has_the_button_the_confirm_line_and_live_counts() -> None:
    view = (UI_SRC / "views" / "FindJobsView.jsx").read_text(encoding="utf-8")
    assert 'data-action="assess-all"' in view and "assessAllButtonLabel(assessAllPlan)" in view
    assert 'data-role="assess-all-plan"' in view and "planLine(assessAllPlan)" in view
    assert "estimateSourceLine(assessAllPlan)" in view and "privacyLine(assessAllPlan.model_target)" in view
    assert 'data-action="assess-all-start"' in view and 'data-action="cancel-assess-all"' in view
    assert "withLiveCounts(newestRun, runId, assessAllCounts)" in view
    assert 'followAssessAll(id, "read")' in view and "loadQuickItems();" in view
    api = (UI_SRC / "api.js").read_text(encoding="utf-8")
    assert "/assess-all`" in api and "export function postAssessAll(runId, fields)" in api


def test_the_built_bundle_carries_assess_all_new() -> None:
    bundle = "".join(path.read_text(encoding="utf-8") for path in (UI / "dist" / "assets").glob("index-*.js"))
    assert "Assess all new (" in bundle and "All new postings" in bundle and "/assess-all" in bundle


def test_a_saved_all_cap_shows_as_all_new_postings_in_the_config_panel_and_profiles() -> None:
    for name in ("components/ConfigPanel.jsx", "views/ProfilesView.jsx"):
        source = (UI_SRC / name).read_text(encoding="utf-8")
        assert 'isAssessAll(config.default_assess_cap) ? "All new postings" : config.default_assess_cap' in source, name

"""0.1.11.5 ASSESS-01: the words of the Assess all dialog and of the batch's progress, run under node.

``ui/src/assessBatchModel.js`` and ``ui/src/postingsModel.js`` are plain JavaScript; the status objects here are
synthetic and shaped as ``GET /api/postings/assess/status`` serves them (``assess_batch_job.status``). LOUD skip
without ``node``.

Pinned, by the ticket's part:
(2) the progress ("12 of 50 assessed", the estimate, the profile), the job page's "Assessing…", how a batch ended,
    and the read loop: it reads only while a batch runs, tells the page when a posting got its result and once when
    the batch ended;
(3) never "This can take a minute": the estimate, "about 29 min for 50";
(4) the low-rank box says what it does ("Include the 105 low-ranked ones in the pool (still 50 per run)") and is
    hidden, with a line that says why, when it cannot change the run;
(5) "Assess top 50 of 177" and "Or assess as <profile>".
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

SCRIPT = """
import * as b from BATCH_URL;
import * as m from POSTINGS_URL;

const profiles = [{ profile_id: "p1", label: "Platform track" }, { profile_id: "p2", label: "Staff Engineer" }];
const running = {
  schema_version: "scout-assess-batch:1", running: true, last: null,
  batch: { id: "batch_a", status: "running", total: 50, assessed: 12, failed: 0, in_flight: 4, profile_id: "p1", estimate_seconds: 1728, pending: ["https://jobs.example/a/1", "https://jobs.example/a/2"], here: true },
};
const failing = { ...running, batch: { ...running.batch, failed: 1, estimate_seconds: null, profile_id: "gone" } };
const cancelling = { ...running, batch: { ...running.batch, status: "cancelling", assessed: 14 } };
const cancellingOne = { ...running, batch: { ...running.batch, status: "cancelling", in_flight: 1 } };
const idle = { schema_version: "scout-assess-batch:1", running: false, batch: null, last: null };
const out = {};
out.minutes = [1728, 60, 30, 0, null, 9000].map((s) => b.aboutMinutes(s));
out.estimateFor = [b.estimateFor(1728, 50), b.estimateFor(null, 50)];
out.lines = [b.batchProgressLine(running, profiles), b.batchProgressLine(failing, profiles), b.batchProgressLine(cancelling, profiles), b.batchProgressLine(idle, profiles), b.batchProgressLine(null)];
out.progress = [b.batchProgress(running, profiles), b.batchProgress(cancelling, profiles), b.batchProgress(cancellingOne, profiles)];
const cancellingNone = { ...running, batch: { ...running.batch, status: "cancelling", in_flight: 0 } };
// B3b: Cancel was clicked and the next status is not read yet: the page says it from the status it holds.
out.cancelSent = [b.batchProgress(running, profiles, { cancelSent: true }), b.batchProgress(cancellingNone, profiles), b.batchProgress(idle, profiles, { cancelSent: true })];
out.running = [b.batchRunning(running), b.batchRunning(idle), b.batchRunning(null), b.batchRunning({ running: true, batch: {} })];
out.started = [b.isBatchStarted({ ...running, status: "started" }), b.isBatchStarted(running), b.isBatchStarted({ status: "assessed", schema_version: "scout-postings-assess:1" })];
out.end = [
  b.batchEndLine({ status: "cancelled", requested: 50, assessed: 14, failed: 0, not_started: 36, failed_codes: [] }),
  b.batchEndLine({ status: "cancelled", requested: 4, assessed: 3, failed: 0, not_started: 1, failed_codes: [] }),
  b.batchEndLine({ status: "done", requested: 50, assessed: 49, failed: 1, not_started: 0, failed_codes: ["assess_timeout"], more_after: 127 }),
  b.batchEndLine({ status: "done", requested: 4, assessed: 4, failed: 0, not_started: 0, failed_codes: [], more_after: 0 }),
  b.batchEndLine({ status: "failed", requested: 50, assessed: 3, failed: 0, error_code: "config_unavailable", failed_codes: [] }),
  b.batchEndLine(null),
];
out.job = [
  b.jobBatchLine(running, "https://jobs.example/a/1"), b.jobBatchLine(running, "https://jobs.example/other"),
  b.jobBatchLine(cancelling, "https://jobs.example/a/2"), b.jobBatchLine(idle, "https://jobs.example/a/1"),
];
out.waits = [b.jobWaitsInBatch(running, "https://jobs.example/a/1"), b.jobWaitsInBatch(running, "https://jobs.example/other"), b.jobWaitsInBatch(idle, "x")];
const later = { ...running, batch: { ...running.batch, assessed: 13, pending: ["https://jobs.example/a/2"] } };
out.left = [b.jobLeftBatch(running, later, "https://jobs.example/a/1"), b.jobLeftBatch(running, later, "https://jobs.example/a/2"), b.jobLeftBatch(running, idle, "https://jobs.example/a/2"), b.jobLeftBatch(idle, idle, "x")];
out.button = [177, 50, 51, 12, 1, 0, null].map((n) => b.assessAllLabel(n));
out.assessAs = b.assessAsLabel("Staff Engineer");

// The approval dialog of the ticket's session: 72 above the threshold, 105 low-ranked, 50 a run.
const estimate = { calls: 50, tokens: 1170000, seconds: 1728, basis_calls: 9 };
const ask = (low) => ({ status: "ask", question: { to_assess: 72, batch: 50, more_after: 22, estimate, by_profile: [], yes: { api: { body: { states: ["not_assessed"] } } } }, low_rank: low });
const lowBlock = (included) => ({ skipped: 105, batch: 50, more_after: 55, min_rank: 50, estimate: { calls: 50 }, yes: { api: { body: { states: ["not_assessed"] } } }, included });
const unchanged = m.approvalDialog(ask(lowBlock({ pool: 177, batch: 50, more_after: 127, low_ranked_in_batch: 0, changes_batch: false, estimate })), []);
const changed = m.approvalDialog(ask(lowBlock({ pool: 177, batch: 50, more_after: 127, low_ranked_in_batch: 12, changes_batch: true, estimate })), []);
const small = m.approvalDialog({ status: "ask", question: { to_assess: 4, batch: 4, more_after: 0, estimate: { calls: 4, seconds: 138 }, by_profile: [], yes: { api: { body: {} } } },
  low_rank: { skipped: 3, batch: 3, more_after: 0, min_rank: 50, estimate: { calls: 3 }, yes: { api: { body: {} } }, included: { pool: 7, batch: 7, more_after: 0, low_ranked_in_batch: 3, changes_batch: true, estimate: { calls: 7, seconds: 242 } } } }, []);
const oldServer = m.approvalDialog(ask({ skipped: 105, batch: 50, more_after: 55, min_rank: 50, estimate: { calls: 50 }, yes: { api: { body: {} } } }), []);
out.box = {
  unchanged: [m.lowRankLine(unchanged.lowRank, unchanged.count), m.lowRankNote(unchanged.lowRank, unchanged.count)],
  changed: [m.lowRankLine(changed.lowRank, changed.count), m.lowRankNote(changed.lowRank, changed.count)],
  small: [m.lowRankLine(small.lowRank, small.count), m.lowRankNote(small.lowRank, small.count)],
  oldServer: [m.lowRankLine(oldServer.lowRank, oldServer.count), m.lowRankNote(oldServer.lowRank, oldServer.count)],
  none: [m.lowRankLine(null, 5), m.lowRankNote(null, 5)],
};
out.smallEstimate = [m.estimateLine(m.shownEstimate(small, false)), m.estimateLine(m.shownEstimate(small, true))];
out.starting = [m.assessingLine(unchanged), m.assessingLine(small), m.assessingLine(small, true), m.assessingLine({ count: 3, lowRank: null })];
out.bodies = [m.approvalBody(changed, false), m.approvalBody(changed, true)];

// The read loop on a fake clock: a read only while a batch runs.
{
  const answers = [running, later, later, { ...idle, last: { status: "done", requested: 50, assessed: 50 } }];
  const log = [];
  const timers = [];
  const watch = b.createBatchWatch({
    read: () => Promise.resolve(answers.shift()),
    onStatus: (status) => log.push(status.running ? `status ${status.batch.assessed}` : "status idle"),
    onProgress: (status) => log.push(`progress ${status.batch.assessed}`),
    onEnd: (last) => log.push(`end ${last.status}`),
    schedule: (fn) => timers.push(fn) && timers.length,
    cancel: () => {},
  });
  await watch.check();
  while (timers.length) {
    await timers.shift()();
  }
  out.watch = { log, readsLeft: answers.length, timersLeft: timers.length };
  const quiet = [];
  const idleWatch = b.createBatchWatch({ read: () => Promise.resolve(idle), onStatus: () => quiet.push("status"), onEnd: () => quiet.push("end"), schedule: (fn) => quiet.push("timer"), cancel: () => {} });
  await idleWatch.check();
  out.idleWatch = quiet;
  // The approval's answer for a batch that is over already (a fast one): the page still hears how it ended.
  const fast = [];
  const fastWatch = b.createBatchWatch({ read: () => Promise.resolve(idle), onStatus: () => {}, onEnd: (last) => fast.push(`end ${last.status}`), schedule: () => fast.push("timer"), cancel: () => {} });
  fastWatch.take({ ...idle, status: "started", last: { status: "done", requested: 2, assessed: 2 } });
  out.fastWatch = fast;
}
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the assess batch model was NOT run")
    assert node is not None
    script = SCRIPT.replace("BATCH_URL", json.dumps((UI_SRC / "assessBatchModel.js").resolve().as_uri())).replace(
        "POSTINGS_URL", json.dumps((UI_SRC / "postingsModel.js").resolve().as_uri())
    )
    completed = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


def test_the_progress_says_how_many_of_how_many_the_estimate_and_the_profile(out: dict) -> None:
    assert out["lines"] == [
        "Assessing: 12 of 50 assessed · about 29 min for 50 · Platform track",
        "Assessing: 12 of 50 assessed, 1 failed",  # no estimate, a profile that is not listed: neither is made up
        "Cancelling: finishing the 4 in flight · 14 of 50 assessed · Platform track",
        None, None,
    ]
    first, cancelling, one = out["progress"]
    assert (first["line"], first["estimate"], first["profile"], first["canCancel"], first["waiting"]) == ("12 of 50 assessed", "about 29 min for 50", "Platform track", True, None)
    assert cancelling["canCancel"] is False
    # 0.1.11.5 B3b: never a bare "Cancelling…": the page says how many calls it is finishing (the status's `in_flight`).
    assert (first["cancelling"], first["cancelLine"]) == (False, None)
    assert cancelling["cancelLine"] == "Cancelling: finishing the 4 in flight"
    assert one["cancelLine"] == "Cancelling: finishing the 1 in flight"
    assert cancelling["waiting"] == one["waiting"] == "No further model call starts. What finished is kept."
    sent, none_flying, no_batch = out["cancelSent"]
    assert (sent["cancelling"], sent["canCancel"], sent["cancelLine"]) == (True, False, "Cancelling: finishing the 4 in flight"), "between the click and the next status the page said nothing of the calls in flight"
    assert none_flying["cancelLine"] == "Cancelling: no call is in flight" and no_batch is None
    assert out["running"] == [True, False, False, False]
    assert out["started"] == [True, False, False]


def test_the_estimate_is_said_never_this_can_take_a_minute(out: dict) -> None:
    assert out["minutes"] == ["about 29 min", "about 1 min", "under a minute", None, None, "about 2.5 h"]
    assert out["estimateFor"] == ["about 29 min for 50", None]
    assert out["starting"][0].startswith("Starting 50 postings: about 29 min for 50.")
    assert out["starting"][1].startswith("Starting 4 postings: about 2 min for 4.")
    assert out["starting"][2].startswith("Starting 7 postings: about 4 min for 7.")  # the box ticked: the run with the low-ranked ones
    assert out["starting"][3].startswith("Starting 3 postings.")  # no estimate: none is made up
    for name in ("postingsModel.js", "assessBatchModel.js", "components/AssessApprovalDialog.jsx", "components/AssessBatchProgress.jsx"):
        assert "can take a minute" not in (UI_SRC / name).read_text(encoding="utf-8").lower(), name


def test_the_low_rank_box_says_what_it_does_or_is_hidden_when_it_changes_nothing(out: dict) -> None:
    box = out["box"]
    # 50 or more are ranked above the threshold: the run would be the same 50. No box; a line says why.
    assert box["unchanged"] == [None, "105 low-ranked ones are left out (rank below 50). Including them would not change this run: its 50 are all ranked higher."]
    # Some of them would be among the 50 (postings not ranked yet come after every ranked one).
    assert box["changed"] == ["Include the 105 low-ranked ones in the pool (still 50 per run): 12 of them would be in this run.", None]
    # Fewer than 50 in all: the run grows.
    assert box["small"] == ["Include the 3 low-ranked ones (rank below 50): 7 postings in this run instead of 4.", None]
    # A server that does not say what the box would change: the box, in the ticket's words.
    assert box["oldServer"] == ["Include the 105 low-ranked ones in the pool (still 50 per run).", None]
    assert box["none"] == [None, None]
    for line in (box["changed"][0], box["small"][0], box["oldServer"][0]):
        assert "too?" not in line and "model call" not in line, "the box must not read as more calls"
    assert out["smallEstimate"] == ["~4 model calls, ~2.3 min", "~7 model calls, ~4 min"]
    assert out["bodies"] == [{"states": ["not_assessed"], "approve": True}, {"states": ["not_assessed"], "approve": True, "include_low_rank": True}]
    dialog = (UI_SRC / "components" / "AssessApprovalDialog.jsx").read_text(encoding="utf-8")
    assert "{lowRankLine(low, dialog.count) && (" in dialog and 'data-testid="approval-low-rank-note"' in dialog


def test_the_button_says_how_many_and_the_row_link_says_or(out: dict) -> None:
    assert out["button"] == ["Assess top 50 of 177", "Assess all 50", "Assess top 50 of 51", "Assess all 12", "Assess all 1", "Assess all", "Assess all"]
    assert out["assessAs"] == "Or assess as Staff Engineer"
    view = (UI_SRC / "views" / "JobsView.jsx").read_text(encoding="utf-8")
    assert "{assessAllLabel(notAssessedCount(counts))}" in view and "{assessAsLabel(tag.label)}" in view
    assert "Assess as {tag.label}" not in view


def test_how_a_batch_ended_is_one_line(out: dict) -> None:
    assert out["end"] == [
        "Cancelled: 14 of 50 assessed. What finished is kept; 36 were not started.",
        "Cancelled: 3 of 4 assessed. What finished is kept; 1 was not started.",
        'Assessed 49 of 50. Not assessed: assess_timeout. 127 more not assessed yet: 50 at a time, "Assess all" takes the next.',
        "Assessed 4 of 4.",
        "The batch stopped: 3 of 50 assessed (config_unavailable). What finished is kept.",
        None,
    ]


def test_a_job_page_says_whether_its_posting_is_still_being_assessed(out: dict) -> None:
    assert out["job"] == [
        "Assessing… this posting is in the running batch (12 of 50 assessed).",
        "An assess batch is running (12 of 50 assessed). This posting is not waiting in it.",
        "The assess batch is being cancelled (14 of 50 assessed). This posting is assessed only if its call has already started.",
        None,
    ]
    assert out["waits"] == [True, False, False]
    assert out["left"] == [True, False, True, False]
    page = (UI_SRC / "views" / "JobPage.jsx").read_text(encoding="utf-8")
    assert 'data-testid="job-assess-batch"' in page and "!jobWaitsInBatch(batch.status, batchJob.current) &&" in page


def test_the_page_reads_only_while_a_batch_runs_and_hears_of_each_result_and_of_the_end(out: dict) -> None:
    assert out["watch"] == {
        "log": ["status 12", "status 13", "progress 13", "status 13", "status idle", "end done"],
        "readsLeft": 0, "timersLeft": 0,
    }
    assert out["idleWatch"] == ["status"], "no batch: one read, no timer, no end"
    assert out["fastWatch"] == ["end done"], "a batch that ended before its approval answered: the end is still said"


def test_the_dialog_closes_at_the_start_and_its_cancel_is_never_off(out: dict) -> None:
    dialog = (UI_SRC / "components" / "AssessApprovalDialog.jsx").read_text(encoding="utf-8")
    assert '<button type="button" className="button secondary" onClick={onCancel} data-action="approval-cancel">' in dialog
    view = (UI_SRC / "views" / "JobsView.jsx").read_text(encoding="utf-8")
    assert "if (isBatchStarted(answer)) {" in view and "batch.adopt(answer);" in view
    assert "{batch.running && <AssessBatchProgress batch={batch} profiles={profiles} />}" in view
    assert "cancelStart.current = approving;" in view

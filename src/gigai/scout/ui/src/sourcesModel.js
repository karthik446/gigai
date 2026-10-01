// uat-batch2 (N11-C): the words and rules behind Settings' "Update sources"
// action and the Jobs page's 'run Update sources' message, as pure
// functions (no React) so the node-backed static test can pin them.
//
// The data is GET /api/sources/update (find_jobs/api/sources.py):
//   running  an update is live: the button is off, the GET is polled
//   update   null (no update ever ran here), or the running / the last one:
//            status running | succeeded | partial | failed | interrupted,
//            boards {total, done, checked, fetched, cached, failed,
//            skipped, never_checked}, summary (already worded),
//            error {code, message}, remaining
//   index    what the Jobs page needs: status ready | empty | stale,
//            needs_update, message (ready to show), companies_indexed,
//            last_checked_at
import { formatCount } from "./runText.js";

export const SOURCES_POLL_MS = 2500;
// A running update rewrites its snapshot every ~5 s (sources_update.py). One
// that has not moved for this long has most likely lost its process; the
// server itself calls it interrupted after 300 s.
export const STUCK_AFTER_SECONDS = 60;

function count(value) {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : 0;
}

function boardsNoun(value) {
  return `company board${value === 1 ? "" : "s"}`;
}

export function isRunning(status) {
  return Boolean(status && (status.running || (status.update && status.update.status === "running")));
}

// How many boards the update really asked: `boards.checked`. A server from
// before that field reports `boards.done` only, which also counts the
// boards its time budget skipped (`boards.skipped`); those were not checked.
export function boardsChecked(boards) {
  const total = count(boards && boards.total);
  const value = boards || {};
  const checked =
    typeof value.checked === "number" && Number.isFinite(value.checked) ? count(value.checked) : Math.max(0, count(value.done) - count(value.skipped));
  return Math.min(checked, total);
}

// The progress bar while an update runs: `boards.checked` of `boards.total`.
// `total` is 0 for the first seconds (the watchlist is being listed), which
// is an indeterminate bar, never "0 of 0". Null when nothing is running.
export function sourcesProgress(update) {
  if (!update || update.status !== "running") {
    return null;
  }
  const boards = update.boards || {};
  const total = count(boards.total);
  if (total === 0) {
    return { determinate: false, percent: null, line: "Listing the company boards to check…" };
  }
  const checked = boardsChecked(boards);
  const failed = count(boards.failed);
  const percent = Math.round((checked / total) * 100);
  const line = `Checked ${formatCount(checked)} of ${formatCount(total)} ${boardsNoun(total)}${failed ? `; ${formatCount(failed)} did not answer` : ""}.`;
  return { determinate: true, percent, line };
}

function checkedLine(update) {
  const boards = update.boards || {};
  const total = count(boards.total);
  if (total === 0) {
    const fresh = count(boards.up_to_date);
    return fresh ? `All ${formatCount(fresh)} ${boardsNoun(fresh)} were checked recently; nothing to refresh.` : "";
  }
  const failed = count(boards.failed);
  return `Checked ${formatCount(boardsChecked(boards))} of ${formatCount(total)} ${boardsNoun(total)}${failed ? `; ${formatCount(failed)} did not answer` : ""}.`;
}

// The real backlog after an update that ran out of time: the boards no
// update has ever checked (`boards.never_checked`). "" when there is none,
// or when the server does not say.
function neverCheckedLine(update) {
  const never = count(update.boards && update.boards.never_checked);
  return never ? `${formatCount(never)} ${boardsNoun(never)} ${never === 1 ? "has" : "have"} never been checked yet.` : "";
}

// What the last update came to, once it is no longer running:
//   {tone, line, detail, backlog, next}  tone is ok | warn | danger
// `line` is the server's own summary; `backlog` (a partial update only)
// says how many boards have never been checked; `next` says what to do
// about a partial / interrupted update. Null while one runs or when none
// ever ran.
export function sourcesResult(update) {
  if (!update || update.status === "running") {
    return null;
  }
  const summary = typeof update.summary === "string" && update.summary.trim() ? update.summary.trim() : "";
  const checked = checkedLine(update);
  if (update.status === "succeeded") {
    return { tone: "ok", line: summary || "Sources are up to date.", detail: checked, next: "" };
  }
  if (update.status === "partial") {
    const remaining = count(update.remaining);
    const left = remaining ? `${formatCount(remaining)} ${boardsNoun(remaining)} ${remaining === 1 ? "is" : "are"} left` : "some company boards are left";
    return {
      tone: "warn",
      line: summary || "The update ran out of time before it reached every board.",
      detail: checked,
      backlog: neverCheckedLine(update),
      next: `Run again to continue: ${left}.`,
    };
  }
  if (update.status === "failed") {
    const message = update.error && typeof update.error.message === "string" ? update.error.message.trim() : "";
    return { tone: "danger", line: "The update failed.", detail: message, next: "" };
  }
  if (update.status === "interrupted") {
    return { tone: "warn", line: "The last update stopped before it finished.", detail: checked, next: "Run again to continue." };
  }
  return { tone: "warn", line: summary || "The last update ended in a state this page does not know.", detail: checked, next: "" };
}

// True when a running update has not written its snapshot for a while: the
// page then offers to start over ({"force": true}).
export function looksStuck(update, now = Date.now()) {
  if (!update || update.status !== "running") {
    return false;
  }
  const at = new Date(update.updated_at || update.started_at || "").getTime();
  if (Number.isNaN(at)) {
    return false;
  }
  return now - at > STUCK_AFTER_SECONDS * 1000;
}

// "10,362 companies stored on this machine", or "" when none is.
export function storedLine(index) {
  const stored = count(index && index.companies_indexed);
  return stored ? `${formatCount(stored)} compan${stored === 1 ? "y" : "ies"} stored on this machine` : "";
}

// The Jobs page's message: the server's own `index.message` when the stored
// postings are missing or out of date. Null when there is nothing to say
// (the index is ready, company boards are off, or the read failed).
export function indexNotice(status, { atsEnabled = true } = {}) {
  const index = status && status.index;
  if (!atsEnabled || !index || !index.needs_update) {
    return null;
  }
  const message =
    typeof index.message === "string" && index.message.trim()
      ? index.message.trim()
      : "The stored company postings need an update. Run Update sources, then search again.";
  return { message, running: isRunning(status), status: index.status || null };
}

// What a refused start says (POST /api/sources/update).
export function startErrorText(error) {
  const code = error && error.code;
  if (code === "sources_update_running") {
    return "An update is already running.";
  }
  if (code === "target_unavailable") {
    return "No Scout project is set up on this machine yet, so there are no sources to update.";
  }
  return (error && (error.detail || error.message)) || "The update could not be started.";
}

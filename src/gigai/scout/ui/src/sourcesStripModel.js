// uat-bug-048: the Jobs page's sources strip and first-run steps, as pure
// functions (no React) so the node-backed static test can pin them.
//
// The data is the same GET /api/sources/update Settings > Sources reads
// (sourcesModel.js): `index` {status ready|empty|stale, companies_indexed,
// last_checked_at} and `update` (the running / last update, boards.total).
import { formatCount } from "./runText.js";
import { boardsChecked, isRunning, sourcesProgress } from "./sourcesModel.js";

// MEASURED, not estimated: the operator's own update on 2026-09-29 (read from
// ~/.gigai/cache/scout/companies/last-update.json, status succeeded,
// started 18:18:14Z, finished 18:33:05Z = 14.85 min for 10,370 boards, 50
// did not answer). Shown as "about 15 min". Re-measure before changing it.
export const MEASURED_UPDATE_MINUTES = 15;

function count(value) {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : 0;
}

function ageLabel(iso, now) {
  const at = new Date(iso || "").getTime();
  if (!iso || Number.isNaN(at)) {
    return "";
  }
  const minutes = Math.max(0, Math.round((now - at) / 60000));
  if (minutes < 1) {
    return "just now";
  }
  if (minutes < 60) {
    return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  }
  const hours = Math.round(minutes / 60);
  if (hours < 24) {
    return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  }
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

// The boards an update would check, only when the server said so (the last
// or running update's boards.total). Never a constant.
function knownBoardTotal(status) {
  return count(status && status.update && status.update.boards && status.update.boards.total);
}

// What the strip shows:
//   kind      unknown (not read / failed) | empty | fresh | stale
//   running   an update is live: `progress` is the inline bar
//   line      the strip's sentence ("" for unknown)
//   amber     stale ("out of date")
//   steps     the numbered first-run steps (empty store only), else null
//   runBlocked  why Run find jobs is off ("" when it is on)
export function sourcesStrip(status, { now = Date.now() } = {}) {
  const index = status && status.index;
  const running = isRunning(status);
  if (!index) {
    return { kind: "unknown", running: false, progress: null, line: "", amber: false, steps: null, runBlocked: "" };
  }
  const stored = count(index.companies_indexed);
  const progress = running ? sourcesProgress(status.update) : null;
  if (index.status === "empty" || stored === 0) {
    const total = knownBoardTotal(status);
    const boards = total ? `~${formatCount(total)} company boards` : "the company boards on your watchlist";
    return {
      kind: "empty",
      running,
      progress,
      line: "Company postings: none stored on this machine yet",
      amber: false,
      steps: {
        highlightStep: 1,
        update: `Update sources: downloads postings from ${boards} (uses the network, no model; about ${MEASURED_UPDATE_MINUTES} min)`,
        run: "Run find jobs",
      },
      runBlocked: running ? "Updating sources… Run find jobs opens when it finishes." : "Update sources first, then run.",
    };
  }
  const stale = index.status === "stale";
  const age = ageLabel(index.last_checked_at, now);
  const companies = `${formatCount(stored)} compan${stored === 1 ? "y" : "ies"} stored`;
  return {
    kind: stale ? "stale" : "fresh",
    running,
    progress,
    line: `Company postings: ${companies}${age ? ` · updated ${age}` : ""}${stale ? " · out of date" : ""}`,
    amber: stale,
    steps: null,
    runBlocked: "",
  };
}

// The empty-state text of a profile with no runs.
export function noRunText(strip) {
  if (strip && strip.kind === "empty") {
    return "No find-jobs run yet for this profile. First click Update sources above (step 1), then Run find jobs (step 2) to see its postings here, or assess a single posting under";
  }
  return "No find-jobs run yet for this profile. Run one above to see its postings here, or assess a single posting under";
}

// Where the setup wizard's Finish lands: always Jobs from a first run; from
// Settings, Jobs only when the store is empty (step 1 highlighted there).
export function finishLanding(status, { fromSettings = false } = {}) {
  const empty = Boolean(status && status.index && (status.index.status === "empty" || count(status.index.companies_indexed) === 0));
  return { toJobs: empty || !fromSettings, highlightUpdateStep: empty };
}

export { boardsChecked };

// uat-bug-048: the Jobs page's sources strip and first-run steps, as pure
// functions (no React) so the node-backed static test can pin them.
//
// The data is the same GET /api/sources/update Settings > Sources reads
// (sourcesModel.js): `index` {status ready|empty|stale, companies_indexed,
// last_checked_at} and `update` (the running / last update, boards.total).
import { formatCount } from "./runText.js";
import { boardsChecked, isRunning, sourcesProgress } from "./sourcesModel.js";

// A lower bound: the operator's own update on 2026-09-29 (read from
// ~/.gigai/cache/scout/companies/last-update.json, status succeeded,
// started 18:18:14Z, finished 18:33:05Z = 14.85 min for 10,370 boards, 50
// did not answer) was partly served from cache. Shown as "15 minutes or more".
// Re-measure a cold first update before turning this into an estimate.
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

// The compact first-run stepper: [{n, title, description, state, action}].
// state is done | current | todo; action is the button the step carries
// ("update-sources") or null. Step 1 is done once the store holds postings
// (or the last update finished with some); step 2 is done once a run exists.
export function firstRunSteps(status, { hasRun = false, boards = "", running = false, stored = "" } = {}) {
  const index = status && status.index;
  const storeReady = Boolean(index) && index.status !== "empty" && count(index.companies_indexed) > 0;
  const step1 = storeReady ? "done" : "current";
  const step2 = hasRun ? "done" : storeReady ? "current" : "todo";
  const scope = boards || "the company boards on your watchlist";
  return [
    {
      n: 1,
      title: "Update sources",
      description: storeReady
        ? `${stored || "Postings stored"} on this machine.`
        : `Downloads postings from ${scope} (network only, no model; can take ${MEASURED_UPDATE_MINUTES} minutes or more the first time).`,
      state: step1,
      action: "update-sources",
    },
    {
      n: 2,
      title: "Run find jobs",
      description: "Finds and ranks postings for this profile.",
      state: step2,
      action: null,
    },
  ];
}

// What the strip shows:
//   kind      unknown (not read / failed) | empty | fresh | stale
//   running   an update is live: `progress` is the inline bar
//   line      the strip's sentence ("" for unknown)
//   amber     stale ("out of date")
//   steps     the first-run stepper (empty store, or no run yet), else null
//   runBlocked  why Run find jobs is off ("" when it is on)
export function sourcesStrip(status, { now = Date.now(), hasRun = true } = {}) {
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
      steps: firstRunSteps(status, { hasRun: false, boards, running }),
      runBlocked: running ? "Updating sources… Run find jobs opens when it finishes." : "Update sources first, then run.",
    };
  }
  const stale = index.status === "stale";
  const age = ageLabel(index.last_checked_at, now);
  const companies = `${formatCount(stored)} compan${stored === 1 ? "y" : "ies"} stored`;
  const steps = hasRun ? null : firstRunSteps(status, { hasRun, running, stored: companies });
  return {
    kind: stale ? "stale" : "fresh",
    running,
    progress,
    line: `Company postings: ${companies}${age ? ` · updated ${age}` : ""}${stale ? " · out of date" : ""}`,
    amber: stale,
    steps,
    runBlocked: "",
  };
}

// The empty-state text of a profile with no runs: short while the stepper
// is showing (the steps already say what to do).
export function noRunText(strip) {
  if (strip && strip.steps) {
    return "No runs yet.";
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

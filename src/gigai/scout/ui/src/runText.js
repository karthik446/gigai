// uat-batch1 (N12): the words the run-confirm dialog and the run's progress
// line use. Pure functions (no React), pinned by the node-backed static
// test. Nothing here says "?" or "unknown": a number that is not known yet
// is left out of the sentence.

export function formatCount(value) {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("en-US") : String(value);
}

// FindJobsConfig.sources keys (contracts.py SourceToggles) in plain words.
const SOURCE_LABELS = {
  exa: "Exa web search",
  ats: "Company job boards (Greenhouse, Lever, Ashby)",
  hiringcafe: "hiring.cafe",
};

export function sourceLabel(name) {
  return SOURCE_LABELS[name] || name;
}

// GET /api/watchlist -> entries[] (WatchlistEntry, contracts.py), counted
// by where each board came from (`first_seen.query_key`):
//   catalog  "catalog:<revision>"  the catalog seeding added it
//            (watchlist.seed_watchlist_from_catalog)
//   added    "operator_add"        "Add company" / `gigai scout watchlist add`
//            (watchlist.OPERATOR_ADDED_QUERY_KEY)
//   found    anything else         a search or a discovery session found it
export function watchlistSummary(entries) {
  const list = Array.isArray(entries) ? entries : [];
  const summary = { total: list.length, catalog: 0, added: 0, found: 0 };
  list.forEach((entry) => {
    const queryKey = entry && entry.first_seen && entry.first_seen.query_key;
    if (typeof queryKey === "string" && queryKey.startsWith("catalog:")) {
      summary.catalog += 1;
    } else if (queryKey === "operator_add") {
      summary.added += 1;
    } else {
      summary.found += 1;
    }
  });
  return summary;
}

// The run-confirm dialog's "Company boards" line. `summary` is
// watchlistSummary()'s result, or null while GET /api/watchlist is loading
// or when it failed (`failed`).
export function companyBoardsLine({ atsEnabled, summary, failed }) {
  if (!atsEnabled) {
    return "Not checked: company job boards are turned off in your configuration.";
  }
  if (failed) {
    return "The boards on your watchlist (the count could not be loaded).";
  }
  if (!summary) {
    return "Counting the boards on your watchlist…";
  }
  if (summary.total === 0) {
    return "No boards on your watchlist yet. This run adds the company catalog's boards that fit your preferences, then checks them.";
  }
  const boards = `${formatCount(summary.total)} company board${summary.total === 1 ? "" : "s"} on your watchlist`;
  const parts = [];
  if (summary.catalog > 0) {
    parts.push(`${formatCount(summary.catalog)} from the company catalog`);
  }
  if (summary.found > 0) {
    parts.push(`${formatCount(summary.found)} found by your searches`);
  }
  if (summary.added > 0) {
    parts.push(`${formatCount(summary.added)} added by you`);
  }
  return `${boards}${parts.length ? ` (${parts.join(", ")})` : ""}. Each run checks the least recently checked boards first, as many as fit in its time limit.`;
}

function cadenceText(runs) {
  if (runs === 1) {
    return "Every board is checked every run.";
  }
  if (typeof runs === "number" && runs > 1) {
    return `A full pass over every board takes about ${formatCount(runs)} runs.`;
  }
  return "";
}

function minutes(seconds) {
  if (typeof seconds !== "number" || !(seconds > 0)) {
    return "";
  }
  const value = Math.round(seconds / 60);
  return value >= 1 ? `${value} min` : `${Math.round(seconds)} s`;
}

// acquire-rotation: one line under the step pills while/after acquire pages
// through the watchlist, from GET /progress's `rotation` and `boards`
// blocks (progress.py). Three states:
//
//   measured   `rotation.last` is set (the pass ended): "Checked boards
//              N–M of T this run."
//   estimated  the pass is running and an earlier run measured a page
//              (`page_size`): "Checking about P of T company boards …"
//   first run  nothing measured yet (or this run already passed the
//              estimate): "Checking up to T company boards this run (as
//              many as fit in 20 min)", with the live count.
export function rotationLine(rotation, boards) {
  if (!rotation || typeof rotation.total !== "number") {
    return null;
  }
  const total = formatCount(rotation.total);
  const first = typeof rotation.first === "number" ? rotation.first : 1;
  const cadence = cadenceText(rotation.runs_per_rotation);
  const running = Boolean(boards && boards.status === "running");
  const done = running && typeof boards.done === "number" ? ` ${formatCount(boards.done)} done so far.` : "";
  const join = (...parts) => parts.filter(Boolean).join(" ").replace(/\s+/g, " ").trim();

  if (typeof rotation.last === "number") {
    return join(`Checked boards ${formatCount(first)}–${formatCount(rotation.last)} of ${total} this run.`, cadence);
  }
  if (!running) {
    const checked = boards ? ["fetched", "cached", "failed"].reduce((sum, name) => sum + (typeof boards[name] === "number" ? boards[name] : 0), 0) : 0;
    return join(checked > 0 ? `Checked ${formatCount(checked)} of ${total} company boards this run.` : `${total} company boards on your watchlist.`, cadence);
  }
  // The estimate is the PREVIOUS run's page. Once this run has passed it
  // (the watchlist grew, or this run has more time), it says nothing about
  // this run, and neither does the cadence derived from it.
  const estimate = typeof rotation.page_size === "number" && rotation.page_size > 0 ? Math.min(rotation.page_size, rotation.total) : null;
  if (estimate !== null && !(typeof boards.done === "number" && boards.done > estimate)) {
    const scope =
      estimate === rotation.total
        ? `Checking all ${total} company boards this run.`
        : `Checking about ${formatCount(estimate)} of ${total} company boards this run, starting at board ${formatCount(first)}.`;
    return join(scope, done, cadence);
  }
  const limit = minutes(boards.budget_seconds);
  return join(`Checking up to ${total} company boards this run${limit ? ` (as many as fit in ${limit})` : ""}.`, done, "Boards it does not reach go first next run.");
}

// uat-bug-012: what a run page says about a run that did not succeed, from
// GET /api/runs/{id} (RunStatusResponse: status + node_receipts[], each
// with `failure` {code, message} when its node failed). `postings` is the
// number of postings the run's own results carry (null while they load).
// Null for a run that succeeded or is still going.
const ENDED_BADLY = { failed: "failed", blocked: "was blocked", cancelled: "was cancelled", interrupted: "was interrupted" };

export function runFailure(runStatus, postings) {
  const ended = runStatus && ENDED_BADLY[runStatus.status];
  if (!ended) {
    return null;
  }
  const receipts = Array.isArray(runStatus.node_receipts) ? runStatus.node_receipts : [];
  const failed = receipts.find((receipt) => receipt && (receipt.failure || receipt.status === "failed")) || null;
  const where = failed && failed.node_slug ? ` during ${failed.node_slug}` : "";
  const rest =
    postings === null || postings === undefined
      ? ""
      : postings === 0
        ? "; no postings"
        : `; the ${formatCount(postings)} posting${postings === 1 ? "" : "s"} it had acquired ${postings === 1 ? "is" : "are"} below`;
  return {
    node: failed && failed.node_slug ? failed.node_slug : null,
    line: `This run ${ended}${where}${rest}.`,
    message: failed && failed.failure && typeof failed.failure.message === "string" ? failed.failure.message : null,
  };
}

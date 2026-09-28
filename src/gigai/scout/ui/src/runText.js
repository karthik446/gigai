// uat-batch1 (N12) + uat-batch2: the words the run-confirm dialog and the
// run's progress line use. Pure functions (no React), pinned by the node-backed static
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

// uat-batch2 (run dialog, coordinator 2026-09-28): the "Company boards"
// line reads GET /api/sources/update's `index` block (a few hundred bytes)
// instead of GET /api/watchlist (4.5 MB / 1.8 s at catalog size): a search
// reads the company boards stored on this machine, so the number that is
// true is how many are stored and when they were last updated.
//   index        {status: ready | empty | stale, needs_update,
//                companies_indexed, last_checked_at}, null while loading
//   lastUpdated  index.last_checked_at in words ("2 hours ago"), or ""
//   failed       the read failed
// `needsUpdate` tells the dialog to link to Settings' "Update sources".
export function indexedBoardsLine({ atsEnabled, index, lastUpdated, failed }) {
  if (!atsEnabled) {
    return { line: "Not checked: company job boards are turned off in your configuration.", needsUpdate: false };
  }
  if (failed) {
    return { line: "The company boards stored on this machine (the count could not be loaded).", needsUpdate: false };
  }
  if (!index) {
    return { line: "Counting the company boards stored on this machine…", needsUpdate: false };
  }
  const stored = typeof index.companies_indexed === "number" && index.companies_indexed > 0 ? index.companies_indexed : 0;
  if (stored === 0 || index.status === "empty") {
    return { line: "Update sources first: no company postings are stored on this machine yet.", needsUpdate: true };
  }
  const boards = `${formatCount(stored)} company board${stored === 1 ? "" : "s"} indexed${lastUpdated ? ` (last updated ${lastUpdated})` : ""}.`;
  if (index.needs_update) {
    return { line: `${boards} They are out of date: update sources first.`, needsUpdate: true };
  }
  return { line: boards, needsUpdate: false };
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

// N11-C part 2: a search reads the company postings stored on this machine
// (GET /progress -> boards.source === "index") and asks no board, so the
// rotation line does not apply to it. What the run page says instead, from
// the `boards` block, in words only:
//
//   read     "Read N indexed companies (updated …)."  boards.index.indexed
//            and its oldest / newest check times
//   fetched  "Fetched F new companies found by Exa."  boards.exa_new.fetched
//            (companies Exa found this run that were not stored yet)
//   waiting  "W more wait for the next Update sources."  boards.exa_new.waiting
//   update   boards.index.message when boards.index.needs_update
//
// `timeLabel(iso)` turns a time into words ("2 hours ago"); it is passed in
// so this stays a pure function. `settings: true` marks a line the page
// follows with a link to Settings' "Update sources". Null for a run that did
// not read the index (one sealed before the swap: its rotation line stands).
export function isIndexSearch(boards) {
  return Boolean(boards) && boards.source === "index";
}

function updatedText(index, timeLabel) {
  const label = (iso) => (iso && typeof timeLabel === "function" ? timeLabel(iso) : "");
  const oldest = label(index.oldest_checked_at);
  const newest = label(index.newest_checked_at);
  if (oldest && newest && oldest !== newest) {
    return ` (updated between ${oldest} and ${newest})`;
  }
  const one = newest || oldest;
  return one ? ` (updated ${one})` : "";
}

export function searchLines(boards, timeLabel) {
  if (!isIndexSearch(boards)) {
    return null;
  }
  const number = (value) => (typeof value === "number" && Number.isFinite(value) && value > 0 ? value : 0);
  const companies = (value) => `compan${value === 1 ? "y" : "ies"}`;
  const index = boards.index && typeof boards.index === "object" ? boards.index : {};
  const exa = boards.exa_new && typeof boards.exa_new === "object" ? boards.exa_new : {};
  const lines = [];

  const read = number(index.indexed) || number(boards.cached);
  if (read > 0) {
    lines.push({ role: "read", text: `Read ${formatCount(read)} indexed ${companies(read)}${updatedText(index, timeLabel)}.`, settings: false });
  } else if (boards.status === "running") {
    lines.push({ role: "read", text: "Reading the company postings stored on this machine…", settings: false });
  }

  const fetched = number(exa.fetched);
  const failed = number(exa.failed);
  if (fetched > 0) {
    lines.push({
      role: "fetched",
      text: `Fetched ${formatCount(fetched)} new ${companies(fetched)} found by Exa${failed ? `; ${formatCount(failed)} did not answer` : ""}.`,
      settings: false,
    });
  }
  const waiting = number(exa.waiting);
  if (waiting > 0) {
    lines.push({ role: "waiting", text: `${formatCount(waiting)} more wait${waiting === 1 ? "s" : ""} for the next Update sources.`, settings: true });
  }
  if (index.needs_update) {
    const message =
      typeof index.message === "string" && index.message.trim()
        ? index.message.trim()
        : "The stored company postings need an update. Run Update sources, then search again.";
    lines.push({ role: "update", text: message, settings: true });
  }
  return lines;
}

// uat-bug-011: GET /api/runs/{id}/progress -> not_imported_count, the
// postings that matched every filter but were left out of this run's
// import. Null (no line) unless it is a number above 0.
export function notImportedLine(count) {
  if (typeof count !== "number" || !Number.isFinite(count) || count <= 0) {
    return null;
  }
  return `${formatCount(count)} more matched, not imported this run.`;
}

// uat-bug-021: what Jev did for this run, from GET /api/runs/{id}/progress
// -> rank_status, and from POST /api/runs/{id}/rank -> rank_status
// (jev_rank.RankStatus.to_json): {status: "scored" | "skipped" | "running",
// scored, total, reason, cost_usd, cost_cap_usd, throttled, spent_today_usd,
// daily_budget_usd}. `reason` is an id (no_key, no_run_input, no_resume,
// cost_cap_reached, daily_budget_reached, ...), or "jev_error:<code>" /
// "error:<ExceptionType>".
//
//   rankSkipWords  the reason in words ("no resume", "cost cap $0.25"), ""
//                  when every posting asked about was scored. A card with
//                  no score says it in its tooltip.
//   rankStatusLine "Jev: scored 412 of 1,458 (cost $0.23, cost cap $0.25)" /
//                  "Jev: skipped (no resume)" / "Jev: scoring, 40 of 500 so
//                  far": the sentence the server logs, word for word. Null
//                  until the ranking pass ended, and for a run sealed
//                  before rank_status existed.
//   rankUsageLine  "Jev: $0.31 of $0.50 today": the day's spend against the
//                  daily budget, from a rank_status or from a `usage` block
//                  (POST /rank, GET /api/jev/usage). Null when neither
//                  amount is known.
const RANK_SKIP_WORDS = {
  no_candidates: "no new postings to score",
  no_home: "no GigAI home",
  no_key: "no Jev key",
  no_profile: "no profile selected",
  no_resume: "no resume",
  no_run_input: "the run's input could not be read",
  no_run_output: "the run's postings could not be read",
  not_requested: "not asked yet",
  disabled: "Rank with Jev is off",
};
const RANK_STATES = ["scored", "skipped", "running"];

function isRankStatus(rankStatus) {
  return Boolean(rankStatus) && typeof rankStatus === "object" && RANK_STATES.includes(rankStatus.status);
}

function amount(value) {
  const number = typeof value === "string" && value.trim() !== "" ? Number(value) : typeof value === "number" ? value : NaN;
  return Number.isFinite(number) ? number : null;
}

// An amount that was spent: cents; below one cent, to four places.
function spentText(value) {
  if (!(value > 0)) {
    return "0.00";
  }
  if (value >= 0.01) {
    return value.toFixed(2);
  }
  const [whole, places = ""] = value.toFixed(4).replace(/0+$/, "").split(".");
  return `${whole}.${places.padEnd(2, "0")}`;
}

export function rankSkipWords(rankStatus) {
  if (!isRankStatus(rankStatus) || typeof rankStatus.reason !== "string" || !rankStatus.reason) {
    return "";
  }
  const reason = rankStatus.reason;
  const dollars = (value) => (typeof value === "string" && value ? ` $${value}` : "");
  if (reason === "cost_cap_reached") {
    const cap = dollars(rankStatus.cost_cap_usd);
    return rankStatus.status === "scored" ? `cost cap${cap}` : `cost cap${cap} reached before any score`;
  }
  if (reason === "daily_budget_reached") {
    return `daily budget${dollars(rankStatus.daily_budget_usd)} reached`;
  }
  if (reason.startsWith("jev_error:")) {
    return `Jev error: ${reason.slice("jev_error:".length)}`;
  }
  if (reason.startsWith("error:")) {
    return `error: ${reason.slice("error:".length)}`;
  }
  return RANK_SKIP_WORDS[reason] || reason;
}

export function rankStatusLine(rankStatus) {
  if (!isRankStatus(rankStatus)) {
    return null;
  }
  const count = (value) => formatCount(typeof value === "number" && Number.isFinite(value) ? value : 0);
  const counts = `${count(rankStatus.scored)} of ${count(rankStatus.total)}`;
  if (rankStatus.status === "running") {
    return `Jev: scoring, ${counts} so far`;
  }
  const throttled = typeof rankStatus.throttled === "string" && rankStatus.throttled ? `throttled: ${rankStatus.throttled}` : "";
  const words = rankSkipWords(rankStatus);
  if (rankStatus.status === "skipped") {
    return `Jev: skipped (${[words, throttled].filter(Boolean).join(", ")})`;
  }
  const cost = `cost $${spentText(amount(rankStatus.cost_usd))}`;
  return `Jev: scored ${counts} (${[cost, words, throttled].filter(Boolean).join(", ")})`;
}

export function rankUsageLine(usage) {
  if (!usage || typeof usage !== "object") {
    return null;
  }
  const spent = amount(usage.spent_today_usd);
  if (spent === null || typeof usage.daily_budget_usd !== "string" || !usage.daily_budget_usd) {
    return null;
  }
  return `Jev: $${spentText(spent)} of $${usage.daily_budget_usd} today`;
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

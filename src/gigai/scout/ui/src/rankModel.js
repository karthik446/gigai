import { formatCount } from "./runText.js";

// SCOPE-ADD-3 D: what the pages do and say about the ranking pass, as plain
// functions (no React) so a node test runs them.
//
// A run ranks every posting that passed its filters with the operator's
// own model target (the local CLI), a batch at a time, and imports its
// top 500 by rank. The pages read what the server serves:
//
//   GET /progress   postings[].rank (in rank order) while the run goes,
//                   `rank` {status, ranked, scored, demoted, total, text,
//                   model_target, ...} and `assess_counts` {selected,
//                   position, finished, status, text}; both null for a run
//                   sealed before ranking was a run step
//   GET /results    rows[].rank {score, reasons, blockers, demoted,
//                   unscored_reason} or null; rows[].rank_score (RankScore)
//   POST /rank      {} reads, {start: true} starts or joins a re-rank pass,
//                   {cancel: true} stops it; answers `scores` (RankScore, one
//                   per posting), `rank_status` and `rank_record` {record_id,
//                   status, ranked, scored, total, text, fail_open_reason}
//
// The copy is honest about what a rank is: the model's guess from the
// posting and the resume. It orders the grid (likely fits first, likely
// no-matches last) and never claims a posting matches: only an assessment
// says that. A posting with a blocker (clearance, relocation, ...) is
// demoted to the end, never hidden.

export const RANK_ORDER_NOTE = "likely fits first · likely no-matches last";
export const RANK_HONEST_NOTE =
  "A rank is your model's guess from the posting and your resume. It orders the list; it does not say you match. Assess a posting for that.";

// rank_run.fit_for: the score's band, for the tile's colour and the filter.
export function fitForScore(score) {
  if (typeof score !== "number" || !Number.isFinite(score)) {
    return null;
  }
  return score >= 70 ? "strong" : score >= 40 ? "maybe" : "no";
}

export const FIT_WORDS = { strong: "likely fit", maybe: "possible fit", no: "likely no-match" };

// An id ("domain_mismatch", an older run's category) in words; model prose
// ("Requires an active clearance") as it is.
function words(value) {
  const text = typeof value === "string" ? value.trim() : "";
  if (!text) {
    return "";
  }
  if (/^[a-z0-9]+(?:[_:.-][a-z0-9]+)+$/.test(text) || /^[a-z]+$/.test(text)) {
    const spaced = text.replace(/[_:.-]+/g, " ");
    return spaced.charAt(0).toUpperCase() + spaced.slice(1);
  }
  return text;
}

function list(value) {
  return Array.isArray(value) ? value.map(words).filter(Boolean) : [];
}

// One posting's rank in one shape, from either served form:
//   the ranker's line  {score, reasons, blockers, demoted, unscored_reason}
//   a RankScore        {fit, score, reasons, mismatch_flags, ...} (the
//                      /rank answer, and an older run's stored score)
// null when there is nothing (a posting its batch has not reached yet).
export function rankEntry(value) {
  if (!value || typeof value !== "object") {
    return null;
  }
  const score = typeof value.score === "number" && Number.isFinite(value.score) ? value.score : null;
  const blockers = score === null ? [] : list(Array.isArray(value.blockers) ? value.blockers : value.mismatch_flags);
  return {
    score,
    fit: fitForScore(score),
    reasons: list(value.reasons),
    blockers,
    demoted: score !== null && (Boolean(value.demoted) || blockers.length > 0),
    unscoredReason: score === null ? [value.unscored_reason, value.unscoredReason].find((why) => typeof why === "string") || null : null,
  };
}

export function isRanked(rank) {
  return Boolean(rank) && typeof rank.score === "number";
}

// A live re-rank's score over the row's stored rank: the row keeps its
// reasons while the scores agree (a /rank answer carries none).
export function mergeRank(stored, fresh) {
  if (!isRanked(fresh)) {
    return stored || fresh || null;
  }
  if (isRanked(stored) && stored.score === fresh.score) {
    return { ...stored, blockers: fresh.blockers.length ? fresh.blockers : stored.blockers, demoted: fresh.demoted || stored.demoted };
  }
  return fresh;
}

// The stored scores with the ones a re-rank pass has added since (both in
// RankScore shape): a posting keeps the score it has unless the newer
// answer scored it too. POST /rank answers one entry per posting, unscored
// ones with score null.
export function mergeRankScores(stored, fresh) {
  const byUrl = new Map();
  (stored || []).forEach((score) => score && byUrl.set(score.normalized_url, score));
  (fresh || []).forEach((score) => {
    if (score && (isRanked(rankEntry(score)) || !byUrl.has(score.normalized_url))) {
      byUrl.set(score.normalized_url, score);
    }
  });
  return [...byUrl.values()];
}

// The grid's order inside one verdict group: scored postings best first,
// then the ones with no score yet (the model could not score them, or
// their batch has not landed), then the demoted (a blocker), best first.
export function rankTier(rank) {
  if (!isRanked(rank)) {
    return rank ? 1 : 2;
  }
  return rank.demoted ? 3 : 0;
}

export function compareRank(a, b) {
  const tierDelta = rankTier(a) - rankTier(b);
  if (tierDelta !== 0) {
    return tierDelta;
  }
  const scoreA = isRanked(a) ? a.score : -1;
  const scoreB = isRanked(b) ? b.score : -1;
  return scoreB - scoreA;
}

// The filter's value for a job: its band, "blocked", or "unranked".
export function rankFilterValue(rank) {
  if (!isRanked(rank)) {
    return "unranked";
  }
  return rank.demoted ? "blocked" : rank.fit;
}

// The streaming grid (operator decision 2): each poll may re-order the
// list as batches land. While the operator points at a card or has focus
// in the list (`hold`), the cards already shown keep their places (their
// scores still update) and a new card joins at the end, so the row the
// operator is on never jumps. `previousIds` is the order last drawn.
export function streamOrder(sortedJobs, previousIds, hold) {
  if (!hold || !previousIds || previousIds.length === 0) {
    return sortedJobs;
  }
  const byId = new Map(sortedJobs.map((job) => [job.id, job]));
  const kept = previousIds.filter((id) => byId.has(id)).map((id) => byId.get(id));
  const keptIds = new Set(kept.map((job) => job.id));
  return kept.concat(sortedJobs.filter((job) => !keptIds.has(job.id)));
}

export function sameOrder(a, b) {
  return a.length === b.length && a.every((job, index) => job.id === b[index].id);
}

// --- words ---------------------------------------------------------------------

const REASON_WORDS = {
  cancelled: "stopped before every posting was ranked",
  call_budget: "stopped at its call limit",
  token_budget: "stopped at its token limit",
  model_target_unavailable: "your model was not available",
  no_candidates: "no postings to rank",
  no_home: "no GigAI home",
  no_profile: "no profile selected",
  no_resume: "no resume",
  no_run_input: "the run's input could not be read",
  no_run_output: "the run's postings could not be read",
  not_requested: "not ranked yet",
  interrupted: "interrupted",
  skipped: "skipped",
};

// Why a pass left postings unranked, in words: the id before the first
// ":" from the table, the rest kept as detail. "" for none.
export function rankReasonWords(reason) {
  if (typeof reason !== "string" || !reason.trim()) {
    return "";
  }
  const text = reason.trim();
  if (text.startsWith("error:")) {
    return `error: ${text.slice("error:".length).trim()}`;
  }
  const colon = text.indexOf(":");
  const id = colon >= 0 ? text.slice(0, colon).trim() : text;
  const detail = colon >= 0 ? text.slice(colon + 1).trim() : "";
  const known = REASON_WORDS[id];
  if (known) {
    return detail && id !== "model_target_unavailable" ? `${known} (${detail})` : known;
  }
  // "12 of 480 postings unscored" (a pass that ended partial) reads as it is.
  return text;
}

// The run page's counts: "Ranked 350 of 1,458 · Assessing 3 of 10", from
// GET /progress's `rank` and `assess_counts` (each may be null: a run
// sealed before either existed says nothing about it). null when neither.
function countsText(block, numbers) {
  if (!block || typeof block !== "object") {
    return "";
  }
  if (typeof block.text === "string" && block.text.trim()) {
    return block.text.trim();
  }
  return numbers(block);
}

export function rankCountsLine(rank, assessCounts) {
  const ranked = countsText(rank, (block) =>
    typeof block.ranked === "number" ? `Ranked ${formatCount(block.ranked)}${typeof block.total === "number" ? ` of ${formatCount(block.total)}` : ""}` : "",
  );
  const assessing = countsText(assessCounts, (block) =>
    typeof block.selected === "number" && typeof block.position === "number" ? `Assessing ${formatCount(block.position)} of ${formatCount(block.selected)}` : "",
  );
  const parts = [ranked, assessing].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

// The model that ranked, only when the server says so (the pass's resolved
// model, else its target); null otherwise.
export function rankedByLine(rankStatus, rank) {
  const pick = (block, key) => (block && typeof block === "object" && typeof block[key] === "string" && block[key].trim() ? block[key].trim() : "");
  const name = pick(rankStatus, "resolved_model") || pick(rankStatus, "model_target") || pick(rank, "model_target");
  return name ? `Ranked by your model: ${name}` : null;
}

// A finished pass that left postings unranked says so, and where they are.
export function rankShortfallLine(rank, rankStatus) {
  const status = rank && typeof rank.status === "string" ? rank.status : null;
  if (status === "running" || status === "complete") {
    return null;
  }
  const reason = (rank && rank.fail_open_reason) || (rankStatus && rankStatus.reason) || null;
  const why = rankReasonWords(reason);
  if (!why) {
    return null;
  }
  return `Some postings were not ranked (${why}). They are listed after the ranked ones.`;
}

// The card's tile and its tooltip: the score and its band, the model's
// reasons, and its blockers. Never a verdict.
export function rankTileText(rank) {
  if (!isRanked(rank)) {
    return { score: "–", label: "not ranked" };
  }
  return { score: String(rank.score), label: rank.demoted ? "blocker" : FIT_WORDS[rank.fit] || "" };
}

export function rankReasonsLine(rank) {
  return rank && Array.isArray(rank.reasons) ? rank.reasons.join(" · ") : "";
}

export function rankBlockersLine(rank) {
  return rank && Array.isArray(rank.blockers) && rank.blockers.length ? `Blocker: ${rank.blockers.join(" · ")}` : "";
}

export function rankTooltip(rank) {
  if (!isRanked(rank)) {
    const why = rank && rank.unscoredReason ? rankReasonWords(rank.unscoredReason) : "";
    return why ? `Not ranked: ${why}` : "Not ranked yet";
  }
  const lines = [`Rank ${rank.score} · ${rank.demoted ? "moved to the end: it names a blocker" : FIT_WORDS[rank.fit]}`];
  const reasons = rankReasonsLine(rank);
  if (reasons) {
    lines.push(`Why: ${reasons}`);
  }
  const blockers = rankBlockersLine(rank);
  if (blockers) {
    lines.push(blockers);
  }
  lines.push("Your model's guess; assess the posting to know if you match.");
  return lines.join("\n");
}

// --- the Rank / Re-rank button -------------------------------------------------

// POST /rank's `rank_record.status`: `running` while a pass runs (in any
// process); `interrupted` when its process is gone (the next start resumes
// it); else how it ended.
export function rankPassRunning(record) {
  return Boolean(record) && record.status === "running";
}

// "Rank" when nothing in the run has a score, "Re-rank" when something has.
export function rankButtonLabel(anyRanked) {
  return anyRanked ? "Re-rank" : "Rank";
}

// What the bar says about the page's pass, from `rank_record` (null: none).
export function rankPassLine(record) {
  if (!record || typeof record !== "object") {
    return null;
  }
  const counts =
    typeof record.text === "string" && record.text.trim()
      ? record.text.trim()
      : typeof record.ranked === "number" && typeof record.total === "number"
        ? `Ranked ${formatCount(record.ranked)} of ${formatCount(record.total)}`
        : "";
  const tail = counts ? `: ${counts}` : "";
  switch (record.status) {
    case "running":
      return `Re-ranking${tail}`;
    case "complete":
      return `Re-rank done${tail}`;
    case "cancelled":
      return `Re-rank cancelled${tail}`;
    case "interrupted":
      return `Re-rank interrupted${tail}; Re-rank picks it up again`;
    case "partial": {
      const why = rankReasonWords(record.fail_open_reason);
      return `Re-rank ended early${tail}${why ? ` (${why})` : ""}`;
    }
    case "skipped":
    case "failed": {
      const why = rankReasonWords(record.fail_open_reason);
      return `Re-rank ${record.status === "failed" ? "failed" : "skipped"}${why ? `: ${why}` : ""}`;
    }
    default:
      return counts || null;
  }
}

// One re-rank pass for one run. `start()` is the click (POST /rank {start:
// true}); while the answer's record is running, the same route is read ({},
// which starts nothing) every `intervalMs` and each answer goes to
// `onResponse`, so the grid re-orders as batches land. `cancel()` is the
// Cancel button ({cancel: true}). `stop()` ends the reads (another run is
// shown, or the page unmounted; the pass itself runs on); an answer that
// lands after it, or when `isCurrent()` is false, is dropped. `onEnd` is
// called when an answer says a pass this object saw running has ended (the
// page then re-reads the results, whose rows carry the new reasons); a
// first read that finds no pass running calls nothing.
export function createRankPass({ runId, postRank, onResponse, onError, onEnd, isCurrent, schedule = setTimeout, cancel: clear = clearTimeout, intervalMs = 2000 }) {
  let timer = null;
  let stopped = false;
  let sawRunning = false;
  const live = () => !stopped && (!isCurrent || isCurrent());

  function clearTimer() {
    if (timer !== null) {
      clear(timer);
      timer = null;
    }
  }
  function handle(response) {
    if (!live()) {
      return;
    }
    onResponse(response);
    if (response && rankPassRunning(response.rank_record)) {
      sawRunning = true;
      clearTimer();
      timer = schedule(read, intervalMs);
    } else if (sawRunning) {
      sawRunning = false;
      if (onEnd) {
        onEnd(response);
      }
    }
  }
  function fail(error) {
    if (live() && onError) {
      onError(error);
    }
  }
  function read() {
    timer = null;
    return postRank(runId, {}).then(handle, fail);
  }
  function start() {
    clearTimer();
    return postRank(runId, { start: true }).then(handle, fail);
  }
  function cancel() {
    clearTimer();
    return postRank(runId, { cancel: true }).then(handle, fail);
  }
  function stop() {
    stopped = true;
    clearTimer();
  }
  return { start, read, cancel, stop };
}

// uat-bug-029: POST /api/assess answers 422 `posting_requirements_unreadable`
// when the posting's text has no requirements it could read. That is not a
// failure of the page: the job stays not assessed and the page says so.
export const REQUIREMENTS_UNREADABLE_CODE = "posting_requirements_unreadable";
export const REQUIREMENTS_UNREADABLE_TEXT = "Couldn't read this posting's requirements";

export function isRequirementsUnreadable(error) {
  return Boolean(error) && error.code === REQUIREMENTS_UNREADABLE_CODE;
}

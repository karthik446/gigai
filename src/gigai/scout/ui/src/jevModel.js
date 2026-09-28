import { isScored } from "./jobModel.js";
import { rankSkipWords } from "./runText.js";

// ui-pass (uat-bug-021): what the pages do and say about Jev, as plain
// functions (no React) so a node test runs them.
//
// The rule (operator 2026-09-28, Jev had cost $1.00): a page never asks Jev
// on its own. Opening a run reads the scores already stored (the results
// pages' `rank_score`, api.storedRankScores); the "Score with Jev" button
// is the one thing on a page that may spend (createRankPass.start).
//
// `settings` is GET /api/jev/settings: {jev_daily_budget_usd,
// jev_rank_enabled, jev_run_cap_usd, daily_budget_env, run_cap_env,
// run_cap_in_force, has_key, usage}. `usage` is a jev_budget.usage block
// ({spent_today_usd, daily_budget_usd, budget_reached, line}), from POST
// /rank's answer when there is a newer one than the settings'.

// The defaults the disclosure states. The api-e2e test checks each against
// the Python that enforces it (jev_rank.DEFAULT_COST_CAP_USD,
// jev_budget.DEFAULT_DAILY_BUDGET_USD, the resume slice jev_rank sends).
export const JEV_RUN_COST_CAP_USD = "0.25";
export const JEV_DEFAULT_DAILY_BUDGET_USD = "0.50";
export const JEV_RESUME_CHARS = 2000;
export const JEV_SETTINGS_FILE = "<home>/local/scout/jev-settings.json";

// True when a search would send the resume to Jev: a key is set and
// "Rank with Jev" is on.
export function jevRankingOn(settings) {
  return Boolean(settings && settings.has_key && settings.jev_rank_enabled);
}

// Wizard finding 2: the Jobs page's privacy line, only when it is true.
// With no key, or ranking off, the page says nothing about Jev here (the
// cards' "– Jev" tooltip says why a posting has no score).
export function jevNoticeText(settings) {
  return jevRankingOn(settings) ? "Your resume is sent to Jev to rank postings." : null;
}

// The per-run cap as the disclosure states it: `run_cap_in_force`
// (`GET /api/jev/settings`, jev_budget.run_cost_cap_usd formatted: env > the
// settings file > the default), the same way the daily budget's "in force"
// value already comes from `settings.usage.daily_budget_usd`.
export function jevRunCapInForce(settings) {
  return (settings && settings.run_cap_in_force) || JEV_RUN_COST_CAP_USD;
}

// The run dialog's consent callout: what this run sends to Jev and may
// cost, only when it will (a key and ranking on); null otherwise.
export function jevRunConsentLine(settings) {
  if (!jevRankingOn(settings)) {
    return null;
  }
  return `Rank with Jev is on: the first ${JEV_RESUME_CHARS.toLocaleString("en-US")} characters of your resume go to Jev to score the postings, up to $${jevRunCapInForce(settings)} for this run (Settings → Jev ranking).`;
}

// "Score with Jev" is offered only when a click could score something: a
// key, ranking on, room left in today's budget, no pass already running,
// and at least one posting with no score.
export function canScoreWithJev({ settings, usage, rankStatus, unscored }) {
  if (!jevRankingOn(settings)) {
    return false;
  }
  const today = usage || settings.usage;
  if (today && today.budget_reached) {
    return false;
  }
  if (rankStatus && rankStatus.status === "running") {
    return false;
  }
  return unscored > 0;
}

// The cards' "– Jev" tooltip: why this run's postings have no score. The
// newest word first: a "Score with Jev" pass on this page, else the run's
// own pass (GET /progress's rank_status), else what the settings say now
// (ranking off, no key). "" when nothing says why.
export function jevCardSkipWords(pageRankStatus, runRankStatus, settings) {
  const words = rankSkipWords(pageRankStatus) || rankSkipWords(runRankStatus);
  if (words || !settings) {
    return words;
  }
  if (!settings.jev_rank_enabled) {
    return rankSkipWords({ status: "skipped", reason: "disabled" });
  }
  return settings.has_key ? "" : rankSkipWords({ status: "skipped", reason: "no_key" });
}

// The stored scores with the ones a pass has added since: a posting keeps
// the score it has unless the newer answer scored it too. POST /rank
// answers one entry per posting, unscored ones with fit/score null.
export function mergeRankScores(stored, fresh) {
  const byUrl = new Map();
  (stored || []).forEach((score) => score && byUrl.set(score.normalized_url, score));
  (fresh || []).forEach((score) => {
    if (score && (isScored(score) || !byUrl.has(score.normalized_url))) {
      byUrl.set(score.normalized_url, score);
    }
  });
  return [...byUrl.values()];
}

// One "Score with Jev" pass for one run. `start()` is the click: POST /rank
// {start: true}. While the answer says `running`, the same route is read
// ({}, which never starts a pass) every `intervalMs`, and each answer goes
// to `onResponse` so the grid fills in as scores arrive. `stop()` ends the
// reads (the page moved to another run, or unmounted); an answer that lands
// after it, or when `isCurrent()` is false, is dropped.
export function createRankPass({ runId, postRank, onResponse, onError, isCurrent, schedule = setTimeout, cancel = clearTimeout, intervalMs = 2000 }) {
  let timer = null;
  let stopped = false;
  const live = () => !stopped && (!isCurrent || isCurrent());

  function handle(response) {
    if (!live()) {
      return;
    }
    onResponse(response);
    if (response && response.rank_status && response.rank_status.status === "running") {
      timer = schedule(read, intervalMs);
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
    if (timer !== null) {
      cancel(timer);
      timer = null;
    }
    return postRank(runId, { start: true }).then(handle, fail);
  }
  function stop() {
    stopped = true;
    if (timer !== null) {
      cancel(timer);
      timer = null;
    }
  }
  return { start, read, stop };
}

// The Settings disclosure (uat-bug-021 decision a; jev-disclosure-fixes
// TARGET 4 added the per-run cap), one sentence a line. `budget` is the
// daily budget in force ("0.50") and `runCap` the per-run cap in force
// ("0.25"), each when known; both default to the stated defaults.
export function jevDisclosureLines(budget, runCap) {
  const chars = JEV_RESUME_CHARS.toLocaleString("en-US");
  const cap = runCap || JEV_RUN_COST_CAP_USD;
  return [
    `With a Jev key and Rank with Jev on, a search spends by default: each run asks Jev to score its postings, up to $${cap} a run${cap && cap !== JEV_RUN_COST_CAP_USD ? "" : " by default"}.`,
    `All Jev calls of a day (runs, Score with Jev, quick assessments) stop at the daily budget: $${budget || JEV_DEFAULT_DAILY_BUDGET_USD} a day${budget && budget !== JEV_DEFAULT_DAILY_BUDGET_USD ? "" : " by default"}. A posting Jev already scored is cached and costs nothing again.`,
    `Sent to Jev: the first ${chars} characters of the selected profile's resume, your target titles, countries and visa need, and each posting's company, title and location. A pasted resume is never sent to Jev.`,
    `Turn Rank with Jev off to send nothing. Both the per-run cap and the daily budget are this GigAI home's, stored in ${JEV_SETTINGS_FILE}.`,
  ];
}

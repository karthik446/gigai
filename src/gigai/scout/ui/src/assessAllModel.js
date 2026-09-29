// uat-bug-042: "Assess all new" and the run dialog's "All new postings".
//
// POST /api/runs/{run_id}/assess-all (find_jobs/api/assess_all.py) answers
// `plan` (count, model_target, concurrency, per_call_seconds and where it
// was measured, estimate_minutes -- null when nothing was measured),
// `job` (the newest job of the run: status, total, assessed, failed,
// remaining) and `counts` (the run's live Assessed / Matched / Need your
// answers). Everything here is pure, so node tests pin the words.
import { MODEL_TARGET_HINTS } from "./modelTargets.js";

// A run's "Full assessments": a number 1..50 or, explicitly, "all".
export const ASSESS_ALL = "all";
export const SELECTION_CAP_MAX = 50;
// find_jobs/contracts.py ASSESS_ALL_CEILING (the import cap).
export const ASSESS_ALL_CEILING = 500;
// find_jobs/assess_all.py ASSESS_CONCURRENCY: calls at a time.
export const ASSESS_CONCURRENCY = 4;
// Billed per token: the dialog keeps a number for these.
export const PER_TOKEN_TARGETS = ["openrouter_api"];

const SHORT_LABELS = {
  ollama_local: "Ollama",
  codex_cli: "Codex",
  claude_cli: "Claude",
  openrouter_api: "OpenRouter",
};

export function isAssessAll(cap) {
  return cap === ASSESS_ALL;
}

export function capValid(cap) {
  return isAssessAll(cap) || (Number.isInteger(cap) && cap >= 1 && cap <= SELECTION_CAP_MAX);
}

// The dialog's starting choice: "All new postings" for a local CLI target
// (Ollama, Codex, Claude: no per-token bill), a number for a target billed per
// token -- the saved number, else 10.
export function defaultSelectionCap(savedCap, modelTarget) {
  if (PER_TOKEN_TARGETS.includes(modelTarget)) {
    return Number.isInteger(savedCap) ? savedCap : 10;
  }
  return ASSESS_ALL;
}

export function modelShortLabel(target) {
  return SHORT_LABELS[target] || target;
}

// The dialog's line under "Full assessments".
export function fullAssessmentsHelp(cap, modelTarget) {
  if (isAssessAll(cap)) {
    return (
      `Every matching posting is ranked, then every new one is assessed in full: one ${modelShortLabel(modelTarget)} call each, ` +
      `${ASSESS_CONCURRENCY} at a time (at most ${ASSESS_ALL_CEILING}). How long that takes depends on how many are new.`
    );
  }
  return "Every matching posting is ranked. This many of the top-ranked ones are then assessed in full; you can assess the rest one at a time with Assess.";
}

export function assessAllButtonLabel(plan) {
  return `Assess all new (${plan && Number.isInteger(plan.count) ? plan.count : 0})`;
}

// "406 postings, one Codex call each, about 72 min at 4 at a time." With no
// measured per-call time there is no minute figure: count and K only.
export function planLine(plan) {
  if (!plan) {
    return "";
  }
  const count = plan.count || 0;
  const noun = count === 1 ? "posting" : "postings";
  const k = plan.concurrency || ASSESS_CONCURRENCY;
  const pace = Number.isInteger(plan.estimate_minutes) ? `about ${plan.estimate_minutes} min at ${k} at a time` : `${k} at a time`;
  return `${count} ${noun}, one ${modelShortLabel(plan.model_target)} call each, ${pace}.`;
}

// Where the minute figure came from (or that there is none).
export function estimateSourceLine(plan) {
  if (!plan || typeof plan.per_call_seconds !== "number") {
    return "No time estimate: no assessment on this run has been timed yet.";
  }
  const samples = plan.per_call_samples || 0;
  const what = plan.per_call_source === "assess_all" ? `earlier "Assess all new" call${samples === 1 ? "" : "s"}` : `assessment${samples === 1 ? "" : "s"} this run made`;
  return `Estimated from ${samples} ${what} on this machine: about ${Math.round(plan.per_call_seconds)} s per call.`;
}

// The existing per-target line ("... sends your resume to OpenAI.").
export function privacyLine(modelTarget) {
  return MODEL_TARGET_HINTS[modelTarget] || "";
}

export function assessAllRunning(job) {
  return Boolean(job && job.status === "running");
}

// "Assessing: 12 of 406 assessed, 1 failed (4 at a time)" and the ends.
export function jobLine(job) {
  if (!job) {
    return "";
  }
  const text = job.text || `${job.assessed || 0} of ${job.total || 0} assessed`;
  switch (job.status) {
    case "running":
      return `Assessing: ${text} (${job.concurrency || ASSESS_CONCURRENCY} at a time)`;
    case "cancelled":
      return `Cancelled: ${text}. What finished is kept; Assess all new picks up the rest.`;
    case "interrupted":
      return `Stopped when Scout stopped: ${text}. Assess all new picks up the rest.`;
    case "failed":
      return `Stopped: ${text} (${job.reason || "failed"}).`;
    default:
      return `Done: ${text}.`;
  }
}

export function skipReasonText(reason) {
  return (
    {
      no_profile: "Select a profile to assess this run's postings.",
      no_run_input: "This run has no postings to assess.",
      run_not_finished: "Wait for the run to finish, then assess the rest.",
    }[reason] || ""
  );
}

// The summary strip describes the newest run: while "Assess all new" works
// on that run, its Assessed / Matched tiles are the live counts.
export function withLiveCounts(lastRun, shownRunId, counts) {
  if (!lastRun || !counts || lastRun.run_id !== shownRunId) {
    return lastRun;
  }
  return { ...lastRun, counts: { ...lastRun.counts, assessed: counts.assessed, matched: counts.matched } };
}

// Polls POST /assess-all while a job runs (the rank pass's pattern).
// `onResponse` gets every answer; `onEnd` once a job it saw running ends.
export function createAssessAllPoll({ runId, post, onResponse, onError, onEnd, isCurrent, schedule = setTimeout, cancel: clear = clearTimeout, intervalMs = 2000 }) {
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
    if (response && assessAllRunning(response.job)) {
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
    return post(runId, {}).then(handle, fail);
  }
  function start() {
    clearTimer();
    return post(runId, { start: true }).then(handle, fail);
  }
  function cancel() {
    clearTimer();
    return post(runId, { cancel: true }).then(handle, fail);
  }
  function stop() {
    stopped = true;
    clearTimer();
  }
  return { start, read, cancel, stop };
}

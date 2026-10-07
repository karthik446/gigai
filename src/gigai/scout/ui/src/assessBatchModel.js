// 0.1.11.5 (ASSESS-01): what the pages say and do about a running "Assess these" batch, as plain functions
// (tests/api_e2e/test_ui_assess_batch_model.py pins the words).
//
// The approval no longer waits for the batch: POST /api/postings/assess {approve, background: true} answers 202 as
// soon as the batch is live, the dialog closes, and the page reads GET /api/postings/assess/status
// (assess_batch_job.py: {schema_version: "scout-assess-batch:1", running, batch, last}) every BATCH_POLL_MS while a
// batch runs. `batch`: {id, status: "running" | "cancelling", total, assessed, failed, in_flight, profile_id,
// estimate_seconds, pending: [job identities], here}. `last` is how the last batch this server ran ended:
// {id, status: "done" | "cancelled" | "failed", requested, assessed, failed, not_started, error_code, failed_codes,
// more_after}. Cancel (POST /api/postings/assess/cancel) starts no further model call; the calls in flight finish
// and what finished is kept.

export const BATCH_SCHEMA = "scout-assess-batch:1";
export const BATCH_POLL_MS = 2000;
// scout_new.BATCH_LIMIT: one approval assesses the top 50 by rank and never more.
export const BATCH_CAP = 50;

const whole = (value) => (Number.isInteger(value) && value >= 0 ? value : 0);
const plural = (count, one, many) => (count === 1 ? one : many);

export function isBatchStatus(answer) {
  return Boolean(answer) && typeof answer === "object" && answer.schema_version === BATCH_SCHEMA;
}

// The approval's answer when its batch runs on (202): the status object with `status: "started"`.
export function isBatchStarted(answer) {
  return isBatchStatus(answer) && answer.status === "started";
}

export function batchRunning(status) {
  return isBatchStatus(status) && status.running === true && Boolean(status.batch);
}

// "about 29 min", "about 1 min", "under a minute", "about 2.5 h"; null when there is no estimate.
export function aboutMinutes(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0) {
    return null;
  }
  if (seconds < 45) {
    return "under a minute";
  }
  const minutes = Math.max(1, Math.round(seconds / 60));
  return minutes < 120 ? `about ${minutes} min` : `about ${(minutes / 60).toFixed(1)} h`;
}

// "about 29 min for 50": the estimate with what it is for, never a vague "a minute".
export function estimateFor(seconds, count) {
  const about = aboutMinutes(seconds);
  return about ? `${about} for ${count}` : null;
}

// What changes when a posting of the batch gets its result: the page reads the list again when this moves.
export function batchSignature(status) {
  if (!batchRunning(status)) {
    return "idle";
  }
  return `${status.batch.id}|${whole(status.batch.assessed)}|${whole(status.batch.failed)}`;
}

function countsText(batch) {
  const failed = whole(batch.failed);
  return `${whole(batch.assessed)} of ${whole(batch.total)} assessed${failed ? `, ${failed} failed` : ""}`;
}

// The Jobs page's progress: {line, profile, estimate, cancelling, cancelLine, waiting, canCancel} or null when no
// batch runs.
//   line        "12 of 50 assessed" (", 1 failed")
//   profile     the label of the profile of the call started last, or null
//   estimate    "about 29 min for 50", or null
//   cancelling  the batch is being cancelled: the status says so, or Cancel was clicked on this page (`cancelSent`)
//               and the next status is not read yet
//   cancelLine  "Cancelling: finishing the 2 in flight" (the status's `in_flight`: the calls still running), never a
//               bare "Cancelling…"; null while it is not cancelling
export function batchProgress(status, profiles = [], { cancelSent = false } = {}) {
  if (!batchRunning(status)) {
    return null;
  }
  const batch = status.batch;
  const found = (profiles || []).find((profile) => profile && profile.profile_id === batch.profile_id);
  const cancelling = batch.status === "cancelling" || cancelSent === true;
  const flying = whole(batch.in_flight);
  return {
    line: countsText(batch),
    profile: found ? found.label : null,
    estimate: estimateFor(batch.estimate_seconds, whole(batch.total)),
    cancelling,
    cancelLine: cancelling ? (flying > 0 ? `Cancelling: finishing the ${flying} in flight` : "Cancelling: no call is in flight") : null,
    waiting: cancelling ? "No further model call starts. What finished is kept." : null,
    canCancel: !cancelling,
  };
}

// One line for the progress: "Assessing: 12 of 50 assessed · about 29 min for 50 · Staff Engineer"; while it is
// cancelled, "Cancelling: finishing the 2 in flight · 14 of 50 assessed · Staff Engineer".
export function batchProgressLine(status, profiles = [], options = {}) {
  const progress = batchProgress(status, profiles, options);
  if (!progress) {
    return null;
  }
  const parts = progress.cancelling ? [progress.cancelLine, progress.line] : [`Assessing: ${progress.line}`];
  if (progress.estimate && !progress.cancelling) {
    parts.push(progress.estimate);
  }
  if (progress.profile) {
    parts.push(progress.profile);
  }
  return parts.join(" · ");
}

// How a batch ended, in one line (the page's notice); null when there is none.
export function batchEndLine(last) {
  if (!last || typeof last !== "object") {
    return null;
  }
  const done = `${whole(last.assessed)} of ${whole(last.requested)} assessed`;
  const codes = Array.isArray(last.failed_codes) && last.failed_codes.length ? ` Not assessed: ${last.failed_codes.join(", ")}.` : "";
  if (last.status === "cancelled") {
    const left = whole(last.not_started);
    return `Cancelled: ${done}. What finished is kept${left ? `; ${left} ${plural(left, "was", "were")} not started` : ""}.${codes}`;
  }
  if (last.status === "failed") {
    return `The batch stopped: ${done} (${last.error_code || "failed"}). What finished is kept.${codes}`;
  }
  const more = whole(last.more_after);
  const next = more > 0 ? ` ${more} more not assessed yet: 50 at a time, "Assess all" takes the next.` : "";
  const stopped = last.error_code ? ` Stopped early: ${last.error_code}.` : "";
  return `Assessed ${whole(last.assessed)} of ${whole(last.requested)}.${codes}${stopped}${next}`;
}

// Whether this posting waits for its result in the running batch (its own Assess button is then not offered).
export function jobWaitsInBatch(status, jobIdentity) {
  return batchRunning(status) && Array.isArray(status.batch.pending) && Boolean(jobIdentity) && status.batch.pending.includes(jobIdentity);
}

// A job page while a batch runs: "is it still assessing?". null when no batch runs.
export function jobBatchLine(status, jobIdentity) {
  if (!batchRunning(status)) {
    return null;
  }
  const batch = status.batch;
  const waiting = jobWaitsInBatch(status, jobIdentity);
  const far = countsText(batch);
  if (waiting) {
    return batch.status === "cancelling"
      ? `The assess batch is being cancelled (${far}). This posting is assessed only if its call has already started.`
      : `Assessing… this posting is in the running batch (${far}).`;
  }
  return `An assess batch is running (${far}). This posting is not waiting in it.`;
}

// Whether the job of this page was waiting in the batch at `before` and has its result (or the batch ended) at `after`.
export function jobLeftBatch(before, after, jobIdentity) {
  const waited = batchRunning(before) && Array.isArray(before.batch.pending) && before.batch.pending.includes(jobIdentity);
  if (!waited) {
    return false;
  }
  return !batchRunning(after) || !(after.batch.pending || []).includes(jobIdentity);
}

// Part 5: the button beside "N not assessed". "Assess top 50 of 177"; "Assess all 12" when one run takes them all.
export function assessAllLabel(notAssessed) {
  const count = whole(notAssessed);
  if (count === 0) {
    return "Assess all";
  }
  return count > BATCH_CAP ? `Assess top ${BATCH_CAP} of ${count}` : `Assess all ${count}`;
}

// Part 5: the link on a row shown for another profile. "Or": the row's own state is that other profile's.
export function assessAsLabel(label) {
  return `Or assess as ${label}`;
}

// The read loop of one page: `read` every `intervalMs` WHILE a batch runs (the next only after the answer), never
// when none does. `onStatus(status)` gets every answer; `onProgress()` when a posting of the batch got its result;
// `onEnd(last)` once, when a batch this loop saw running has ended.
export function createBatchWatch({ read, onStatus, onProgress, onEnd, schedule = setTimeout, cancel = clearTimeout, intervalMs = BATCH_POLL_MS }) {
  let timer = null;
  let stopped = false;
  let reading = false;
  let seen = "idle";

  function take(status) {
    if (stopped || !isBatchStatus(status)) {
      return;
    }
    onStatus && onStatus(status);
    const signature = batchSignature(status);
    // A "started" answer whose batch is over already (a fast one) is a batch that ran: the page hears its end.
    const wasRunning = seen !== "idle" || status.status === "started";
    if (batchRunning(status)) {
      if (wasRunning && signature !== seen) {
        onProgress && onProgress(status);
      }
      seen = signature;
      if (timer === null) {
        timer = schedule(tick, intervalMs);
      }
      return;
    }
    seen = "idle";
    if (wasRunning) {
      onEnd && onEnd(status.last || null, status);
    }
  }
  function tick() {
    timer = null;
    if (stopped || reading) {
      return undefined;
    }
    reading = true;
    return read().then(
      (status) => {
        reading = false;
        take(status);
      },
      () => {
        reading = false;
        if (!stopped && seen !== "idle" && timer === null) {
          timer = schedule(tick, intervalMs); // a read that failed is read again at the next tick
        }
      },
    );
  }
  function stop() {
    stopped = true;
    if (timer !== null) {
      cancel(timer);
      timer = null;
    }
  }
  return { check: tick, take, stop };
}

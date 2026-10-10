// 0.1.11.10 Part A slice 1/2 and Part B G6: the Learning pathways tab's pure rules (no React), exercised through a
// real browser by tests/ui/test_learning_tab.py (the list) and tests/ui/test_learning_request.py (the form, the
// estimate dialog, progress, Stop/Resume).
//
// GET /api/learning/pathways answers one row per pathway (find_jobs/api/learning.py's `pathway_summary`): id,
// role_text, requested_at, status (queued | running | done | failed | interrupted), cost (every key nullable),
// course (entry, lessons, size_bytes, generated_at) or null, source (requested | imported), imported_from, error,
// error_code, progress ({step, steps[], calls, fetches, max_calls, max_fetches, dropped[]} or null), url (the
// course's address, only once `done`). This module turns one row into the plain-text fields the view prints and
// builds the estimate dialog's text; it invents nothing the server did not send.
//
// POST /api/learning/pathways without `approve` answers the estimate (`learning_job.ask_response`): {estimate:
// {calls, fetches, minutes, tokens, basis}, caps: {calls, fetches}}. With `approve: true` it starts the job (202).

export const WHAT_IS_A_COURSE =
  "A course for a role: what the role is expected to do, the technologies it names with how many postings name " +
  "each, and lessons with practice paths. It is built from postings on your machine and checked links.";

export const EMPTY_NOTE = "No courses yet";

export const IMPORT_COMMAND = 'gigai scout learning import <folder> --role "<role>"';

export const ROLE_PLACEHOLDER = "for example: MLOps engineer, Product manager, Director of AI enablement";

export const LEARNING_POLL_MS = 2000;

const STATUS_TEXT = { queued: "Queued", running: "Running", done: "Ready", failed: "Failed", interrupted: "Interrupted" };

// "Ready" / "Queued" / "Running" / "Failed" / "Interrupted" (the error itself, when there is one, is its own line:
// `errorLine`).
export function statusText(pathway) {
  return STATUS_TEXT[pathway && pathway.status] || "Unknown";
}

// Whether the tab should poll this row (only while generation is live): queued or running, a requested pathway.
export function isLive(pathway) {
  return Boolean(pathway) && pathway.source === "requested" && (pathway.status === "queued" || pathway.status === "running");
}

// Whether any row of the list is live (the tab polls the whole list while true, stops otherwise).
export function anyLive(pathways) {
  return (pathways || []).some(isLive);
}

const STEP_LABELS = { corpus: "reading postings", course: "writing the curriculum and lessons", paths: "building practice paths", render: "rendering the course", audit: "checking links", import: "saving the course" };

// The running row's one plain progress line: the current step's own `detail` ("Writing module 3 of 8"), else a
// label built from `progress.step`, else null (no progress yet: the row still says "Queued").
export function progressLine(progress) {
  if (!progress || !progress.step) {
    return null;
  }
  const current = (progress.steps || []).find((step) => step.id === progress.step);
  if (current && current.detail) {
    return current.detail;
  }
  return STEP_LABELS[progress.step] ? `Working: ${STEP_LABELS[progress.step]}` : null;
}

// "12 of 41 model calls, 180 of 1500 page fetches" -- the short counter beside the progress line; null without a
// progress object.
export function progressCounters(progress) {
  if (!progress) {
    return null;
  }
  const calls = `${progress.calls} of ${progress.max_calls} model call${progress.max_calls === 1 ? "" : "s"}`;
  const fetches = `${progress.fetches} of ${progress.max_fetches} page fetch${progress.max_fetches === 1 ? "" : "es"}`;
  return `${calls}, ${fetches}`;
}

// The failed/interrupted row's own error line (the server's own message), or null (neither, or no error sent).
export function errorLine(pathway) {
  if (pathway && (pathway.status === "failed" || pathway.status === "interrupted") && pathway.error) {
    return pathway.error;
  }
  return null;
}

function round1(value) {
  return Math.round(value * 10) / 10;
}

// "43 model calls, 430 page fetches, about 45 minutes" (every known number, in this order; worker_minutes rounds to
// one decimal and reads "about N minutes"), or null when every cost key is unknown.
export function costLine(cost) {
  if (!cost) {
    return null;
  }
  const parts = [];
  if (typeof cost.cli_model_calls === "number") {
    parts.push(`${cost.cli_model_calls} model call${cost.cli_model_calls === 1 ? "" : "s"}`);
  }
  if (typeof cost.web_fetches === "number") {
    parts.push(`${cost.web_fetches} page fetch${cost.web_fetches === 1 ? "" : "es"}`);
  }
  if (typeof cost.web_searches === "number") {
    parts.push(`${cost.web_searches} web search${cost.web_searches === 1 ? "" : "es"}`);
  }
  if (typeof cost.worker_minutes === "number") {
    parts.push(`about ${round1(cost.worker_minutes)} minute${round1(cost.worker_minutes) === 1 ? "" : "s"}`);
  }
  if (cost.note) {
    parts.push(cost.note);
  }
  return parts.length ? parts.join(", ") : null;
}

export const COST_NOT_RECORDED = "cost not recorded";

// The cost line to show: `costLine(cost)` or COST_NOT_RECORDED.
export function costDisplay(cost) {
  return costLine(cost) || COST_NOT_RECORDED;
}

// Bytes -> "N.N MB" (one decimal; 0 reads "0.0 MB", never "0 bytes").
export function sizeMb(bytes) {
  if (typeof bytes !== "number" || Number.isNaN(bytes)) {
    return null;
  }
  return `${round1(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function requestedAtLabel(isoDate) {
  if (!isoDate) {
    return "unknown date";
  }
  const parsed = new Date(isoDate);
  if (Number.isNaN(parsed.getTime())) {
    return isoDate;
  }
  return parsed.toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

// "Imported from <imported_from>" (an imported row) or "Requested" (a requested row); null when `source` is
// neither (a row from a server that has not shipped this field).
export function sourceLine(pathway) {
  const source = pathway && pathway.source;
  if (source === "imported") {
    return `Imported from ${pathway.imported_from}`;
  }
  if (source === "requested") {
    return "Requested";
  }
  return null;
}

// Whether "Open course" shows (only once done, and only with a url the server sent).
export function canOpenCourse(pathway) {
  return Boolean(pathway && pathway.status === "done" && pathway.url);
}

// Whether the row shows "Stop" (POST .../cancel): a requested pathway that is queued or running, and the stop was
// not already asked for (the row shows "Stopping..." instead until the status changes).
export function canStop(pathway, { stopping = false } = {}) {
  return isLive(pathway) && !stopping;
}

// Whether the row shows "Resume" (POST .../resume): a requested pathway that failed or was interrupted.
export function canResume(pathway) {
  return Boolean(pathway) && pathway.source === "requested" && (pathway.status === "failed" || pathway.status === "interrupted");
}

// One pathway -> the view's display row. `lessons`/`sizeLabel` are null when there is no course yet; `sourceText`/
// `errorText` are null when the server sent nothing to show (see `sourceLine`/`errorLine` above). `stopping`: this
// row's own Stop was just clicked and the status has not changed yet (the view passes it in; it is not server state).
export function displayRow(pathway, { stopping = false } = {}) {
  const course = pathway.course || null;
  const progress = pathway.progress || null;
  return {
    id: pathway.id,
    roleText: pathway.role_text,
    requestedAtLabel: requestedAtLabel(pathway.requested_at),
    statusText: statusText(pathway),
    costText: costDisplay(pathway.cost),
    lessons: course ? course.lessons : null,
    sizeLabel: course ? sizeMb(course.size_bytes) : null,
    sourceText: sourceLine(pathway),
    errorText: errorLine(pathway),
    canOpen: canOpenCourse(pathway),
    url: pathway.url || null,
    progressLine: isLive(pathway) ? progressLine(progress) : null,
    progressCounters: isLive(pathway) ? progressCounters(progress) : null,
    canStop: canStop(pathway, { stopping }),
    stopping: isLive(pathway) && stopping,
    canResume: canResume(pathway),
  };
}

// The server's list answer -> display rows, in the order the server sent them (newest first already). `stoppingIds`
// is the set of pathway ids whose Stop was just clicked (the view's own local state).
export function displayRows(pathwaysResponse, { stoppingIds } = {}) {
  const rows = (pathwaysResponse && pathwaysResponse.pathways) || [];
  const stopping = stoppingIds || new Set();
  return rows.map((pathway) => displayRow(pathway, { stopping: stopping.has(pathway.id) }));
}

// ---------------------------------------------------------------------------
// The form and the estimate dialog (Part B, design section 3)
// ---------------------------------------------------------------------------

// The role as the form would send it (trimmed); "Generate course" is disabled when this is empty.
export function cleanedRole(typed) {
  return (typed || "").trim();
}

export function canSubmitRole(typed) {
  return cleanedRole(typed).length > 0;
}

// The estimate's own sentence (design section 3): "Estimate: about N model calls on your <label> login, about M
// page fetches from public documentation sites, about T minutes in the background." plus a second sentence that
// names its basis. `modelLabel` is the chosen model target's display label (modelTargets.js), or null.
export function estimateSentence(estimate, modelLabel) {
  if (!estimate) {
    return "";
  }
  const onLogin = modelLabel ? ` on your ${modelLabel} login` : "";
  let sentence =
    `Estimate: about ${estimate.calls} model call${estimate.calls === 1 ? "" : "s"}${onLogin}, about ${estimate.fetches} page ` +
    `fetch${estimate.fetches === 1 ? "" : "es"} from public documentation sites, about ${estimate.minutes} minute${estimate.minutes === 1 ? "" : "s"} in the background.`;
  if (estimate.basis === "history") {
    const tokens = typeof estimate.tokens === "number" && estimate.tokens >= 1000 ? ` about ${Math.round(estimate.tokens / 1000)}k tokens,` : "";
    sentence += ` Sized from your last course:${tokens} about ${estimate.minutes} minute${estimate.minutes === 1 ? "" : "s"}.`;
  } else {
    sentence += " There is no recorded course yet to estimate tokens or time from.";
  }
  return sentence;
}

export const SENDS_SENTENCE =
  "It sends: the typed role, sentences from job postings stored on your machine, and the course text as it is written. " +
  "The course uses your resume to mark what you already know (contact lines are removed first).";

// `caps` is the ask's own `{calls, fetches}` (learning_job.MAX_CALLS / MAX_FETCHES).
export function stopsSentence(caps) {
  const calls = caps ? caps.calls : null;
  const fetches = caps ? caps.fetches : null;
  return (
    `It stops by itself after ${calls} model calls or ${fetches} page fetches. You can stop it any time from this tab; ` +
    "a stopped course keeps the lessons already verified."
  );
}

// The dialog's title: "Generate a course for 'Director of AI enablement'?"
export function generateDialogTitle(role) {
  return `Generate a course for '${role}'?`;
}

// The second button's label: "Generate (about N calls)".
export function generateButtonLabel(estimate) {
  const calls = estimate ? estimate.calls : null;
  return `Generate (about ${calls} call${calls === 1 ? "" : "s"})`;
}

// `response` is the ask's answer (POST /api/learning/pathways without `approve`: learning_job.ask_response).
// `modelLabel` is the chosen model target's display label, or null (no config read yet). Returns null while there
// is no ask to show.
export function generateDialog(response, modelLabel) {
  if (!response || response.status !== "ask") {
    return null;
  }
  const role = response.role_text;
  return {
    role,
    title: generateDialogTitle(role),
    estimateText: estimateSentence(response.estimate, modelLabel),
    sendsText: SENDS_SENTENCE,
    stopsText: stopsSentence(response.caps),
    approveLabel: generateButtonLabel(response.estimate),
    approveBody: { role_text: role, approve: true },
  };
}

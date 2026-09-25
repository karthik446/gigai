// All Scout find-jobs API calls live here. Routes and DTO shapes are frozen
// in src/gigai/scout/find_jobs/contracts.py (ROUTES, API_BIND, RunRequest,
// UIConsentEnvelope, RunStatusResponse, RunResultsResponse). This module
// does not invent fields; it mirrors that contract exactly.

const STATUS_MESSAGES = {
  400: "The run request was malformed. Reload and try again.",
  403: "This action was refused: consent was missing, stale, or the target isn't allowed from this UI.",
  409: "The configuration changed since it was loaded. Reload the config and try again.",
  422: "The server rejected this request as invalid. Reload and try again.",
  503: "This feature is not available yet.",
  504: "The server timed out handling this request. It may still be running; try checking status again shortly.",
};

// uat-bug-006-r2: a 404 is only "that run could not be found" for a
// run-scoped route (/api/runs/{id}[/...]) -- every 404 IS "not_found"
// server-side (present_api.py's server.py uses that one code for both a
// route that doesn't exist at all, "no such route", and a run id that
// doesn't exist, "run not found"), so the code alone can't tell them apart.
// A stale/pre-upgrade Scout server serving an older route table 404s on
// routes like /api/profiles or /api/runs itself (not a specific run) --
// showing "That run could not be found." there is actively misleading (the
// operator's actual repro: a same-version reinstall left an old server
// running, and the UI told them a run was missing when no run was ever
// requested). A generic route-level 404 shows the server's own message, or
// a hint to restart Scout when the server has none.
const RUN_SCOPED_PATH = /^\/api\/runs\/[^/]+(\/|$)/;

function messageFor404(path, detail) {
  if (RUN_SCOPED_PATH.test(path)) {
    return "That run could not be found.";
  }
  return detail || "The Scout server is out of date: run `gigai scout run` to restart it.";
}

// Error codes whose backend message is specific enough to show as-is,
// instead of the generic per-status text above. config_missing in
// particular used to fall through to the 404 default ("That run could not
// be found."), which is wrong: no run is missing, the config file is.
// prefs_missing/discovery_unavailable/discovery_running are the S2-B setup/
// discover routes' own named error codes, same reasoning.
const CODES_WITH_OWN_MESSAGE = new Set([
  "config_missing",
  "prefs_missing",
  "discovery_unavailable",
  "discovery_running",
]);

class ApiError extends Error {
  // `extra` carries any additional error-body fields beyond code/message --
  // PUT /api/setup's `field_errors` and GET /api/setup's 404 `prefill`, so
  // callers don't have to re-parse the response body to reach them.
  constructor(status, message, code, extra) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    Object.assign(this, extra || {});
  }
}

function messageForStatus(path, status, code, detail) {
  if (code && CODES_WITH_OWN_MESSAGE.has(code) && detail) {
    return detail;
  }
  if (status === 404) {
    return messageFor404(path, detail);
  }
  return STATUS_MESSAGES[status] || detail || `Request failed with status ${status}.`;
}

async function request(method, path, body) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (networkError) {
    throw new ApiError(0, "Could not reach the local API. Is the server running on 127.0.0.1:8765?");
  }

  let payload = null;
  const text = await response.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    const errorBody = payload && typeof payload.error === "object" ? payload.error : null;
    const code = errorBody && typeof errorBody.code === "string" ? errorBody.code : undefined;
    const detail = errorBody && typeof errorBody.message === "string" ? errorBody.message : undefined;
    const { code: _code, message: _message, ...extra } = errorBody || {};
    // Q4b-ui: `detail` is the server's own message verbatim, for callers
    // that render it (the tailored-resume panel: model_output_invalid names
    // the line that failed a guard) instead of the per-status text above.
    throw new ApiError(response.status, messageForStatus(path, response.status, code, detail), code, { ...extra, detail });
  }

  return payload;
}

export function getConfig() {
  return request("GET", "/api/config");
}

export function startRun(runRequest) {
  return request("POST", "/api/run", runRequest);
}

export function getRunStatus(runId) {
  return request("GET", `/api/runs/${encodeURIComponent(runId)}`);
}

export function getRunResults(runId) {
  return request("GET", `/api/runs/${encodeURIComponent(runId)}/results`);
}

// B4: the non-authoritative live-progress view (steps + postings +
// assessments as they happen). Never the final-results authority -- see
// present_api.py's run_progress docstring -- but lets the UI render cards
// well before the sealed outputs exist.
export function getRunProgress(runId) {
  return request("GET", `/api/runs/${encodeURIComponent(runId)}/progress`);
}

// Builds the fixed-shape consent envelope (D5). Every field except the two
// generated IDs is a frozen constant from UIConsentEnvelope.
export function buildConsentEnvelope() {
  return {
    schema_version: "1.0",
    kind: "operator_run_consent",
    action: "run",
    actor: { kind: "operator", id: "local-user" },
    source: "direct_local_ui_confirm",
    invocation_id: crypto.randomUUID(),
    occurrence_id: crypto.randomUUID(),
  };
}

// Builds the RunRequest body exactly per the frozen schema.
export function buildRunRequest({ configDigest, selectionCap, modelTarget }) {
  return {
    schema_version: "scout-find-jobs-run-request:1",
    consent: buildConsentEnvelope(),
    config_digest: configDigest,
    selection_cap: selectionCap,
    selection_rule: "new_or_edited_role_match",
    model_target: modelTarget,
  };
}

// S2-B: the setup interview + "Discover companies" panel routes
// (present_api.py's GET/PUT /api/setup, POST /api/discover, GET
// /api/discover/latest). A 404 from getSetup carries `prefill` on the
// thrown ApiError (see present_api.py's _handle_get_setup); a 400 from
// putSetup carries `field_errors`, one message per invalid field.
export function getSetup() {
  return request("GET", "/api/setup");
}

export function putSetup(prefsFields) {
  return request("PUT", "/api/setup", prefsFields);
}

export function startDiscovery() {
  return request("POST", "/api/discover", {});
}

export function getDiscoverLatest() {
  return request("GET", "/api/discover/latest");
}

// F1: profiles (S25). GET lists every profile + which one is selected;
// POST creates one; PUT edits one; archive/selection are their own routes
// (see find_jobs/api/profiles.py -- this module mirrors that contract).
export function getProfiles() {
  return request("GET", "/api/profiles");
}

export function createProfile(fields) {
  return request("POST", "/api/profiles", fields);
}

export function updateProfile(profileId, fields) {
  return request("PUT", `/api/profiles/${encodeURIComponent(profileId)}`, fields);
}

export function archiveProfile(profileId, replacementProfileId) {
  return request("POST", `/api/profiles/${encodeURIComponent(profileId)}/archive`, {
    ...(replacementProfileId ? { replacement_profile_id: replacementProfileId } : {}),
  });
}

export function selectProfile(profileId) {
  return request("POST", "/api/profiles/selection", { profile_id: profileId });
}

// P5: one standalone ("quick") assessment -- URL or pasted text, a profile
// or a pasted resume. Synchronous: the handler blocks for the model call
// (present_api's docstring); a 504 assess_timeout surfaces through ApiError
// like any other error code.
export function postAssess(request_) {
  return request("POST", "/api/assess", request_);
}

export function getAssessments(params) {
  const query = new URLSearchParams();
  if (params && params.profileId) {
    query.set("profile_id", params.profileId);
  }
  if (params && params.verdict) {
    query.set("verdict", params.verdict);
  }
  const qs = query.toString();
  return request("GET", `/api/assessments${qs ? `?${qs}` : ""}`);
}

// P3: the Q&A loop. POST upserts one answered question and optionally
// re-assesses the named job (by job_identity) with every answered question
// applied; GET lists every answered question recorded so far.
export function postAnswer(fields) {
  return request("POST", "/api/answers", fields);
}

export function getAnswers() {
  return request("GET", "/api/answers");
}

// P6: Jev pre-rank for one run's postings, against a profile (default: the
// selected one). No key configured -> scores come back empty (fail open),
// never an error.
export function postRank(runId, fields) {
  return request("POST", `/api/runs/${encodeURIComponent(runId)}/rank`, fields || {});
}

// P9c: every find-jobs run for this target (newest first), with per-run
// counts (found/new/assessed/matched) -- the dashboard's "last run"/"new
// since last run", the Profiles run-history table, and the Find-jobs
// past-run picker. Optionally scoped to one profile.
export function getRuns(params) {
  const query = new URLSearchParams();
  if (params && params.profileId) {
    query.set("profile_id", params.profileId);
  }
  const qs = query.toString();
  return request("GET", `/api/runs${qs ? `?${qs}` : ""}`);
}

// P9c: the application pipeline + needs-action panels, and the posting
// card's "Mark applied" action. GET lists every recorded application event
// (incl. `linked_posting`, A1's find-jobs join, `null` when unmatched);
// POST records one via external_ref = the posting's normalized_url.
export function getApplications() {
  return request("GET", "/api/applications");
}

export function postApplication(fields) {
  return request("POST", "/api/applications", fields);
}

// Q3/Q4b-ui: one tailored resume for one posting (find_jobs/api/
// tailored_resumes.py). POST is synchronous (~20-30 s: one model call, one
// retry on a rejected draft); its errors are 422 (bad request), 502
// model_output_invalid (the draft failed a guard; the message names the
// line), 504 tailor_timeout. GET lists the stored ones, newest first,
// optionally filtered by profile_id and/or job_identity.
export function postTailoredResume(request_) {
  return request("POST", "/api/tailored-resumes", request_);
}

export function getTailoredResumes(params) {
  const query = new URLSearchParams();
  if (params && params.profileId) {
    query.set("profile_id", params.profileId);
  }
  if (params && params.jobIdentity) {
    query.set("job_identity", params.jobIdentity);
  }
  const qs = query.toString();
  return request("GET", `/api/tailored-resumes${qs ? `?${qs}` : ""}`);
}

export { ApiError };

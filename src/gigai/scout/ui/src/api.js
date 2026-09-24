// All Scout find-jobs API calls live here. Routes and DTO shapes are frozen
// in src/gigai/scout/find_jobs/contracts.py (ROUTES, API_BIND, RunRequest,
// UIConsentEnvelope, RunStatusResponse, RunResultsResponse). This module
// does not invent fields; it mirrors that contract exactly.

const STATUS_MESSAGES = {
  400: "The run request was malformed. Reload and try again.",
  403: "This action was refused: consent was missing, stale, or the target isn't allowed from this UI.",
  404: "That run could not be found.",
  409: "The configuration changed since it was loaded. Reload the config and try again.",
  422: "The server rejected this request as invalid. Reload and try again.",
  503: "This feature is not available yet.",
  504: "The server timed out handling this request. It may still be running; try checking status again shortly.",
};

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

function messageForStatus(status, code, detail) {
  if (code && CODES_WITH_OWN_MESSAGE.has(code) && detail) {
    return detail;
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
    throw new ApiError(response.status, messageForStatus(response.status, code, detail), code, extra);
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

export { ApiError };

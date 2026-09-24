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
  504: "The server timed out handling this request. It may still be running; try checking status again shortly.",
};

// Error codes whose backend message is specific enough to show as-is,
// instead of the generic per-status text above. config_missing in
// particular used to fall through to the 404 default ("That run could not
// be found."), which is wrong: no run is missing, the config file is.
const CODES_WITH_OWN_MESSAGE = new Set(["config_missing"]);

class ApiError extends Error {
  constructor(status, message, code) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
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
    throw new ApiError(response.status, messageForStatus(response.status, code, detail), code);
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

export { ApiError };

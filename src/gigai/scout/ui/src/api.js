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

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function messageForStatus(status, fallback) {
  return STATUS_MESSAGES[status] || fallback || `Request failed with status ${status}.`;
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
    const detail = payload && typeof payload.message === "string" ? payload.message : undefined;
    throw new ApiError(response.status, messageForStatus(response.status, detail));
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

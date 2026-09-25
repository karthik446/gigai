// P9b: the setup wizard's own fetch helpers. api.js belongs to P9a, so this
// module carries its own `request` (the same shape: JSON in/out, an ApiError
// with status/code/extra on any non-2xx) and only borrows api.js's ApiError
// class so `instanceof ApiError` holds across both modules.
//
// Routes (all frozen server-side):
//   GET  /api/setup             find_jobs/api/setup.py   (200 prefs | 404 prefs_missing + prefill)
//   PUT  /api/setup             find_jobs/api/setup.py
//   GET  /api/config            find_jobs/api/config.py  (resume_label / resume_created_at / resume_preview)
//   GET  /api/profiles          find_jobs/api/profiles.py
//   POST /api/profiles          find_jobs/api/profiles.py
//   PUT  /api/profiles/{id}     find_jobs/api/profiles.py
//   POST /api/resume/extract    find_jobs/api/extract.py (P9b, A3)
import { ApiError } from "../api.js";

const STATUS_MESSAGES = {
  400: "The server rejected some fields. Check the messages next to them.",
  403: "This action was refused by the local server.",
  404: "That resource could not be found.",
  415: "The server refused the request format. Reload and try again.",
  422: "The server rejected this request as invalid. Reload and try again.",
  502: "The model's answer could not be used. Try again or pick another model target.",
  503: "The model target is not available right now. Check your GigAI setup.",
  504: "The model call timed out. Try again or pick a faster model target.",
};

// Codes whose server message is specific enough to show verbatim.
const CODES_WITH_OWN_MESSAGE = new Set([
  "config_missing",
  "prefs_missing",
  "discovery_unavailable",
  "model_target_unavailable",
  "model_unavailable",
  "model_output_invalid",
  "extract_timeout",
  "profile_not_found",
  "profile_unavailable",
  "resume_unavailable",
  "resume_input_invalid",
  "invalid_value",
]);

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
  } catch {
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

export function getSetup() {
  return request("GET", "/api/setup");
}

export function putSetup(prefsFields) {
  return request("PUT", "/api/setup", prefsFields);
}

export function getConfig() {
  return request("GET", "/api/config");
}

export function getProfiles() {
  return request("GET", "/api/profiles");
}

export function createProfile(fields) {
  return request("POST", "/api/profiles", fields);
}

export function updateProfile(profileId, fields) {
  return request("PUT", `/api/profiles/${encodeURIComponent(profileId)}`, fields);
}

// A3: {resume_text | profile_id, model_target?} -> {stack, seniority,
// titles, extractor, model_target, resolved_target, resume}. Synchronous on
// the server (the handler blocks for the model call); a 504 extract_timeout
// arrives as an ApiError like every other code.
export function extractResume(fields) {
  return request("POST", "/api/resume/extract", fields);
}

export { ApiError };

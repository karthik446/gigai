// All Scout find-jobs API calls live here. Routes and DTO shapes are frozen
// in src/gigai/scout/find_jobs/contracts.py (ROUTES, API_BIND, RunRequest,
// UIConsentEnvelope, RunStatusResponse, RunResultsResponse). This module
// does not invent fields; it mirrors that contract exactly.

import { rememberCompanyNames } from "./display.js";

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
// posting_requirements_unreadable (uat-bug-029, POST /api/assess 422): "Couldn't
// read this posting's requirements", which the pages show as a note, not an
// error (rankModel.isRequirementsUnreadable).
const CODES_WITH_OWN_MESSAGE = new Set([
  "config_missing",
  "prefs_missing",
  "discovery_unavailable",
  "discovery_running",
  "posting_requirements_unreadable",
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

// 0110-9-01: `options.signal` (an AbortController's) cancels the request; a
// cancelled one rejects with code "aborted", which its caller ignores.
async function request(method, path, body, options) {
  const signal = options && options.signal ? options.signal : undefined;
  let response;
  try {
    response = await fetch(path, {
      method,
      // A DELETE carries no body but is a write: the server's CSRF check
      // wants the JSON content type on every write.
      headers: body || method === "DELETE" ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (networkError) {
    if (signal && signal.aborted) {
      throw new ApiError(0, "The request was cancelled.", "aborted");
    }
    throw new ApiError(0, "Could not reach the local API. Is the server running on 127.0.0.1:8765?");
  }

  let payload = null;
  let text;
  try {
    text = await response.text();
  } catch (readError) {
    if (signal && signal.aborted) {
      throw new ApiError(0, "The request was cancelled.", "aborted");
    }
    throw readError;
  }
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

  // 0110-8-11: the company names this response carries (display.js).
  return rememberCompanyNames(payload);
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

// run-reads-fast (uat-bug-022): a run's results are read a page at a time
// (find_jobs/api/run_reads.py). A page is the no-query response's shape for
// `limit` rows from `offset`, in the grid's own order, plus `total` /
// `limit` / `offset`, the run's `counts` and `created_at`. No posting
// carries its `text` (getRunPosting reads one posting whole), and each row
// has `rank_score` (the score stored for it, RankScore) and `rank` (the
// model's line: score, reasons, blockers), each null when there is none.
// The read never calls a model.
//
// The server sends `carried_forward_assessments` beside `payload`, and the
// cards are built from `payload` alone (boardRows.rowsFromResults reads
// `payload.carried_forward_assessments`): a page is answered with the list
// in both places, so a posting a run found unchanged shows the assessment
// it carried forward.
export const RESULTS_PAGE_SIZE = 100;

export async function getRunResultsPage(runId, { limit = RESULTS_PAGE_SIZE, offset = 0 } = {}) {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  const response = await request("GET", `/api/runs/${encodeURIComponent(runId)}/results?${query}`);
  const carried = response.carried_forward_assessments || [];
  return { ...response, carried_forward_assessments: carried, payload: { ...response.payload, carried_forward_assessments: carried } };
}

// Two pages of one run as one response: the later page's rows, assessments,
// not-assessed rows and carried-forward assessments after the earlier one's.
export function mergeRunResultsPages(earlier, later) {
  const carried = earlier.carried_forward_assessments.concat(later.carried_forward_assessments);
  return {
    ...earlier,
    payload: {
      ...earlier.payload,
      rows: earlier.payload.rows.concat(later.payload.rows),
      assessments: earlier.payload.assessments.concat(later.payload.assessments),
      not_assessed: earlier.payload.not_assessed.concat(later.payload.not_assessed),
      carried_forward_assessments: carried,
    },
    carried_forward_assessments: carried,
  };
}

// Every page of a run's results, merged. `onPage(response)` is called after
// each page with everything read so far, so a caller can draw the first page
// (the top of the grid) while the rest loads; returning false from it stops
// the read (the caller moved to another run).
export async function getRunResults(runId, { pageSize = RESULTS_PAGE_SIZE, onPage } = {}) {
  let merged = await getRunResultsPage(runId, { limit: pageSize, offset: 0 });
  let wanted = !onPage || onPage(merged) !== false;
  while (wanted && merged.payload.rows.length < merged.total) {
    const page = await getRunResultsPage(runId, { limit: pageSize, offset: merged.payload.rows.length });
    if (page.payload.rows.length === 0) {
      break; // the run has fewer rows than the first page said: never loop on an empty page
    }
    merged = mergeRunResultsPages(merged, page);
    wanted = !onPage || onPage(merged) !== false;
  }
  return merged;
}

// N33: the Jobs page's own reader. It asks for `pageSize` rows at a time,
// only when the page wants them: `ensure(count)` reads pages until `count`
// rows (or the run's `total`) are in, each page from where the last ended,
// so earlier pages are never asked for again. Calls queue, so two waiting
// for rows never read the same page twice. `onPage(response)` gets everything
// read so far after each page (the merged response); returning false stops
// reading (another run is shown). `ensure` answers the merged response.
export const JOBS_PAGE_ROWS = 50;

export function createResultsPager(runId, { pageSize = JOBS_PAGE_ROWS, fetchPage = getRunResultsPage, onPage } = {}) {
  let merged = null;
  let stopped = false;
  let queue = Promise.resolve();
  const loaded = () => (merged ? merged.payload.rows.length : 0);

  async function read(count) {
    while (!stopped && (merged === null || (loaded() < count && loaded() < merged.total))) {
      const page = await fetchPage(runId, { limit: pageSize, offset: loaded() });
      if (stopped) {
        break;
      }
      if (merged === null) {
        merged = page;
      } else if (page.payload.rows.length === 0) {
        break; // the run has fewer rows than it said: never loop on an empty page
      } else {
        merged = mergeRunResultsPages(merged, page);
      }
      if (onPage && onPage(merged) === false) {
        stopped = true;
      }
    }
    return merged;
  }

  return {
    ensure(count) {
      const result = queue.then(() => read(count));
      queue = result.catch(() => {});
      return result;
    },
    stop() {
      stopped = true;
    },
    loaded,
    total: () => (merged ? merged.total : null),
  };
}

// The stored scores of a results response, in POST /rank's `scores` shape
// (jobModel.buildJobs' `rankScores`). An older response whose rows carry
// no `rank_score` (or no rows) reads as none.
export function storedRankScores(response) {
  const rows = (response && response.payload && response.payload.rows) || [];
  return rows.map((row) => row && row.rank_score).filter(Boolean);
}

// One posting of a run, complete: {row: {posting (with its text), outcome,
// rank_score, h1b?, job_state?}, assessment, not_assessed_reason,
// carried_forward}. `normalizedUrl` is the posting's normalized_url.
export function getRunPosting(runId, normalizedUrl) {
  const query = new URLSearchParams({ url: normalizedUrl });
  return request("GET", `/api/runs/${encodeURIComponent(runId)}/posting?${query}`);
}

// B4: the non-authoritative live-progress view (steps + postings +
// assessments as they happen). Never the final-results authority -- see
// present_api.py's run_progress docstring -- but lets the UI render cards
// well before the sealed outputs exist. run-reads-fast: read as its summary,
// which has no posting text and no list of skipped boards.
export function getRunProgress(runId) {
  return request("GET", `/api/runs/${encodeURIComponent(runId)}/progress?summary=1`);
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

// uat-bug-033: {exa: bool} -> {sources: {exa}}. Only the optional Exa source
// changes in find-jobs.json.
export function putConfigSources(fields) {
  return request("PUT", "/api/config/sources", fields);
}

// {keys: {exa, openrouter, openai: true|false}}: whether each key is set.
export function getSecretsStatus() {
  return request("GET", "/api/secrets/status");
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

// 0110-047: archive a profile as `deleted`; the answer carries the selection after the delete.
export function deleteProfile(profileId) {
  return request("DELETE", `/api/profiles/${encodeURIComponent(profileId)}`);
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

// 0.1.10.7 C: the user's answers and stories (find_jobs/api/answers.py,
// api/story_bank.py). They belong to the user, not to a profile. Local, no
// model call. The UI only reads and deletes them (the Answers and stories
// page) and saves an answer from a job page's question box. `revision` on a
// delete is the value this page read: a stale one answers 409
// revision_conflict with the current `answer` / `story` (ApiError.answer /
// ApiError.story), written since by the agent or another window.
const bankQuery = (fields) => {
  const pairs = Object.entries(fields).filter(([, value]) => value !== undefined && value !== null && value !== "");
  return pairs.length ? `?${pairs.map(([key, value]) => `${key}=${encodeURIComponent(value)}`).join("&")}` : "";
};

export function getAnswers() {
  return request("GET", "/api/answers");
}

export function getAnswerMatch({ questionId, question }) {
  return request("GET", `/api/answers/match${bankQuery({ question_id: questionId, question })}`);
}

export function deleteAnswer(questionId, revision) {
  return request("DELETE", `/api/answers/${encodeURIComponent(questionId)}${bankQuery({ revision })}`);
}

export function getStories() {
  return request("GET", "/api/stories");
}

export function deleteStory(storyId, revision) {
  return request("DELETE", `/api/stories/${encodeURIComponent(storyId)}${bankQuery({ revision })}`);
}

// SCOPE-ADD-3: the ranking pass for one run's postings, by the run's own
// model target (rankModel.createRankPass). `{}` only READS (the scores so
// far, `rank_status` and `rank_record`); `{start: true}` is the Rank /
// Re-rank click (starts a pass, or joins the one running); `{cancel: true}`
// stops it. An older server's answer may carry a `usage` block or no
// `rank_record`: both are ignored.
export function postRank(runId, fields) {
  return request("POST", `/api/runs/${encodeURIComponent(runId)}/rank`, fields || {});
}

// uat-bug-042: "Assess all new" for one run: {} reads the plan, the newest job
// and the run's live counts; {start: true} starts or joins; {cancel: true}
// stops (what finished is kept). See assessAllModel.js.
export function postAssessAll(runId, fields) {
  return request("POST", `/api/runs/${encodeURIComponent(runId)}/assess-all`, fields || {});
}

// 0110-019: the run's posted window: {} reads what was searched (run_days,
// searched_days); {days: N} searches the stored boards for the postings of
// the last N days the run does not hold, adds them, and ranks and assesses
// only those. See postedWindowModel.js.
export function postPostedWindow(runId, fields) {
  return request("POST", `/api/runs/${encodeURIComponent(runId)}/posted-window`, fields || {});
}

// P9c: every find-jobs run for this target (newest first), with per-run
// counts (found/new/assessed/matched) -- the dashboard's "last run"/"new
// since last run", the Profiles run-history table, and the Find-jobs
// past-run picker. Optionally scoped to one profile. run-reads-fast:
// `status` keeps the runs with that status and `limit` the newest N, so
// {profileId, status: "succeeded", limit: 1} is the run the Jobs page opens
// on, read without the history behind it.
export function getRuns(params) {
  const query = new URLSearchParams();
  if (params && params.profileId) {
    query.set("profile_id", params.profileId);
  }
  if (params && params.status) {
    query.set("status", params.status);
  }
  if (params && params.limit) {
    query.set("limit", String(params.limit));
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

// 0110-006: show the original or the rewrite of one line (PUT
// /api/tailored-resumes/lines). `updatedAt` is the updated_at of the resume
// the caller is looking at; a newer tailoring answers 409
// tailored_resume_changed. Answers the updated TailorResponse.
export function putTailoredResumeLine({ profileId, jobIdentity, updatedAt, lineId, use }) {
  return request("PUT", "/api/tailored-resumes/lines", {
    profile_id: profileId,
    job_identity: jobIdentity,
    updated_at: updatedAt,
    line_id: lineId,
    use,
  });
}

// 0110-10-05 C: put back what a tailored resume left out for length (use:
// "restore"), or leave it out again (use: "cut"): PUT
// /api/tailored-resumes/length. Same revision check as a line choice.
// Answers the updated TailorResponse.
export function putTailoredResumeLength({ profileId, jobIdentity, updatedAt, use }) {
  return request("PUT", "/api/tailored-resumes/length", {
    profile_id: profileId,
    job_identity: jobIdentity,
    updated_at: updatedAt,
    use,
  });
}

// 0.1.10.9 master P5: Add or Remove one master line on a job's tailored
// resume (PUT /api/tailored-resumes/selection). `fit` answers an Add that
// pushes the resume over 2 pages: "ask" (the default) stores nothing and
// names what would be cut, "cut" makes room, "keep" keeps both. Answers the
// TailorResponse plus `selection_change`.
export function putTailoredResumeSelection({ profileId, jobIdentity, updatedAt, use, itemId, fit }) {
  return request("PUT", "/api/tailored-resumes/selection", {
    profile_id: profileId,
    job_identity: jobIdentity,
    updated_at: updatedAt,
    use,
    item_id: itemId,
    ...(fit ? { fit } : {}),
  });
}

// 0.1.10.9 master P5: the master resume (find_jobs/api/master.py). The reads
// answer 200 with `master: null` when there is none yet. Every write sends
// the `revision` the page read; a 409 revision_conflict carries `current`
// (the revision the master is at now) on the error.
export function getMaster(revision) {
  return request("GET", `/api/master${revision ? `?revision=${encodeURIComponent(revision)}` : ""}`);
}

export function getMasterHistory() {
  return request("GET", "/api/master/history");
}

export function postMasterLine({ revision, entryId, section, text, force }) {
  return request("POST", "/api/master/lines", { revision, text, ...(entryId ? { entry_id: entryId } : { section }), ...(force ? { force: true } : {}) });
}

// use: "edit" (with text), "retire" or "restore".
export function putMasterLine({ revision, id, use = "edit", text }) {
  return request("PUT", "/api/master/lines", { revision, id, use, ...(use === "edit" ? { text } : {}) });
}

export function postMasterEntry({ revision, section, heading, sublines }) {
  return request("POST", "/api/master/entries", { revision, section, heading, sublines });
}

export function putMasterEntry({ revision, id, use = "edit", heading, sublines }) {
  return request("PUT", "/api/master/entries", { revision, id, use, ...(use === "edit" ? { heading, sublines } : {}) });
}

export function getMasterMigration() {
  return request("GET", "/api/master/migration");
}

export function postMasterMigration(answers) {
  return request("POST", "/api/master/migration", { answers: answers || {} });
}

export function getMasterSelection() {
  return request("GET", "/api/master/selection");
}

export function postMasterSelection({ profileId, use }) {
  return request("POST", "/api/master/selection", { profile_id: profileId, use });
}

// 0.1.10-003 / 0110-046: the per-profile title and the layout of a resume PDF
// (find_jobs/api/resume_display.py). GET carries `saved`, the values and,
// while this profile has no title, a local `suggested` title; PUT saves
// (per-profile `titles` merge). GigAI stores no name or contact details.
export function getResumeDisplay(profileId) {
  const qs = profileId ? `?profile_id=${encodeURIComponent(profileId)}` : "";
  return request("GET", `/api/resume-display${qs}`);
}

export function putResumeDisplay(body) {
  return request("PUT", "/api/resume-display", body);
}

// 0110-10-05 A: the resumes folder (find_jobs/api/resumes_folder.py): where
// it is ({path, shown, source, default, exists}) and, with a profile and a
// job, the names of that job's files there (`files`). PUT {path} chooses
// another folder; an empty path is the default.
export function getResumesFolder({ profileId, jobIdentity } = {}) {
  const params = new URLSearchParams();
  if (profileId && jobIdentity) {
    params.set("profile_id", profileId);
    params.set("job_identity", jobIdentity);
  }
  const qs = params.toString();
  return request("GET", `/api/resumes-folder${qs ? `?${qs}` : ""}`);
}

export function putResumesFolder(body) {
  return request("PUT", "/api/resumes-folder", body);
}

// 0110-046: the one-time contact cleanup's report (find_jobs/api/
// privacy_cleanup.py). GET runs the cleanup when it has not run yet; PUT
// records that the UI showed the report.
export function getPrivacyCleanup() {
  return request("GET", "/api/privacy/cleanup");
}

export function putPrivacyCleanupShown() {
  return request("PUT", "/api/privacy/cleanup", { shown: true });
}

// The PDF routes answer bytes: the blob and the Content-Disposition file name
// come back for a download. `header` is the Generate PDF form's values
// (generatePdfModel.headerBody): sent in this one request body, never stored
// by the server, never kept here.
async function postPdf(path, body) {
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (networkError) {
    throw new ApiError(0, "Could not reach the local API. Is the server running on 127.0.0.1:8765?");
  }
  if (!response.ok) {
    let detail;
    let code;
    try {
      const payload = JSON.parse(await response.text());
      detail = payload && payload.error && payload.error.message;
      code = payload && payload.error && payload.error.code;
    } catch {
      /* not JSON: fall back to the status text */
    }
    throw new ApiError(response.status, detail || `Request failed with status ${response.status}.`, code, { detail });
  }
  return { blob: await response.blob(), fileName: pdfFileName(response.headers.get("Content-Disposition")) };
}

export function postTailoredResumePdf({ profileId, jobIdentity, header }) {
  const body = { profile_id: profileId, job_identity: jobIdentity };
  if (header) {
    body.header = header;
  }
  return postPdf("/api/tailored-resumes/pdf", body);
}

export function postResumePdf({ markdown, profileId, header }) {
  const body = { markdown };
  if (profileId) {
    body.profile_id = profileId;
  }
  if (header) {
    body.header = header;
  }
  return postPdf("/api/resume/pdf", body);
}

// The file name in `attachment; filename="<name>"`, or a plain fallback (the
// server names it <company>-<role>-<YYYY-MM-DD>.pdf, never after the user).
export function pdfFileName(disposition) {
  const match = /filename="([^"]+)"/.exec(disposition || "");
  return match ? match[1] : "resume.pdf";
}

// uat-batch2 (N11-C): "Update sources" (find_jobs/api/sources.py). POST
// starts a background update and answers 202 {update_id, status: "running"}
// at once (409 sources_update_running while one runs, 404
// target_unavailable with no target); GET is polled for its progress and
// carries `index`, what the Jobs page needs to know about the stored
// postings. `force` starts over a stuck update.
// 0110-020: `fullRefresh` asks every board; the default update is
// incremental (boards checked within the stale window are left alone).
export function startSourcesUpdate({ force = false, fullRefresh = false } = {}) {
  const body = {};
  if (force) {
    body.force = true;
  }
  if (fullRefresh) {
    body.full_refresh = true;
  }
  return request("POST", "/api/sources/update", body);
}

export function getSourcesUpdate() {
  return request("GET", "/api/sources/update");
}

// 0110-024 P4 / 0110-025 R4 / 0110-026 S3: the per-project background
// settings (find_jobs/background_settings.py). PUT takes only the keys to
// change and answers the same body as GET; 422 wrong_type / unknown_key /
// bad_enum / invalid_value, 404 target_unavailable, 409 settings_unreadable
// (the stored file cannot be read and is never overwritten).
export function getBackgroundSettings() {
  return request("GET", "/api/settings/background");
}

export function putBackgroundSettings(patch) {
  return request("PUT", "/api/settings/background", patch);
}

// 0.1.10.7 E: GET /api/metrics -> {schema_version, kind, model, aggregates,
// comparison}: the averages of the recorded model calls (tokens, seconds,
// cost, error rate), per (kind, model_target, model) and per (kind,
// model_target). {kind, model} narrow it. Numbers only; no model is called.
export function getMetrics({ kind, model } = {}) {
  const query = new URLSearchParams();
  if (kind) {
    query.set("kind", kind);
  }
  if (model) {
    query.set("model", model);
  }
  const text = query.toString();
  return request("GET", text ? `/api/metrics?${text}` : "/api/metrics");
}

// 0.1.10.7 M4b: the Jobs page by posting, with no run.
//
// GET /api/postings (posting_search.search_postings) is the live search over
// the stored postings, across the active profiles: `query` is the string
// postingsModel.postingsQuery builds. It never moves the "new since" anchor.
// GET /api/new is the PEEK the "New since last check (N)" chip counts from
// (a GET never moves the anchor); POST /api/new/seen is "Mark all seen" (it
// moves the anchor to now). POST /api/postings/assess is "Assess these":
// without `approve: true` it answers `status: "ask"` (count and estimate)
// and assesses nothing.
//
// 0110-9-01: while the server prepares the stored postings for the first time
// (once after an upgrade), GET /api/postings and GET /api/new answer 202 with
// `status: "preparing"` and the percent instead of rows; GET
// /api/postings/status says how far it is, at once. postingsStore.js is the
// one caller that waits on it.
export function getPostings(query, options) {
  return request("GET", `/api/postings${query ? `?${query}` : ""}`, undefined, options);
}

export function getNewPeek(options) {
  return request("GET", "/api/new?peek=1", undefined, options);
}

export function getPostingsStatus(options) {
  return request("GET", "/api/postings/status", undefined, options);
}

export function postMarkAllSeen() {
  return request("POST", "/api/new/seen", {});
}

export function postAssessThese(body) {
  return request("POST", "/api/postings/assess", body);
}

// 0.1.10.7 M4b: the background pipeline (find_jobs/api/pipeline.py).
// GET /api/pipeline is its status (lanes, today's counters against their
// caps, approvals, last errors); GET /api/pipeline/job one job's steps with
// their numbers, the requirements met before and after tailoring, the Scout
// ATS breakdown and the Scout label. An approval is decided with
// {approve: true|false}; "process now" queues one assessed job and never
// waits for a model (202).
export function getPipeline() {
  return request("GET", "/api/pipeline");
}

export function getPipelineJob({ jobIdentity, profileId }) {
  const query = new URLSearchParams({ job_identity: jobIdentity, profile_id: profileId });
  return request("GET", `/api/pipeline/job?${query}`);
}

export function postPipelineApproval(approvalId, body) {
  return request("POST", `/api/pipeline/approvals/${encodeURIComponent(approvalId)}`, body);
}

export function postPipelineProcess(body) {
  return request("POST", "/api/pipeline/process", body);
}

export { ApiError };

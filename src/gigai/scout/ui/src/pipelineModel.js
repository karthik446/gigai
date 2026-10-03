// 0.1.10.7 M4b: the background pipeline as the UI shows it, as pure
// functions (no React, no fetch) so the rules run under node.
//
// Two readers:
//   the job page's step timeline   GET /api/pipeline/job (scout-pipeline-job:1):
//       tailor -> reassess + Scout ATS -> Scout label, each step with its
//       state and the numbers of its last attempt, the requirements met
//       before and after tailoring, the Scout ATS breakdown, the Scout label
//   Settings' Background pipeline  GET /api/pipeline (scout-pipeline:1) and
//       the `pipeline` / `rank` blocks of GET / PUT /api/settings/background:
//       lanes, today's calls against their caps, approvals, settings, errors
//
// Every status here is a code the server sends, shown in words made from the
// code. The two sentences that go with the Scout ATS score and the Scout
// label (`ats.wording`, `label.wording`) are the server's own constants,
// shown as they arrive; this file writes no wording of its own for them.
import { secondsText, targetName, tokensText } from "./metricsModel.js";
import { MODEL_TARGETS, MODEL_TARGET_LABELS } from "./modelTargets.js";
import { LABEL_WORDS, SCOUT_ATS_NAME, SCOUT_LABEL_NAME } from "./postingsModel.js";

export const STEP_ORDER = ["tailor", "reassess", "ats", "label"];
export const STEP_TITLES = {
  tailor: "Tailor resume",
  reassess: "Assess the tailored resume",
  ats: SCOUT_ATS_NAME,
  label: SCOUT_LABEL_NAME,
};
// The timeline's columns: the re-assessment and the Scout ATS score both read the tailored resume.
export const STEP_STAGES = [["tailor"], ["reassess", "ats"], ["label"]];

const STATE_WORDS = {
  not_started: "Not started",
  blocked: "Waiting for the step before",
  ready: "Queued",
  running: "Running",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
  awaiting_approval: "Waiting for your approval",
};
const WAIT_WORDS = {
  daily_cap_reached: "the daily cap of model calls is reached",
  lane_backoff: "the model is backed off",
  retry_backoff: "it is tried again shortly",
};

export function words(code) {
  return String(code || "").replace(/_/g, " ");
}

function tone(state) {
  if (state === "done") {
    return "ok";
  }
  if (state === "failed") {
    return "danger";
  }
  if (state === "running" || state === "awaiting_approval") {
    return "warn";
  }
  return "plain";
}

// One step of the timeline: {name, title, state, stateLabel, tone, model,
// tokens, seconds, attempts, error, waiting}. `model`, `tokens`, `seconds`
// are of the step's last attempt (null for a step that makes no model call
// or has not run).
function stepRow(name, step) {
  if (!step) {
    return { name, title: STEP_TITLES[name], state: "not_started", stateLabel: STATE_WORDS.not_started, tone: "plain", model: null, tokens: null, seconds: null, attempts: 0, error: null, waiting: null };
  }
  const run = step.last_run || null;
  const used = run && typeof run.input_tokens === "number" ? run.input_tokens + (typeof run.output_tokens === "number" ? run.output_tokens : 0) : null;
  return {
    name,
    title: STEP_TITLES[name] || words(name),
    state: step.state,
    stateLabel: STATE_WORDS[step.state] || words(step.state),
    tone: tone(step.state),
    model: step.model_target ? [targetName(step.model_target), run && run.model].filter(Boolean).join(" · ") : null,
    tokens: used === null ? null : `${tokensText(used)} tokens`,
    seconds: run ? secondsText(run.seconds) : null,
    attempts: step.attempts || 0,
    error: step.state === "failed" && step.error_code ? step.error_code : null,
    waiting: step.waiting ? WAIT_WORDS[step.waiting] || words(step.waiting) : null,
  };
}

// The timeline: the four steps in their three stages, always all four (a
// job that never entered the pipeline shows each as not started).
export function stepTimeline(detail) {
  const byName = new Map(((detail && detail.steps) || []).map((step) => [step.name, step]));
  return STEP_STAGES.map((names) => names.map((name) => stepRow(name, byName.get(name))));
}

// "73 -> 91 after tailoring" from the backend's requirements_met numbers,
// with the counts behind it; null until both assessments exist.
export function variantLine(detail) {
  const met = detail && detail.requirements_met;
  const base = met && met.base;
  const tailored = met && met.tailored;
  if (!base || !tailored || typeof base.percent !== "number" || typeof tailored.percent !== "number") {
    return null;
  }
  return {
    text: `${base.percent} → ${tailored.percent} after tailoring`,
    detail: `Requirements met: ${base.met} of ${base.total} → ${tailored.met} of ${tailored.total}`,
    improved: tailored.percent > base.percent,
  };
}

// The Scout ATS chip and its breakdown popover; null until the score exists.
// `wording` is the server's ATS_WORDING, shown as it is.
export function atsChip(detail) {
  const ats = detail && detail.ats;
  if (!ats || typeof ats.score !== "number") {
    return null;
  }
  const parts = ats.parts || {};
  const part = (label, value, of) => (typeof value === "number" ? `${label}: ${value} of ${of}` : null);
  const rows = [
    part("Parse fidelity", parts.fidelity, 40),
    part("Keyword coverage", parts.coverage, 40),
    part("Format rules", parts.format, 20),
    ats.key_skills && ats.key_skills !== "0/0" ? `Key skills found: ${ats.key_skills}` : null,
    Array.isArray(ats.missing) && ats.missing.length ? `Missing: ${ats.missing.join(", ")}` : null,
    Array.isArray(ats.failed_rules) && ats.failed_rules.length ? `Format rules not met: ${ats.failed_rules.join(", ")}` : null,
  ].filter(Boolean);
  return { label: `${SCOUT_ATS_NAME} ${ats.score}`, score: ats.score, line: ats.line || null, rows, wording: ats.wording || null };
}

// The Scout label chip; null until the label step is done.
export function labelChip(detail) {
  const label = detail && detail.label;
  if (!label || !LABEL_WORDS[label.label]) {
    return null;
  }
  return {
    label: `${label.name || SCOUT_LABEL_NAME}: ${LABEL_WORDS[label.label]}`,
    code: label.label,
    tone: label.label === "recommended" ? "ok" : "warn",
    reasons: (label.reasons || []).map(words),
    minAts: typeof label.min_ats === "number" ? label.min_ats : null,
    wording: label.wording || null,
  };
}

const LIVE_STATES = new Set(["running", "waiting"]);

// True while the job's pipeline is still moving: the page reads it again.
export function pipelineLive(detail) {
  return Boolean(detail) && LIVE_STATES.has(detail.state);
}

// The "process now" button: {enabled, label, reason, body}. A job enters the
// pipeline only once it is assessed; a finished one is processed again with `force`.
export function processAction(detail, { assessed, jobIdentity, profileId }) {
  const state = detail ? detail.state : null;
  const body = { job_identity: jobIdentity, profile_id: profileId };
  if (!jobIdentity || !profileId) {
    return { enabled: false, label: "Process now", reason: "No profile is selected.", body };
  }
  if (!assessed) {
    return { enabled: false, label: "Process now", reason: "Assess this posting first.", body };
  }
  if (detail && detail.enabled === false) {
    return { enabled: false, label: "Process now", reason: "The background pipeline is off (Settings).", body };
  }
  if (state === "running") {
    return { enabled: false, label: "Processing…", reason: "", body };
  }
  if (state === "done" || state === "failed" || state === "cancelled") {
    return { enabled: true, label: "Process again", reason: "", body: { ...body, force: true } };
  }
  return { enabled: true, label: "Process now", reason: "", body };
}

// What POST /api/pipeline/process answered, in a line (codes in words).
export function processResultLine(response) {
  if (!response) {
    return null;
  }
  const result = words(response.result);
  return response.runner === false ? `${result}; this server runs no pipeline (run: gigai scout pipeline run --once)` : result;
}

// --- Settings: lanes, caps, approvals, errors -------------------------------------------------

// The two daily counters: [{id, label, used, limit, state, note}].
//   state  ok | warning | reached. `warning` is the server's own flag for the
//          rank counter (the count has passed `warn_at`); `reached`: at the cap.
export function capRows(overview) {
  const caps = overview && overview.caps;
  if (!caps) {
    return [];
  }
  const row = (id, label, block, warnAt) => {
    const used = (block && block.used) || 0;
    const limit = (block && block.limit) || 0;
    const reached = used >= limit;
    const warning = !reached && Boolean(block && block.warning);
    return {
      id,
      label,
      used,
      limit,
      warnAt: typeof warnAt === "number" ? warnAt : null,
      state: reached ? "reached" : warning ? "warning" : "ok",
      text: `${used} of ${limit} today`,
      note: reached ? "The cap is reached: it starts again tomorrow." : warning ? `Past the warning level of ${warnAt}.` : typeof warnAt === "number" ? `Warning at ${warnAt}.` : "",
    };
  };
  return [
    row("pipeline", "Pipeline model calls", caps.pipeline_calls, null),
    row("rank", "Background rank calls", caps.rank_calls, caps.rank_calls && caps.rank_calls.warn_at),
  ];
}

// [{lane, text, state, note}]: steps running per model lane against its cap, and a backed-off lane's code.
export function laneRows(overview) {
  return ((overview && overview.lanes) || []).map((lane) => ({
    lane: lane.lane,
    text: `${lane.running} of ${lane.cap} running`,
    state: lane.error_code ? "backoff" : lane.running > 0 ? "running" : "idle",
    note: lane.error_code ? `${lane.error_code}${lane.retry_at ? `, again at ${lane.retry_at}` : ""}` : "",
  }));
}

// What the pipeline is doing, in one line of codes: on/off, its runner, what it yields to.
export function statusLine(overview) {
  if (!overview) {
    return "";
  }
  const setting = overview.setting || {};
  const parts = [setting.enabled ? "On" : "Off"];
  if (overview.readable === false) {
    parts.push("the settings file cannot be read");
  } else if (setting.source && setting.source !== "default" && setting.source !== "setting") {
    parts.push(`set by: ${words(setting.source)}`);
  }
  if (overview.runner === null || overview.runner === undefined) {
    parts.push("no runner in this server");
  } else if (!overview.runner.active) {
    parts.push("runner stopped");
  }
  if (overview.yielding_to) {
    parts.push(`waiting for: ${words(overview.yielding_to)}`);
  }
  const jobs = (overview.counts && overview.counts.jobs) || {};
  const counted = Object.keys(jobs)
    .sort()
    .map((state) => `${jobs[state]} ${words(state)}`);
  if (counted.length) {
    parts.push(`jobs: ${counted.join(", ")}`);
  }
  return parts.join(" · ");
}

// The approvals that wait: [{id, jobs, calls, tokens, trigger, createdAt, text}].
export function approvalRows(overview) {
  const items = (overview && overview.approvals && overview.approvals.items) || [];
  return items.map((item) => {
    const jobs = typeof item.waiting_jobs === "number" ? item.waiting_jobs : item.jobs;
    const tokens = tokensText(item.est_tokens);
    return {
      id: item.id,
      jobs,
      calls: item.est_calls,
      tokens,
      trigger: words(item.trigger),
      createdAt: item.created_at || null,
      text: `${jobs} job${jobs === 1 ? "" : "s"} · ~${item.est_calls} model call${item.est_calls === 1 ? "" : "s"}${tokens ? ` · ~${tokens} tokens` : ""}`,
    };
  });
}

// The body of POST /api/pipeline/approvals/{id}.
export function decisionBody(approve) {
  return { approve: Boolean(approve), actor: "operator" };
}

// The last errors, as codes: [{key, step, code, attempt, at, job, profileId}].
export function errorRows(overview) {
  return ((overview && overview.errors) || []).map((item, index) => ({
    key: `${index}:${item.at}:${item.step}`,
    step: STEP_TITLES[item.step] || words(item.step),
    code: item.error_code || "unknown",
    attempt: item.attempt,
    at: item.at,
    job: item.job_identity,
    profileId: item.profile_id,
  }));
}

// --- Settings: the pipeline's settings form ---------------------------------------------------

export const MAX_CAP = 1000;
export const MODEL_STEPS = ["tailor", "reassess"];
const PROJECT_MODEL = "";

function block(value) {
  return value && typeof value === "object" ? value : {};
}

// The form's draft, from what the FILE says (`settings`, never the overridden values).
export function pipelineDraft(response) {
  const settings = block(response && response.settings);
  const pipeline = block(settings.pipeline);
  const rank = block(settings.rank);
  const models = block(pipeline.models);
  const number = (value) => (typeof value === "number" ? String(value) : "");
  return {
    enabled: pipeline.enabled !== false,
    jobsPerTrigger: number(pipeline.auto_jobs_per_trigger),
    callsPerDay: number(pipeline.max_model_calls_per_day),
    labelMinAts: number(pipeline.label_min_ats),
    rankCallsPerDay: number(rank.max_calls_per_day),
    rankWarnAt: number(rank.warn_calls_per_day),
    tailorModel: typeof models.tailor === "string" ? models.tailor : PROJECT_MODEL,
    reassessModel: typeof models.reassess === "string" ? models.reassess : PROJECT_MODEL,
  };
}

// False when the server sends no pipeline block (an older one): the form is not shown.
export function hasPipelineSettings(response) {
  const pipeline = block(response && response.settings).pipeline;
  return Boolean(pipeline) && typeof pipeline === "object";
}

export function modelOptions() {
  return [{ value: PROJECT_MODEL, label: "The model Scout is set up with" }].concat(MODEL_TARGETS.map((value) => ({ value, label: MODEL_TARGET_LABELS[value] })));
}

function whole(text, most) {
  const value = String(text == null ? "" : text).trim();
  if (!/^\d+$/.test(value)) {
    return null;
  }
  const number = Number(value);
  return number <= most ? number : null;
}

const FIELDS = [
  ["jobsPerTrigger", "Jobs per trigger", MAX_CAP],
  ["callsPerDay", "Pipeline model calls a day", MAX_CAP],
  ["rankCallsPerDay", "Rank calls a day", MAX_CAP],
  ["rankWarnAt", "Rank warning level", MAX_CAP],
  ["labelMinAts", "Scout ATS minimum", 100],
];

// "" when the draft can be saved, else the first problem (the server's own rules).
export function pipelineFormError(draft) {
  for (const [key, label, most] of FIELDS) {
    if (whole(draft[key], most) === null) {
      return `${label}: a whole number from 0 to ${most}.`;
    }
  }
  if (whole(draft.rankWarnAt, MAX_CAP) > whole(draft.rankCallsPerDay, MAX_CAP)) {
    return "Rank warning level: not above the rank calls a day.";
  }
  return "";
}

// The PUT body: only what differs from the loaded file. Null when nothing changed.
export function pipelinePatch(draft, response) {
  const loaded = pipelineDraft(response);
  const pipeline = {};
  const rank = {};
  if (draft.enabled !== loaded.enabled) {
    pipeline.enabled = draft.enabled;
  }
  const count = (key, into, name, most = MAX_CAP) => {
    if (String(draft[key]).trim() !== loaded[key] && whole(draft[key], most) !== null) {
      into[name] = whole(draft[key], most);
    }
  };
  count("jobsPerTrigger", pipeline, "auto_jobs_per_trigger");
  count("callsPerDay", pipeline, "max_model_calls_per_day");
  count("labelMinAts", pipeline, "label_min_ats", 100);
  count("rankCallsPerDay", rank, "max_calls_per_day");
  count("rankWarnAt", rank, "warn_calls_per_day");
  const models = {};
  if (draft.tailorModel !== loaded.tailorModel) {
    models.tailor = draft.tailorModel === PROJECT_MODEL ? null : draft.tailorModel;
  }
  if (draft.reassessModel !== loaded.reassessModel) {
    models.reassess = draft.reassessModel === PROJECT_MODEL ? null : draft.reassessModel;
  }
  if (Object.keys(models).length) {
    pipeline.models = models;
  }
  const body = {};
  if (Object.keys(pipeline).length) {
    body.pipeline = pipeline;
  }
  if (Object.keys(rank).length) {
    body.rank = rank;
  }
  return Object.keys(body).length ? body : null;
}

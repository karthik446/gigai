// uat-bug-018: a job's STATE, in the operator's words. Pure functions (no
// React) so the node-backed static test can pin them.
//
// The state is derived by the server (find_jobs/job_state.py), never
// stored, and served as `job_state` {state, since, next_events}:
//   GET /api/runs/{id}/results   payload.rows[].job_state (boardRows keeps
//                                it as row.jobState)
//   GET /api/assessments         items[].job_state
//   GET /api/applications        applications[].job_state, from the events
//                                alone (null for a job that was only saved)
//
// Precedence, the server's: application events > tailored resume > latest
// verdict > not assessed. This module applies the same order to what the
// page holds NOW, so a card is right the moment something changes and
// before any list is read again:
//   1. the job's application state, from the applications list (re-read
//      after every recorded event);
//   2. tailored, when the served state says so or a tailored resume was
//      made on this page since (`tailoredIds`);
//   3. the verdict of the job's latest assessment (jobModel.buildJobs' own
//      latest-of rule, which a re-assessment updates in place).
//
// A state id is never shown: STATE_LABELS has the words, and an id this
// table does not know is humanized.
import { humanizeId } from "./jobModel.js";

export const STATE_LABELS = {
  not_assessed: "Not assessed",
  assessed: "Assessed",
  needs_answers: "Needs your answers",
  matched: "Matched",
  not_a_match: "Not a match",
  tailored: "Resume tailored",
  applied: "Applied",
  interview_scheduled: "Interview scheduled",
  offer_received: "Offer received",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};

// The order of the filter chips: the pipeline, start to end.
export const STATE_ORDER = [
  "not_assessed",
  "needs_answers",
  "matched",
  "assessed",
  "not_a_match",
  "tailored",
  "applied",
  "interview_scheduled",
  "offer_received",
  "rejected",
  "withdrawn",
];

// "Applied and beyond": what the Applications page lists.
export const APPLICATION_STATES = ["applied", "interview_scheduled", "offer_received", "rejected", "withdrawn"];

// The button that records each event (POST /api/applications event_kind).
export const EVENT_ACTION_LABELS = {
  applied: "Mark applied",
  interview_scheduled: "Interview scheduled",
  offer_received: "Offer received",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};

// An application with no news for this long needs a follow-up.
export const STALE_DAYS = 10;

const VERDICT_STATES = {
  not_assessed: "not_assessed",
  assessed: "assessed",
  pending_user_answers: "needs_answers",
  matched_above_threshold: "matched",
  not_a_match: "not_a_match",
};

export function stateLabel(state) {
  return STATE_LABELS[state] || humanizeId(state);
}

export function eventActionLabel(eventKind) {
  return EVENT_ACTION_LABELS[eventKind] || humanizeId(eventKind);
}

export function isApplicationState(state) {
  return APPLICATION_STATES.includes(state);
}

function served(value) {
  if (!value || typeof value !== "object" || typeof value.state !== "string" || !value.state) {
    return null;
  }
  return {
    state: value.state,
    since: typeof value.since === "string" && value.since ? value.since : null,
    nextEvents: Array.isArray(value.next_events) ? value.next_events.filter((kind) => typeof kind === "string") : [],
  };
}

export function applicationIdentity(row) {
  if (!row) {
    return null;
  }
  return row.external_ref || row.opportunity_ref || null;
}

// identity -> the job's application state, for every job that has one.
export function applicationStates(applications) {
  const states = new Map();
  (applications || []).forEach((row) => {
    const identity = applicationIdentity(row);
    const state = served(row && row.job_state);
    if (identity && state && !states.has(identity)) {
      states.set(identity, state);
    }
  });
  return states;
}

function idSet(value) {
  if (value instanceof Set) {
    return value;
  }
  return new Set(value || []);
}

// One job's state: {state, since, nextEvents}. `job` is jobModel's job
// shape; `states` is applicationStates(); `tailoredIds` names the jobs a
// tailored resume was made for on this page since the lists were read.
export function jobStateFor(job, states, tailoredIds) {
  const applied = states instanceof Map ? states.get(job.id) : null;
  if (applied) {
    return applied;
  }
  const candidates = [served(job.quick && job.quick.job_state), served(job.row && job.row.jobState)].filter(Boolean);
  const tailored = candidates.find((candidate) => candidate.state === "tailored");
  if (tailored) {
    return tailored;
  }
  if (idSet(tailoredIds).has(job.id)) {
    return { state: "tailored", since: null, nextEvents: ["applied"] };
  }
  const state = VERDICT_STATES[job.verdict] || "assessed";
  const same = candidates.find((candidate) => candidate.state === state);
  return { state, since: same ? same.since : null, nextEvents: ["applied"] };
}

export function withJobStates(jobs, applications, tailoredIds) {
  const states = applicationStates(applications);
  return (jobs || []).map((job) => ({ ...job, state: jobStateFor(job, states, tailoredIds) }));
}

function stateOf(job) {
  return job && job.state && job.state.state ? job.state.state : "not_assessed";
}

export function stateCounts(jobs) {
  const counts = {};
  (jobs || []).forEach((job) => {
    const state = stateOf(job);
    counts[state] = (counts[state] || 0) + 1;
  });
  return counts;
}

// The filter chips: "All" and every state at least one job is in, each with
// its count, in pipeline order. `selected` stays in the list at 0 so the
// chosen chip never vanishes under the operator.
export function stateOptions(jobs, selected = "all") {
  const counts = stateCounts(jobs);
  const known = STATE_ORDER.concat(Object.keys(counts).filter((state) => !STATE_ORDER.includes(state)).sort());
  const options = [{ value: "all", label: "All", count: (jobs || []).length }];
  known.forEach((state) => {
    const count = counts[state] || 0;
    if (count > 0 || state === selected) {
      options.push({ value: state, label: stateLabel(state), count });
    }
  });
  return options;
}

export function jobMatchesState(job, state) {
  return !state || state === "all" || stateOf(job) === state;
}

// How many jobs wait on the operator's answers (the count the Questions
// badge used to stand for, now per job and per list).
export function needAnswersCount(jobs) {
  return (jobs || []).filter((job) => stateOf(job) === "needs_answers").length;
}

export function needAnswersLabel(count) {
  return `${count} ${count === 1 ? "job needs" : "jobs need"} your answers`;
}

// --- the Applications page ---------------------------------------------------------

function daysSince(iso, now) {
  const at = new Date(iso || "").getTime();
  return Number.isNaN(at) ? null : Math.floor((now - at) / 86400000);
}

// One entry per job that is applied or beyond, newest state first:
//   {identity, state, since, nextEvents, title, company, url, pasted,
//    events (this job's rows, oldest first), daysInState, needsAction}
// `known` (optional) maps an identity to {title, company} for a job the
// applications list cannot name itself (no run posting has its address).
export function applicationJobs(applications, { known, now = Date.now() } = {}) {
  const states = applicationStates(applications);
  const names = known instanceof Map ? known : new Map();
  const byIdentity = new Map();
  (applications || []).forEach((row) => {
    const identity = applicationIdentity(row);
    if (!identity || !states.has(identity)) {
      return;
    }
    if (!byIdentity.has(identity)) {
      byIdentity.set(identity, []);
    }
    byIdentity.get(identity).push(row);
  });
  const jobs = [];
  byIdentity.forEach((rows, identity) => {
    const state = states.get(identity);
    const linked = rows.map((row) => row.linked_posting).find(Boolean) || null;
    const named = names.get(identity) || null;
    const pasted = identity.startsWith("text:");
    const isUrl = /^https?:\/\//.test(identity);
    const title = (linked && linked.title) || (named && named.title) || (pasted ? "Pasted posting" : isUrl ? identity : "Posting");
    const daysInState = daysSince(state.since, now);
    jobs.push({
      identity,
      state: state.state,
      since: state.since,
      nextEvents: state.nextEvents,
      title,
      company: (linked && linked.company) || (named && named.company) || "",
      url: (linked && linked.url) || (isUrl ? identity : null),
      pasted,
      linked: Boolean(linked),
      events: rows.slice().sort((a, b) => (a.occurred_at || "").localeCompare(b.occurred_at || "")),
      daysInState,
      needsAction: (state.state === "applied" || state.state === "interview_scheduled") && daysInState !== null && daysInState >= STALE_DAYS,
    });
  });
  return jobs.sort((a, b) => (b.since || "").localeCompare(a.since || ""));
}

// identity -> {title, company} from the quick-assess store's items.
export function namesFromAssessments(items) {
  const names = new Map();
  (items || []).forEach((item) => {
    const job = item && item.job;
    if (job && job.job_identity && !names.has(job.job_identity) && (job.title || job.company)) {
      names.set(job.job_identity, { title: job.title || "", company: job.company || "" });
    }
  });
  return names;
}

// "In progress": applied, interviewing or holding an offer.
export function inProgressCount(applications) {
  let count = 0;
  applicationStates(applications).forEach((state) => {
    if (state.state === "applied" || state.state === "interview_scheduled" || state.state === "offer_received") {
      count += 1;
    }
  });
  return count;
}

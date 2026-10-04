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
  weak_fit: "Weak fit",
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
  "weak_fit",
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

// Ledger 32: the server adds `assessment_stale` {reason: "posting_changed"}
// to a job_state whose assessment was made on an older posting text (the
// key is absent otherwise). The verdict is never changed or hidden; the
// page only adds a marker next to it. Read from the raw served states.
export const STALE_ASSESSMENT_TEXT = "Posting text changed since this assessment: re-assess";

// 0110-039: the same marker also says a stored assessment was made with an
// older prompt, other candidate settings (work mode, countries, location,
// sponsorship need) or a story bank that has since changed
// (assessment_basis.py). A quiet note, never an error: the verdict still
// reads, and one click re-assesses. Nothing re-assesses on its own.
export const BASIS_STALE_REASONS = ["older_prompt", "settings_changed", "story_bank_changed", "resume_changed"];
export const OLDER_SETTINGS_TEXT = "Assessed with older settings: re-assess";
export const STORY_BANK_CHANGED_TEXT = "Your story bank changed since this assessment: re-assess";
export const OLDER_SETTINGS_CHIP = "Older settings";

// 0110-041: story_bank_changed is targeted. The stored item says which bank
// entries made it stale (`basis_stale_bank`: {match: "exact" | "near" |
// "cited", bank_question_id, bank_question?, question_id?, question?}), so
// the note names the question that is now answered instead of "your story
// bank changed". Without the list (a run row alone) the older line stays.
export const BANK_CITED_TEXT = "A story bank answer this assessment used has changed: re-assess";
const BANK_QUESTION_MAX = 90;

function shortQuestion(text) {
  const flat = String(text || "").split(/\s+/).filter(Boolean).join(" ");
  return flat.length > BANK_QUESTION_MAX ? `${flat.slice(0, BANK_QUESTION_MAX - 3).trimEnd()}...` : flat;
}

export function storyBankNote(matches) {
  const list = Array.isArray(matches) ? matches.filter((item) => item && typeof item === "object") : [];
  const answered = list.filter((item) => item.match === "exact" || item.match === "near");
  if (answered.length > 0) {
    const first = answered[0];
    const question = shortQuestion(first.bank_question || first.question || first.bank_question_id);
    const others = new Set(answered.map((item) => item.bank_question_id)).size - 1;
    const more = others > 0 ? ` (and ${others} more)` : "";
    return question ? `Answered in your story bank: ${question}${more}: re-assess` : STORY_BANK_CHANGED_TEXT;
  }
  return list.some((item) => item.match === "cited") ? BANK_CITED_TEXT : STORY_BANK_CHANGED_TEXT;
}

// 0.1.10.9 master P7: resume_changed is targeted too. The stored item says
// what changed for it (`basis_stale_resume`: {change: "line_changed",
// requirement} | {change: "new_line", question_id, question?}): a line it
// quoted is gone, or a new line names one of its open questions.
export const RESUME_CHANGED_TEXT = "Your resume changed since this assessment: re-assess";
export const RESUME_LINE_CHANGED_TEXT = "A resume line this assessment used has changed: re-assess";
export const RESUME_CHANGED_CHIP = "Resume changed";

export function resumeChangedNote(changes) {
  const list = Array.isArray(changes) ? changes.filter((item) => item && typeof item === "object") : [];
  const added = list.filter((item) => item.change === "new_line");
  if (added.length > 0) {
    const question = shortQuestion(added[0].question || added[0].question_id);
    const others = new Set(added.map((item) => item.question_id)).size - 1;
    const more = others > 0 ? ` (and ${others} more)` : "";
    return question ? `A new line of your resume may answer: ${question}${more}: re-assess` : RESUME_CHANGED_TEXT;
  }
  return list.some((item) => item.change === "line_changed") ? RESUME_LINE_CHANGED_TEXT : RESUME_CHANGED_TEXT;
}

export function isBasisStaleReason(reason) {
  return BASIS_STALE_REASONS.includes(reason);
}

// A stored item that was just made on this page (the POST /api/assess or
// POST /api/answers response replaces job.quick) carries its basis and is
// not flagged: the run row's marker, read before it, no longer applies.
function quickIsCurrent(quick) {
  if (!quick || typeof quick !== "object" || quick.basis_stale === true) {
    return false;
  }
  if (quick.job_state && typeof quick.job_state === "object" && quick.job_state.assessment_stale) {
    return false;
  }
  return quick.basis_stale === false || Boolean(quick.prompt_version);
}

export function assessmentStaleFor(job) {
  if (!job) {
    return null;
  }
  const raws = [job.quick && job.quick.job_state, job.row && job.row.jobState];
  const found = raws.find((raw) => raw && typeof raw === "object" && raw.assessment_stale && typeof raw.assessment_stale === "object");
  if (!found) {
    return null;
  }
  const reason = found.assessment_stale.reason || "posting_changed";
  if (isBasisStaleReason(reason) && quickIsCurrent(job.quick)) {
    return null;
  }
  const bank = reason === "story_bank_changed" && job.quick && Array.isArray(job.quick.basis_stale_bank) ? job.quick.basis_stale_bank : [];
  if (reason === "resume_changed") {
    return { reason, resume: job.quick && Array.isArray(job.quick.basis_stale_resume) ? job.quick.basis_stale_resume : [] };
  }
  return bank.length > 0 ? { reason, bank } : { reason };
}

export function staleAssessmentNote(job) {
  const stale = assessmentStaleFor(job);
  if (!stale) {
    return null;
  }
  if (stale.reason === "story_bank_changed") {
    return storyBankNote(stale.bank);
  }
  if (stale.reason === "resume_changed") {
    return resumeChangedNote(stale.resume);
  }
  return isBasisStaleReason(stale.reason) ? OLDER_SETTINGS_TEXT : STALE_ASSESSMENT_TEXT;
}

// The card's short marker: only for an assessment made with older settings
// (a changed posting text keeps its own line on the job page).
export function olderSettingsChip(job) {
  const stale = assessmentStaleFor(job);
  const label = stale && stale.reason === "resume_changed" ? RESUME_CHANGED_CHIP : OLDER_SETTINGS_CHIP;
  return stale && isBasisStaleReason(stale.reason) ? { label, title: staleAssessmentNote(job) } : null;
}

export function olderSettingsCount(jobs) {
  return (jobs || []).filter((job) => olderSettingsChip(job)).length;
}

// "3 assessed with older settings: open one to re-assess it."
export function olderSettingsLine(count) {
  return count > 0 ? `${count} assessed with older settings: open ${count === 1 ? "it" : "one"} to re-assess it.` : "";
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

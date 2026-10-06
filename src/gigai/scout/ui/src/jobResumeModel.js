// 0.1.11 N6 (SPEC section 6): the job page's suggested resume, as pure
// functions (no React, no fetch) so the rules run under node.
//
// 0.1.11 has no tailor call. The resume for a job is PICKED from the master
// when the job is assessed, and only for a job the gate lets through. This
// module reads four stored things and says what the page shows:
//
//   the assessment      job.assessment (the matrix: each v9 row carries `id`,
//                       `class_basis`, `alternatives`, `sources`) and the
//                       stored item's `resume_gate` {decision, reasons}
//   the job resume      GET /api/tailored-resumes (the store kept its path):
//                       `selection` (Picked / Left out), `producer`, `edited`
//   the suggestions    GET /api/jobs/suggestions: {gate: {decision, ready,
//                       reasons}, counts, suggestions}. The stale list, the
//                       conflicts and `proposed` are served only by the answer
//                       of POST /api/job-resumes/pick (the job's stored view
//                       after a step): `pickView`
//   the master          GET /api/master, only for a line's text
//
// Nothing here recomputes a decision the server stored (SPEC A5). Where the
// server does not serve a field yet, ONE adapter here derives it from what
// the page already holds, and says so:
//
//   gateOf          the record's gate, else the assessment's `resume_gate`,
//                   else the verdict (an assessment made before 0.1.11)
//   coverageRows    the record's rows, else SPEC 2.3 worked out from the
//                   rows' `sources` and the ids the stored resume prints
//   staleCodes      the served `stale` list, else the assessment's own stale
//                   reason and "a later assessment exists"
//   resumeOrigin    the served `origin`, else `producer.callable` + `edited`
//
// Every sentence here is the page's own; a requirement's words are the
// posting's and a line's text is the user's, both passed through untouched.
import { isThinMatch, THIN_LABEL, THIN_POSTING } from "./display.js";
import { dateLabel, effectiveVerdict, minorGaps, openQuestions } from "./jobModel.js";
import { assessmentStaleFor } from "./jobStateModel.js";
import { staleWords } from "./postingsModel.js";

const text = (value) => (typeof value === "string" ? value : "");
const list = (value) => (Array.isArray(value) ? value : []);
const object = (value) => (value && typeof value === "object" && !Array.isArray(value) ? value : null);
const plural = (count, word, many) => `${count} ${count === 1 ? word : many || `${word}s`}`;

export const GATE_SUGGEST = "suggest";
export const GATE_HOLD_QUESTION = "hold_question";
export const GATE_HOLD_UNMET = "hold_unmet";
export const GATE_NOT_A_MATCH = "not_a_match";
const GATE_DECISIONS = new Set([GATE_SUGGEST, GATE_HOLD_QUESTION, GATE_HOLD_UNMET, GATE_NOT_A_MATCH]);
const VERDICT_GATES = { matched_above_threshold: GATE_SUGGEST, pending_user_answers: GATE_HOLD_QUESTION, not_a_match: GATE_NOT_A_MATCH };

// The cost of each refresh, said in its button (SPEC section 6, item 4).
export const REPICK_LABEL = "Re-pick · no model call";
export const REASSESS_LABEL = "Re-assess · 1 model call";
export const DRAFT_LABEL = "Make a draft anyway";
export const APPLY_LABEL = "Generate PDF";

// --- the suggestion route's answer -------------------------------------------------------

// The page's `record`: what the two routes serve, put in the shape of the stored suggestion record (SPEC 2.1) the
// rules below read. `suggestions` is GET /api/jobs/suggestions' answer, `view` the answer of the last POST
// /api/job-resumes/pick (the stored view: gate, stale, picked, conflicts, proposed, selection_error). Either may be
// null. What neither serves stays absent (`requirements`, the model's pick, `line_marks`): the rules that need it
// derive it from the assessment and the stored resume, which is all the page can know.
export function suggestionsAnswer(suggestions, view = null) {
  const body = object(suggestions);
  // GET /api/jobs/suggestions serves no stale list, pages, conflicts or proposed selection today (they are in the pick
  // view, which only a step returns). If the server ever puts the pick view's fields in that answer, they are read
  // from it: the `view` of a step always wins.
  const inBody = body && (Array.isArray(body.stale) || object(body.picked) || object(body.proposed) || Array.isArray(body.conflicts)) ? body : null;
  const pick = object(view) || inBody;
  if (!body && !pick) {
    return { record: null, stale: null, origin: null };
  }
  const gate = (pick && object(pick.gate)) || (body && object(body.gate)) || null;
  const picked = pick && object(pick.picked);
  const record = {
    schema_version: "scout-job-suggestions:1",
    job_identity: text((body && body.job_identity) || (pick && pick.job_identity)),
    profile_id: text((body && body.profile_id) || (pick && pick.profile_id)),
    gate,
    requirements: [],
    selection: picked ? { ...picked, conflicts: list(pick.conflicts), added_by_code: list(pick.added_by_code), problems: list(pick.problems) } : null,
    proposed: pick && object(pick.proposed) ? pick.proposed : null,
    selection_error: pick ? pick.selection_error || null : null,
    // What a resume for this job is made from now ("master" | "profile_resume"), and whether a master is stored at all.
    resume_basis: pick ? text(pick.basis) || null : null,
    master_stored: pick && typeof pick.master_stored === "boolean" ? pick.master_stored : null,
    suggestions: body ? list(body.suggestions) : [],
  };
  const stale = pick && Array.isArray(pick.stale) ? pick.stale.filter((code) => typeof code === "string") : null;
  const made = pick && object(pick.resume) ? text(pick.resume.made_by) : "";
  // The pick view says whether a re-pick may replace the resume (`replaceable`): a pick made by the assessment that is no
  // longer replaceable has been changed by the user. Anything else (the 0.1.10 tailoring's) is read from the resume itself.
  const mine = pick && object(pick.resume) ? Boolean(pick.resume.edited) || (made === "scout.pick" && pick.resume.replaceable === false) : false;
  const origin = pick && object(pick.resume) ? (mine ? "edited" : made === "scout.pick" ? "pick" : null) : null;
  return { record, stale, origin };
}

// --- requirement rows ----------------------------------------------------------------------

// A must-have row: `hard` or `askable` (a row with no class is an old one, read as `hard`: resume_gate.is_mandatory).
export function isMandatory(row) {
  const kind = row && (row.class === undefined ? row.requirement_class : row.class);
  return !kind || kind === "hard" || kind === "askable";
}

// row id -> the assessment's row (its requirement words). A row made before 0.1.11 has no id.
export function rowsById(assessment) {
  const rows = new Map();
  list(assessment && assessment.matrix).forEach((row) => {
    if (row && text(row.id)) {
      rows.set(row.id, row);
    }
  });
  return rows;
}

// A requirement's words for a row id; the id itself when the assessment shown does not hold the row.
export function requirementText(rows, id) {
  const row = rows instanceof Map ? rows.get(id) : null;
  return (row && text(row.requirement)) || text(id);
}

// --- the gate ------------------------------------------------------------------------------

// {decision, ready, reasons: [{code, requirement}], from}. `ready` is null when nothing stored says it (no record).
// `from`: "record" | "assessment" | "verdict" (the adapter line: an assessment that stores no gate is read by its verdict).
export function gateOf({ record = null, quick = null, assessment = null } = {}) {
  const stored = object(record && record.gate);
  if (stored && GATE_DECISIONS.has(stored.decision)) {
    return { decision: stored.decision, ready: stored.ready === true, reasons: list(stored.reasons).filter(object), from: "record" };
  }
  const own = object(quick && quick.resume_gate);
  if (own && GATE_DECISIONS.has(own.decision)) {
    return { decision: own.decision, ready: null, reasons: list(own.reasons).filter(object), from: "assessment" };
  }
  const decision = VERDICT_GATES[effectiveVerdict(assessment)];
  return decision ? { decision, ready: null, reasons: [], from: "verdict" } : null;
}

export function gateHolds(gate) {
  return Boolean(gate) && gate.decision !== GATE_SUGGEST;
}

// The rows a holding gate names, as the posting's words (no duplicates, in the gate's order).
function heldRequirements(gate, assessment) {
  const rows = rowsById(assessment);
  const named = list(gate && gate.reasons)
    .map((reason) => (text(reason.requirement) ? requirementText(rows, reason.requirement) : ""))
    .filter(Boolean);
  return [...new Set(named)];
}

// The one sentence of a held job: "2 must-have requirements wait for your answer", "Has a gap: Kubernetes in
// production", "Not a match: ...". null when the gate does not hold.
export function holdSentence(gate, assessment) {
  if (!gateHolds(gate)) {
    return null;
  }
  const named = heldRequirements(gate, assessment);
  if (gate.decision === GATE_HOLD_QUESTION) {
    const matrix = list(assessment && assessment.matrix);
    const asked = new Set(openQuestions(assessment).map((question) => text(question.requirement)));
    const waiting = named.length || matrix.filter((row) => isMandatory(row) && asked.has(row.requirement)).length || openQuestions(assessment).length;
    return waiting > 0 ? `${plural(waiting, "must-have requirement waits", "must-have requirements wait")} for your answer` : "This job waits for your answers";
  }
  if (gate.decision === GATE_HOLD_UNMET) {
    const gaps = named.length ? named : list(assessment && assessment.matrix).filter((row) => row.class === "askable" && row.status === "unmet").map((row) => row.requirement);
    return gaps.length ? `Has a gap: ${gaps.join("; ")}` : "Has a gap: a must-have requirement is not met";
  }
  const why = text(assessment && assessment.not_a_match_reason) || named.join("; ");
  return why ? `Not a match: ${why}` : "Not a match";
}

// --- the header's chip ----------------------------------------------------------------------

const FIT_LABELS = {
  not_assessed: "Not assessed",
  assessed: "Assessed",
  needs_answers: "Needs your answers",
  weak_fit: "Weak fit",
  matched: "Matched",
  has_gap: "Has a gap",
  not_a_match: "Not a match",
};
const FIT_TONES = { matched: "ok", has_gap: "warn", needs_answers: "warn", not_a_match: "danger" };
const MATCHED_VERDICT = "matched_above_threshold";

// The header's ONE chip: Matched · Matched · 2 minor gaps · Needs your answers · Has a gap · Not a match · Weak fit.
// `fit` is jobStateModel.fitStateFor(job): the gate's reading of the verdict, never the application state.
export function headerChip(fit, { assessment = null, gate = null } = {}) {
  const label = FIT_LABELS[fit] || FIT_LABELS.assessed;
  if (fit === THIN_POSTING || (fit === "matched" && isThinMatch(MATCHED_VERDICT, assessment))) {
    // 0.1.11.2: a match read from fewer than 4 requirement rows; the chip only, the gate and the resume are as before.
    return { state: THIN_POSTING, label: THIN_LABEL, tone: "warn", title: null };
  }
  if (fit === "matched") {
    const gaps = minorGaps(assessment).length;
    return { state: fit, label: gaps > 0 ? `${label} · ${plural(gaps, "minor gap")}` : label, tone: "ok", title: null };
  }
  if (fit === "needs_answers") {
    const open = openQuestions(assessment).length;
    return { state: fit, label: open > 0 ? `${label} (${open})` : label, tone: "warn", title: null };
  }
  return { state: fit, label, tone: FIT_TONES[fit] || "plain", title: fit === "has_gap" ? holdSentence(gate, assessment) : null };
}

// --- the stale list (SPEC 2.4) ---------------------------------------------------------------

const ASSESSMENT_STALE = "assessment_stale";
export const STALE_PICKED_LINE = "picked_line_changed";
export const STALE_MASTER_NEWER = "master_newer";
export const STALE_RULES = "selection_rules_changed";
export const STALE_ASSESSMENT_NEWER = "assessment_newer";

// The stale codes of one job: the server's list when it sends one. Without it (the adapter line) the page says
// only what it can read itself: the assessment's own stale reason, and a stored resume made before the assessment
// shown. Both stamps are the server's fixed-width UTC stamps, so they compare as strings.
export function staleCodes({ served = null, job = null, stored = null, assessedAt = "" } = {}) {
  if (Array.isArray(served)) {
    return served;
  }
  const codes = [];
  const stale = assessmentStaleFor(job);
  if (stale) {
    codes.push(`${ASSESSMENT_STALE}:${stale.reason}`);
  }
  const made = text(stored && stored.updated_at);
  if (made && assessedAt && made < assessedAt) {
    codes.push(STALE_ASSESSMENT_NEWER);
  }
  return codes;
}

// One row per stale code: {code, label, note, action}. `note` is true for the one that is not a warning
// (`master_newer`: the stored resume is still exactly what was checked). `action`: "reassess" | "repick" | "compare".
// A re-pick is offered only while the assessment is not stale: a new selection never sits beside scores made on
// other evidence.
export function staleItems(codes) {
  const all = list(codes).filter((code) => typeof code === "string" && code);
  const assessmentStale = all.some((code) => code.startsWith(`${ASSESSMENT_STALE}:`) || code === ASSESSMENT_STALE);
  const refresh = assessmentStale ? "reassess" : "repick";
  return all.map((code) => {
    if (code === ASSESSMENT_STALE || code.startsWith(`${ASSESSMENT_STALE}:`)) {
      const reason = code.slice(ASSESSMENT_STALE.length + 1);
      return { code, label: reason ? `old assessment: ${staleWords(reason)}` : "old assessment", note: false, action: "reassess" };
    }
    if (code === STALE_PICKED_LINE) {
      return { code, label: "a line this resume prints was changed or retired in your master", note: false, action: refresh };
    }
    if (code === STALE_MASTER_NEWER) {
      return { code, label: "your master changed after this resume was picked; no line it prints changed, so it is still exactly what was checked", note: true, action: refresh };
    }
    if (code === STALE_RULES) {
      return { code, label: "Scout's rules for picking changed after this resume was picked", note: false, action: refresh };
    }
    if (code === STALE_ASSESSMENT_NEWER) {
      return { code, label: "this resume was made before the latest assessment", note: false, action: "compare" };
    }
    return { code, label: code.replace(/[_:]/g, " "), note: false, action: refresh };
  });
}

// The refresh buttons under the stale labels, each once, each with its cost: [{use, label}].
export function staleActions(items) {
  const wanted = new Set(list(items).map((item) => item.action));
  const out = [];
  if (wanted.has("repick")) {
    out.push({ use: "repick", label: REPICK_LABEL });
  }
  if (wanted.has("reassess")) {
    out.push({ use: "reassess", label: REASSESS_LABEL });
  }
  return out;
}

// True when a stale code is a warning (anything but the `master_newer` note): Apply then says so first.
export function isStale(items) {
  return list(items).some((item) => !item.note);
}

// --- what the stored resume prints -------------------------------------------------------------

function resumeLines(result) {
  const out = [];
  list(result && result.sections).forEach((section) => {
    list(section.lines).forEach((line) => out.push({ line, section: section.heading, entry: null }));
    list(section.entries).forEach((entry) => {
      const heading = list(entry.heading)[0];
      list(entry.bullets).forEach((line) => out.push({ line, section: section.heading, entry: heading ? text(heading.text) : null }));
    });
  });
  return out;
}

// The master line ids the job resume prints, in print order: a copied line's own id; a line changed in chat counts
// for every master id it cites (SPEC 2.3).
export function printedIds(stored) {
  const ids = [];
  resumeLines(stored && stored.result).forEach(({ line }) => {
    const base = line && line.kind === "custom" && object(line.edited_from) ? line.edited_from : line;
    list(base && base.refs).forEach((ref) => {
      if (ref && ref.kind === "resume" && text(ref.item_id) && !ids.includes(ref.item_id)) {
        ids.push(ref.item_id);
      }
    });
  });
  return ids;
}

// How many content lines the resume prints (headings and the header are not lines).
export function printedLineCount(stored) {
  return resumeLines(stored && stored.result).length;
}

// --- coverage (SPEC 2.3) -------------------------------------------------------------------------

const isAnswerSource = (source) => text(source).startsWith("A ");
const COVERAGE_WORDS = { kept: "in the resume", lost: "not in the resume", answer_only: "from your answer only" };

// row id -> {coverage, inResume, sources, putBack}: where a `met` row's support is printed. The record's rows when
// there is a record; else the same rule worked out here from the rows' `sources` and what the stored resume prints
// (only when there is a stored resume: nothing is "not in the resume" of a job that has none).
export function coverageRows({ assessment = null, record = null, stored = null } = {}) {
  const out = new Map();
  const recorded = list(record && record.requirements).filter(object);
  if (recorded.length > 0) {
    recorded.forEach((row) => {
      const sources = list(row.sources).filter((source) => typeof source === "string");
      out.set(row.id, { coverage: text(row.coverage) || null, inResume: list(row.in_resume), sources, putBack: sources.filter((source) => !isAnswerSource(source)) });
    });
    return out;
  }
  if (!stored) {
    return out;
  }
  const printed = new Set(printedIds(stored));
  list(assessment && assessment.matrix).forEach((row) => {
    if (!row || !text(row.id) || row.status !== "met" || !Array.isArray(row.sources)) {
      return;
    }
    const lines = row.sources.filter((source) => typeof source === "string" && !isAnswerSource(source));
    const kept = lines.filter((source) => printed.has(source));
    const coverage = kept.length ? "kept" : lines.length ? "lost" : row.sources.length ? "answer_only" : "none";
    out.set(row.id, { coverage, inResume: kept, sources: row.sources, putBack: lines });
  });
  return out;
}

// What the Requirements table says beside a `met` row: {coverage, label, putBack}; null for a row with nothing to say.
export function coverageNote(row, coverage) {
  const found = row && row.status === "met" && coverage instanceof Map ? coverage.get(row.id) : null;
  if (!found || !COVERAGE_WORDS[found.coverage]) {
    return null;
  }
  return { coverage: found.coverage, label: COVERAGE_WORDS[found.coverage], putBack: found.coverage === "lost" ? found.putBack : [] };
}

// "any one of: Cassandra, MongoDB" for an alternatives row; "" otherwise.
export function alternativesLine(row) {
  const names = list(row && row.alternatives).filter((name) => typeof name === "string" && name);
  return names.length > 0 ? `any one of: ${names.join(", ")}` : "";
}

// --- who made the stored resume (SPEC 4.3) -------------------------------------------------------

export const ORIGIN_PICK = "pick";
export const ORIGIN_EDITED = "edited";
export const ORIGIN_OLD_TAILOR = "old_tailor";
export const ORIGIN_OLD_TAILOR_YOURS = "old_tailor_yours";
const ORIGINS = new Set([ORIGIN_PICK, ORIGIN_EDITED, ORIGIN_OLD_TAILOR, ORIGIN_OLD_TAILOR_YOURS]);
const PICK_CALLABLE = "scout.pick";

// True when the user changed what this resume prints: a line choice or a changed line, or an Add or a Remove
// (SPEC 2.4: "edited, attached, a line choice, an Add or Remove").
function hasUserLine(stored) {
  const selection = object(stored && stored.selection) || {};
  const moved = list(selection.picked).some((line) => line && line.code === "added_by_you") || list(selection.left_out).some((line) => line && line.code === "removed_by_you");
  return moved || resumeLines(stored && stored.result).some(({ line }) => line && (line.origin === "user" || line.kind === "custom"));
}

// The origin of a stored job resume: what the server says (`served`, or `selection.resume.origin` of the record),
// else read from the resume itself: an `edited` mark, a user's line, an Add or a Remove makes it the user's;
// `scout.pick` made it a pick; anything else was made by the tailoring of 0.1.10.
export function resumeOrigin(stored, { served = null, record = null } = {}) {
  if (!stored) {
    return null;
  }
  if (ORIGINS.has(served)) {
    return served;
  }
  const picked = text(stored.producer && stored.producer.callable) === PICK_CALLABLE;
  if (object(stored.edited)) {
    return ORIGIN_EDITED;
  }
  if (picked) {
    return hasUserLine(stored) ? ORIGIN_EDITED : ORIGIN_PICK;
  }
  const recorded = text(record && record.selection && record.selection.resume && record.selection.resume.origin);
  if (recorded === ORIGIN_PICK && !hasUserLine(stored)) {
    return ORIGIN_PICK;
  }
  return hasUserLine(stored) ? ORIGIN_OLD_TAILOR_YOURS : ORIGIN_OLD_TAILOR;
}

// True when a refresh never replaces the stored resume (it is the user's, or the old tailor's): a new selection
// waits as `proposed`.
export function isUsersResume(origin) {
  return origin === ORIGIN_EDITED || origin === ORIGIN_OLD_TAILOR || origin === ORIGIN_OLD_TAILOR_YOURS;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// "4 Oct" from a UTC stamp; "" when it is not one.
export function shortDay(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(text(iso));
  return match ? `${Number(match[3])} ${MONTHS[Number(match[2]) - 1] || match[2]}` : "";
}

const FALLBACK_WORDS = {
  no_pick: "the assessment gave no pick",
  pick_too_small: "too few lines",
  master_revision_unreadable: "the master revision it read could not be loaded",
  pick_failed: "it could not be fitted",
};

// The ONE provenance line over the resume: {origin, pickedBy, draft, text}.
//   "Picked by the assessment from your master (revision 5) · 27 lines · 2 pages"
//   "Picked by Scout's own rules: the assessment's pick could not be used (too few lines)"
//   "A draft, picked by Scout's own rules because you asked for one · 27 lines · 2 pages"
//   "Your edited resume (agent, 4 Oct)"
//   "Made by the tailoring of 0.1.10"
export function provenanceLine({ stored = null, record = null, origin = null } = {}) {
  if (!stored) {
    return null;
  }
  const selection = object(record && record.selection) || {};
  const own = object(stored.selection) || {};
  const pickedBy = text(selection.picked_by) || text(own.picked_by) || null;
  const fallback = text(selection.fallback) || text(own.fallback) || null;
  const draft = selection.draft === true || fallback === "draft_requested";
  const pages = Number.isInteger(selection.pages) ? selection.pages : null;
  const size = [plural(printedLineCount(stored), "line"), pages ? plural(pages, "page") : ""].filter(Boolean).join(" · ");
  const base = { origin, pickedBy, draft };
  if (origin === ORIGIN_EDITED) {
    const edited = object(stored.edited);
    const who = edited ? (edited.written_by === "agent" ? "agent" : "you") : "";
    const when = edited ? shortDay(edited.edited_at) : "";
    const by = [who, when].filter(Boolean).join(", ");
    return { ...base, text: `Your edited resume${by ? ` (${by})` : ""}` };
  }
  if (origin === ORIGIN_OLD_TAILOR || origin === ORIGIN_OLD_TAILOR_YOURS) {
    return { ...base, text: origin === ORIGIN_OLD_TAILOR_YOURS ? "Made by the tailoring of 0.1.10, with your own changes" : "Made by the tailoring of 0.1.10" };
  }
  if (draft) {
    return { ...base, text: `A draft, picked by Scout's own rules because you asked for one · ${size}` };
  }
  if (pickedBy === "code") {
    const why = FALLBACK_WORDS[fallback] || (fallback ? fallback.replace(/_/g, " ") : "");
    return { ...base, text: `Picked by Scout's own rules: the assessment's pick could not be used${why ? ` (${why})` : ""} · ${size}` };
  }
  const revision = stored.sources && stored.sources.master ? stored.sources.master.revision : null;
  return { ...base, text: `Picked by the assessment from your master${revision ? ` (revision ${revision})` : ""} · ${size}` };
}

// --- "needs attention": conflicts and lost evidence (SPEC 2.3, 3.3) ----------------------------------

const LOST_EVIDENCE = "lost_mandatory_evidence";
const SELECTION_CONFLICT = "selection_conflict";

// The banner over the resume: [{code, requirement, text, lines}], the conflicts first (SPEC 3.3: "the first thing
// the job page shows above the resume"). `lines` are master line ids: what was cut, or what to put back.
export function attentionItems({ record = null, assessment = null, stored = null } = {}) {
  const rows = rowsById(assessment);
  const coverage = coverageRows({ record, assessment, stored });
  const items = [];
  const selection = object(record && record.selection) || {};
  list(selection.conflicts).filter(object).forEach((conflict) => {
    const requirement = text(conflict.requirement) || null;
    const lines = list(conflict.lines).filter((id) => typeof id === "string");
    const about = requirement ? requirementText(rows, requirement) : "";
    const max = Number.isInteger(selection.max_pages) ? selection.max_pages : 2;
    let line;
    if (conflict.code === "mandatory_evidence_does_not_fit") {
      line = `the evidence for ${about || "a must-have requirement"} does not fit ${max} pages${lines.length ? `: ${plural(lines.length, "line was", "lines were")} cut` : ""}`;
    } else if (conflict.code === "pinned_line_does_not_fit") {
      line = `${plural(lines.length || 1, "pinned line does", "pinned lines do")} not fit ${max} pages`;
    } else if (conflict.code === "skills_do_not_fit") {
      line = `your Skills section does not fit ${max} pages whole: some groups were cut`;
    } else if (conflict.code === "over_page_limit") {
      line = `the resume is over ${max} pages and nothing more can be cut`;
    } else {
      line = text(conflict.code).replace(/_/g, " ");
    }
    items.push({ code: text(conflict.code), requirement, text: line, lines });
  });
  const gate = object(record && record.gate) || {};
  list(gate.reasons).filter(object).forEach((reason) => {
    if (reason.code === LOST_EVIDENCE) {
      const requirement = text(reason.requirement) || null;
      const found = requirement ? coverage.get(requirement) : null;
      items.push({
        code: LOST_EVIDENCE,
        requirement,
        text: `the resume no longer shows the evidence for ${requirement ? requirementText(rows, requirement) : "a must-have requirement"}`,
        lines: found ? found.putBack : [],
      });
    } else if (reason.code === SELECTION_CONFLICT && list(selection.conflicts).length === 0) {
      items.push({ code: SELECTION_CONFLICT, requirement: text(reason.requirement) || null, text: "the selection has a conflict", lines: [] });
    }
  });
  if (items.length === 0 && gate.decision === GATE_SUGGEST && gate.ready === false && object(record && record.selection)) {
    items.push({ code: "not_ready", requirement: null, text: "the selection did not pass its check", lines: [] });
  }
  return items;
}

// --- Picked: which requirements a line supports, and what code added ----------------------------------

const ADDED_WORDS = {
  recent_role_present: "added by Scout: a recent role always shows at least one line",
  pinned_line: "pinned by this profile",
  room_left: "room left on the page",
};

// line id -> {supports: [row id], added: {code, requirement} | null}, from the record.
export function pickedNotes(record, assessment = null) {
  const notes = new Map();
  const at = (id) => {
    if (!notes.has(id)) {
      notes.set(id, { supports: [], added: null });
    }
    return notes.get(id);
  };
  // The record's rows; without them (the routes serve none) the assessment's own rows, which carry the same ids and sources.
  const recorded = list(record && record.requirements).filter(object);
  const rowsOf = recorded.length > 0 ? recorded : list(assessment && assessment.matrix).filter((row) => object(row) && text(row.id));
  rowsOf.forEach((row) => {
    if (row.status !== "met") {
      return;
    }
    list(row.sources).forEach((source) => {
      if (typeof source === "string" && !isAnswerSource(source) && !at(source).supports.includes(row.id)) {
        at(source).supports.push(row.id);
      }
    });
  });
  list(record && record.selection && record.selection.added_by_code).filter(object).forEach((added) => {
    if (text(added.id)) {
      at(added.id).added = { code: text(added.code), requirement: text(added.requirement) || null };
    }
  });
  return notes;
}

// A picked line's reason: "supports req-3fa91c, req-77b0aa", "added by Scout: the only evidence for <requirement>",
// "room left on the page"; the stored resume's own sentence (`fallback`) for a line the record says nothing about.
// `keep` puts that sentence first whatever the record says: a line the USER added stays "you added it to this resume".
export function pickedReason(id, notes, rows, fallback = "", { keep = false } = {}) {
  const note = notes instanceof Map ? notes.get(id) : null;
  if (!note) {
    return text(fallback);
  }
  const parts = keep && text(fallback) ? [text(fallback)] : [];
  if (note.added) {
    parts.push(
      note.added.code === "added_for_coverage"
        ? `added by Scout: the only evidence for ${note.added.requirement ? requirementText(rows, note.added.requirement) : "a must-have requirement"}`
        : ADDED_WORDS[note.added.code] || `added by Scout: ${note.added.code.replace(/_/g, " ")}`,
    );
  }
  if (note.supports.length > 0) {
    parts.push(`supports ${note.supports.join(", ")}`);
  }
  return parts.join(" · ") || text(fallback);
}

// --- Changed in chat (SPEC 5.3) -----------------------------------------------------------------------

function clean(value) {
  return text(value).replace(/^(?:[#>*\-•–—]+\s*)+/, "").trim();
}

// Each line of the stored resume that is not a copy of a master line, beside the line it replaced:
//   {id, text, before, sources: [{label, text}], by, restore}
// `by`: "your agent" / "you" for a line changed in chat (origin user), "the old tailor" for a 0.1.10 rewrite.
// `restore` is the `use` of PUT /api/tailored-resumes/lines that puts the master line back, or null.
export function changedLines(stored) {
  const edited = object(stored && stored.edited);
  const writer = edited && edited.written_by === "agent" ? "your agent" : "you";
  const out = [];
  resumeLines(stored && stored.result).forEach(({ line, entry }) => {
    if (!line || (line.kind !== "rewritten" && line.kind !== "custom")) {
      return;
    }
    const alternative = object(line.alternative);
    const base = line.kind === "custom" ? object(line.edited_from) : alternative && alternative.kind === "copy" ? alternative : null;
    const refs = list(line.refs).filter(object);
    const before = base ? clean(base.text) : refs.filter((ref) => ref.kind === "resume").map((ref) => clean(ref.text)).join(" ");
    const mine = line.origin === "user" || line.kind === "custom";
    const canRestore = Boolean(text(line.id)) && (line.kind === "custom" ? Boolean(base) : Boolean(alternative && alternative.kind === "copy"));
    out.push({
      id: text(line.id) || null,
      entry: entry ? clean(entry) : null,
      text: text(line.text),
      before,
      sources: refs.map((ref) => ({
        label: ref.kind === "resume" ? `master line ${text(ref.item_id) || ref.line}` : ref.kind === "answer" ? `your answer ${text(ref.question_id)}` : text(ref.kind),
        text: text(ref.text),
      })),
      by: mine ? writer : "the old tailor",
      mine,
      restore: canRestore ? "original" : null,
    });
  });
  return out;
}

// --- Skills ------------------------------------------------------------------------------------------------

// The Skills groups of the selection: {shown: [name], cut: [{name, reason}], other: [{name, reason}]}.
// `cut` are the groups left out for length (their code says so); `other` any other group not shown.
export function skillsView(stored) {
  const skills = object(stored && stored.selection && stored.selection.skills) || {};
  const leftOut = list(skills.left_out).filter(object);
  const row = (skill) => ({ name: text(skill.name), reason: text(skill.reason) });
  const forLength = (skill) => /length/.test(text(skill.code));
  return {
    shown: list(skills.picked).filter(object).map((skill) => text(skill.name)),
    cut: leftOut.filter(forLength).map(row),
    other: leftOut.filter((skill) => !forLength(skill)).map(row),
  };
}

// --- proposed -----------------------------------------------------------------------------------------------

function markIds(selection) {
  const marks = selection && selection.line_marks;
  if (Array.isArray(marks)) {
    return marks.filter(object).map((mark) => text(mark.id)).filter(Boolean);
  }
  return object(marks) ? Object.keys(marks) : [];
}

// "A new suggested resume is available": what it would change, by master line id: {adds, drops, pages}; null
// when nothing is proposed.
export function proposedChange(record, stored) {
  const proposed = object(record && record.proposed);
  if (!proposed) {
    return null;
  }
  // The pick route names no lines of a proposed selection (only who picked it, its pages and its conflicts): the
  // lists are null then, and Compare says what it knows. With `line_marks` (a record read whole) they are lists.
  const known = Array.isArray(proposed.line_marks) || object(proposed.line_marks);
  const next = markIds(proposed);
  const now = printedIds(stored);
  return {
    adds: known ? next.filter((id) => !now.includes(id)) : null,
    drops: known ? now.filter((id) => !next.includes(id)) : null,
    pages: Number.isInteger(proposed.pages) ? proposed.pages : null,
    pickedBy: text(proposed.picked_by) || null,
    draft: proposed.draft === true,
    conflicts: list(proposed.conflicts).length,
  };
}

// --- Suggestions (SPEC section 6, item 6) -----------------------------------------------------------------------

export const SUGGESTION_KINDS = {
  reword: "Reword a line",
  keyword: "Show a keyword",
  order: "Change the order",
  gap: "Close a gap",
  master_line: "Add a line to your master",
};
const WRITERS = { assessment: "the assessment", agent: "your agent", operator: "you" };
const HOW_WORDS = {
  job_resume_edit: "this job's resume was changed",
  master_line: "a line of your master was added or changed",
  answer: "you saved an answer",
  dismissed: "dismissed",
};

// The suggestion list: [{id, kind, kindLabel, about, line, requirement, phrase, why, who, status, statusLine, open, stored}].
// From the record; without one (the adapter line) the assessment's own structured suggestions are shown as open,
// with `stored: false`: they have no id in a record yet, so Done and Dismiss are not offered for them.
// `lineText(id)` (optional) gives a master line's text.
export function suggestionRows({ record = null, assessment = null, lineText = null } = {}) {
  const rows = rowsById(assessment);
  const recorded = record ? list(record.suggestions).filter(object) : null;
  const items = recorded || list(assessment && assessment.structured_suggestions).filter(object).map((item, index) => ({ ...item, id: `new-${index + 1}`, source: "assessment", status: "open" }));
  return items.map((item) => {
    const line = text(item.line) || null;
    const requirement = text(item.requirement) || null;
    const lineWords = line && typeof lineText === "function" ? text(lineText(line)) : "";
    const about = [line ? lineWords || `line ${line}` : "", requirement ? requirementText(rows, requirement) : ""].filter(Boolean).join(" · ");
    const status = text(item.status) || "open";
    const resolved = object(item.resolved);
    const how = resolved ? HOW_WORDS[resolved.how] || text(resolved.how).replace(/_/g, " ") : "";
    const by = resolved ? WRITERS[resolved.by] || text(resolved.by) : "";
    const closed = status === "dismissed" ? "Dismissed" : "Done";
    return {
      id: text(item.id),
      kind: text(item.kind),
      kindLabel: SUGGESTION_KINDS[item.kind] || text(item.kind).replace(/_/g, " "),
      about,
      line,
      requirement,
      phrase: text(item.posting_phrase),
      why: text(item.why),
      who: WRITERS[item.source] || text(item.source),
      status,
      statusLine: status === "open" ? "Open" : [closed, status === "done" && how ? how : "", by ? `by ${by}` : "", resolved ? dateLabel(resolved.at) : ""].filter(Boolean).join(" · "),
      open: status === "open",
      stored: Boolean(recorded),
    };
  });
}

function shellQuote(value) {
  return `'${String(value).replace(/'/g, `'\\''`)}'`;
}

// "Work on this with your agent": the two brief commands to copy (SPEC 5.2: never one response). No model call.
export function briefCommands(jobUrl, profileId) {
  if (!jobUrl) {
    return [];
  }
  const base = `gigai scout resume brief --job-url ${shellQuote(jobUrl)}${profileId ? ` --profile ${shellQuote(profileId)}` : ""}`;
  const route = (part) => `GET /api/jobs/brief?${new URLSearchParams({ url: jobUrl, part, ...(profileId ? { profile_id: profileId } : {}) })}`;
  return [
    { part: "yours", label: "Your part: the rules, the resume, your master lines, your answers, the suggestions", command: base, route: route("yours") },
    { part: "posting", label: "The posting's part: text written by strangers, as data", command: `${base} --posting`, route: route("posting") },
  ];
}

// --- Apply (SPEC section 6, item 7) ---------------------------------------------------------------------------

// {available, stale, ask, offers}. With a stale resume Apply first says so (`ask`) and offers the refresh that is
// allowed, or "Use it as it is". After the PDF: nothing.
export function applyState({ stored = null, items = [] } = {}) {
  if (!stored) {
    return { available: false, stale: false, ask: null, offers: [] };
  }
  if (!isStale(items)) {
    return { available: true, stale: false, ask: null, offers: [] };
  }
  const warnings = items.filter((item) => !item.note);
  const canRepick = warnings.some((item) => item.action === "repick");
  const refresh = canRepick ? { use: "repick", label: "Re-pick first · no model call" } : warnings.some((item) => item.action === "reassess") ? { use: "reassess", label: "Re-assess first · 1 model call" } : null;
  return {
    available: true,
    stale: true,
    ask: `This resume is stale: ${warnings.map((item) => item.label).join("; ")}.`,
    offers: [...(refresh ? [refresh] : []), { use: "as_is", label: "Use it as it is" }],
  };
}

// --- no master (SPEC A2), and a profile on its own resume ------------------------------------------------------

export const NO_MASTER_TEXT = "This profile's own resume is used as it is. Build your master resume to get a resume picked for each job";
export const OWN_RESUME_TEXT =
  "This profile uses the resume you put in by hand, so no resume is picked from your master for its jobs. To get one picked for each job, make this profile's resume from your master again";

// Why NOTHING can be picked for this job's profile: "no_master" | "own_resume", or null (a resume can be picked).
// The server says what a resume is made from now (`resume_basis`, `master_stored`); a record in the stored shape
// says only whether its assessment read a master (`basis.master`).
export function unpickable(record) {
  if (!record || object(record.selection)) {
    return null;
  }
  if (record.resume_basis) {
    return record.resume_basis === "profile_resume" ? (record.master_stored ? "own_resume" : "no_master") : null;
  }
  return object(record.basis) && record.basis.master === null ? "no_master" : null;
}

// True when there is no master to pick from: nothing can be picked for this profile.
export function hasNoMaster(record) {
  return unpickable(record) === "no_master";
}

// True when the stale list says the ASSESSMENT is old: a pick from it is refused, and the step that works is Re-assess.
export function assessmentIsOld(items) {
  return list(items).some((item) => item && item.action === "reassess" && text(item.code).startsWith(ASSESSMENT_STALE));
}

export const NO_RESUME_OLD_ASSESSMENT_TEXT = "No resume is stored for this job yet. Its assessment is old, so a resume cannot be picked from it: re-assess the job to get one.";

// --- a pick that was refused, in the user's words -------------------------------------------------------------
//
// 0.1.11.3: the page never prints what the server sent for a refused pick (it names commands, and once named a
// function: "scout.pick.settle_stored is not part of it"). Each refusal is one sentence by its CODE; a code this
// page does not know reads as the general one.

const PICK_FAILED_TEXT = "The resume could not be picked for this job. Try again, or re-assess the job to get a new pick.";
const PICK_ERRORS = {
  pick_failed: PICK_FAILED_TEXT,
  pick_not_available: PICK_FAILED_TEXT,
  pages_unmeasured: "The resume could not be picked: its pages could not be measured on this computer. Try again.",
  assessment_stale: "This job's assessment is old, so a new pick would not match it. Re-assess the job first.",
  assessment_missing: "This job is not assessed yet. Assess it first.",
  no_master: "There is no master resume to pick from. Build your master resume on the Master page to get a resume picked for each job.",
  profile_resume_in_use:
    "This profile uses the resume you put in by hand, so no resume is picked from your master for its jobs. On the Master page, make this profile's resume from your master again.",
  profile_not_found: "The profile this job was assessed for is no longer there. Assess the job again for a profile you have.",
  resume_held: "No resume is suggested for this job yet: a must-have requirement is waiting for your answer or is not met. Answer its questions, or make a draft.",
  draft_not_needed: "A resume is suggested for this job already. Pick it instead of making a draft.",
  no_proposed_resume: "No new suggested resume is waiting for this job.",
  // 0.1.11.3 item 15: "Shorten automatically" (the Generate PDF form).
  no_resume_to_shorten: "There is no resume stored for this job yet, so there is nothing to shorten. Pick one first.",
  resume_short_already: "This resume already fits its pages with most of a page to spare, so nothing was left out.",
};

// "Shorten automatically": the server's own sentence about what the shorter resume leaves out (it holds the lines'
// text and no command); a refusal is said in this page's words, by its code, like every refused pick.
export function shortenedText(view) {
  return (view && view.shortened && text(view.shortened.message)) || "The resume was shortened. Generate the PDF again.";
}

// One sentence for a refused or failed POST /api/job-resumes/pick. A request that never reached the server says so.
export function pickErrorText(err) {
  const code = err && text(err.code);
  if (code && PICK_ERRORS[code]) {
    return PICK_ERRORS[code];
  }
  if (err && err.status === 0 && text(err.message)) {
    return err.message;
  }
  return PICK_FAILED_TEXT;
}

// Why the assessment stored no resume (the record's `selection_error`), as one sentence; "" when it says nothing.
export function selectionErrorText(code) {
  if (!text(code)) {
    return "";
  }
  return code === "pages_unmeasured" ? "Its pages could not be measured on this computer when the job was assessed." : "It could not be picked when the job was assessed.";
}

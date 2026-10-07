// uat-batch1 (N5/N7): the rules behind the job page's answer boxes and its
// action, as pure functions (no React) so the node-backed static test can
// pin them.
//
//   N5  every open question sits in the requirement row it settles
//       (AssessmentQuestion.requirement == RequirementMatrixRow.requirement);
//       ONE "Re-assess" saves every filled box and re-assesses once.
//   N7  Re-assess is enabled once at least one box is filled. A disabled
//       action always carries its reason. (0.1.11: Re-assess is the ONE
//       action; "Tailor resume" and its gate went with the tailor call.)
//   0110-10-12  an OLD assessment (older prompt, settings changed, answers
//       changed, resume changed, posting changed) is itself a reason to
//       re-assess: Re-assess is on with no question open and no box filled,
//       and says why and what it costs (one model call). After an upgrade
//       every assessment is old; the gate looked at the boxes alone and the
//       page could not renew what it marked old.
//
// POST /api/answers records ONE answer per call and re-assesses only when
// `reassess` names the job (find_jobs/api/answers.py), so N answers are N
// POSTs: `reassess: null` on all but the last, the job identity on the
// last. That is one model call, with every answer already recorded when it
// runs.

const key = (value) => (typeof value === "string" ? value.trim().toLowerCase() : "");

// Which row each open question belongs to. `rows` maps a requirement (as
// the matrix row spells it) to its questions; `unplaced` are the questions
// whose requirement names no row (the table shows them in rows of their
// own, so a question is never dropped).
export function placeQuestions(matrix, questions) {
  const rowFor = new Map();
  (matrix || []).forEach((row) => {
    const name = key(row && row.requirement);
    if (name && !rowFor.has(name)) {
      rowFor.set(name, row.requirement);
    }
  });
  const rows = new Map();
  const unplaced = [];
  (questions || []).forEach((question) => {
    const requirement = rowFor.get(key(question && question.requirement));
    if (requirement === undefined) {
      unplaced.push(question);
      return;
    }
    rows.set(requirement, (rows.get(requirement) || []).concat([question]));
  });
  return { rows, unplaced };
}

// One entry per open question: what its box holds (`value`, trimmed), the
// answer already on record for it (`recorded`, from GET /api/answers), and
// whether the box is filled / differs from the record.
//
// `drafts` holds only what the operator typed (question_id -> string); a
// question they never touched shows its recorded answer, so an answer
// given on another posting counts here without being typed again.
//
// 0110-034: `bank` (optional) is {suggestions, used}: `suggestions` maps a
// question_id to its near match among the user's answers (GET /api/answers/match) and
// `used` to the bank question the operator took with "Use it". A state
// then also carries the question's words, `suggestion` (only while the box
// is empty and nothing is on record: nothing is filled in for the user) and
// `fromBank`. Without `bank` a state is exactly what it was.
export function answerStates(questions, drafts, priorAnswers, bank) {
  return (questions || []).map((question) => {
    const id = question.question_id;
    const prior = priorAnswers && priorAnswers.get ? priorAnswers.get(id) : null;
    const recorded = prior && typeof prior.answer === "string" ? prior.answer.trim() : "";
    const typed = drafts && Object.prototype.hasOwnProperty.call(drafts, id) ? drafts[id] : null;
    const value = (typed === null ? recorded : String(typed)).trim();
    const state = { question_id: id, value, recorded, filled: value.length > 0, isNew: value.length > 0 && value !== recorded };
    if (!bank) {
      return state;
    }
    const suggestion = bank.suggestions && bank.suggestions.get ? bank.suggestions.get(id) || null : null;
    const used = bank.used && Object.prototype.hasOwnProperty.call(bank.used, id) ? bank.used[id] : null;
    return {
      ...state,
      question: typeof question.question === "string" ? question.question : "",
      suggestion: suggestion && !recorded && !state.filled ? suggestion : null,
      fromBank: used && state.filled ? used : null,
    };
  });
}

// What a POST /api/answers body carries beyond the answer: the question's
// own words (kept with it) and the question a confirmed suggestion came
// from. Each only when known, so a caller without them sends what it always
// did. 0.1.10.7 C: no profile; an answer is the user's.
function bankFields(state) {
  const fields = {};
  if (state.question) {
    fields.question = state.question;
  }
  if (state.fromBank) {
    fields.from_bank = state.fromBank;
  }
  return fields;
}

// The POST /api/answers bodies for one "Re-assess": every filled box, in
// question order, re-assessing on the last one only.
// 0.1.11.6 AN1: `profileId` is the profile of the page that asks. The re-assessment is made for it: without it the
// server takes the one profile that holds the job and refuses a job two profiles hold (it used to take the newest
// assessment's profile, which re-assessed the job for a profile the page did not show).
export function answerRequests(states, jobIdentity, profileId = null) {
  const filled = (states || []).filter((state) => state.filled);
  const reassess = jobIdentity ? { job_identity: jobIdentity, ...(profileId ? { profile_id: profileId } : {}) } : null;
  return filled.map((state, index) => ({
    question_id: state.question_id,
    answer: state.value,
    reassess: index === filled.length - 1 ? reassess : null,
    ...bankFields(state),
  }));
}

// `stale` is why the assessment shown is old, in words ("older prompt"), or null for a current one; `canAssess` says the
// page can assess the posting again by its link (a pasted posting has none: its text is never stored).
export function reassessGate({ assessed, states, stale = null, canAssess = true }) {
  if (!assessed) {
    return { enabled: false, reason: "This posting is not assessed yet, so there is nothing to re-assess." };
  }
  const open = (states || []).length;
  const filled = (states || []).filter((state) => state.filled).length;
  if (stale && filled === 0) {
    if (!canAssess) {
      return { enabled: false, stale: true, reason: `This assessment is old (${stale}), but this posting has no stored link to assess it from again.` };
    }
    return { enabled: true, stale: true, reason: `This assessment is old (${stale}). Re-assessing it is one model call.` };
  }
  if (open === 0) {
    return { enabled: false, reason: "There are no open questions, so there is nothing new to re-assess with." };
  }
  if (filled === 0) {
    return {
      enabled: false,
      reason: `Type an answer to ${open === 1 ? "the open question" : `at least one of the ${open} open questions`} in the table first.`,
    };
  }
  return { enabled: true, reason: `Saves ${filled === 1 ? "your answer" : `your ${filled} answers`} and re-assesses this posting once.` };
}

// 0110-10-13: a failed assessment with a typed cause (assess_causes.py) carries its own facts and next action:
// the server's message, whether a model call started and may have used tokens, and what to do next. null for
// any other error.
export function assessCauseText(err) {
  if (!err || typeof err.next_action !== "string") {
    return null;
  }
  const facts = err.model_call_started
    ? "A model call started and may have used tokens."
    : "No model call was made and no tokens were used.";
  const said = String(err.detail || err.message || err.code || "The assessment failed").replace(/\.$/, "");
  return `${said.charAt(0).toUpperCase()}${said.slice(1)}. ${facts} No new assessment was stored. Next: ${err.next_action}`;
}

// 0110-10-13: what one assessment sends, said beside the Assess / Re-assess actions (the agent guide's "What one
// assessment sends"). `target` is the config's default_model_target, `label` its display name.
const OWN_LOGIN_TARGETS = new Set(["codex_cli", "claude_cli"]);
export function assessSendsLine(target, label) {
  const where = target ? ` (${label || target}${OWN_LOGIN_TARGETS.has(target) ? ", your own login" : ""})` : "";
  return (
    `What one assessment sends to your model target${where}: the stored posting, your resume with its contact lines removed by pattern ` +
    "(which can miss an unusual name or contact format), your search preferences, your saved answers and the stories that match."
  );
}

// The no-call preview's `model_input_summary` (POST /api/postings/assess without approve) as short lines.
const RESUME_SOURCE_WORDS = {
  master_evidence: "the lines of your master resume picked for this posting",
  profile_view: "this profile's own resume",
};
export function assessSummaryLines(summary) {
  if (!summary || !Array.isArray(summary.profiles)) {
    return [];
  }
  return [
    ...summary.profiles.map((item) => `Profile ${item.label}: ${RESUME_SOURCE_WORDS[item.resume_source] || item.resume_source}.`),
    `Saved answers: ${summary.answers_used ? summary.answers_saved : "none"}. Saved stories that can match: ${summary.stories_used ? summary.stories_saved : "none"}.`,
    summary.public_fetch_needed
      ? "The posting's text is not stored: it is fetched from its public board first."
      : "The posting's text is stored: nothing is fetched.",
  ];
}

// What a failed re-assessment of an old assessment says. The assessment shown stays: nothing was replaced.
export function reassessErrorText(err) {
  const code = err && err.code;
  const typed = assessCauseText(err);
  if (typed) {
    return typed;
  }
  if (code === "posting_requirements_unreadable") {
    return "The posting's requirements could not be read, so it was not assessed again. The assessment shown stays.";
  }
  if (err && err.status === 504) {
    return "The model timed out assessing this posting. Try again, or a faster model target.";
  }
  return (err && err.message) || String(err);
}

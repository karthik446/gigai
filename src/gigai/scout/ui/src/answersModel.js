// uat-batch1 (N5/N7): the rules behind the job page's answer boxes and its
// two actions, as pure functions (no React) so the node-backed static test
// can pin them.
//
//   N5  every open question sits in the requirement row it settles
//       (AssessmentQuestion.requirement == RequirementMatrixRow.requirement);
//       ONE "Re-assess" saves every filled box and re-assesses once.
//   N7  Re-assess is enabled once at least one box is filled; Tailor resume
//       is enabled when the verdict is matched, or when every open question
//       has an answer. A disabled action always carries its reason.
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
// question_id to its story bank near match (GET /api/story-bank/match) and
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

// 0110-034: what a POST /api/answers body carries beyond the answer: the
// question's own words (kept with it in the story bank), the bank question
// a confirmed suggestion came from, and whose bank it is. Each only when
// known, so a caller without them sends what it always did.
function bankFields(state, profileId) {
  const fields = {};
  if (state.question) {
    fields.question = state.question;
  }
  if (state.fromBank) {
    fields.from_bank = state.fromBank;
  }
  if (profileId) {
    fields.profile_id = profileId;
  }
  return fields;
}

// The POST /api/answers bodies for one "Re-assess": every filled box, in
// question order, re-assessing on the last one only.
export function answerRequests(states, jobIdentity, profileId) {
  const filled = (states || []).filter((state) => state.filled);
  return filled.map((state, index) => ({
    question_id: state.question_id,
    answer: state.value,
    reassess: jobIdentity && index === filled.length - 1 ? { job_identity: jobIdentity } : null,
    ...bankFields(state, profileId),
  }));
}

// The bodies "Tailor resume" sends first: only the answers the record does
// not hold yet, never re-assessing (tailoring reads the recorded answers).
export function unsavedAnswerRequests(states, profileId) {
  return (states || [])
    .filter((state) => state.isNew)
    .map((state) => ({ question_id: state.question_id, answer: state.value, reassess: null, ...bankFields(state, profileId) }));
}

const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

export function reassessGate({ assessed, states }) {
  if (!assessed) {
    return { enabled: false, reason: "This posting is not assessed yet, so there is nothing to re-assess." };
  }
  const open = (states || []).length;
  if (open === 0) {
    return { enabled: false, reason: "There are no open questions, so there is nothing new to re-assess with." };
  }
  const filled = states.filter((state) => state.filled).length;
  if (filled === 0) {
    return {
      enabled: false,
      reason: `Type an answer to ${open === 1 ? "the open question" : `at least one of the ${open} open questions`} in the table first.`,
    };
  }
  return { enabled: true, reason: `Saves ${filled === 1 ? "your answer" : `your ${filled} answers`} and re-assesses this posting once.` };
}

export function tailorGate({ assessed, verdict, states, hasUrl, hasProfile }) {
  if (!hasUrl) {
    return { enabled: false, reason: "This posting has no stored URL, so a resume cannot be tailored from here." };
  }
  if (!hasProfile) {
    return { enabled: false, reason: "Select a profile first." };
  }
  if (!assessed) {
    return { enabled: false, reason: "Assess this posting first." };
  }
  if (verdict === "matched_above_threshold") {
    return { enabled: true, reason: "This posting is a match." };
  }
  const open = (states || []).length;
  const missing = (states || []).filter((state) => !state.filled).length;
  if (missing > 0) {
    return {
      enabled: false,
      reason: `Answer ${open === 1 ? "the open question" : `all ${open} open questions`} first (${missing} left), or re-assess to a match.`,
    };
  }
  const unsaved = (states || []).filter((state) => state.isNew).length;
  if (unsaved > 0) {
    return { enabled: true, reason: `Every open question has an answer. Tailoring saves ${plural(unsaved, "new answer")} first.` };
  }
  return { enabled: true, reason: open > 0 ? "Every open question has an answer." : "There are no open questions." };
}

// uat-bug-004: number the setup interview's questions from their VISIBLE
// order, with no gaps whatever `work_mode` is. The interview shows a fixed
// sequence of questions, but one of them (the city/area question) is
// conditionally hidden (`showCity` in SetupInterviewForm.jsx) -- with the
// old hard-coded "5." label, choosing "Remote-only" hid question 5 and the
// visible numbers jumped 4 -> 6. This module is the single source of truth
// for numbering so the label text and the visibility check can never drift
// apart again.
//
// Pure and framework-free on purpose: there is no JS component test runner
// in this UI (see package.json), so this logic is extracted here to be
// covered directly (see test_present_ui.py's manual-check note / this
// module's own doc comment) without pulling in a new test framework.

// The interview's fixed question order. `id` is stable (used as a React
// key and to look up a question's assigned number); `text` is the question
// body with no leading number; `visible(fields)` decides whether the
// question is shown for the current form state. Keep this in the same
// order the form renders questions in.
export const QUESTIONS = [
  { id: "roles", text: "What roles/level are you targeting?", visible: () => true },
  { id: "titles_to_avoid", text: "Any job titles to avoid?", visible: () => true },
  { id: "countries", text: "Which countries should postings be in?", visible: () => true },
  { id: "work_mode", text: "Remote-only, hybrid, onsite, or any?", visible: () => true },
  {
    id: "city",
    text: "If not remote-only, what city/area?",
    visible: (fields) => fields.work_mode !== "remote",
  },
  { id: "visa_sponsorship_required", text: "Do you need visa sponsorship?", visible: () => true },
  { id: "exclude_companies", text: "Any companies to exclude?", visible: () => true },
  { id: "watch_companies", text: "Any companies to always watch?", visible: () => true },
  { id: "company_stage_size", text: "Preferred company stage/size?", visible: () => true },
  { id: "industries_include", text: "Industries to include?", visible: () => true },
  { id: "industries_exclude", text: "Industries to exclude?", visible: () => true },
  { id: "must_have_stack", text: "Must-have tech stack?", visible: () => true },
  { id: "dealbreaker_stack", text: "Deal-breaker tech stack?", visible: () => true },
  { id: "cadence_days", text: "How often should discovery run? (days)", visible: () => true },
  {
    id: "budget_usd_per_session",
    text: "Acceptable Exa/OpenAI spend per run ($)",
    visible: () => true,
  },
];

// Returns a Map of question id -> 1-based number, counting only the
// questions `visible(fields)` returns true for, in QUESTIONS order. A
// hidden question (e.g. `city` when work_mode is "remote") is left out of
// the map entirely rather than being assigned a skipped number, so the
// visible sequence is always consecutive (1, 2, 3, ... with no gaps).
export function numberQuestions(fields, questions = QUESTIONS) {
  const numbers = new Map();
  let next = 1;
  for (const question of questions) {
    if (question.visible(fields)) {
      numbers.set(question.id, next);
      next += 1;
    }
  }
  return numbers;
}

// Convenience: "<n>. <text>" for a single question, given the numbers Map
// `numberQuestions` produced. Returns the bare text (no leading number) if
// the question isn't in the map (it's currently hidden) -- callers only
// invoke this for questions they are actually rendering, so that path is
// unreached in practice, but staying defined instead of throwing keeps a
// stray call harmless.
export function questionLabel(id, numbers, questions = QUESTIONS) {
  const question = questions.find((item) => item.id === id);
  const text = question ? question.text : id;
  const number = numbers.get(id);
  return number === undefined ? text : `${number}. ${text}`;
}

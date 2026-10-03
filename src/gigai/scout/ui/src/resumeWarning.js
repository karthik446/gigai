// 0.1.10-001: the personal-info warning shown wherever a resume enters
// Scout, and the local heads-up. Pure. The wording matches the README's
// "Privacy and security" section and gigai.scout.resume_pii.RESUME_WARNING
// (the general wording is pinned to it by test_ui_resume_warning_model).
// 0.1.10.7 K: GigAI stores no contact details, so nothing is "added back":
// the import removes them and the Generate PDF form takes them for one PDF.

const REMOVED = "Scout removes your name and contact lines (email, phone, address, links) before sending your resume to";
const NEVER_STORED = "and never stores them.";
const CAVEAT = "It can't catch personal details elsewhere in the text (a first line that holds both a title and your name, or contact details inside a sentence), so keep those out.";
const WHERE = "You type your name and contact details only when you make a PDF.";
// The end of the heads-up for a resume about to be imported (resume_pii.HEADS_UP_REMOVED).
const HEADS_UP_REMOVED = "Scout removes contact lines when it stores the resume. Check that nothing else personal is in the text.";
const PROVIDERS = {
  codex_cli: "OpenAI",
  claude_cli: "Anthropic",
  openrouter_api: "your provider (OpenRouter)",
};

// `modelTarget` is the model the resume will go to (find-jobs.json
// `default_model_target` / the wizard's pick). Unknown or unset: the
// general wording, naming every provider.
export function resumeWarningText(modelTarget) {
  let sentTo;
  if (modelTarget === "ollama_local") {
    sentTo = "your Ollama model (it stays on this machine)";
  } else if (PROVIDERS[modelTarget]) {
    sentTo = PROVIDERS[modelTarget];
  } else {
    sentTo = "the model you pick (Codex -> OpenAI, Claude -> Anthropic, OpenRouter -> your provider)";
  }
  return `${REMOVED} ${sentTo}, ${NEVER_STORED} ${CAVEAT} ${WHERE}`;
}

// `found` is POST /api/resume/check's list. Nothing found says nothing:
// no message, never "clean".
export function contactHeadsUp(found, { stored = false } = {}) {
  if (!Array.isArray(found) || found.length === 0) {
    return null;
  }
  const subject = stored ? "This stored resume" : "This resume";
  const list = found.join(", ");
  return stored
    ? `${subject} seems to contain: ${list}.`
    : `${subject} seems to contain: ${list}. ${HEADS_UP_REMOVED}`;
}

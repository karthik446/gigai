// 0.1.10-001: the personal-info warning shown wherever a resume enters
// Scout, and the local heads-up. Pure. The wording matches the README's
// "Privacy and security" section and gigai.scout.resume_pii.RESUME_WARNING
// (the general wording is pinned to it by test_ui_resume_warning_model).

const REMOVED = "Scout removes your name and contact lines (email, phone, address, links) before sending your resume to";
const BACK = "and adds them back only in your PDF, on this machine.";
const CAVEAT = "It can't catch personal details elsewhere in the text (a first line that holds both a title and your name, or contact details inside a sentence), so keep those out.";
const WHERE = "Your contact line lives in Settings > Resume display.";
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
  return `${REMOVED} ${sentTo}, ${BACK} ${CAVEAT} ${WHERE}`;
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
    : `${subject} seems to contain: ${list}. Remove them before continuing?`;
}

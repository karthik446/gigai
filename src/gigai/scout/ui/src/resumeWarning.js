// 0.1.10-001: the personal-info warning shown wherever a resume enters
// Scout, and the local heads-up. Pure. The wording matches the README's
// "Privacy and security" section and gigai.scout.resume_pii.RESUME_WARNING.

const LEAD = "Remove your personal info before adding a resume: name, email, phone, street address and links.";
const PROVIDERS = {
  codex_cli: "OpenAI",
  claude_cli: "Anthropic",
  openrouter_api: "your provider (OpenRouter)",
};

// `modelTarget` is the model the resume will go to (find-jobs.json
// `default_model_target` / the wizard's pick). Unknown or unset: the
// general wording, naming every provider.
export function resumeWarningText(modelTarget) {
  if (modelTarget === "ollama_local") {
    return (
      `${LEAD} Scout sends your resume text to the model you pick to assess postings and tailor your resume, ` +
      "and it does not remove personal info for you yet. With Ollama it stays on this machine, " +
      "but keep it out anyway if you might switch models."
    );
  }
  const provider = PROVIDERS[modelTarget];
  const sentTo = provider
    ? `Scout sends your resume text to ${provider}`
    : "Scout sends your resume text to the model you pick (Codex -> OpenAI, Claude -> Anthropic, OpenRouter -> your provider)";
  return `${LEAD} ${sentTo} to assess postings and tailor your resume, and it does not remove personal info for you yet.`;
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

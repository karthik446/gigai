// uat-bug-035: the sealed ModelTarget values (find_jobs/contracts.py), in
// the order the wizard and the run dialog offer them, with the label each
// select shows. The value is what the API takes and find-jobs.json stores.
export const MODEL_TARGETS = ["ollama_local", "codex_cli", "claude_cli", "openrouter_api"];

export const MODEL_TARGET_LABELS = {
  ollama_local: "Ollama (local)",
  codex_cli: "Codex (codex CLI)",
  claude_cli: "Claude (claude CLI)",
  openrouter_api: "OpenRouter (API)",
};

// One line per target. Same privacy posture as assess: the resume goes only
// to the chosen model, never to web search. Claude: assessments run in the
// CLI's plan mode, which ignores --model, so they use Claude Code's own
// default model (the ranking pass honours a configured model).
export const MODEL_TARGET_HINTS = {
  ollama_local: "Runs locally through Ollama. Nothing leaves this machine.",
  codex_cli: "Uses the Codex CLI, which sends your resume to OpenAI.",
  claude_cli: "Uses the Claude Code CLI, which sends your resume to Anthropic. Assessments use Claude Code's default model.",
  openrouter_api: "Sends your resume to OpenRouter's API (the model you configured there).",
};

// The label for a stored value; an unknown value is shown as it is.
export function modelTargetLabel(target) {
  return MODEL_TARGET_LABELS[target] || target;
}

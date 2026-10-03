// 0.1.10.7 E: what the model calls have taken, from GET /api/metrics.
//
// The report's `comparison` has one entry per (kind, model_target): the
// averages of every recorded call of that kind through that model target.
// Scout records numbers only (tokens, seconds, cost, outcome), so everything
// here is a number turned into a short phrase. Pure functions: the label
// beside Re-assess / Assess all, and the rows of the Settings table.

// The short name a sentence uses for a model target.
const TARGET_NAMES = {
  ollama_local: "ollama",
  codex_cli: "codex",
  claude_cli: "claude",
  openrouter_api: "openrouter",
};

// What one call of a kind is about, for "per job".
const KIND_UNITS = { assess: "job", tailor: "resume" };

const KIND_LABELS = {
  assess: "Assess",
  rank: "Rank",
  tag: "Tag titles",
  tailor: "Tailor resume",
  extract: "Read resume",
  interview: "Interview prep",
};

export function targetName(target) {
  return TARGET_NAMES[target] || target || "unknown";
}

// 19500 -> "19.5k", 20000 -> "20k", 850 -> "850".
export function tokensText(tokens) {
  if (typeof tokens !== "number" || !Number.isFinite(tokens)) {
    return null;
  }
  if (tokens < 1000) {
    return String(Math.round(tokens));
  }
  return `${(tokens / 1000).toFixed(1).replace(/\.0$/, "")}k`;
}

// 11.2 -> "11 s", 2.44 -> "2.4 s", 150 -> "2.5 min".
export function secondsText(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) {
    return null;
  }
  if (seconds >= 120) {
    return `${(seconds / 60).toFixed(1).replace(/\.0$/, "")} min`;
  }
  if (seconds >= 10) {
    return `${Math.round(seconds)} s`;
  }
  return `${seconds.toFixed(1).replace(/\.0$/, "")} s`;
}

export function costText(cost) {
  if (typeof cost !== "number" || !Number.isFinite(cost)) {
    return null;
  }
  return cost >= 0.01 ? `$${cost.toFixed(2)}` : `$${cost.toFixed(4)}`;
}

function comparison(report) {
  return report && Array.isArray(report.comparison) ? report.comparison : [];
}

// "avg 19.5k tokens, 11 s per job on codex": the averages of `kind` on the
// model target in use. null with no history for it (nothing is shown).
export function averageLabel(report, kind, target) {
  const row = comparison(report).find((item) => item.kind === kind && item.model_target === target);
  if (!row) {
    return null;
  }
  const tokens = tokensText(row.avg_tokens);
  const seconds = secondsText(row.avg_seconds);
  const parts = [tokens && `${tokens} tokens`, seconds].filter(Boolean);
  if (parts.length === 0) {
    return null;
  }
  return `avg ${parts.join(", ")} per ${KIND_UNITS[kind] || "call"} on ${targetName(target)}`;
}

// The Settings table: one row per (kind, model target), in the report's order.
export function comparisonRows(report) {
  return comparison(report).map((row) => ({
    key: `${row.kind}:${row.model_target}`,
    kind: KIND_LABELS[row.kind] || row.kind,
    model: targetName(row.model_target),
    models: Array.isArray(row.models) ? row.models.join(", ") : "",
    calls: row.calls,
    tokens: tokensText(row.avg_tokens) || "-",
    seconds: secondsText(row.avg_seconds) || "-",
    cost: costText(row.avg_cost_usd) || "-",
    failed: row.calls ? `${Math.round((row.error_rate || 0) * 100)}%` : "-",
  }));
}

// P9b: pure helpers for the setup wizard -- no React, no fetch. Everything
// here is a function of API responses (GET /api/setup, /api/config,
// /api/profiles, POST /api/resume/extract) or of the wizard's own field
// state, so the screens stay thin and the derivations are easy to read.

export const MODEL_TARGETS = ["ollama_local", "codex_cli", "openrouter_api"];

// One line per sealed ModelTarget (find_jobs/contracts.py). Same privacy
// posture as assess: the resume goes only to the chosen model, never to
// web search.
export const MODEL_TARGET_HINTS = {
  ollama_local: "Runs locally through Ollama. Nothing leaves this machine.",
  codex_cli: "Uses the Codex CLI, which sends your resume to OpenAI.",
  openrouter_api: "Sends your resume to OpenRouter's API (the model you configured there).",
};

export const WORK_MODES = [
  { value: "remote", label: "Remote-only" },
  { value: "hybrid", label: "Hybrid" },
  { value: "onsite", label: "Onsite" },
  { value: "any", label: "Any" },
];

export const STEPS = ["Resume", "Target", "Companies", "Discovery + finish"];

export const JEV_PRIVACY_LINE = "Your resume is sent to Jev to rank postings.";

// Q1 (v0.1.9): the rolling publication window (find-jobs.json
// `max_age_days`; contracts.DEFAULT_MAX_AGE_DAYS / MAX_AGE_DAYS_MAXIMUM).
// The wizard always writes this rolling form; a hand-edited fixed
// `published_after` is cleared by the save (the fixed date would otherwise
// keep winning -- filters.published_cutoff's rule).
export const DEFAULT_MAX_AGE_DAYS = 60;
export const MAX_AGE_DAYS_MAXIMUM = 365;

export function clampMaxAgeDays(value) {
  const parsed = Math.floor(Number(value));
  if (!Number.isFinite(parsed) || parsed < 1) {
    return 1;
  }
  return Math.min(MAX_AGE_DAYS_MAXIMUM, parsed);
}

// The wizard's field state. `prefs` is GET /api/setup's saved prefs (200)
// or the 404's `prefill`; `config` is GET /api/config's body (or null);
// `selectedProfile` is the /api/profiles entry that is currently selected
// (or null).
export function initialFields({ prefs, config, selectedProfile }) {
  const p = prefs || {};
  const countries = Array.isArray(p.countries) && p.countries.length > 0 ? p.countries : ["US"];
  const configuredTarget = config && config.config && config.config.default_model_target;
  const configuredWindow = config && config.config ? config.config.max_age_days : null;
  return {
    // screen 1
    profileMode: selectedProfile ? "update" : "new",
    profileName: selectedProfile ? selectedProfile.label || "" : "",
    resumeMode: "paste",
    resumeText: "",
    uploadName: null,
    existingRef: null,
    modelTarget: MODEL_TARGETS.includes(configuredTarget) ? configuredTarget : "ollama_local",
    extraction: null,
    stack: [],
    seniority: [],
    suggestedTitles: [],
    // screen 2 (titles seed from the extraction on the first visit)
    titles: selectedProfile && Array.isArray(selectedProfile.titles) ? selectedProfile.titles : [],
    titlesSeeded: false,
    titlesToAvoid:
      selectedProfile && Array.isArray(selectedProfile.titles_to_avoid) && selectedProfile.titles_to_avoid.length > 0
        ? selectedProfile.titles_to_avoid
        : p.titles_to_avoid || [],
    countries,
    workMode: p.work_mode || "any",
    city: p.city || "",
    visaSponsorshipRequired: Boolean(p.visa_sponsorship_required),
    maxAgeDays: Number.isInteger(configuredWindow) && configuredWindow >= 1 ? clampMaxAgeDays(configuredWindow) : DEFAULT_MAX_AGE_DAYS,
    // screen 3
    excludeCompanies: p.exclude_companies || [],
    watchCompanies: p.watch_companies || [],
    // screen 4
    cadenceDays: p.cadence_days ?? 7,
    budgetUsdPerSession: p.budget_usd_per_session ?? 0.5,
  };
}

function shortDate(iso) {
  if (!iso) {
    return null;
  }
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return parsed.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

// The "choose an existing resume" list: every distinct resume_ref across the
// profiles. The one GET /api/config resolved for the selected profile is the
// only one the API gives a file name + import date for (resume_label /
// resume_created_at); the others can only be named by the profile that pins
// them plus that profile's own updated_at (the profiles route never exposes
// the reference's label -- see profiles.py's no-resume-bytes rule).
export function existingResumes({ profiles, config }) {
  const preview = config && config.resume_preview;
  const seen = new Set();
  const items = [];
  for (const profile of profiles || []) {
    const ref = profile.resume_ref;
    if (!ref || !ref.record_id || !ref.revision_id) {
      continue;
    }
    const key = `${ref.record_id}/${ref.revision_id}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    const isPreview = preview && preview.record_id === ref.record_id && preview.revision_id === ref.revision_id;
    if (isPreview && config.resume_label) {
      const added = shortDate(config.resume_created_at);
      items.unshift({
        key,
        record_id: ref.record_id,
        revision_id: ref.revision_id,
        name: config.resume_label,
        date: added ? `added ${added}` : null,
        profileLabel: profile.label,
      });
    } else {
      const updated = shortDate(profile.updated_at);
      items.push({
        key,
        record_id: ref.record_id,
        revision_id: ref.revision_id,
        name: `Resume used by "${profile.label}"`,
        date: updated ? `profile updated ${updated}` : null,
        profileLabel: profile.label,
      });
    }
  }
  return items;
}

export function resumeSummary(fields, resumes) {
  if (fields.resumeMode === "existing") {
    const chosen = (resumes || []).find((item) => fields.existingRef && item.key === fields.existingRef.key);
    return chosen ? chosen.name : "(none chosen)";
  }
  if (fields.resumeMode === "upload") {
    return fields.uploadName ? `${fields.uploadName} (${fields.resumeText.length} characters)` : "(no file)";
  }
  return fields.resumeText.trim() ? `pasted text (${fields.resumeText.trim().length} characters)` : "(empty)";
}

export function hasResume(fields) {
  if (fields.resumeMode === "existing") {
    return Boolean(fields.existingRef);
  }
  return fields.resumeText.trim().length > 0;
}

export function screenIsComplete(step, fields) {
  if (step === 1) {
    return fields.profileName.trim().length > 0 && hasResume(fields);
  }
  if (step === 2) {
    return fields.titles.length > 0 && fields.countries.length > 0 && fields.maxAgeDays >= 1;
  }
  if (step === 4) {
    return fields.cadenceDays >= 1 && fields.budgetUsdPerSession > 0;
  }
  return true;
}

// The bodies the Finish step sends. `existingPrefs` is GET /api/setup's
// saved prefs (or null): the S23 fields this wizard no longer asks about
// (company stage/size, industries, must-have/deal-breaker stack -- the
// dropped questions 9/10) pass through unchanged so an edit never wipes
// them; a first run sends them empty.
export function profileBody(fields) {
  const body = {
    label: fields.profileName.trim(),
    titles: fields.titles,
    titles_to_avoid: fields.titlesToAvoid,
  };
  if (fields.resumeMode === "existing" && fields.existingRef) {
    body.resume_record_id = fields.existingRef.record_id;
    body.resume_revision_id = fields.existingRef.revision_id;
  }
  return body;
}

export function setupBody(fields, existingPrefs) {
  const prev = existingPrefs || {};
  return {
    roles: fields.titles,
    titles_to_avoid: fields.titlesToAvoid,
    countries: fields.countries,
    work_mode: fields.workMode,
    city: fields.workMode === "remote" ? null : fields.city.trim() || null,
    visa_sponsorship_required: fields.visaSponsorshipRequired,
    exclude_companies: fields.excludeCompanies,
    watch_companies: fields.watchCompanies,
    company_stage_size: prev.company_stage_size || null,
    industries_include: prev.industries_include || [],
    industries_exclude: prev.industries_exclude || [],
    must_have_stack: prev.must_have_stack || [],
    dealbreaker_stack: prev.dealbreaker_stack || [],
    cadence_days: fields.cadenceDays,
    budget_usd_per_session: fields.budgetUsdPerSession,
    max_age_days: clampMaxAgeDays(fields.maxAgeDays),
  };
}

export function reviewRows(fields, resumes) {
  const list = (values) => (values.length ? values.join(", ") : "(none)");
  const workMode = WORK_MODES.find((mode) => mode.value === fields.workMode);
  const workModeText =
    (workMode ? workMode.label : fields.workMode) +
    (fields.workMode !== "remote" && fields.city.trim() ? ` · ${fields.city.trim()}` : "");
  const extractor = fields.extraction
    ? `${fields.extraction.extractor} (${fields.extraction.resolved_target || fields.extraction.model_target})`
    : "not run";
  return [
    ["Profile", `${fields.profileName.trim() || "(unnamed)"}${fields.profileMode === "update" ? " (update)" : " (new)"}`],
    ["Resume", resumeSummary(fields, resumes)],
    ["Extracted by", extractor],
    ["Tech stack", list(fields.stack)],
    ["Seniority", list(fields.seniority)],
    ["Target titles", list(fields.titles)],
    ["Titles to avoid", list(fields.titlesToAvoid)],
    ["Countries", list(fields.countries)],
    ["Work mode", workModeText],
    ["Visa sponsorship required", fields.visaSponsorshipRequired ? "Yes (hard filter)" : "No"],
    ["Posting age", `last ${clampMaxAgeDays(fields.maxAgeDays)} days`],
    ["Exclude companies", list(fields.excludeCompanies)],
    ["Always watch", list(fields.watchCompanies)],
    ["Discovery cadence", `${fields.cadenceDays} days`],
    ["Budget per run", `$${Number(fields.budgetUsdPerSession).toFixed(2)}`],
  ];
}

// The exact commands to run, as [{text, comment}] lines. `profileId` is the
// saved profile's id (real once Finish succeeded, a placeholder before).
// The secrets names are the ones `gigai secrets add` accepts
// (secrets_catalog.py: openrouter / exa / jev); `--profile` is the real
// `gigai scout resume add` flag and takes a profile ID.
export function buildCommands({ modelTarget, resumeMode, uploadName, profileId }) {
  const lines = [{ text: "uv tool install gigai", comment: "or: uv tool upgrade gigai" }];
  if (modelTarget === "openrouter_api") {
    lines.push({ text: "gigai secrets add openrouter", comment: null });
  } else if (modelTarget === "ollama_local") {
    lines.push({ text: "ollama serve", comment: "Ollama must be running with your configured model pulled" });
  }
  lines.push({ text: "gigai secrets add exa", comment: "optional: open-web discovery" });
  lines.push({ text: "gigai secrets add jev", comment: `optional: pre-rank postings. ${JEV_PRIVACY_LINE}` });
  lines.push({ text: "gigai scout install", comment: null });
  if (resumeMode !== "existing") {
    const file = uploadName ? `./${uploadName}` : "./resume.md";
    const id = profileId || "<profile id, shown after Finish>";
    lines.push({
      text: `gigai scout resume add ${file} --profile ${id}`,
      comment: resumeMode === "paste" ? "save the pasted text to that file first" : null,
    });
  }
  lines.push({ text: "gigai scout run", comment: null });
  return lines;
}

export function commandsAsText(lines) {
  return lines.map((line) => (line.comment ? `${line.text}   # ${line.comment}` : line.text)).join("\n");
}

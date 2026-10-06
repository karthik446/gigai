// P9b: pure helpers for the setup wizard -- no React, no fetch. Everything
// here is a function of API responses (GET /api/setup, /api/config,
// /api/profiles, /api/secrets/status, POST /api/resume/extract) or of the
// wizard's own field state, so the screens stay thin and the derivations
// are easy to read.

// uat-bug-035: the targets, their labels and hints live in ../modelTargets.js
// (the run dialog offers the same list); re-exported for the wizard.
export { MODEL_TARGETS, MODEL_TARGET_HINTS, MODEL_TARGET_LABELS, modelTargetLabel } from "../modelTargets.js";
import { MODEL_TARGETS } from "../modelTargets.js";
import { buildPutBody, layoutLine } from "../resumeDisplayModel.js";

// uat-bug-028: each mode is a filter (find-jobs.json `work_mode`, applied
// by the index search before ranking); `hint` says what it keeps.
export const WORK_MODES = [
  { value: "remote", label: "Remote-only", hint: "Only remote postings." },
  { value: "hybrid", label: "Hybrid", hint: "Remote postings, plus hybrid ones in your city or area." },
  { value: "onsite", label: "Onsite", hint: "Remote postings, plus hybrid and on-site ones in your city or area." },
  { value: "any", label: "Any", hint: "Every posting in your countries." },
];

// uat-bug-028: the starter find-jobs.json's location text
// (contracts.LOCATION_PLACEHOLDER_PREFIX). An older save could carry it as
// the city; the wizard starts empty instead (the server refuses to save it).
export const LOCATION_PLACEHOLDER_PREFIX = "REPLACE_WITH_YOUR_LOCATION";

export function isLocationPlaceholder(value) {
  return typeof value === "string" && value.trim().toUpperCase().startsWith(LOCATION_PLACEHOLDER_PREFIX);
}

// Only Hybrid and Onsite filter on a city/area (Remote-only and Any ignore it).
export function usesArea(workMode) {
  return workMode === "hybrid" || workMode === "onsite";
}

// The saved work mode: the prefs' own, else find-jobs.json's `work_mode`,
// else Remote-only when nothing at all is stored (fresh onboarding, 0.1.11.2), else Any. An older file's `remote` flag alone says nothing: the old
// wizard saved `remote: true` for Any as well as Remote-only.
export function initialWorkMode(prefs, config) {
  const valid = (value) => WORK_MODES.some((mode) => mode.value === value);
  if (prefs && valid(prefs.work_mode)) {
    return prefs.work_mode;
  }
  const stored = config && config.config;
  if (stored && valid(stored.work_mode)) {
    return stored.work_mode;
  }
  // Nothing stored: a fresh onboarding. A config that exists without a work_mode is an older file (Any).
  return stored ? "any" : "remote";
}

// uat-bug-020 (A2): Discover is hidden in 0.1.9, so the last step is the
// review alone: no discovery cadence, no budget.
export const STEPS = ["Resume", "Resume display", "Target", "Review"];

// uat-bug-020: what Finish can store as a resume -- the formats and the
// size `gigai scout resume add` accepts (resume_import.py).
export const RESUME_FILE_PATTERN = /\.(txt|md|markdown)$/i;
export const RESUME_MAX_BYTES = 1048576;

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
// (or null); `resumes` is existingResumes()' list.
//
// uat-bug-020 (A1): a resume that is already stored is the one the wizard
// starts with ("Choose existing", the first of the list chosen), so a
// resume added with `gigai scout resume add` is used without pasting it
// again. With none stored the wizard starts on "Paste text".
export function initialFields({ prefs, config, selectedProfile, resumes }) {
  const p = prefs || {};
  const stored = Array.isArray(resumes) && resumes.length > 0 ? resumes[0] : null;
  const countries = Array.isArray(p.countries) && p.countries.length > 0 ? p.countries : ["US"];
  const configuredTarget = config && config.config && config.config.default_model_target;
  const configuredWindow = config && config.config ? config.config.max_age_days : null;
  return {
    // screen 1
    profileMode: selectedProfile ? "update" : "new",
    profileName: selectedProfile ? selectedProfile.label || "" : "",
    resumeMode: stored ? "existing" : "paste",
    resumeText: "",
    uploadName: null,
    // The uploaded file's own bytes (base64), sent as they are on Finish.
    uploadBase64: null,
    existingRef: stored,
    // uat-bug-038: the one model choice. The saved default target, else Codex
    // (no availability check is reported to the wizard; a missing CLI shows as
    // the extraction's own "not available on PATH" message).
    modelTarget: MODEL_TARGETS.includes(configuredTarget) ? configuredTarget : "codex_cli",
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
    workMode: initialWorkMode(prefs, config),
    city: typeof p.city === "string" && !isLocationPlaceholder(p.city) ? p.city : "",
    visaSponsorshipRequired: Boolean(p.visa_sponsorship_required),
    maxAgeDays: Number.isInteger(configuredWindow) && configuredWindow >= 1 ? clampMaxAgeDays(configuredWindow) : DEFAULT_MAX_AGE_DAYS,
    // screen 2 (0110-013): the PDF title/layout draft (resumeDisplayModel's
    // draft), loaded from GET /api/resume-display when the step is first
    // opened; null until then. `displaySkipped` leaves the saved one untouched.
    display: null,
    displaySkipped: false,
    // company lists: no wizard screen (0.1.11.2); Settings edits them, a save passes them through
    excludeCompanies: p.exclude_companies || [],
    watchCompanies: p.watch_companies || [],
    // Not asked (A2: Discover is hidden in 0.1.9). PUT /api/setup still
    // takes them, so the saved values, or the defaults, are sent as they are.
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
//
// uat-bug-020 (A1): with no profile selected, GET /api/config's
// resume_preview is the newest stored resume -- the one `gigai scout resume
// add` stored before any profile existed. No profile pins it, so it was
// missing from this list and the wizard could not use it; it is listed
// first, with `profileLabel: null`.
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
  if (preview && preview.record_id && preview.revision_id && !seen.has(`${preview.record_id}/${preview.revision_id}`)) {
    const added = shortDate(config.resume_created_at);
    items.unshift({
      key: `${preview.record_id}/${preview.revision_id}`,
      record_id: preview.record_id,
      revision_id: preview.revision_id,
      name: config.resume_label || "Stored resume",
      date: added ? `added ${added}` : null,
      profileLabel: null,
    });
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
  if (step === 3) {
    return fields.titles.length > 0 && fields.countries.length > 0 && fields.maxAgeDays >= 1;
  }
  return true;
}

// POST /api/resume/extract's body for the resume on screen 1.
//   pasted / uploaded:        its text (`resume_text`)
//   existing, used by a profile: that profile (`profile_id`); the route
//                              reads the profile's pinned resume
//   existing, used by none:   the stored resume itself (`resume_ref`). This
//                              is the resume `gigai scout resume add` stored
//                              before any profile existed (A1); the route
//                              reads its text on this machine.
// The text goes only to the chosen model (`model_target`) in all three.
export function extractBody(fields, profiles) {
  const body = { model_target: fields.modelTarget };
  if (fields.resumeMode !== "existing") {
    body.resume_text = fields.resumeText;
    return body;
  }
  const ref = fields.existingRef;
  const owner = (profiles || []).find(
    (item) =>
      item.resume_ref &&
      ref &&
      item.resume_ref.record_id === ref.record_id &&
      item.resume_ref.revision_id === ref.revision_id,
  );
  if (owner) {
    body.profile_id = owner.profile_id;
  } else {
    body.resume_ref = { record_id: ref.record_id, revision_id: ref.revision_id };
  }
  return body;
}

// A file's bytes as base64, for POST /api/resumes' `content_base64`.
export function bytesToBase64(bytes) {
  let binary = "";
  const step = 0x8000;
  for (let index = 0; index < bytes.length; index += step) {
    binary += String.fromCharCode.apply(null, bytes.subarray(index, index + step));
  }
  return btoa(binary);
}

// The bodies the Finish step sends (wizardFinish.js).
//
// uat-bug-020: POST /api/resumes' body -- the pasted text, or the uploaded
// file's name and bytes. Null when an existing resume was chosen (nothing
// to store).
export function resumeBody(fields) {
  if (fields.resumeMode === "existing") {
    return null;
  }
  if (fields.resumeMode === "upload" && fields.uploadName && fields.uploadBase64) {
    return { file_name: fields.uploadName, content_base64: fields.uploadBase64 };
  }
  return { text: fields.resumeText };
}

// `resumeRef` is the resume Finish just stored (POST /api/resumes'
// `resume_ref`), or the existing one chosen on screen 1.
export function profileBody(fields, resumeRef) {
  const body = {
    label: fields.profileName.trim(),
    titles: fields.titles,
    titles_to_avoid: fields.titlesToAvoid,
  };
  const ref = resumeRef || (fields.resumeMode === "existing" ? fields.existingRef : null);
  if (ref && ref.record_id && ref.revision_id) {
    body.resume_record_id = ref.record_id;
    body.resume_revision_id = ref.revision_id;
  }
  return body;
}

// Which profile Finish writes to: its id, or null to create one.
//   1. "Update <selected>" on screen 1: that profile.
//   2. A profile with this name and this resume already exists: that one.
//      It is what an earlier Finish made (one that failed after the profile
//      was saved), so pressing Finish again never makes a second copy.
//   3. No profile was selected when the wizard opened, and one is now: the
//      default profile the server makes once the first resume is stored
//      (profile_records.ensure_default_profile). It becomes this profile.
export function profileToUpdate({ fields, selectedAtLoad, profiles, selectedProfileId, resumeRef }) {
  if (fields.profileMode === "update" && selectedAtLoad) {
    return selectedAtLoad.profile_id;
  }
  const active = (profiles || []).filter((profile) => profile.state !== "archived");
  const label = fields.profileName.trim();
  const same = active.find(
    (profile) =>
      profile.label === label &&
      resumeRef &&
      profile.resume_ref &&
      profile.resume_ref.record_id === resumeRef.record_id &&
      profile.resume_ref.revision_id === resumeRef.revision_id,
  );
  if (same) {
    return same.profile_id;
  }
  if (!selectedAtLoad) {
    const selected = active.find((profile) => profile.profile_id === selectedProfileId);
    if (selected && selected.origin === "migrated_default") {
      return selected.profile_id;
    }
  }
  return null;
}

// `existingPrefs` is GET /api/setup's saved prefs (or null): the S23 fields
// this wizard no longer asks about (company stage/size, industries,
// must-have/deal-breaker stack -- the dropped questions 9/10) pass through
// unchanged so an edit never wipes them; a first run sends them empty.

export function setupBody(fields, existingPrefs) {
  const prev = existingPrefs || {};
  return {
    roles: fields.titles,
    titles_to_avoid: fields.titlesToAvoid,
    countries: fields.countries,
    work_mode: fields.workMode,
    city: usesArea(fields.workMode) ? fields.city.trim() || null : null,
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
    // uat-bug-038: the one model choice is also the run's default target.
    model_target: fields.modelTarget,
  };
}

// 0110-013: whether Finish saves the Resume display step: it was opened
// (a draft exists) and not skipped. Skipping leaves the saved one alone.
export function shouldSaveDisplay(fields) {
  return Boolean(fields.display) && !fields.displaySkipped;
}

// PUT /api/resume-display's body for the step, for the profile Finish saved
// (this profile's title goes with it); null when the step is not saved.
export function displayBody(fields, profileId) {
  return shouldSaveDisplay(fields) ? buildPutBody(fields.display, profileId) : null;
}

// The Review step's "PDF layout" value.
export function displayReviewText(fields) {
  if (fields.displaySkipped || !fields.display) {
    return "(skipped: the saved title and layout stay as they are)";
  }
  return layoutLine(fields.display) || "(empty)";
}

export function reviewRows(fields, resumes) {
  const list = (values) => (values.length ? values.join(", ") : "(none)");
  const workMode = WORK_MODES.find((mode) => mode.value === fields.workMode);
  const workModeText =
    (workMode ? workMode.label : fields.workMode) +
    (usesArea(fields.workMode) && fields.city.trim() ? ` · ${fields.city.trim()}` : "");
  const extractor = fields.extraction
    ? `${fields.extraction.extractor} (${fields.extraction.resolved_target || fields.extraction.model_target})`
    : "not run";
  return [
    ["Profile", `${fields.profileName.trim() || "(unnamed)"}${fields.profileMode === "update" ? " (update)" : " (new)"}`],
    ["Resume", resumeSummary(fields, resumes)],
    ["PDF layout", displayReviewText(fields)],
    ["Extracted by", extractor],
    ["Tech stack", list(fields.stack)],
    ["Seniority", list(fields.seniority)],
    ["Target titles", list(fields.titles)],
    ["Titles to avoid", list(fields.titlesToAvoid)],
    ["Countries", list(fields.countries)],
    ["Work mode", workModeText],
    ["Visa sponsorship required", fields.visaSponsorshipRequired ? "Yes (postings are labelled, never filtered)" : "No"],
    ["Posting age", `last ${clampMaxAgeDays(fields.maxAgeDays)} days`],
  ];
}

// uat-bug-020: what is still missing before a search, as [{id, text,
// command, note}] lines -- one line per missing thing and nothing else.
// `keys` is GET /api/secrets/status' `keys` ({service: true|false}), or
// null when it could not be read: a key is named only when the server said
// it is not set. The service names are the ones `gigai secrets add` accepts
// (secrets_catalog.py); keys are added from the CLI only.
//
// Ollama: named only when it is the chosen model and nothing in this
// session shows it running. An extraction that just answered through
// `ollama_local` does show it, so the line is left out then.
export function setupHints({ modelTarget, keys, extraction, exaEnabled = false }) {
  const unset = (service) => Boolean(keys) && keys[service] === false;
  const lines = [];
  if (modelTarget === "openrouter_api" && unset("openrouter")) {
    lines.push({
      id: "openrouter",
      text: "OpenRouter key not set",
      command: "gigai secrets add openrouter",
      note: "needed for the model you chose",
    });
  }
  const ollamaAnswered = Boolean(extraction) && extraction.model_target === "ollama_local";
  if (modelTarget === "ollama_local" && !ollamaAnswered) {
    lines.push({
      id: "ollama",
      text: "Ollama must be running for the model you chose",
      command: "ollama serve",
      note: null,
    });
  }
  // uat-bug-033: Exa is an optional extra, off unless the saved config turns
  // it on: its key is named only then.
  if (exaEnabled && unset("exa")) {
    lines.push({ id: "exa", text: "Exa key not set", command: "gigai secrets add exa", note: "optional: finds companies on the open web" });
  }
  return lines;
}

// 0110-024 P4 / 0110-025 R4 / 0110-026 S3: Settings' "Background updates",
// as pure functions (no React, no fetch) so the rules run under node.
//
// GET / PUT /api/settings/background (find_jobs/background_settings.py):
//   readable   false when the settings file exists and cannot be read: every
//              background job is then off and a PUT answers 409
//   settings   what the file says (a key it does not hold shows its default):
//              sources {auto_refresh}, tagging {model_enabled,
//              backfill_enabled, tag_backfill_model configured | haiku |
//              openai}, snapshot {enabled, manifest_url}
//   effective  the same after the environment overrides, each block with
//              `source` default | setting | environment | settings_unreadable
// A PUT names only the keys to change; `snapshot.manifest_url: null` puts the
// default address back. Nothing is saved until the Save button.

export const MAX_MANIFEST_URL_LENGTH = 2048;

export const BACKFILL_MODEL_LABELS = {
  configured: "The model Scout is set up with",
  haiku: "Claude Haiku (through the Claude CLI)",
  openai: "OpenAI (your OpenAI API target)",
};

export const AUTO_REFRESH_HELP =
  "While Scout is open, the company boards are checked about once an hour. Turning this off also pauses tagging titles with the model in the background.";
export const MODEL_TAGS_HELP = "Titles the built-in rules cannot place are sent to the model, only for the roles your profiles search for.";
export const BACKFILL_HELP = "Also tags every other stored title, a few at a time. Off unless you turn it on: it sends many more titles to the model.";
export const SNAPSHOT_HELP =
  "Update sources first downloads a shared starter file (company boards, posting titles and their tags; no descriptions), so a new install does not start from nothing. Nothing about you is sent.";
export const UNREADABLE_WARNING =
  "The settings file on this machine cannot be read, so every background job is off and nothing can be saved here. Fix or remove the file, then reload this page.";

function block(value) {
  return value && typeof value === "object" ? value : {};
}

// The form's draft, from what the FILE says (never the overridden values:
// saving must not write an environment override into the file).
export function draftFromResponse(response) {
  const settings = block(response && response.settings);
  const sources = block(settings.sources);
  const tagging = block(settings.tagging);
  const snapshot = block(settings.snapshot);
  return {
    autoRefresh: sources.auto_refresh !== false,
    modelEnabled: tagging.model_enabled !== false,
    backfillEnabled: tagging.backfill_enabled === true,
    backfillModel: typeof tagging.tag_backfill_model === "string" ? tagging.tag_backfill_model : "configured",
    snapshotEnabled: snapshot.enabled !== false,
    manifestUrl: typeof snapshot.manifest_url === "string" ? snapshot.manifest_url : "",
  };
}

// The choices of "tag the rest with": configured and haiku; openai only when
// it is already what is saved or what runs (it needs an OpenAI target the
// page cannot set up).
export function backfillModelOptions(response, draft) {
  const effective = block(block(response && response.effective).tagging).tag_backfill_model;
  const stored = block(block(response && response.settings).tagging).tag_backfill_model;
  const values = ["configured", "haiku"];
  if (effective === "openai" || stored === "openai" || (draft && draft.backfillModel === "openai")) {
    values.push("openai");
  }
  return values.map((value) => ({ value, label: BACKFILL_MODEL_LABELS[value] }));
}

// "" for a usable manifest address (empty = the default one), else why not.
// The same rule the server applies: http(s), a host, at most 2,048 characters.
export function manifestUrlError(text) {
  const url = String(text == null ? "" : text).trim();
  if (url === "") {
    return "";
  }
  const problem = `The address must be an http(s) URL of at most ${MAX_MANIFEST_URL_LENGTH.toLocaleString("en-US")} characters, or empty for the default one.`;
  if (url.length > MAX_MANIFEST_URL_LENGTH) {
    return problem;
  }
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return problem;
  }
  return (parsed.protocol === "http:" || parsed.protocol === "https:") && parsed.host ? "" : problem;
}

export function formError(draft) {
  return manifestUrlError(draft.manifestUrl);
}

// The PUT body: only what differs from the loaded file. Null when nothing
// changed (Save stays off: the server refuses an empty change).
export function buildPatch(draft, response) {
  const loaded = draftFromResponse(response);
  const body = {};
  const put = (name, key, value) => {
    body[name] = { ...(body[name] || {}), [key]: value };
  };
  if (draft.autoRefresh !== loaded.autoRefresh) {
    put("sources", "auto_refresh", draft.autoRefresh);
  }
  if (draft.modelEnabled !== loaded.modelEnabled) {
    put("tagging", "model_enabled", draft.modelEnabled);
  }
  if (draft.backfillEnabled !== loaded.backfillEnabled) {
    put("tagging", "backfill_enabled", draft.backfillEnabled);
  }
  if (draft.backfillModel !== loaded.backfillModel) {
    put("tagging", "tag_backfill_model", draft.backfillModel);
  }
  if (draft.snapshotEnabled !== loaded.snapshotEnabled) {
    put("snapshot", "enabled", draft.snapshotEnabled);
  }
  const url = draft.manifestUrl.trim();
  if (url !== loaded.manifestUrl.trim()) {
    put("snapshot", "manifest_url", url === "" ? null : url);
  }
  return Object.keys(body).length > 0 ? body : null;
}

const ON_OFF = (value) => (value ? "on" : "off");

// What the page says when the environment decides a block, keyed by block:
//   {sources, tagging, snapshot}  each "" or one sentence
// The saved value still shows in the form and can be changed; it applies
// once the variable is gone.
export function overrideNotes(response) {
  const effective = block(response && response.effective);
  const settings = block(response && response.settings);
  const notes = { sources: "", tagging: "", snapshot: "" };
  const env = (name) => block(effective[name]).source === "environment";
  if (env("sources")) {
    notes.sources = `Set by the environment (GIGAI_SCOUT_AUTO_REFRESH): automatic checks are ${ON_OFF(effective.sources.auto_refresh)} now, whatever is saved here.`;
  }
  if (env("tagging")) {
    notes.tagging = `Set by the environment (GIGAI_SCOUT_MODEL_TAGS): tagging with the model is ${ON_OFF(effective.tagging.model_enabled)} now, whatever is saved here.`;
  }
  if (env("snapshot")) {
    const stored = block(settings.snapshot);
    const parts = [];
    if (effective.snapshot.enabled !== stored.enabled) {
      parts.push(`the starter file is ${ON_OFF(effective.snapshot.enabled)} now`);
    }
    if (effective.snapshot.manifest_url !== stored.manifest_url && typeof effective.snapshot.manifest_url === "string") {
      parts.push(`its address is ${effective.snapshot.manifest_url}`);
    }
    notes.snapshot = `Set by the environment (GIGAI_SCOUT_SNAPSHOT / GIGAI_SCOUT_SNAPSHOT_MANIFEST_URL): ${parts.length ? parts.join(" and ") : "the saved values are overridden"}, whatever is saved here.`;
  }
  return notes;
}

// True when the page must not offer Save: the stored file cannot be read.
export function isUnreadable(response) {
  return Boolean(response) && response.readable === false;
}

// With automatic checks off (saved or forced), the model-tagging switches do
// nothing: said next to them.
export function taggingPausedNote(draft, response) {
  const effective = block(block(response && response.effective).sources);
  const forcedOff = effective.source === "environment" && effective.auto_refresh === false;
  if (!draft.autoRefresh || forcedOff) {
    return "Paused while automatic checks are off.";
  }
  return "";
}

// What a refused load or save says.
export function saveErrorText(error) {
  const code = error && error.code;
  if (code === "settings_unreadable") {
    return UNREADABLE_WARNING;
  }
  if (code === "target_unavailable") {
    return "No Scout project is set up on this machine yet, so there are no background settings to save.";
  }
  return (error && (error.detail || error.message)) || "The settings could not be saved.";
}

// 0110-022: a profile's own search settings (location, work mode, countries,
// posted window). Pure (no React, no fetch), so the rules run under node.
//
// GET /api/profiles gives each profile `is_default` and `search_settings`
// (null = it uses the default's), plus `default_search_settings`: what the
// default profile searches with (the setup settings). The default profile is
// edited in Preferences, never here. `location` is the search area; it is not
// the contact line of the Resume display.
import { MAX_AGE_DAYS_MAXIMUM, WORK_MODES } from "./wizard/wizardState.js";

const EMPTY = { location: null, work_mode: "any", countries: [], max_age_days: null };

export function hasOwnSettings(profile) {
  return Boolean(profile && !profile.is_default && profile.search_settings);
}

// What a run for this profile searches with: its own, else the default's.
export function effectiveSettings(profile, defaults) {
  return (hasOwnSettings(profile) && profile.search_settings) || defaults || EMPTY;
}

export function workModeLabel(value) {
  const mode = WORK_MODES.find((item) => item.value === value);
  return mode ? mode.label : "Any";
}

export function windowLabel(days) {
  return Number.isInteger(days) ? `last ${days} days` : "same window as the default profile";
}

// One line for the profile list and the detail panel.
export function settingsSummary(profile, defaults) {
  const settings = effectiveSettings(profile, defaults);
  const parts = [
    settings.location || "no area",
    workModeLabel(settings.work_mode),
    settings.countries && settings.countries.length > 0 ? settings.countries.join(", ") : "any country",
  ];
  if (hasOwnSettings(profile)) {
    parts.push(windowLabel(settings.max_age_days));
  }
  return parts.join(" · ");
}

export function settingsSource(profile) {
  if (profile && profile.is_default) {
    return "Default profile: uses the settings from Preferences.";
  }
  return hasOwnSettings(profile) ? "This profile's own settings." : "Same as the default profile.";
}

// The edit form, prefilled with what the profile searches with now.
export function initialSettingsForm(profile, defaults) {
  const settings = effectiveSettings(profile, defaults);
  return {
    location: settings.location || "",
    workMode: settings.work_mode || "any",
    countries: [...(settings.countries || [])],
    maxAgeDays: hasOwnSettings(profile) && Number.isInteger(settings.max_age_days) ? String(settings.max_age_days) : "",
  };
}

// An empty window keeps the default profile's; else 1..365.
export function settingsFormError(form) {
  const text = String(form.maxAgeDays).trim();
  if (text === "") {
    return null;
  }
  const days = Number(text);
  if (!Number.isInteger(days) || days < 1 || days > MAX_AGE_DAYS_MAXIMUM) {
    return `Posted window must be 1 to ${MAX_AGE_DAYS_MAXIMUM} days, or empty for the default profile's.`;
  }
  return null;
}

// PUT /api/profiles/{id} body: every key of search_settings.
export function settingsBody(form) {
  const text = String(form.maxAgeDays).trim();
  return {
    search_settings: {
      location: form.location.trim() || null,
      work_mode: form.workMode,
      countries: form.countries,
      max_age_days: text === "" ? null : Number(text),
    },
  };
}

// Back to "same as the default profile".
export function clearSettingsBody() {
  return { search_settings: null };
}

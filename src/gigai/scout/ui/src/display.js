// Display-only helpers for scout find-jobs results. Nothing here changes or
// re-interprets a DTO field's meaning; these are purely presentational
// transforms of contract-shaped data already validated by contracts.py.

// A company name looks like a raw ATS board slug (e.g. "customerio",
// "onetrust", "gongio") when it has no spaces and no uppercase letters,
// which is how Exa-sourced rows currently carry `company` (U20). A real
// display name ("Customer.io", "OneTrust") already has spacing/casing, so
// it passes through unchanged.
const SLUG_LIKE = /^[a-z0-9][a-z0-9-]*$/;

export function displayCompanyName(company) {
  if (!company) {
    return company;
  }
  if (!SLUG_LIKE.test(company)) {
    return company;
  }
  return company
    .split("-")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

// PinnedResume (contracts.py) currently carries only record_id/revision_id/
// content_sha256 — no human label or source filename. U17 asked for the
// resume's label/filename to show instead of raw ids; until the contract
// grows a field for that (see the note in .orchestrator/workers/p3-ui.md),
// this falls back to the ids so the UI still reads as "resume used" rather
// than opaque record noise.
export function resumeDisplayLabel(pinnedResume) {
  if (!pinnedResume) {
    return null;
  }
  return `${pinnedResume.record_id} (${pinnedResume.revision_id})`;
}

const NOT_ASSESSED_REASON_LABELS = {
  unchanged: "Already seen with no content change.",
  duplicate: "Duplicate of another posting in this batch.",
  failed: "Acquisition failed for this posting.",
  over_cap: "Assessment cap was reached before this posting.",
  role_mismatch: "Title did not match the configured role filter.",
  no_resume: "No resume was pinned for this run.",
  model_unavailable: "The model target was unavailable.",
  model_denied: "The model target was denied (missing credentials or consent).",
  location_mismatch: "Location did not match the configured country/location filter.",
  sponsorship_excluded: "Posting does not offer visa sponsorship, which this config requires.",
  model_output_invalid: "The model's answer for this posting could not be parsed.",
};

export function notAssessedReasonLabel(reason) {
  return NOT_ASSESSED_REASON_LABELS[reason] || reason;
}

const SPONSORSHIP_LABELS = {
  offered: "Sponsorship offered",
  not_offered: "No sponsorship",
  unknown: "Sponsorship unknown",
};

export function sponsorshipLabel(sponsorship) {
  if (!sponsorship) {
    return SPONSORSHIP_LABELS.unknown;
  }
  return SPONSORSHIP_LABELS[sponsorship] || sponsorship;
}

export { NOT_ASSESSED_REASON_LABELS };

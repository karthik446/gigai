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

// PinnedResume (contracts.py) itself still carries only record_id/
// revision_id/content_sha256 — no human label or source filename (U17).
// uat-bug-004: GET /api/config now also returns the resume reference's
// label and created date alongside those ids (resume_label/
// resume_created_at, additive — see present_api.py's resume_metadata /
// run.resolve_newest_resume_details), so the Configuration card can show
// e.g. "kar-omada-staff-resume.md · added Sep 23" instead of raw record/
// revision ids. Falls back to the ids when a label isn't available (older
// server, or a reference missing that field) so the card never renders
// blank.
export function resumeDisplayLabel(pinnedResume, label, createdAt) {
  if (!pinnedResume) {
    return null;
  }
  const idFallback = `${pinnedResume.record_id} (${pinnedResume.revision_id})`;
  if (!label) {
    return idFallback;
  }
  const dateText = formatShortDate(createdAt);
  return dateText ? `${label} · added ${dateText}` : label;
}

// The ids to show in a tooltip (title attribute) alongside the friendly
// resumeDisplayLabel text.
export function resumeIdsTooltip(pinnedResume) {
  if (!pinnedResume) {
    return undefined;
  }
  return `${pinnedResume.record_id} (${pinnedResume.revision_id})`;
}

function formatShortDate(isoDate) {
  if (!isoDate) {
    return null;
  }
  const parsed = new Date(isoDate);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" }).format(parsed);
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

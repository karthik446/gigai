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

// uat-bug-009: plain-word labels for the not-assessed reason each card shows
// -- the operator's own words ("Over cap", "Filtered: location",
// "Duplicate") in place of the longer sentences below, which stay as the
// tooltip/detail text (see notAssessedReasonDetail). "Unchanged since
// <date>" is built separately, from the carried-forward entry's
// from_run_date, by unchangedSinceLabel -- a bare "unchanged" reason with no
// carried-forward date (shouldn't normally happen after this fix, but an
// older run dir or a resume-revision-unknown case can still produce one)
// falls back to this table's plain "Unchanged".
const NOT_ASSESSED_REASON_LABELS = {
  unchanged: "Unchanged",
  duplicate: "Duplicate",
  failed: "Acquisition failed",
  over_cap: "Over cap",
  role_mismatch: "Filtered: role",
  no_resume: "No resume pinned",
  model_unavailable: "Model unavailable",
  model_denied: "Model denied",
  location_mismatch: "Filtered: location",
  region_only: "Filtered: location",
  sponsorship_excluded: "Filtered: sponsorship",
  model_output_invalid: "Model output invalid",
};

// The longer, original sentence form -- kept for a detail/tooltip line
// under the short plain-word badge above.
const NOT_ASSESSED_REASON_DETAILS = {
  unchanged: "Already seen with no content change.",
  duplicate: "Duplicate of another posting in this batch.",
  failed: "Acquisition failed for this posting.",
  over_cap: "Assessment cap was reached before this posting.",
  role_mismatch: "Title did not match the configured role filter.",
  no_resume: "No resume was pinned for this run.",
  model_unavailable: "The model target was unavailable.",
  model_denied: "The model target was denied (missing credentials or consent).",
  location_mismatch: "Location did not match the configured country/location filter.",
  region_only: "Location is a region label (e.g. AMER/EMEA/APAC/APJ), not a specific country.",
  sponsorship_excluded: "Posting does not offer visa sponsorship, which this config requires.",
  model_output_invalid: "The model's answer for this posting could not be parsed.",
};

export function notAssessedReasonLabel(reason) {
  return NOT_ASSESSED_REASON_LABELS[reason] || reason;
}

export function notAssessedReasonDetail(reason) {
  return NOT_ASSESSED_REASON_DETAILS[reason] || reason;
}

// uat-bug-009: "Unchanged since <date>" for a posting carried forward from
// an earlier run's successful assessment -- falls back to the plain
// "Unchanged" badge when no date is available (e.g. the earlier run's
// outputs/assess.json mtime couldn't be read).
export function unchangedSinceLabel(fromRunDate) {
  const dateText = formatShortDate(fromRunDate);
  return dateText ? `Unchanged since ${dateText}` : "Unchanged";
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

// P2/P9: the verdict enum (contracts.py's Verdict) in plain words, for the
// card-level badge PostingCard/AssessmentBody show alongside the matrix.
const VERDICT_LABELS = {
  matched_above_threshold: "Matched",
  pending_user_answers: "Needs your answer",
  not_a_match: "Not a match",
};

export function verdictLabel(verdict) {
  if (!verdict) {
    return null;
  }
  return VERDICT_LABELS[verdict] || verdict;
}

// P9c: "2 hours ago"/"3 days ago"-style copy for a run's created_at, for the
// dashboard's "last run" line and the Profiles run-history table (mockup's
// lastRun.when). Falls back to the raw ISO string for an unparseable date
// rather than hiding the field.
export function relativeTimeLabel(isoDate) {
  if (!isoDate) {
    return "unknown time";
  }
  const parsed = new Date(isoDate);
  if (Number.isNaN(parsed.getTime())) {
    return isoDate;
  }
  const diffMs = Date.now() - parsed.getTime();
  const diffMinutes = Math.round(diffMs / 60000);
  if (diffMinutes < 1) {
    return "just now";
  }
  if (diffMinutes < 60) {
    return `${diffMinutes} minute${diffMinutes === 1 ? "" : "s"} ago`;
  }
  const diffHours = Math.round(diffMinutes / 60);
  if (diffHours < 24) {
    return `${diffHours} hour${diffHours === 1 ? "" : "s"} ago`;
  }
  const diffDays = Math.round(diffHours / 24);
  return `${diffDays} day${diffDays === 1 ? "" : "s"} ago`;
}

export { NOT_ASSESSED_REASON_LABELS };

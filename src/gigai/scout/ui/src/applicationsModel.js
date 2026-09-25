// Q4a-nav: pure rules over GET /api/applications rows, shared by the Jobs
// summary strip and the Applications view (they used to live in the
// Dashboard view, which the top bar replaced).
//
// The 5-stage funnel is a display-only fold of `application_events.
// EVENT_KINDS` (mockups/README.md open question #4): every row keeps its
// real `event_kind`; only the bucket is a UI-side rollup. "Needs action"
// is a plain read over the same data: an applied/interview event with no
// update in 10+ days (no prep status or interview-date modelling exists).
export const PIPELINE_STAGES = [
  { key: "saved", label: "Saved", kinds: ["saved"] },
  { key: "applied", label: "Applied", kinds: ["applied"] },
  { key: "interviewing", label: "Interviewing", kinds: ["interview_scheduled"] },
  { key: "offer", label: "Offer", kinds: ["offer_received"] },
  { key: "closed", label: "Closed", kinds: ["rejected", "withdrawn"] },
];

export const STALE_DAYS = 10;

export const EVENT_KIND_LABELS = {
  saved: "Saved",
  applied: "Applied",
  interview_scheduled: "Interview scheduled",
  offer_received: "Offer received",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};

export function eventKindLabel(kind) {
  return EVENT_KIND_LABELS[kind] || kind;
}

// `current` (projection.py's own field): the latest, non-superseded event
// per posting identity -- never double-counts a corrected/superseded row.
export function currentApplications(applications) {
  return (applications || []).filter((item) => item.current !== false);
}

export function pipelineCounts(applications) {
  const counts = Object.fromEntries(PIPELINE_STAGES.map((stage) => [stage.key, 0]));
  for (const item of currentApplications(applications)) {
    const stage = PIPELINE_STAGES.find((candidate) => candidate.kinds.includes(item.event_kind));
    if (stage) {
      counts[stage.key] += 1;
    }
  }
  return counts;
}

export function daysSince(iso, now = Date.now()) {
  const occurred = new Date(iso);
  return Number.isNaN(occurred.getTime()) ? null : Math.floor((now - occurred.getTime()) / 86400000);
}

export function needsAction(applications, now = Date.now()) {
  return currentApplications(applications)
    .filter((item) => ["applied", "interview_scheduled"].includes(item.event_kind))
    .map((item) => ({ ...item, daysAgo: daysSince(item.occurred_at, now) }))
    .filter((item) => item.daysAgo === null || item.daysAgo >= STALE_DAYS)
    .sort((a, b) => (b.daysAgo ?? 0) - (a.daysAgo ?? 0));
}

// What to call an application row: its linked find-jobs posting's title
// (A1's join, `linked_posting`, null when unmatched), else the raw ref.
export function applicationTitle(item) {
  return (item.linked_posting && item.linked_posting.title) || item.external_ref || item.opportunity_ref || "(unknown posting)";
}

export function applicationCompany(item) {
  return (item.linked_posting && item.linked_posting.company) || "";
}

// Newest first by occurred_at.
export function sortApplications(applications) {
  return (applications || []).slice().sort((a, b) => (b.occurred_at || "").localeCompare(a.occurred_at || ""));
}

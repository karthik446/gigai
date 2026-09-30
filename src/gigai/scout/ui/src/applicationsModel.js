// Q4a-nav: pure rules over GET /api/applications rows.
//
// uat-bug-018: the pipeline (which job is applied, interviewing, ...) is
// the job's derived STATE now (jobStateModel.js, from each row's
// `job_state`); what stays here is the event list's own words and order.
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

// Newest first by occurred_at.
export function sortApplications(applications) {
  return (applications || []).slice().sort((a, b) => (b.occurred_at || "").localeCompare(a.occurred_at || ""));
}

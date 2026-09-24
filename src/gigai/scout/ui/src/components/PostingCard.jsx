import AssessmentBody from "./AssessmentBody.jsx";
import SponsorshipBadge from "./SponsorshipBadge.jsx";
import { displayCompanyName, notAssessedReasonDetail, notAssessedReasonLabel, unchangedSinceLabel } from "../display.js";

// One card per posting (B4: operator "load ui sooner, one search -> assess
// load, as a card"). `row` is the normalized shape `buildBoardRows` produces
// in ResultsView.jsx: acquisition fields are always present (a card exists
// the moment its posting is acquired); `status` drives what's shown below
// the header, and `assessment` is only present once assess actually
// finishes for this posting -- never waited on to render the card itself.
//
// uat-bug-009: "carried_forward" is a posting skipped this run as unchanged
// because a successful earlier assessment for it already exists (same
// content digest, same resume revision) -- `row.assessment` is that earlier
// result and `row.fromRunDate` names which run it came from, so the card
// shows the carried fit/reasons instead of a bare "Not assessed" (see
// boardRows.js's rowsFromResults).
const STATUS_LABELS = {
  acquired: "Waiting to be assessed…",
  assessing: "Assessing…",
  assessed: "Assessed",
  failed: "Assessment failed",
  not_assessed: "Not assessed",
  carried_forward: "Unchanged",
};

export default function PostingCard({ row }) {
  const { posting, status, assessment, notAssessedReason, fromRunDate } = row;
  const statusLabel = status === "carried_forward" ? unchangedSinceLabel(fromRunDate) : STATUS_LABELS[status] || status;

  return (
    <details className="posting-card" open={status === "assessed" || status === "carried_forward"}>
      <summary>
        <div className="posting-card-heading">
          <a
            href={posting.url}
            target="_blank"
            rel="noreferrer"
            onClick={(event) => event.stopPropagation()}
          >
            {posting.title || "(untitled posting)"}
          </a>
          <span className={`status-badge posting-status-${status}`}>{statusLabel}</span>
        </div>
        <div className="posting-card-meta">
          <span>{displayCompanyName(posting.company)}</span>
          {posting.location && <span>{posting.location}</span>}
          {posting.source_kind && <span className="muted">{posting.source_kind}</span>}
          <SponsorshipBadge sponsorship={assessment?.sponsorship || posting.sponsorship} />
        </div>
      </summary>

      {status === "assessing" && <p className="muted">The model is assessing this posting…</p>}

      {status === "not_assessed" && notAssessedReason && (
        <p className="muted" title={notAssessedReasonDetail(notAssessedReason)}>
          {notAssessedReasonLabel(notAssessedReason)}
        </p>
      )}

      {status === "failed" && (
        <p className="muted">{notAssessedReason ? notAssessedReasonLabel(notAssessedReason) : "The model call for this posting failed."}</p>
      )}

      {assessment && <AssessmentBody assessment={assessment} />}
    </details>
  );
}

import AssessmentBody from "./AssessmentBody.jsx";
import SponsorshipBadge from "./SponsorshipBadge.jsx";
import PrepPanel from "./PrepPanel.jsx";
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
//
// P6/P9 (v0.1.9, additive/optional): `rankScore` is this posting's Jev
// pre-rank (RankScore, jev_contracts.py) when the caller has one (only
// FindJobsView does, via POST /api/runs/{id}/rank) -- shows fit/score plus
// its category-id reasons/mismatch flags (Jev has no free-text output).
// `profileId` + `showPrep` gate the "Prep for interview" panel, shown only
// on an assessed card with a real posting URL.
const STATUS_LABELS = {
  acquired: "Waiting to be assessed…",
  assessing: "Assessing…",
  assessed: "Assessed",
  failed: "Assessment failed",
  not_assessed: "Not assessed",
  carried_forward: "Unchanged",
};

function RankBadge({ rankScore }) {
  if (!rankScore || rankScore.fit === null || rankScore.fit === undefined) {
    return null;
  }
  const cls = rankScore.fit === "strong" ? "met" : rankScore.fit === "no" ? "unmet" : "unclear";
  return (
    <span className={`status-badge ${cls}`} title={rankScore.reasons.join(", ")}>
      Jev: {rankScore.fit}
      {typeof rankScore.score === "number" ? ` (${rankScore.score})` : ""}
    </span>
  );
}

export default function PostingCard({ row, rankScore, profileId, showPrep }) {
  const { posting, status, assessment, notAssessedReason, fromRunDate } = row;
  const statusLabel = status === "carried_forward" ? unchangedSinceLabel(fromRunDate) : STATUS_LABELS[status] || status;
  const jobIdentity = posting.normalized_url || null;

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
          <RankBadge rankScore={rankScore} />
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

      {rankScore && rankScore.mismatch_flags && rankScore.mismatch_flags.length > 0 && (
        <p className="muted">Jev mismatch flags: {rankScore.mismatch_flags.join(", ")}</p>
      )}

      {assessment && <AssessmentBody assessment={assessment} jobIdentity={jobIdentity} />}

      {showPrep && status === "assessed" && posting.url && <PrepPanel postingUrl={posting.url} profileId={profileId} />}
    </details>
  );
}

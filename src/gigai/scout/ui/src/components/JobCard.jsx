import JevBadge from "./JevBadge.jsx";
import VerdictChip from "./VerdictChip.jsx";
import SponsorshipBadge from "./SponsorshipBadge.jsx";
import ProviderBadge from "./ProviderBadge.jsx";
import QuickAssessChip from "./QuickAssessChip.jsx";
import { displayCompanyName, unchangedSinceLabel } from "../display.js";
import { ageLabel, jevReasonsLine, notAssessedLine, payLabel, requirementSummary, workModeLabel } from "../jobModel.js";
import { jobHash } from "../routing.js";

// Q4a: one hiring.cafe-style card per posting (mockups/cards-and-job-page.html).
// A plain <a href="#/jobs/…"> so the browser back button returns to the grid.
//
// Every field comes from a real response (see jobModel.js's header):
// title/company/location/provider/published_at from the posting row, the
// Jev tile from POST /api/runs/{id}/rank, the verdict chip from the latest
// assessment (run or quick store), the sponsorship chip from the
// assessment's read falling back to the posting's (shown only when
// find-jobs.json visa_sponsorship_required is true, operator amendment),
// "Unchanged since" from carried_forward_assessments (uat-bug-009).
// Q4a-nav-r1: an on-demand job (quick-assess store, status "on_demand")
// shows the "Quick assess" source chip where a run posting shows its
// provider badge; nothing else on the card differs.
//
// Operator answer 2: the requirement summary only after assess; an
// unassessed card shows Jev's reasons instead (or the not-assessed reason
// when Jev never scored it). Operator answer 3: work-mode/pay chips only
// when the posting lists them (phase 2 fields) -- no "not listed" chips.
function RequirementSummary({ assessment }) {
  const { shown, more } = requirementSummary(assessment);
  if (shown.length === 0) {
    return null;
  }
  return (
    <ul className="req-summary">
      {shown.map((row) => (
        <li key={row.requirement} title={row.requirement}>
          <span className={`dot ${row.status}`} />
          <span className="txt">{row.requirement}</span>
        </li>
      ))}
      {more > 0 && (
        <li className="more">
          <span className="dot" style={{ visibility: "hidden" }} />
          <span className="txt">
            + {more} more requirement{more === 1 ? "" : "s"}
          </span>
        </li>
      )}
    </ul>
  );
}

function UnassessedSummary({ job }) {
  const reasons = jevReasonsLine(job.rank);
  return (
    <ul className="req-summary">
      <li className="muted">
        <span className="txt">{notAssessedLine(job)}</span>
      </li>
      {reasons && (
        <li className="muted" title={reasons}>
          <span className="txt">Jev: {reasons}</span>
        </li>
      )}
    </ul>
  );
}

export default function JobCard({ job, visaRequired }) {
  const { posting } = job;
  const dimmed = job.verdict === "not_a_match" || (job.rank && job.rank.fit === "no");
  const mode = workModeLabel(posting);
  const pay = payLabel(posting.pay);

  return (
    <a className={`job-card${dimmed ? " dimmed" : ""}`} href={jobHash(job.id)} data-job-id={job.id}>
      <div className="card-top">
        <div style={{ minWidth: 0 }}>
          <div className="card-title">{posting.title || "(untitled posting)"}</div>
          <div className="card-company">
            <span>{displayCompanyName(posting.company)}</span>
            {posting.location && (
              <>
                <span>·</span>
                <span>{posting.location}</span>
              </>
            )}
            {job.status === "on_demand" ? <QuickAssessChip fetchKind={job.quick && job.quick.job && job.quick.job.fetch_kind} /> : <ProviderBadge posting={posting} />}
          </div>
        </div>
        <JevBadge rank={job.rank} />
      </div>

      <div className="card-chips">
        {mode && <span className="mode-chip">{mode}</span>}
        {pay && <span className="pay">{pay}</span>}
        <span className="card-age" title={posting.published_at || undefined}>
          {job.status === "on_demand" ? "assessed on demand" : ageLabel(posting.published_at)}
        </span>
      </div>

      <div className="card-chips">
        <VerdictChip verdict={job.verdict} assessment={job.assessment} />
        {visaRequired && <SponsorshipBadge sponsorship={job.sponsorship} h1bFilings={posting.h1b_filings_fy2024} />}
        {job.status === "carried_forward" && <span className="tag">{unchangedSinceLabel(job.fromRunDate)}</span>}
      </div>

      {job.assessment ? <RequirementSummary assessment={job.assessment} /> : <UnassessedSummary job={job} />}
    </a>
  );
}

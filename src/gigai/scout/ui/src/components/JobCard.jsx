import JevBadge from "./JevBadge.jsx";
import VerdictChip from "./VerdictChip.jsx";
import SponsorshipBadge from "./SponsorshipBadge.jsx";
import ProviderBadge from "./ProviderBadge.jsx";
import QuickAssessChip from "./QuickAssessChip.jsx";
import StateChip from "./StateChip.jsx";
import { displayCompanyName, unchangedSinceLabel } from "../display.js";
import { ageLabel, assessedAt, jevReasonsLine, notAssessedLine, payLabel, requirementSummary, workModeLabel } from "../jobModel.js";
import { assessmentHash, jobHash } from "../routing.js";

// Q4a: one hiring.cafe-style card per posting (mockups/cards-and-job-page.html).
// A plain <a href="#/jobs/…"> so the browser back button returns to the grid.
//
// Every field comes from a real response (see jobModel.js's header):
// title/company/location/provider/published_at from the posting row, the
// Jev tile from the run's stored scores (and a "Score with Jev" pass), the verdict chip from the latest
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
// when the posting lists them (Q4b fields: posting.work_mode, posting.pay;
// rows[].h1b goes on the sponsorship chip) -- no "not listed" chips.
//
// uat-batch2 (uat-bug-016): a card on the Assessments page opens the same
// job page under #/assessments/<id> (`from="assessments"`), so the top bar
// and the page's back link stay on Assessments. A quick assessment with no
// Jev score carries why (job.rankSkipReason) in the tile's tooltip; a run
// posting carries its run's reason (`jevSkipWords`, ui-pass).
//
// uat-bug-018: a job that has a tailored resume, or is applied or beyond,
// says so in a second chip beside its verdict (StateChip; the verdict
// states are the verdict chip itself).
//
// uat-batch1 (O1/O2): Jev's reasons read as words (jobModel.jevReasonsLine,
// never the raw ids); the card is tighter -- the age joins the mode/pay
// line instead of a row of its own, the title clamps to two lines -- and
// every card in the grid has the same height (styles.css .card-grid).
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

// "assessed today" / "assessed 3d ago", from the store's own timestamps.
function onDemandAge(job) {
  const at = assessedAt(job);
  return at ? `assessed ${ageLabel(at)}` : "assessed on demand";
}

export default function JobCard({ job, visaRequired, from, jevSkipWords }) {
  const { posting } = job;
  const dimmed = job.verdict === "not_a_match" || (job.rank && job.rank.fit === "no");
  const mode = workModeLabel(posting);
  const pay = payLabel(posting.pay);
  const companyLine = [displayCompanyName(posting.company), posting.location].filter(Boolean).join(" · ");

  return (
    <a className={`job-card${dimmed ? " dimmed" : ""}`} href={from === "assessments" ? assessmentHash(job.id) : jobHash(job.id)} data-job-id={job.id}>
      <div className="card-top">
        <div style={{ minWidth: 0 }}>
          <div className="card-title" title={posting.title || undefined}>
            {posting.title || "(untitled posting)"}
          </div>
          <div className="card-company" title={companyLine}>
            {companyLine}
          </div>
        </div>
        <JevBadge rank={job.rank} skipReason={job.rankSkipReason} runSkipWords={jevSkipWords} />
      </div>

      <div className="card-meta">
        {job.status === "on_demand" ? <QuickAssessChip fetchKind={job.quick && job.quick.job && job.quick.job.fetch_kind} /> : <ProviderBadge posting={posting} />}
        {mode && <span className="mode-chip">{mode}</span>}
        {pay && <span className="pay">{pay}</span>}
        <span className="card-age" title={posting.published_at || undefined}>
          {job.status === "on_demand" ? onDemandAge(job) : ageLabel(posting.published_at)}
        </span>
      </div>

      <div className="card-chips">
        <VerdictChip verdict={job.verdict} assessment={job.assessment} />
        <StateChip state={job.state} />
        {visaRequired && <SponsorshipBadge sponsorship={job.sponsorship} h1b={job.h1b} />}
        {job.status === "carried_forward" && <span className="tag">{unchangedSinceLabel(job.fromRunDate)}</span>}
      </div>

      {job.assessment ? <RequirementSummary assessment={job.assessment} /> : <UnassessedSummary job={job} />}
    </a>
  );
}

import RankBadge from "./RankBadge.jsx";
import VerdictChip from "./VerdictChip.jsx";
import SponsorshipBadge from "./SponsorshipBadge.jsx";
import ProviderBadge from "./ProviderBadge.jsx";
import QuickAssessChip from "./QuickAssessChip.jsx";
import StateChip from "./StateChip.jsx";
import { displayCompanyName, unchangedSinceLabel } from "../display.js";
import { ageLabel, assessedAt, notAssessedLine, payLabel, requirementSummary, whyPassedLine, workModeChip } from "../jobModel.js";
import { olderSettingsChip } from "../jobStateModel.js";
import { rankBlockersLine, rankReasonsLine } from "../rankModel.js";
import { assessmentHash, jobHash } from "../routing.js";

// Q4a: one hiring.cafe-style card per posting (mockups/cards-and-job-page.html).
// A plain <a href="#/jobs/…"> so the browser back button returns to the grid.
//
// Every field comes from a real response (see jobModel.js's header):
// title/company/location/provider/published_at from the posting row, the
// rank tile from the model's rank (the row's `rank`, or a re-rank pass's
// score: RankBadge), the verdict chip from the latest
// assessment (run or quick store), the sponsorship chip from the
// assessment's read falling back to the posting's (shown only when
// find-jobs.json visa_sponsorship_required is true, operator amendment),
// "Unchanged since" from carried_forward_assessments (uat-bug-009).
// Q4a-nav-r1: an on-demand job (quick-assess store, status "on_demand")
// shows the "Quick assess" source chip where a run posting shows its
// provider badge; nothing else on the card differs.
//
// Operator answer 2: the requirement summary only after assess; an
// unassessed card shows the model's reasons for its rank instead, and a
// blocker the model named (a demoted posting stays visible and says why). Operator answer 3: work-mode/pay chips only
// when the posting lists them (Q4b fields: posting.work_mode, posting.pay;
// rows[].h1b goes on the sponsorship chip) -- no "not listed" chips.
// uat-bug-028: the mode chip also shows a mode read from the location text
// (workModeChip, marked derived), and a line under the company says why the
// posting passed the run's work mode + area (whyPassedLine).
//
// uat-batch2 (uat-bug-016): a card on the Assessments page opens the same
// job page under #/assessments/<id> (`from="assessments"`), so the top bar
// and the page's back link stay on Assessments. An on-demand assessment
// has no rank tile unless a run's card lends it one.
//
// uat-bug-018: a job that has a stored resume, or is applied or beyond,
// says so in a second chip beside its verdict (StateChip; the verdict
// states are the verdict chip itself).
//
// uat-batch1 (O1/O2): the rank's reasons read as words (rankModel, never
// raw ids); the card is tighter -- the age joins the mode/pay
// line instead of a row of its own, the title clamps to two lines -- and
// every card in the grid has the same height (styles.css .card-grid).
//
// 0110-039: a card whose stored assessment was made with older settings
// (older prompt, changed work mode / countries / location / sponsorship
// need, or a changed story bank) says so in a quiet tag; the job page has
// the one-click Re-assess.
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
  const reasons = rankReasonsLine(job.rank);
  const blockers = rankBlockersLine(job.rank);
  return (
    <ul className="req-summary">
      <li className="muted">
        <span className="txt">{notAssessedLine(job)}</span>
      </li>
      {blockers && (
        <li className="rank-blocker-line" title={blockers} data-role="rank-blockers">
          <span className="txt">{blockers}</span>
        </li>
      )}
      {reasons && (
        <li className="muted" title={reasons} data-role="rank-reasons">
          <span className="txt">Why ranked here: {reasons}</span>
        </li>
      )}
    </ul>
  );
}

// An assessed card keeps its requirement summary; a blocker the model named
// still shows under it.
function BlockerLine({ rank }) {
  const blockers = rankBlockersLine(rank);
  return blockers ? (
    <div className="rank-blocker-line" title={blockers} data-role="rank-blockers">
      {blockers}
    </div>
  ) : null;
}

// "assessed today" / "assessed 3d ago", from the store's own timestamps.
function onDemandAge(job) {
  const at = assessedAt(job);
  return at ? `assessed ${ageLabel(at)}` : "assessed on demand";
}

export default function JobCard({ job, visaRequired, from }) {
  const { posting } = job;
  const dimmed = job.verdict === "not_a_match" || Boolean(job.rank && (job.rank.fit === "no" || job.rank.demoted));
  const mode = workModeChip(job);
  const whyPassed = whyPassedLine(job.workModeFit);
  const pay = payLabel(posting.pay);
  const companyLine = [displayCompanyName(posting.company), posting.location].filter(Boolean).join(" · ");
  const olderSettings = job.assessment ? olderSettingsChip(job) : null;

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
          {whyPassed && (
            <div className="card-company card-why-passed" title="Why this posting passed your work mode and city/area">
              {whyPassed}
            </div>
          )}
        </div>
        <RankBadge rank={job.rank} hideWhenNone={job.status === "on_demand"} />
      </div>

      <div className="card-meta">
        {job.status === "on_demand" ? <QuickAssessChip fetchKind={job.quick && job.quick.job && job.quick.job.fetch_kind} /> : <ProviderBadge posting={posting} />}
        {mode && (
          <span className="mode-chip" title={mode.title} data-derived={mode.derived ? "true" : undefined}>
            {mode.label}
          </span>
        )}
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
        {olderSettings && (
          <span className="tag" data-role="assessment-older-settings" title={olderSettings.title}>
            {olderSettings.label}
          </span>
        )}
      </div>

      {job.assessment ? (
        <>
          <RequirementSummary assessment={job.assessment} />
          <BlockerLine rank={job.rank} />
        </>
      ) : (
        <UnassessedSummary job={job} />
      )}
    </a>
  );
}

import JobsGrid from "../components/JobsGrid.jsx";
import { ASSESS_HASH } from "../routing.js";

// uat-bug-016 (operator UAT N21): Assessments (#/assessments). Every
// on-demand assessment for the selected profile, newest first, as the same
// card the Jobs grid shows; each opens the same job page
// (#/assessments/<id>). "+ Assess a job" lives here; Jobs shows search
// (run) results only.
//
// uat-batch2-r1: on demand ONLY. A posting a search found stays under Jobs
// when it is assessed or re-assessed from its job page; it is never a card
// here too.
//
// `jobs` is jobModel.assessmentJobs() over the quick-assess store
// (GET /api/assessments?profile_id=…), built by FindJobsView, which owns
// the store's state so a new assessment shows here at once.
export default function AssessmentsView({ jobs, loading, profileLabel, visaRequired }) {
  return (
    <div>
      <section className="panel jobs-header">
        <div className="jobs-header-row">
          <h2>
            Assessments <span className="muted">{profileLabel}</span>
          </h2>
          <div className="jobs-header-actions">
            <a className="button" href={ASSESS_HASH} data-action="assess">
              + Assess a job
            </a>
          </div>
        </div>
        <div className="jobs-header-meta">
          <p className="muted" style={{ margin: 0 }}>
            Postings you assessed on demand with "+ Assess a job", newest first. A posting a search found stays under Jobs, with every
            assessment you run from its page.
          </p>
        </div>
      </section>

      {loading && jobs.length === 0 && (
        <div className="panel">
          <p className="muted" style={{ margin: 0 }}>
            Loading assessments…
          </p>
        </div>
      )}

      {!loading && jobs.length === 0 && (
        <div className="panel" data-role="assessments-empty">
          <p className="muted" style={{ margin: 0 }}>
            No assessments yet for this profile. Paste a posting's address or its text under <a href={ASSESS_HASH}>+ Assess a job</a>.
          </p>
        </div>
      )}

      {jobs.length > 0 && <JobsGrid jobs={jobs} visaRequired={visaRequired} from="assessments" emptyMessage="No assessments yet." />}
    </div>
  );
}

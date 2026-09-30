import { useEffect, useMemo, useState } from "react";
import { getAssessments } from "../api.js";
import StateChip from "../components/StateChip.jsx";
import { displayCompanyName, relativeTimeLabel } from "../display.js";
import { dateLabel } from "../jobModel.js";
import { applicationJobs, applicationIdentity, namesFromAssessments, stateLabel, stateOptions } from "../jobStateModel.js";
import { assessmentHash, jobHash } from "../routing.js";
import { eventKindLabel, sortApplications } from "../applicationsModel.js";

// uat-bug-018: Applications (#/applications) is the "applied and beyond"
// view: one row per JOB whose state is applied, interview scheduled, offer
// received, rejected or withdrawn, with state chips that count them.
//
// The rows are GET /api/applications' own (App.jsx's useApplications): each
// carries `job_state`, the job's state from its application events
// (find_jobs/job_state.py), and `linked_posting` when a run found the
// posting. A job no run found (a quick assessment, a pasted posting) gets
// its title from the quick-assess store (GET /api/assessments, read here).
// A job that was only saved has no application state and is not listed.
//
// The next step for a job is recorded on its own page (the job's title
// links there): a run posting's under Jobs, a pasted posting's under
// Assessments. "Every event" below is the full record, newest first,
// saved and corrected events included.
function jobLink(job) {
  return job.pasted ? assessmentHash(job.identity) : jobHash(job.identity);
}

function StateFilter({ options, value, onChange }) {
  return (
    <div className="chip-list" data-role="state-filter">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className={`chip${value === option.value ? " active" : ""}`}
          aria-pressed={value === option.value}
          data-state={option.value}
          onClick={() => onChange(option.value)}
        >
          {option.label} <span className="chip-count">{option.count}</span>
        </button>
      ))}
    </div>
  );
}

export default function ApplicationsView({ applications, loading, error, reload }) {
  const [names, setNames] = useState(() => new Map());
  const [filter, setFilter] = useState("all");

  useEffect(() => {
    let live = true;
    getAssessments()
      .then((response) => live && setNames(namesFromAssessments(response.items)))
      .catch(() => {
        /* titles are a nicety: a job is still listed without one */
      });
    return () => {
      live = false;
    };
  }, []);

  const jobs = useMemo(() => applicationJobs(applications, { known: names }), [applications, names]);
  const options = useMemo(() => stateOptions(jobs.map((job) => ({ state: { state: job.state } })), filter), [jobs, filter]);
  const shown = useMemo(() => (filter === "all" ? jobs : jobs.filter((job) => job.state === filter)), [jobs, filter]);
  const waiting = useMemo(() => jobs.filter((job) => job.needsAction), [jobs]);
  const events = useMemo(() => sortApplications(applications), [applications]);
  const titles = useMemo(() => new Map(jobs.map((job) => [job.identity, job])), [jobs]);
  const firstLoad = loading && (applications || []).length === 0;

  return (
    <div>
      <section className="panel">
        <h2>Applications</h2>
        <p className="muted">Every job you applied to, and what happened next. Record the next step on the job's own page.</p>
        {firstLoad && <p className="muted">Loading applications…</p>}
        {error && (
          <div className="callout danger">
            Could not load applications: {error}{" "}
            <button className="button small secondary" onClick={reload}>
              Retry
            </button>
          </div>
        )}
        {!firstLoad && !error && jobs.length === 0 && (
          <p className="muted" data-role="applications-empty">
            No applications yet. "Mark applied" on a job's page records the first one.
          </p>
        )}
        {!firstLoad && !error && jobs.length > 0 && (
          <>
            <StateFilter options={options} value={filter} onChange={setFilter} />
            {waiting.length > 0 && (
              <p className="callout info" data-role="needs-action">
                {waiting.length === 1 ? "1 job has" : `${waiting.length} jobs have`} had no news for 10 days or more:{" "}
                {waiting.map((job, index) => (
                  <span key={job.identity}>
                    {index > 0 && ", "}
                    <a href={jobLink(job)}>{job.title}</a>
                  </span>
                ))}
                .
              </p>
            )}
            <table className="data-table applications-table" data-role="applications-jobs">
              <thead>
                <tr>
                  <th>Job</th>
                  <th>State</th>
                  <th>Since</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((job) => (
                  <tr key={job.identity} data-state={job.state}>
                    <td>
                      <a href={jobLink(job)}>{job.title}</a>
                      {job.company && <div className="muted small">{displayCompanyName(job.company)}</div>}
                    </td>
                    <td>
                      <StateChip state={{ state: job.state, since: job.since }} always />
                    </td>
                    <td title={job.since || undefined}>
                      {dateLabel(job.since) || "–"}
                      {job.since && <div className="muted small">{relativeTimeLabel(job.since)}</div>}
                    </td>
                  </tr>
                ))}
                {shown.length === 0 && (
                  <tr>
                    <td colSpan={3} className="muted">
                      No job is {stateLabel(filter).toLowerCase()} right now.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </>
        )}
      </section>

      {!firstLoad && !error && events.length > 0 && (
        <section className="panel">
          <h3>Every event</h3>
          <table className="data-table applications-table" data-role="applications-events">
            <thead>
              <tr>
                <th>Job</th>
                <th>Event</th>
                <th>When</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {events.map((item) => {
                const identity = applicationIdentity(item);
                const job = titles.get(identity) || null;
                const named = names.get(identity) || null;
                const title =
                  (item.linked_posting && item.linked_posting.title) ||
                  (job && job.title) ||
                  (named && named.title) ||
                  (identity && identity.startsWith("text:") ? "Pasted posting" : identity || "Posting");
                const company = (item.linked_posting && item.linked_posting.company) || (job && job.company) || (named && named.company) || "";
                const href = item.external_ref ? (item.external_ref.startsWith("text:") ? assessmentHash(item.external_ref) : jobHash(item.external_ref)) : null;
                return (
                  <tr key={item.event_id} className={item.current === false ? "superseded" : ""}>
                    <td>
                      {href ? <a href={href}>{title}</a> : title}
                      {company && <div className="muted small">{displayCompanyName(company)}</div>}
                    </td>
                    <td>
                      {eventKindLabel(item.event_kind)}
                      {item.current === false && <div className="muted small">corrected later</div>}
                    </td>
                    <td title={item.occurred_at || undefined}>
                      {dateLabel(item.occurred_at) || "–"}
                      <div className="muted small">{relativeTimeLabel(item.occurred_at)}</div>
                    </td>
                    <td className="muted">{item.notes || ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

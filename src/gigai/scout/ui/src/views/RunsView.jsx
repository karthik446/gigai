import { relativeTimeLabel } from "../display.js";
import { dateTimeLabel } from "../jobModel.js";
import { JOBS_HASH, runHash } from "../routing.js";

// Q4a-nav: Runs (#/runs) -- every find-jobs run for the selected profile,
// newest first, from GET /api/runs?profile_id=<selected> (P9c: date /
// found / new / assessed / matched / status are the run's own fields;
// cost/duration have no source field and stay out). Each row opens
// #/runs/<run_id>, the run page (status, node receipts, its postings).
// Starting a run happens on Jobs ("Run find jobs"), where the config and
// the consent dialog live.
export default function RunsView({ profile, runs, loading, error, reload }) {
  return (
    <div>
      <section className="panel">
        <h2>
          Runs {profile && <span className="muted">{profile.label}</span>}
        </h2>
        <p className="muted">
          Every find-jobs run for this profile, newest first. Start a new one from <a href={JOBS_HASH}>Jobs</a>.
        </p>
        {!profile && <p className="muted">Select a profile first.</p>}
        {profile && loading && <p className="muted">Loading runs…</p>}
        {profile && error && (
          <div className="callout danger">
            Could not load runs: {error}{" "}
            <button className="button small secondary" onClick={reload}>
              Retry
            </button>
          </div>
        )}
        {profile && !loading && !error && runs.length === 0 && <p className="muted">No runs yet for this profile.</p>}
        {profile && !loading && !error && runs.length > 0 && (
          <table className="data-table runs-table">
            <thead>
              <tr>
                <th>Run</th>
                <th>Found</th>
                <th>New</th>
                <th>Assessed</th>
                <th>Matched</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.run_id}>
                  <td>
                    <a href={runHash(run.run_id)} title={run.run_id}>
                      {relativeTimeLabel(run.created_at)}
                    </a>
                    <div className="muted small">{dateTimeLabel(run.created_at) || run.run_id}</div>
                  </td>
                  <td>{run.counts.found}</td>
                  <td>{run.counts.new}</td>
                  <td>{run.counts.assessed}</td>
                  <td>{run.counts.matched}</td>
                  <td>
                    <span className={`run-status ${run.status}`}>{run.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

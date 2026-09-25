import { useMemo } from "react";
import { relativeTimeLabel } from "../display.js";
import { dateLabel } from "../jobModel.js";
import { jobHash } from "../routing.js";
import {
  PIPELINE_STAGES,
  applicationCompany,
  applicationTitle,
  eventKindLabel,
  needsAction,
  pipelineCounts,
  sortApplications,
} from "../applicationsModel.js";

// Q4a-nav: Applications (#/applications) -- GET /api/applications' own
// rows: the 5-stage pipeline and "needs action" the Dashboard used to show
// (P9c), plus every recorded event, newest first. A row linked to a
// find-jobs posting (A1's `linked_posting`, external_ref = normalized_url)
// links to its job page; an unlinked one still shows (never dropped).
export default function ApplicationsView({ applications, loading, error, reload }) {
  const counts = useMemo(() => pipelineCounts(applications), [applications]);
  const actionable = useMemo(() => needsAction(applications), [applications]);
  const rows = useMemo(() => sortApplications(applications), [applications]);

  return (
    <div>
      <section className="panel">
        <h2>Applications</h2>
        {loading && <p className="muted">Loading applications…</p>}
        {error && (
          <div className="callout danger">
            Could not load applications: {error}{" "}
            <button className="button small secondary" onClick={reload}>
              Retry
            </button>
          </div>
        )}
        {!loading && !error && (
          <>
            <div className="pipeline-row">
              {PIPELINE_STAGES.map((stage) => (
                <div className="pipeline-stage" key={stage.key}>
                  <div className="stage-count">{counts[stage.key]}</div>
                  <div className="stage-label">{stage.label}</div>
                </div>
              ))}
            </div>

            <h3>Needs action</h3>
            {actionable.length === 0 ? (
              <p className="muted">Nothing needs a follow-up right now.</p>
            ) : (
              <div className="profile-list">
                {actionable.map((item) => (
                  <div key={item.event_id} className="action-item">
                    <div>
                      <div style={{ fontWeight: 600 }}>
                        {item.external_ref ? <a href={jobHash(item.external_ref)}>{applicationTitle(item)}</a> : applicationTitle(item)}
                      </div>
                      <div className="muted" style={{ fontSize: "0.8rem" }}>
                        {eventKindLabel(item.event_kind)}
                        {item.daysAgo !== null ? `, ${item.daysAgo} day${item.daysAgo === 1 ? "" : "s"} ago, no update` : ""}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </section>

      {!loading && !error && (
        <section className="panel">
          <h3>Every event</h3>
          {rows.length === 0 ? (
            <p className="muted">No applications recorded yet. "Mark applied" on a job page records the first one.</p>
          ) : (
            <table className="data-table applications-table">
              <thead>
                <tr>
                  <th>Posting</th>
                  <th>Event</th>
                  <th>When</th>
                  <th>Notes</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => (
                  <tr key={item.event_id} className={item.current === false ? "superseded" : ""}>
                    <td>
                      {item.external_ref ? <a href={jobHash(item.external_ref)}>{applicationTitle(item)}</a> : applicationTitle(item)}
                      {applicationCompany(item) && <div className="muted small">{applicationCompany(item)}</div>}
                    </td>
                    <td>
                      {eventKindLabel(item.event_kind)}
                      {item.current === false && <div className="muted small">superseded</div>}
                    </td>
                    <td title={item.occurred_at || undefined}>
                      {dateLabel(item.occurred_at) || "–"}
                      <div className="muted small">{relativeTimeLabel(item.occurred_at)}</div>
                    </td>
                    <td className="muted">{item.notes || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
    </div>
  );
}

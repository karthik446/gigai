import AssessmentCard from "./AssessmentCard.jsx";

const NOT_ASSESSED_REASONS = {
  unchanged: "Already seen with no content change.",
  duplicate: "Duplicate of another posting in this batch.",
  failed: "Acquisition failed for this posting.",
  over_cap: "Assessment cap was reached before this posting.",
  role_mismatch: "Title did not match the configured role filter.",
  no_resume: "No resume was pinned for this run.",
  model_unavailable: "The model target was unavailable.",
  model_denied: "The model target was denied (missing credentials or consent).",
};

function postingsByUrl(payload) {
  const map = new Map();
  for (const row of payload.rows) {
    map.set(row.posting.normalized_url, row.posting);
  }
  return map;
}

export default function ResultsView({ payload }) {
  const postingByUrl = postingsByUrl(payload);

  return (
    <section>
      {payload.pinned_resume && (
        <div className="panel">
          <h2>Resume used</h2>
          <div className="field-row">
            <div className="field">
              <div className="label">Record</div>
              <div className="value">{payload.pinned_resume.record_id}</div>
            </div>
            <div className="field">
              <div className="label">Revision</div>
              <div className="value">{payload.pinned_resume.revision_id}</div>
            </div>
          </div>
        </div>
      )}

      <div className="panel">
        <h2>Postings ({payload.rows.length})</h2>
        {payload.rows.length === 0 ? (
          <p className="muted">No postings yet.</p>
        ) : (
          <table className="postings-table">
            <thead>
              <tr>
                <th>Title</th>
                <th>Company</th>
                <th>Location</th>
                <th>Source</th>
                <th>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {payload.rows.map((row) => (
                <tr key={row.posting.normalized_url}>
                  <td>
                    <a href={row.posting.url} target="_blank" rel="noreferrer">
                      {row.posting.title}
                    </a>
                  </td>
                  <td>{row.posting.company}</td>
                  <td>{row.posting.location || "—"}</td>
                  <td>{row.posting.source_kind}</td>
                  <td>{row.outcome}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel">
        <h2>Assessments ({payload.assessments.length})</h2>
        {payload.assessments.length === 0 ? (
          <p className="muted">No postings were assessed.</p>
        ) : (
          payload.assessments.map((assessment) => (
            <AssessmentCard
              key={assessment.posting.normalized_url}
              assessment={assessment}
              posting={postingByUrl.get(assessment.posting.normalized_url)}
            />
          ))
        )}
      </div>

      <div className="panel">
        <h2>Not assessed ({payload.not_assessed.length})</h2>
        {payload.not_assessed.length === 0 ? (
          <p className="muted">Every candidate posting was assessed.</p>
        ) : (
          <ul className="not-assessed-list">
            {payload.not_assessed.map((entry) => (
              <li key={entry.posting.normalized_url}>
                <strong>{entry.posting.title}</strong> ({entry.posting.company}):{" "}
                {NOT_ASSESSED_REASONS[entry.reason] || entry.reason}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="panel">
        <h2>Failures ({payload.failures.length})</h2>
        {payload.failures.length === 0 ? (
          <p className="muted">No acquisition failures.</p>
        ) : (
          <ul className="failures-list">
            {payload.failures.map((failure, index) => (
              <li key={`${failure.source_kind}-${failure.query_key}-${index}`}>
                <strong>{failure.source_kind}</strong> / {failure.query_key}: {failure.message}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

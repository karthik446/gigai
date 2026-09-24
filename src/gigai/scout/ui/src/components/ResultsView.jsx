import { useMemo } from "react";
import PostingsBoard from "./PostingsBoard.jsx";
import { rowsFromResults } from "../boardRows.js";
import { resumeDisplayLabel, resumeIdsTooltip } from "../display.js";

// B4: the final sealed-results view. Postings/assessments render through the
// same PostingsBoard the live /progress view uses (see boardRows.js), so a
// run finishing doesn't swap the UI to a different layout -- cards already
// on screen just gain their terminal status.
export default function ResultsView({ payload }) {
  const rows = useMemo(() => rowsFromResults(payload), [payload]);

  return (
    <section>
      {payload.pinned_resume && (
        <div className="panel">
          <h2>Resume used</h2>
          <div className="field-row">
            <div className="field">
              <div className="label">Resume</div>
              <div
                className="value"
                title={resumeIdsTooltip(payload.pinned_resume)}
              >
                {resumeDisplayLabel(payload.pinned_resume, payload.resume_label, payload.resume_created_at)}
              </div>
            </div>
          </div>
        </div>
      )}

      <PostingsBoard rows={rows} cap={null} />

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

import MatrixBadge from "./MatrixBadge.jsx";

// The requirement x resume matrix + suggestions + questions body, factored
// out of the original AssessmentCard.jsx (B4) so both the standalone
// AssessmentCard (kept for anything that already has a full assessment in
// hand) and the progressive PostingCard (which may not) render identical
// markup for the assessed state instead of two copies drifting apart.
export default function AssessmentBody({ assessment }) {
  return (
    <>
      <table className="matrix-table">
        <thead>
          <tr>
            <th>Requirement</th>
            <th>Resume evidence</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {(assessment.matrix || []).map((row) => (
            <tr key={row.requirement}>
              <td>{row.requirement}</td>
              <td>
                {row.resume_evidence && row.resume_evidence.length ? (
                  <ul>
                    {row.resume_evidence.map((evidence) => (
                      <li key={evidence}>{evidence}</li>
                    ))}
                  </ul>
                ) : (
                  <span className="muted">none</span>
                )}
              </td>
              <td>
                <MatrixBadge status={row.status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {assessment.suggestions && assessment.suggestions.length > 0 && (
        <>
          <div className="label" style={{ marginTop: 8 }}>
            Suggestions
          </div>
          <ul>
            {assessment.suggestions.map((suggestion) => (
              <li key={suggestion}>{suggestion}</li>
            ))}
          </ul>
        </>
      )}

      {assessment.questions && assessment.questions.length > 0 && (
        <>
          <div className="label">Questions</div>
          <ul>
            {assessment.questions.map((question) => (
              <li key={question}>{question}</li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

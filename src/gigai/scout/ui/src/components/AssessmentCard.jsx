import MatrixBadge from "./MatrixBadge.jsx";
import SponsorshipBadge from "./SponsorshipBadge.jsx";
import { displayCompanyName } from "../display.js";

export default function AssessmentCard({ assessment, posting }) {
  return (
    <details className="assessment-card">
      <summary>
        {posting ? `${posting.title} · ${displayCompanyName(posting.company)}` : assessment.posting.normalized_url}
        <SponsorshipBadge sponsorship={assessment.sponsorship || (posting && posting.sponsorship)} />
      </summary>

      <table className="matrix-table">
        <thead>
          <tr>
            <th>Requirement</th>
            <th>Resume evidence</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {assessment.matrix.map((row) => (
            <tr key={row.requirement}>
              <td>{row.requirement}</td>
              <td>
                {row.resume_evidence.length ? (
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

      {assessment.suggestions.length > 0 && (
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

      {assessment.questions.length > 0 && (
        <>
          <div className="label">Questions</div>
          <ul>
            {assessment.questions.map((question) => (
              <li key={question}>{question}</li>
            ))}
          </ul>
        </>
      )}
    </details>
  );
}

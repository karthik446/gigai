import { useState } from "react";
import MatrixBadge from "./MatrixBadge.jsx";
import { postAnswer } from "../api.js";

// One structured question (P2/P3): its id, the question text, and an inline
// answer box that POSTs /api/answers. `jobIdentity` (when known -- see
// PostingCard's caller) is passed as `reassess.job_identity` so answering
// re-runs the whole assessment immediately and `onAnswered` receives the
// fresh AssessResponse to let the card update its own verdict in place.
function StructuredQuestion({ question, jobIdentity, onAnswered }) {
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    const answer = draft.trim();
    if (!answer) {
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const response = await postAnswer({
        question_id: question.question_id,
        answer,
        reassess: jobIdentity ? { job_identity: jobIdentity } : null,
      });
      setSaved(true);
      setDraft("");
      if (response.reassessed && onAnswered) {
        onAnswered(response.reassessed);
      }
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <li className="structured-question">
      <div>
        {question.question} <code className="question-id">{question.question_id}</code>
      </div>
      {saved ? (
        <p className="muted">Answer saved.</p>
      ) : (
        <form className="answer-form" onSubmit={handleSubmit}>
          <input
            type="text"
            className="text-input"
            placeholder="Your answer…"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            disabled={saving}
          />
          <button type="submit" className="button small" disabled={saving || !draft.trim()}>
            {saving ? "Saving…" : "Answer"}
          </button>
        </form>
      )}
      {error && <div className="field-error">{error}</div>}
    </li>
  );
}

// The requirement x resume matrix + suggestions + questions body, factored
// out of the original AssessmentCard.jsx (B4) so both the standalone
// AssessmentCard (kept for anything that already has a full assessment in
// hand) and the progressive PostingCard (which may not) render identical
// markup for the assessed state instead of two copies drifting apart.
//
// P2/P3 (v0.1.9): `assessment.verdict` and `assessment.structured_questions`
// are additive -- an old assessment result (or a run-path AssessmentResult,
// which carries neither today) renders exactly as before. `jobIdentity` and
// `onAnswered` are optional; pass them (PostingCard does, for a quick-assess
// card) to let a structured question's answer box re-assess in place.
export default function AssessmentBody({ assessment, jobIdentity, onAnswered }) {
  return (
    <>
      {assessment.verdict && (
        <div style={{ marginBottom: 8 }}>
          <MatrixBadge status={assessment.verdict} kind="verdict" />
        </div>
      )}

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

      {/* Structured questions (P2/P3), each with an id + inline answer box.
          Takes priority over the plain-string `questions` list below when
          present -- `questions` is always derived from these server-side
          (assessment_core's normalizer), so showing both would duplicate
          the same text. */}
      {assessment.structured_questions && assessment.structured_questions.length > 0 ? (
        <>
          <div className="label">Questions</div>
          <ul className="structured-question-list">
            {assessment.structured_questions.map((question) => (
              <StructuredQuestion
                key={question.question_id}
                question={question}
                jobIdentity={jobIdentity}
                onAnswered={onAnswered}
              />
            ))}
          </ul>
        </>
      ) : (
        assessment.questions &&
        assessment.questions.length > 0 && (
          <>
            <div className="label">Questions</div>
            <ul>
              {assessment.questions.map((question) => (
                <li key={question}>{question}</li>
              ))}
            </ul>
          </>
        )
      )}
    </>
  );
}

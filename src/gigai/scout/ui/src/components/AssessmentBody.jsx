import { useEffect, useState } from "react";
import MatrixBadge from "./MatrixBadge.jsx";
import { ApiError, postAnswer } from "../api.js";
import { CLASS_LABELS, sortMatrixRows } from "../jobModel.js";

// One structured question (P2/P3): its id, the question text, and an inline
// answer box that POSTs /api/answers. `jobIdentity` (when known -- see
// PostingCard's caller) is passed as `reassess.job_identity` so answering
// re-runs the whole assessment immediately and `onAnswered` receives the
// fresh AssessResponse to let the card update its own verdict in place.
//
// Q4a: `priorAnswer` (from GET /api/answers) pre-fills the box when this
// question was already answered for another posting -- saving records it
// again (an upsert, experience_answers) and re-assesses; the prompt alone
// decides what the answer means (operator answer 5).
//
// Q4a: `onReassessUnavailable` -- a posting assessed only by a find-jobs
// run has no quick-assess store entry yet, so POST /api/answers' reassess
// answers 404 reassess_not_found (answers.py resolves the job through that
// store). The answer itself IS recorded before that lookup, so the job page
// passes a fallback that runs POST /api/assess {job_url} instead: one model
// call, with the just-recorded answer in the prompt like any other prior
// answer, and the result lands in the store (with its history) for every
// later answer to re-assess through /api/answers as usual.
function StructuredQuestion({ question, jobIdentity, onAnswered, priorAnswer, onReassessUnavailable }) {
  const [draft, setDraft] = useState(priorAnswer ? priorAnswer.answer : "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);

  // GET /api/answers usually lands after this box mounted: fill an untouched box then.
  useEffect(() => {
    if (priorAnswer && !draft) {
      setDraft(priorAnswer.answer);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [priorAnswer]);

  async function handleSubmit(event) {
    event.preventDefault();
    const answer = draft.trim();
    if (!answer) {
      return;
    }
    setSaving(true);
    setError(null);
    try {
      let reassessed = null;
      try {
        const response = await postAnswer({
          question_id: question.question_id,
          answer,
          reassess: jobIdentity ? { job_identity: jobIdentity } : null,
        });
        reassessed = response.reassessed;
      } catch (err) {
        if (!(err instanceof ApiError && err.code === "reassess_not_found" && onReassessUnavailable)) {
          throw err;
        }
        reassessed = await onReassessUnavailable();
      }
      setSaved(true);
      setDraft("");
      if (reassessed && onAnswered) {
        onAnswered(reassessed);
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
      {question.requirement && <div className="muted question-requirement">Settles: {question.requirement}</div>}
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
            {saving ? "Saving…" : jobIdentity ? "Save and re-assess" : "Answer"}
          </button>
        </form>
      )}
      {saving && jobIdentity && (
        <p className="reassess-progress">
          <span className="spinner" /> Re-assessing with your answers…
        </p>
      )}
      {!saved && priorAnswer && <p className="muted question-prior">Answered before (for another posting); edit or save as is.</p>}
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
//
// Q4a (operator amendment): this ONE table is also the job page's
// requirement view. Two additions apply everywhere it renders: rows sorted
// unclear + unmet above met (jobModel.sortMatrixRows), and the row's
// requirement class (hard / askable / nice-to-have, RequirementMatrixRow's
// `class`) as a small label next to the status chip when the prompt set it.
// `priorAnswers` (Map question_id -> GET /api/answers row) and
// `onReassessUnavailable` (see StructuredQuestion) are optional.
export default function AssessmentBody({ assessment, jobIdentity, onAnswered, priorAnswers, onReassessUnavailable }) {
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
          {sortMatrixRows(assessment.matrix).map((row) => (
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
                {row.class && <span className="req-class">{CLASS_LABELS[row.class] || row.class}</span>}
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
                priorAnswer={priorAnswers ? priorAnswers.get(question.question_id) : undefined}
                onReassessUnavailable={onReassessUnavailable}
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

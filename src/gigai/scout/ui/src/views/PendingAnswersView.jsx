import { useCallback, useEffect, useMemo, useState } from "react";
import { getAnswers, getAssessments } from "../api.js";
import AssessmentBody from "../components/AssessmentBody.jsx";

// P3/P9: Pending answers. The pending list is DERIVED (operator decision 3,
// plan section 8): every stored quick assessment with verdict
// "pending_user_answers" (GET /api/assessments?verdict=pending_user_answers),
// minus any question_id already answered (GET /api/answers) -- never stored
// or computed server-side. Answering a question re-assesses in place
// (AssessmentBody's own answer box) and the card's verdict updates from the
// response, without a full page reload.
export default function PendingAnswersView() {
  const [state, setState] = useState({ loading: true, items: [], answeredIds: new Set(), error: null });

  const reload = useCallback(() => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    Promise.all([getAssessments({ verdict: "pending_user_answers" }), getAnswers()])
      .then(([assessments, answers]) => {
        setState({
          loading: false,
          items: assessments.items,
          answeredIds: new Set(answers.answers.map((answer) => answer.question_id)),
          error: null,
        });
      })
      .catch((error) => setState({ loading: false, items: [], answeredIds: new Set(), error: error.message || String(error) }));
  }, []);

  useEffect(reload, [reload]);

  // GET /api/assessments?verdict=pending_user_answers already narrows to
  // the right verdict server-side; the client-side pass below only needs
  // to drop a question this profile already answered (P3's derived-list
  // rule) from each card's own list, purely for display -- answering here
  // always re-assesses (AssessmentBody's onAnswered -> reload()), so the
  // server's own verdict is what removes a fully-resolved card, not a
  // client-side guess.
  const cards = useMemo(
    () =>
      state.items.map((item) => {
        const pendingQuestions = (item.result.structured_questions || []).filter(
          (question) => !state.answeredIds.has(question.question_id),
        );
        return { item, result: item.result, pendingQuestions };
      }),
    [state.items, state.answeredIds],
  );

  const stillPending = cards.filter((card) => card.result.verdict === "pending_user_answers" || card.pendingQuestions.length > 0);

  return (
    <div className="panel">
      <h2>Pending answers</h2>
      <p className="muted">
        Quick-assessed postings waiting on your answer to at least one structured question. Answering updates the card's
        verdict in place.
      </p>

      {state.loading && <p className="muted">Loading…</p>}
      {state.error && <div className="callout danger">Could not load pending answers: {state.error}</div>}

      {!state.loading && !state.error && stillPending.length === 0 && (
        <p className="muted">Nothing is waiting on an answer right now.</p>
      )}

      <div className="posting-card-list">
        {stillPending.map(({ item, result }) => (
          <div className="posting-card" key={item.job.job_identity}>
            <div className="posting-card-heading">
              <span>
                {item.job.title || "(pasted text)"} {item.job.company ? `· ${item.job.company}` : ""}
              </span>
            </div>
            <AssessmentBody assessment={result} jobIdentity={item.job.job_identity} onAnswered={() => reload()} />
          </div>
        ))}
      </div>
    </div>
  );
}

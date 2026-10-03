import MatrixBadge from "./MatrixBadge.jsx";
import RequirementActions from "./RequirementActions.jsx";
import { useAnswerDrafts } from "../answerDrafts.js";
import { placeQuestions } from "../answersModel.js";
import { requirementStatusLabel, sortMatrixRows } from "../jobModel.js";
import { suggestionNotice } from "../answersStoriesModel.js";

// One open question, inside the requirement row it settles (uat-batch1 N5):
// the question's own words and an answer box. The id is a normalized token
// (question_ids.py), so it is never shown on its own; it sits in the
// tooltip. Nothing is sent from here: the ONE "Re-assess" above the table
// saves every filled box (answerDrafts.js).
//
// 0110-034: when the profile's story bank holds a near match for the
// question, the box says "We already know: <answer>" with "Use it": that
// fills the box, and the operator confirms it (Re-assess) or edits it
// first. Nothing is filled in or saved without that click.
function QuestionBox({ question, state, value, onChange, disabled, showRequirement = false, onUseSuggestion }) {
  const inputId = `answer-${question.question_id}`;
  const notice = suggestionNotice(state && state.suggestion);
  return (
    <div className="row-question" data-question-id={question.question_id}>
      <label htmlFor={inputId} title={question.question_id}>
        {question.question}
      </label>
      {showRequirement && question.requirement && <p className="muted small question-requirement">About: {question.requirement}</p>}
      <input
        id={inputId}
        type="text"
        className="text-input"
        placeholder="Your answer…"
        value={value}
        onChange={(event) => onChange(question.question_id, event.target.value)}
        disabled={disabled}
      />
      {state && state.recorded && !state.isNew && state.filled && (
        <p className="muted question-prior">Your saved answer. Edit it, or re-assess with it as is.</p>
      )}
      {notice && onUseSuggestion && (
        <div className="question-suggestion" data-role="bank-suggestion">
          <p>{notice.text}</p>
          <p className="muted small">{notice.source}</p>
          <button type="button" className="button small secondary" data-action="use-suggestion" disabled={disabled} onClick={() => onUseSuggestion(question.question_id)}>
            {notice.action}
          </button>
        </div>
      )}
      {state && state.fromBank && <p className="muted question-prior">From your story bank. Edit it if this job needs a different answer, then re-assess.</p>}
    </div>
  );
}

// uat-batch1 (N8, operator decision: option A): a row's class and status
// are one chip, "Must-have: Met" / "Can ask: Unclear" / "Bonus: Met".
function StatusCells({ row }) {
  if (!row) {
    return <td className="muted">–</td>;
  }
  return (
    <td>
      <span className={`status-badge ${row.status}`}>{requirementStatusLabel(row)}</span>
    </td>
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
// which carries neither today) renders exactly as before.
//
// Q4a (operator amendment): this ONE table is also the job page's
// requirement view; rows sort unclear + unmet above met
// (jobModel.sortMatrixRows).
//
// uat-batch1 (N5): the questions are no longer a block under the table.
// Each one sits in the row it settles (rows with a question come first),
// and ONE "Re-assess" above the table saves every filled box and
// re-assesses once. `controller` is a useAnswerDrafts() result when the
// caller needs the same state (the job page gates "Tailor resume" on it);
// without one this component keeps its own. `tailor` (optional) is the
// second action's {enabled, reason, label, busy, onClick}; `showVerdict`
// hides the verdict badge where the page already shows the chip.
//
// uat-bug-027: `questionsFirst` (the job page) supersedes N5's boxes inside
// the table. The open questions get their own section at the top (the task,
// with the same Re-assess / Tailor actions and the same drafts); the table
// below it is the reference, always open (uat-bug-045), and carries no answer boxes. No open
// questions, no questions section. Other callers keep the N5 layout.
export default function AssessmentBody({
  assessment,
  jobIdentity,
  onAnswered,
  priorAnswers,
  onReassessUnavailable,
  controller,
  tailor,
  showVerdict = true,
  questionsFirst = false,
  profileId,
}) {
  const own = useAnswerDrafts({ assessment, jobIdentity, priorAnswers, onAnswered, onReassessUnavailable });
  const answers = controller || own;
  const { rows: questionsByRow, unplaced } = placeQuestions(assessment.matrix, answers.questions);
  const stateFor = new Map(answers.states.map((state) => [state.question_id, state]));
  const sorted = sortMatrixRows(assessment.matrix);
  const ordered = sorted.filter((row) => questionsByRow.has(row.requirement)).concat(sorted.filter((row) => !questionsByRow.has(row.requirement)));
  const hasQuestions = answers.questions.length > 0;
  const busy = Boolean(answers.busy);

  const boxes = (questions) =>
    questions.map((question) => (
      <QuestionBox
        key={question.question_id}
        question={question}
        state={stateFor.get(question.question_id)}
        value={answers.valueFor(question.question_id)}
        onChange={answers.setDraft}
        onUseSuggestion={answers.applySuggestion}
        disabled={busy}
      />
    ));

  const table = (withBoxes) => (
    <table className="matrix-table">
      <thead>
        <tr>
          <th>Requirement</th>
          <th>Resume evidence</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {(withBoxes ? ordered : sorted).map((row) => {
          const questions = withBoxes ? questionsByRow.get(row.requirement) || [] : [];
          return (
            <tr key={row.requirement} className={questions.length ? "has-question" : undefined}>
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
                {boxes(questions)}
              </td>
              <StatusCells row={row} />
            </tr>
          );
        })}
        {/* A question whose requirement names no row still gets its box. */}
        {withBoxes &&
          unplaced.map((question) => (
            <tr key={`question-${question.question_id}`} className="has-question">
              <td>{question.requirement || <span className="muted">Other question</span>}</td>
              <td>{boxes([question])}</td>
              <StatusCells row={null} />
            </tr>
          ))}
      </tbody>
    </table>
  );

  const suggestions = assessment.suggestions && assessment.suggestions.length > 0 && (
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
  );

  const actions = (hasQuestions || tailor) && (
    <RequirementActions
      reassess={{
        ...answers.gate,
        label: jobIdentity ? "Re-assess" : "Save answers",
        busy: answers.busy === "reassess",
        onClick: answers.reassess,
      }}
      tailor={tailor}
      busy={busy || Boolean(tailor && tailor.busy)}
      error={answers.error}
    />
  );

  if (questionsFirst) {
    return (
      <>
        {hasQuestions ? (
          <section className="panel questions-section" data-role="questions-section">
            <h3>Questions for you ({answers.questions.length})</h3>
            <p className="muted small">Answer what you can, then Re-assess once. Your answers are saved with it.</p>
            {answers.questions.map((question) => (
              <QuestionBox
                key={question.question_id}
                question={question}
                state={stateFor.get(question.question_id)}
                value={answers.valueFor(question.question_id)}
                onChange={answers.setDraft}
                onUseSuggestion={answers.applySuggestion}
                disabled={busy}
                showRequirement
              />
            ))}
            {actions}
          </section>
        ) : (
          actions && <section className="panel">{actions}</section>
        )}
        <section className="panel" data-role="requirements-section">
          <h3>Requirements ({assessment.matrix ? assessment.matrix.length : 0})</h3>
          {table(false)}
          {suggestions}
          {!hasQuestions && assessment.questions && assessment.questions.length > 0 && (
            <>
              <div className="label">Questions</div>
              <ul>
                {assessment.questions.map((question) => (
                  <li key={question}>{question}</li>
                ))}
              </ul>
            </>
          )}
        </section>
      </>
    );
  }

  return (
    <>
      {showVerdict && assessment.verdict && (
        <div style={{ marginBottom: 8 }}>
          <MatrixBadge status={assessment.verdict} kind="verdict" />
        </div>
      )}

      {actions}

      {table(true)}

      {suggestions}

      {/* An old result carries plain-string questions only (no id, so no
          answer box); structured ones are in the table above, and
          `questions` is always derived from those server-side
          (assessment_core's normalizer), so showing both would duplicate
          the same text. */}
      {!hasQuestions && assessment.questions && assessment.questions.length > 0 && (
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

import AssessmentBody from "../components/AssessmentBody.jsx";
import { jobHash } from "../routing.js";

// P3/P9 → Q4a-nav: Questions (#/questions). The list is DERIVED (operator
// decision 3, plan section 8): every stored quick assessment with verdict
// "pending_user_answers" (GET /api/assessments?verdict=pending_user_answers),
// minus any question_id already answered (GET /api/answers) -- never stored
// or computed server-side. That derivation now lives in
// hooks.usePendingQuestions, shared with the top bar's badge, so both agree.
// Answering a question re-assesses in place (AssessmentBody's own answer
// box) and the card's verdict updates from the response; the server's own
// verdict is what removes a fully-resolved card, not a client-side guess.
export default function PendingAnswersView({ pending }) {
  const { loading, error, cards, count, reload } = pending;

  return (
    <div className="panel">
      <h2>
        Questions {!loading && !error && <span className="muted">{count === 0 ? "" : `${count} open`}</span>}
      </h2>
      <p className="muted">
        Every open question across your assessed postings. Answering re-assesses that posting with all your answers.
      </p>

      {loading && cards.length === 0 && <p className="muted">Loading…</p>}
      {error && <div className="callout danger">Could not load questions: {error}</div>}

      {!loading && !error && cards.length === 0 && <p className="muted">Nothing is waiting on an answer right now.</p>}

      <div className="posting-card-list">
        {cards.map(({ item, result }) => (
          <div className="posting-card" key={item.job.job_identity}>
            <div className="posting-card-heading">
              <a href={jobHash(item.job.job_identity)}>
                {item.job.title || "(pasted text)"} {item.job.company ? `· ${item.job.company}` : ""}
              </a>
            </div>
            <AssessmentBody assessment={result} jobIdentity={item.job.job_identity} onAnswered={() => reload()} />
          </div>
        ))}
      </div>
    </div>
  );
}

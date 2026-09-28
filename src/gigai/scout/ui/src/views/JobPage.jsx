import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, getAnswers, getApplications, postApplication, postAssess } from "../api.js";
import AssessmentBody from "../components/AssessmentBody.jsx";
import RequirementActions from "../components/RequirementActions.jsx";
import JevBadge from "../components/JevBadge.jsx";
import VerdictChip from "../components/VerdictChip.jsx";
import SponsorshipBadge from "../components/SponsorshipBadge.jsx";
import ProviderBadge from "../components/ProviderBadge.jsx";
import QuickAssessChip from "../components/QuickAssessChip.jsx";
import PrepPanel from "../components/PrepPanel.jsx";
import TailoredResumePanel, { useTailoredResume } from "../components/TailoredResumePanel.jsx";
import { useAnswerDrafts } from "../answerDrafts.js";
import { reassessGate, tailorGate } from "../answersModel.js";
import { displayCompanyName, notAssessedReasonDetail, unchangedSinceLabel } from "../display.js";
import { ageLabel, dateLabel, jdExcerpt, jevReasonsLine, notAssessedLine, payLabel, questionPromptIndex, statusStyleFrom, workModeLabel } from "../jobModel.js";
import { JOBS_HASH } from "../routing.js";

// Q4a: one posting's job page (#/jobs/<normalized_url>), per
// mockups/cards-and-job-page.html with the operator amendment: the
// requirement table is the SAME AssessmentBody the Pending answers view
// renders (Requirement | Resume evidence | Status), unclear/unmet rows first.
//
// uat-batch1 (operator UAT 2026-09-27: "great start, excellent details, but
// too much info"): ONE full-width column.
//   N3  a plain "← Jobs" link at the top (the breadcrumb is gone here)
//   N4  a short excerpt of the posting, no expander: "Open posting" has
//       the rest
//   N5  each open question sits in the requirement row it settles; ONE
//       "Re-assess" at the top of Requirements saves every filled box
//       (answerDrafts.js) and re-assesses once
//   N6  no right-hand column: the verdict history and the tailored-resume
//       explainer are gone; "Re-assess" and "Tailor resume" are the two
//       actions at the top of Requirements, and the tailored-resume panel
//       opens under Requirements
//   N7  both actions are gated (answersModel.js) and say why when off
//
// Data, all from existing routes:
//   header/JD   the run's posting row (GET /api/runs/{id}/results ->
//               rows[].posting; `text` is present when acquire fetched it)
//   assessment  jobModel's latest-of(run's own, quick store) -- see buildJobs
//   answers     POST /api/answers {question_id, answer, reassess}; the
//               `reassessed` AssessResponse replaces the quick item in place
//               (onQuickUpdated), so the verdict, the table and its
//               questions update without a reload. GET /api/answers fills
//               the box of a question already answered for another posting
//               (the prompt decides what it means, operator answer 5 --
//               nothing here maps an answer to a status)
//   assess      POST /api/assess {job:{job_url}} for a not-assessed row
//   applied     GET/POST /api/applications (external_ref = normalized_url)
//   tailored    GET/POST /api/tailored-resumes (Q3), see TailoredResumePanel
//
// Q4b: work_mode / pay (posting) and h1b (the row, via job.h1b) render only
// when present -- no placeholder chips (operator answer 3).
function MarkApplied({ normalizedUrl, applications, onRecorded }) {
  const [state, setState] = useState("idle"); // idle | saving | error
  const [error, setError] = useState(null);
  const applied = (applications || []).find((item) => item.external_ref === normalizedUrl && item.event_kind === "applied");
  if (applied) {
    const when = dateLabel(applied.occurred_at);
    return <span className="status-badge sponsorship-offered">Marked applied{when ? ` ${when}` : ""}</span>;
  }
  return (
    <span>
      <button
        type="button"
        className="button small secondary"
        disabled={state === "saving"}
        onClick={() => {
          setState("saving");
          setError(null);
          postApplication({ normalized_url: normalizedUrl, event_kind: "applied" })
            .then(() => {
              setState("idle");
              onRecorded();
            })
            .catch((err) => {
              setState("idle");
              setError(err.message || String(err));
            });
        }}
      >
        {state === "saving" ? "Marking…" : "Mark applied"}
      </button>
      {error && (
        <span className="muted" style={{ marginLeft: 6, fontSize: "0.8rem" }}>
          {error}
        </span>
      )}
    </span>
  );
}

function OpenPosting({ url, children }) {
  return (
    <a href={url} target="_blank" rel="noreferrer">
      {children} ↗
    </a>
  );
}

function JobDescription({ posting }) {
  const excerpt = jdExcerpt(posting.text);
  if (!excerpt) {
    return (
      <section className="panel">
        <h3>Job description</h3>
        <p className="muted">
          The posting text was not captured for this row. {posting.url && <OpenPosting url={posting.url}>Open the posting</OpenPosting>}
        </p>
      </section>
    );
  }
  return (
    <section className="panel">
      <h3>Job description</h3>
      <p className="jd-excerpt">{excerpt.text}</p>
      {excerpt.truncated && (
        <p className="muted jd-more">
          This is the start of the posting. {posting.url && <OpenPosting url={posting.url}>Open posting for the rest</OpenPosting>}
        </p>
      )}
    </section>
  );
}

function AssessNow({ posting, onAssessed }) {
  const [state, setState] = useState("idle");
  const [error, setError] = useState(null);
  if (!posting.url) {
    return null;
  }
  return (
    <span>
      <button
        type="button"
        className="button small secondary"
        disabled={state === "saving"}
        style={{ marginLeft: 6 }}
        onClick={() => {
          setState("saving");
          setError(null);
          postAssess({ job: { job_url: posting.url } })
            .then((response) => {
              setState("idle");
              onAssessed(response);
            })
            .catch((err) => {
              setState("idle");
              if (err instanceof ApiError && err.status === 504) {
                setError("The model timed out assessing this posting. Try again, or a faster model target.");
              } else {
                setError(err.message || String(err));
              }
            });
        }}
      >
        {state === "saving" ? "Assessing…" : "Assess this posting"}
      </button>
      {error && <div className="field-error">{error}</div>}
    </span>
  );
}

function BackToJobs() {
  return (
    <a className="back-link" href={JOBS_HASH}>
      ← Jobs
    </a>
  );
}

export default function JobPage({ job, jobId, profileId, profileLabel, visaRequired, loading, onQuickUpdated, onApplicationsChanged }) {
  const [answers, setAnswers] = useState([]);
  const [applications, setApplications] = useState([]);
  const [tailorError, setTailorError] = useState(null);

  const reloadApplications = useCallback(() => {
    getApplications()
      .then((response) => setApplications(response.applications || []))
      .catch(() => setApplications([]));
  }, []);
  const handleApplicationRecorded = useCallback(() => {
    reloadApplications();
    if (onApplicationsChanged) {
      onApplicationsChanged();
    }
  }, [reloadApplications, onApplicationsChanged]);

  useEffect(() => {
    getAnswers()
      .then((response) => setAnswers(response.answers || []))
      .catch(() => setAnswers([]));
    reloadApplications();
    setTailorError(null);
  }, [reloadApplications, jobId]);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [jobId]);

  const posting = job ? job.posting : null;
  const assessment = job ? job.assessment : null;
  const jobUrl = posting && posting.url ? posting.url : null;
  const priorAnswers = useMemo(() => new Map(answers.map((answer) => [answer.question_id, answer])), [answers]);
  const assessByUrl = useCallback(() => postAssess({ job: { job_url: jobUrl } }), [jobUrl]);

  // Hooks run on every render, a missing job included (its state is empty).
  const answerDrafts = useAnswerDrafts({
    assessment,
    jobIdentity: posting ? posting.normalized_url : null,
    priorAnswers,
    onAnswered: onQuickUpdated,
    onReassessUnavailable: jobUrl ? assessByUrl : undefined,
  });
  const tailored = useTailoredResume({ jobIdentity: job ? job.id : null, jobUrl, profileId });

  if (!job) {
    return (
      <div>
        <BackToJobs />
        <section className="panel">
          <h2>{loading ? "Loading run…" : "Job not found"}</h2>
          {!loading && (
            <p className="muted">
              No posting with this address in the loaded run: <code>{jobId}</code>
            </p>
          )}
        </section>
      </div>
    );
  }

  const reasons = jevReasonsLine(job.rank);
  const mode = workModeLabel(posting);
  const pay = payLabel(posting.pay);
  // A question is shown by its prompt wherever it appears (the tailored
  // resume's answer refs); the id is secondary detail. Prompts come from
  // the recorded answers and the assessments' own questions.
  const questionPrompts = questionPromptIndex({
    answers,
    assessment,
    assessments: [job.row && job.row.assessment, job.quick && job.quick.result],
  });

  const gate = tailorGate({
    assessed: Boolean(assessment),
    verdict: job.verdict,
    states: answerDrafts.states,
    hasUrl: Boolean(jobUrl),
    hasProfile: Boolean(profileId),
  });
  // Tailoring reads the RECORDED answers, so an answer typed but not yet
  // saved is saved first (no re-assessment); a failed save stops here.
  const handleTailor = () => {
    setTailorError(null);
    answerDrafts
      .saveUnsaved()
      .then(() => tailored.tailor())
      .catch((err) => setTailorError(err.message || String(err)));
  };
  const tailorAction = {
    ...gate,
    enabled: gate.enabled && !tailored.loadingStored,
    label: tailored.tailoring ? "Tailoring…" : tailored.stored ? "Tailor again" : "Tailor resume",
    helpName: "Tailor resume",
    busy: tailored.tailoring,
    onClick: handleTailor,
  };
  const actionsBusy = Boolean(answerDrafts.busy) || tailored.tailoring;

  return (
    <div className="job-page">
      <BackToJobs />

      <section className="panel">
        <div className="job-header">
          <div style={{ minWidth: 0, flex: 1 }}>
            <h2 className="job-title">{posting.title || "(untitled posting)"}</h2>
            <div className="job-sub">
              <strong style={{ color: "var(--text)" }}>{displayCompanyName(posting.company)}</strong>
              {posting.location && <span>{posting.location}</span>}
              {job.status === "on_demand" ? <QuickAssessChip fetchKind={job.quick && job.quick.job && job.quick.job.fetch_kind} /> : <ProviderBadge posting={posting} />}
              {job.status === "on_demand" ? (
                <span title={job.quick && job.quick.created_at ? job.quick.created_at : undefined}>
                  assessed on demand{job.quick && job.quick.created_at ? ` ${dateLabel(job.quick.created_at)}` : ""}
                </span>
              ) : (
                <span title={posting.published_at || undefined}>
                  posted {ageLabel(posting.published_at)}
                  {posting.published_at ? ` (${dateLabel(posting.published_at)})` : ""}
                </span>
              )}
            </div>
            <div className="job-facts">
              {mode && <span className="mode-chip">{mode}</span>}
              {pay && <span className="pay">{pay}</span>}
              <VerdictChip verdict={job.verdict} assessment={assessment} />
              {visaRequired && <SponsorshipBadge sponsorship={job.sponsorship} h1b={job.h1b} />}
              {job.status === "carried_forward" && <span className="tag">{unchangedSinceLabel(job.fromRunDate)}</span>}
            </div>
            {assessment && assessment.not_a_match_reason && (
              <div className="callout danger" style={{ margin: "12px 0 0" }}>
                {assessment.not_a_match_reason}
              </div>
            )}
            {!assessment && (
              <div className="callout info" style={{ margin: "12px 0 0" }} title={job.notAssessedReason ? notAssessedReasonDetail(job.notAssessedReason) : undefined}>
                {notAssessedLine(job)}.
                {job.status !== "assessing" && job.status !== "acquired" && <AssessNow posting={posting} onAssessed={onQuickUpdated} />}
              </div>
            )}
          </div>
          <div className="header-side">
            <JevBadge rank={job.rank} />
            {reasons && <div className="jev-reasons">{reasons}</div>}
          </div>
        </div>
        <div className="job-actions">
          {posting.url && (
            <a className="button secondary small" href={posting.url} target="_blank" rel="noreferrer">
              Open posting ↗
            </a>
          )}
          {posting.url && <MarkApplied normalizedUrl={posting.normalized_url} applications={applications} onRecorded={handleApplicationRecorded} />}
        </div>
      </section>

      <JobDescription posting={posting} />

      <section className="panel">
        <h3>Requirements</h3>
        {assessment ? (
          <AssessmentBody
            assessment={assessment}
            jobIdentity={posting.normalized_url}
            controller={answerDrafts}
            tailor={tailorAction}
            showVerdict={false}
            statusStyle={statusStyleFrom(window.location.search)}
          />
        ) : (
          <>
            <RequirementActions
              reassess={{ ...reassessGate({ assessed: false, states: [] }), label: "Re-assess", onClick: () => {} }}
              tailor={tailorAction}
              busy={actionsBusy}
            />
            <p className="muted">Not assessed yet. The requirement table and its questions appear once the posting is assessed.</p>
          </>
        )}
        {tailorError && <div className="field-error">Could not save your answers before tailoring: {tailorError}</div>}
      </section>

      <TailoredResumePanel state={tailored} profileLabel={profileLabel} questionPrompts={questionPrompts} />

      {assessment && posting.url && <PrepPanel postingUrl={posting.url} profileId={profileId} />}
    </div>
  );
}

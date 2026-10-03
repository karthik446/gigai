import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, getAnswers, getConfig, getRunPosting, postApplication, postAssess } from "../api.js";
import { REQUIREMENTS_UNREADABLE_TEXT, isRequirementsUnreadable } from "../rankModel.js";
import AssessmentBody from "../components/AssessmentBody.jsx";
import RequirementActions from "../components/RequirementActions.jsx";
import RankBadge from "../components/RankBadge.jsx";
import VerdictChip from "../components/VerdictChip.jsx";
import SponsorshipBadge from "../components/SponsorshipBadge.jsx";
import ProviderBadge from "../components/ProviderBadge.jsx";
import QuickAssessChip from "../components/QuickAssessChip.jsx";
import StateChip from "../components/StateChip.jsx";
import PrepPanel from "../components/PrepPanel.jsx";
import TailoredResumePanel, { useTailoredResume } from "../components/TailoredResumePanel.jsx";
import { useAnswerDrafts } from "../answerDrafts.js";
import { reassessGate, tailorGate } from "../answersModel.js";
import { displayCompanyName, notAssessedReasonDetail, unchangedSinceLabel } from "../display.js";
import {
  ORIGIN_JOB_PAGE,
  ageLabel,
  assessOriginFor,
  dateLabel,
  jdExcerpt,
  notAssessedLine,
  payLabel,
  questionPromptIndex,
  storedOrigin,
  workModeLabel,
} from "../jobModel.js";
import { eventActionLabel, jobStateFor, staleAssessmentNote } from "../jobStateModel.js";
import { modelTargetLabel } from "../modelTargets.js";
import { ASSESSMENTS_HASH, JOBS_HASH } from "../routing.js";

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
//   N5  (superseded by uat-bug-027: the open questions are their own section
//       at the TOP, the requirement table below is collapsed reference)
//       each open question sat in the requirement row it settles; ONE
//       "Re-assess" at the top of Requirements saves every filled box
//       (answerDrafts.js) and re-assesses once
//   N6  no right-hand column: the verdict history and the tailored-resume
//       explainer are gone; "Re-assess" and "Tailor resume" are the two
//       actions at the top of Requirements, and the tailored-resume panel
//       opens under Requirements
//   N7  both actions are gated (answersModel.js) and say why when off
//
// uat-batch2:
//   uat-bug-016  opened from Assessments (#/assessments/<id>, `from`), the
//                back link reads "← Assessments"
//   uat-bug-014  a quick-assessed URL job shows its excerpt (the store's
//                posting_text, jobModel.quickOnlyJob); a PASTED job says
//                its text is never stored
//
// SCOPE-ADD-3 D: the header's tile is the model's rank (RankBadge), with
// its reasons and blockers under it and the note that a rank is a guess,
// not a verdict. An on-demand assessment has no rank and no tile.
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
//   assess      POST /api/assess {job:{job_url}, origin} for a not-assessed
//               row; `origin` is "job_page" for a run's posting
//               (jobModel.assessOriginFor), so the result stays under Jobs
//   state       uat-bug-018: the job's derived state (job.state,
//               jobStateModel.js) and a button for each event that may
//               follow it: "Mark applied", then "Interview scheduled",
//               "Offer received", "Rejected", "Withdrawn". Each is a POST
//               /api/applications {job_identity, event_kind}; the server
//               decides what may follow (a 409 says why not, in words).
//               `job_identity` is the job's id, so a pasted posting can be
//               applied to as well
//   tailored    GET/POST /api/tailored-resumes (Q3), see TailoredResumePanel
//
// Q4b: work_mode / pay (posting) and h1b (the row, via job.h1b) render only
// when present -- no placeholder chips (operator answer 3).
function JobStateActions({ jobId, state, pasted, onRecorded }) {
  const [saving, setSaving] = useState(null); // the event kind being recorded
  const [error, setError] = useState(null);

  useEffect(() => {
    setSaving(null);
    setError(null);
  }, [jobId]);

  const record = (eventKind) => {
    setSaving(eventKind);
    setError(null);
    postApplication({ job_identity: jobId, event_kind: eventKind })
      .then(() => {
        setSaving(null);
        onRecorded();
      })
      .catch((err) => {
        setSaving(null);
        setError(err.message || String(err));
      });
  };

  return (
    <div className="job-state" data-role="job-state" data-state={state.state}>
      <div className="job-state-row">
        <span className="chip-group-label">State</span>
        <StateChip state={state} always showSince />
        {state.nextEvents.map((eventKind) => (
          <button
            key={eventKind}
            type="button"
            className="button small secondary"
            disabled={saving !== null}
            data-event={eventKind}
            onClick={() => record(eventKind)}
          >
            {saving === eventKind ? "Saving…" : eventActionLabel(eventKind)}
          </button>
        ))}
      </div>
      {error && (
        <div className="field-error" data-role="job-state-error">
          {error}
        </div>
      )}
      {pasted && (
        <p className="muted small job-state-note">
          You pasted this posting's text. If you paste an edited version later, Scout treats it as a new job with a state of its own.
        </p>
      )}
    </div>
  );
}

function OpenPosting({ url, children }) {
  return (
    <a href={url} target="_blank" rel="noreferrer">
      {children} ↗
    </a>
  );
}

function JobDescription({ posting, pasted }) {
  const excerpt = jdExcerpt(posting.text);
  if (!excerpt) {
    return (
      <section className="panel">
        <h3>Job description</h3>
        <p className="muted" data-role="jd-missing">
          {pasted ? "You pasted this posting's text. Pasted text is used for the assessment only and is never stored." : "The posting text was not captured for this row."}{" "}
          {posting.url && <OpenPosting url={posting.url}>Open the posting</OpenPosting>}
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

// uat-bug-029: a posting whose requirements could not be read (POST
// /api/assess 422 posting_requirements_unreadable) stays not assessed, and
// the page says so as a note, not an error.
function AssessNow({ posting, origin, onAssessed, label = "Assess" }) {
  const [state, setState] = useState("idle");
  const [error, setError] = useState(null);
  const [unreadable, setUnreadable] = useState(false);
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
          setUnreadable(false);
          postAssess({ job: { job_url: posting.url }, origin })
            .then((response) => {
              setState("idle");
              onAssessed(response);
            })
            .catch((err) => {
              setState("idle");
              if (isRequirementsUnreadable(err)) {
                setUnreadable(true);
              } else if (err instanceof ApiError && err.status === 504) {
                setError("The model timed out assessing this posting. Try again, or a faster model target.");
              } else {
                setError(err.message || String(err));
              }
            });
        }}
      >
        {state === "saving" ? "Assessing…" : label}
      </button>
      {unreadable && (
        <div className="muted requirements-unreadable" data-role="requirements-unreadable">
          {REQUIREMENTS_UNREADABLE_TEXT}. It stays not assessed; open the posting to read it yourself.
        </div>
      )}
      {error && <div className="field-error">{error}</div>}
    </span>
  );
}

function BackToList({ from }) {
  return from === "assessments" ? (
    <a className="back-link" href={ASSESSMENTS_HASH}>
      ← Assessments
    </a>
  ) : (
    <a className="back-link" href={JOBS_HASH}>
      ← Jobs
    </a>
  );
}

// uat-bug-043: the tailoring status shown next to the Tailor resume button.
// No promised duration: Codex tailoring measured mean 24-27 s, p95 30-42 s,
// max 42 s, and a rejected draft retries once (research/evals/
// 2026-09-29-tailor-confirm*.md), so "20-30 seconds" was too tight. The
// model is the config's default target (the tailor route's own default).
function tailorStatusFor(tailored, modelName) {
  const jump = () => {
    const panel = document.getElementById("tailored-resume");
    if (panel && typeof panel.scrollIntoView === "function") {
      panel.scrollIntoView({ block: "start", behavior: "smooth" });
    }
  };
  if (tailored.tailoring) {
    const who = modelName ? `with ${modelName}` : "your resume";
    return { phase: "running", text: `Tailoring ${who}… ${tailored.elapsed}s` };
  }
  if (tailored.outcome === "done") {
    return { phase: "done", text: "Done: see Tailored resume below.", onJump: jump };
  }
  if (tailored.outcome === "error") {
    return { phase: "error", text: "Tailoring failed: see the message below.", onJump: jump };
  }
  return null;
}

export default function JobPage({
  job,
  jobId,
  runId,
  from,
  profileId,
  profileLabel,
  visaRequired,
  loading,
  onQuickUpdated,
  onApplicationsChanged,
  onTailored,
}) {
  const [answers, setAnswers] = useState([]);
  const [tailorError, setTailorError] = useState(null);
  const [modelName, setModelName] = useState(null);
  useEffect(() => {
    let current = true;
    getConfig()
      .then((config) => current && config && config.default_model_target && setModelName(modelTargetLabel(config.default_model_target).replace(/\s*\(.*\)$/, "")))
      .catch(() => {});
    return () => {
      current = false;
    };
  }, []);
  // run-reads-fast (uat-bug-022): a run's rows come without their posting
  // text (the grid does not show it). This page reads its own posting,
  // text included, from GET /api/runs/{run_id}/posting.
  const [postingText, setPostingText] = useState(null);
  const needsText = Boolean(runId && job && job.row && !job.posting.text);
  useEffect(() => {
    setPostingText(null);
    if (!needsText) {
      return undefined;
    }
    let current = true;
    getRunPosting(runId, jobId)
      .then((response) => current && setPostingText(response.row.posting.text || null))
      .catch(() => {});
    return () => {
      current = false;
    };
  }, [runId, jobId, needsText]);

  const handleApplicationRecorded = useCallback(() => {
    if (onApplicationsChanged) {
      onApplicationsChanged();
    }
  }, [onApplicationsChanged]);

  useEffect(() => {
    // 0.1.10.7 C: the user's answers, the same for every profile.
    getAnswers()
      .then((response) => setAnswers(response.answers || []))
      .catch(() => setAnswers([]));
    setTailorError(null);
  }, [jobId, profileId]);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [jobId]);

  const posting = useMemo(
    () => (job && postingText && !job.posting.text ? { ...job.posting, text: postingText } : job ? job.posting : null),
    [job, postingText],
  );
  const assessment = job ? job.assessment : null;
  const jobUrl = posting && posting.url ? posting.url : null;
  const priorAnswers = useMemo(() => new Map(answers.map((answer) => [answer.question_id, answer])), [answers]);
  const assessOrigin = assessOriginFor(job);
  const assessByUrl = useCallback(() => postAssess({ job: { job_url: jobUrl }, origin: assessOrigin }), [jobUrl, assessOrigin]);

  // Hooks run on every render, a missing job included (its state is empty).
  const answerDrafts = useAnswerDrafts({
    assessment,
    jobIdentity: posting ? posting.normalized_url : null,
    priorAnswers,
    onAnswered: onQuickUpdated,
    onReassessUnavailable: jobUrl ? assessByUrl : undefined,
  });
  const tailored = useTailoredResume({ jobIdentity: job ? job.id : null, jobUrl, profileId });

  // uat-bug-018: a tailored resume stored for this job makes its state
  // "Resume tailored" at once, on this page and on its card.
  const tailoredJobId = job && tailored.stored ? job.id : null;
  useEffect(() => {
    if (tailoredJobId && onTailored) {
      onTailored(tailoredJobId);
    }
  }, [tailoredJobId, onTailored]);

  if (!job) {
    return (
      <div>
        <BackToList from={from} />
        <section className="panel">
          <h2>{loading ? (from === "assessments" ? "Loading assessment…" : "Loading run…") : "Job not found"}</h2>
          {!loading && (
            <p className="muted">
              {from === "assessments" ? "No assessment with this address for this profile: " : "No posting with this address in the loaded run: "}
              <code>{jobId}</code>
            </p>
          )}
        </section>
      </div>
    );
  }

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
  const tailorStatus = tailorStatusFor(tailored, modelName);
  const tailorAction = {
    status: tailorStatus,
    ...gate,
    enabled: gate.enabled && !tailored.loadingStored,
    label: tailored.tailoring ? "Tailoring…" : tailored.stored ? "Tailor again" : "Tailor resume",
    helpName: "Tailor resume",
    busy: tailored.tailoring,
    onClick: handleTailor,
  };
  const actionsBusy = Boolean(answerDrafts.busy) || tailored.tailoring;
  const pasted = Boolean(job.quick && job.quick.job && job.quick.job.fetch_kind === "pasted" && job.status === "on_demand");
  const state = job.state || jobStateFor(job, null, tailoredJobId ? [tailoredJobId] : null);

  return (
    <div className="job-page">
      <BackToList from={from} />

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
                  {storedOrigin(job.quick) === ORIGIN_JOB_PAGE ? "assessed from its job page" : "assessed on demand"}
                  {job.quick && job.quick.created_at ? ` ${dateLabel(job.quick.created_at)}` : ""}
                  {job.pastedResume ? " against a pasted resume" : ""}
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
            {assessment && staleAssessmentNote(job) && (
              <p className="muted small" data-role="assessment-stale">
                {staleAssessmentNote(job)}
                <AssessNow posting={posting} origin={assessOrigin} onAssessed={onQuickUpdated} label="Re-assess" />
              </p>
            )}
            {assessment && assessment.not_a_match_reason && (
              <div className="callout danger" style={{ margin: "12px 0 0" }}>
                {assessment.not_a_match_reason}
              </div>
            )}
            {!assessment && (
              <div className="callout info" style={{ margin: "12px 0 0" }} title={job.notAssessedReason ? notAssessedReasonDetail(job.notAssessedReason) : undefined}>
                {notAssessedLine(job)}.
                {job.status !== "assessing" && (job.status !== "acquired" || job.runEnded) && <AssessNow posting={posting} origin={assessOrigin} onAssessed={onQuickUpdated} />}
              </div>
            )}
          </div>
          <div className="header-side">
            <RankBadge rank={job.rank} detail hideWhenNone={job.status === "on_demand"} />
          </div>
        </div>
        <div className="job-actions">
          {posting.url && (
            <a className="button secondary small" href={posting.url} target="_blank" rel="noreferrer">
              Open posting ↗
            </a>
          )}
        </div>
        <JobStateActions jobId={job.id} state={state} pasted={pasted} onRecorded={handleApplicationRecorded} />
      </section>

      <JobDescription posting={posting} pasted={pasted} />

      {assessment ? (
        <AssessmentBody
          assessment={assessment}
          jobIdentity={posting.normalized_url}
          controller={answerDrafts}
          tailor={tailorAction}
          showVerdict={false}
          questionsFirst
        />
      ) : (
        <section className="panel">
          <h3>Requirements</h3>
          <RequirementActions
            reassess={{ ...reassessGate({ assessed: false, states: [] }), label: "Re-assess", onClick: () => {} }}
            tailor={tailorAction}
            busy={actionsBusy}
          />
          <p className="muted">Not assessed yet. The requirement table and its questions appear once the posting is assessed.</p>
        </section>
      )}
      {tailorError && <div className="field-error">Could not save your answers before tailoring: {tailorError}</div>}

      <TailoredResumePanel state={tailored} profileLabel={profileLabel} questionPrompts={questionPrompts} />

      {assessment && posting.url && <PrepPanel postingUrl={posting.url} profileId={profileId} />}
    </div>
  );
}

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getAnswers, getConfig, getJob, getRunPosting, postApplication, postAssess, postAssessThese } from "../api.js";
import { REQUIREMENTS_UNREADABLE_TEXT, isRequirementsUnreadable } from "../rankModel.js";
import AssessmentBody from "../components/AssessmentBody.jsx";
import RequirementActions from "../components/RequirementActions.jsx";
import RankBadge from "../components/RankBadge.jsx";
import HelpLink from "../components/HelpLink.jsx";
import { VERDICT_WORDING } from "../wording.js";
import { modelNoticeLine, requirementsNoteLine } from "../modelNoticeModel.js";
import SponsorshipBadge from "../components/SponsorshipBadge.jsx";
import ProviderBadge from "../components/ProviderBadge.jsx";
import QuickAssessChip from "../components/QuickAssessChip.jsx";
import StateChip from "../components/StateChip.jsx";
import PrepPanel from "../components/PrepPanel.jsx";
import JobResumePanel, { useJobResume } from "../components/JobResumePanel.jsx";
import SuggestionsPanel from "../components/SuggestionsPanel.jsx";
import PipelineTimeline from "../components/PipelineTimeline.jsx";
import { useAnswerDrafts } from "../answerDrafts.js";
import { assessSendsLine, assessSummaryLines, reassessErrorText, reassessGate } from "../answersModel.js";
import { REASSESS_LABEL, coverageRows, gateOf, headerChip, staleCodes, staleItems } from "../jobResumeModel.js";
import { applicationBadge, boardLink, closedBanner, postedLine, postingDate, scoreBox } from "../postingsModel.js";
import { displayCompanyName, notAssessedReasonDetail, thinPostingLine, unchangedSinceLabel } from "../display.js";
import { jobBatchLine, jobLeftBatch, jobWaitsInBatch } from "../assessBatchModel.js";
import { useAssessBatch } from "../useAssessBatch.js";
import {
  ORIGIN_JOB_PAGE,
  ageLabel,
  assessOriginFor,
  assessedAt,
  dateLabel,
  jdExcerpt,
  notAssessedLine,
  payLabel,
  showQuickAssessChip,
  storedOrigin,
  workModeLabel,
  h1bLabel,
  PASTED_RESUME_KEY,
} from "../jobModel.js";
import { assessmentStaleFor, eventActionLabel, fitStateFor, isApplicationState, jobStateFor, staleAssessmentNote, staleReasonWords } from "../jobStateModel.js";
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
//   N6  no right-hand column: the verdict history and the resume explainer
//       are gone; "Re-assess" is the action at the top of Requirements, and
//       the job's resume opens under Requirements
//   N7  the action is gated (answersModel.js) and says why when off
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
//   resume      GET /api/tailored-resumes (the job resume store kept its
//               path) and GET /api/jobs/suggestions, see JobResumePanel
//   pipeline    0.1.10.7 M4b: GET /api/pipeline/job, the step timeline
//               (0.1.11: four rows, Assessed -> Resume picked -> Scout ATS
//               -> Scout label) with the Scout ATS chip and the Scout label
//               chip; "Process now" is POST /api/pipeline/process
//               (PipelineTimeline). When the pipeline's pick step finishes,
//               the timeline says so and the stored resume is read again
//               (useJobResume.reload): the panel and the state "Resume
//               ready" follow without a reload. The timeline is read again
//               (its refreshKey) for a new verdict and for a resume stored
//               or edited on this page, not for one the pipeline stored
//
// 0.1.11 N6 (SPEC section 6): no tailor call. Top to bottom:
//   1. header       title, company, ONE chip for the fit (Matched · Matched ·
//                   2 minor gaps · Needs your answers · Has a gap · Not a
//                   match · Weak fit: jobResumeModel.headerChip) and beside
//                   it the stale label, in the Jobs row's words ("old
//                   assessment: older prompt")
//   2. questions    as before: saving an answer re-assesses, and the pick
//                   comes with that assessment
//   3. requirements the matrix, each row with the posting wording behind its
//                   class, "any one of: ..." and, for a met row, where its
//                   evidence is on the resume. ONE action: "Re-assess · 1
//                   model call"
//   4. the suggested resume, with Picked / Left out (JobResumePanel) and,
//                   under its heading, the ONE "Generate PDF" button
//                   (ApplyPanel; 0.1.11.3 item 5: it was a card at the bottom)
//   5. suggestions  (SuggestionsPanel): ONE line, "Suggestions (N open)",
//                   closed by default (0.1.11.3 item 9); no button rewrites
//                   with a model
//   6. the pipeline's four rows
// Opening the page recomputes nothing: every refresh is a button that says
// what it costs ("Re-pick · no model call", "Re-assess · 1 model call").
//
//
// 0110-10-12 (an old assessment, and what a re-assessment leaves behind):
//   Re-assess   is ON for an old assessment (older prompt, settings changed,
//               answers / resume / posting changed) with no question open:
//               the header says the one reason, in the Jobs row's words, and
//               the ONE Re-assess says what it costs (answersModel
//               .reassessGate). The reason is the stored item's own
//               `basis_stale`, which a "Resume ready" job's state does
//               not carry (jobStateModel.assessmentStaleFor)
//   the date    "assessed ... <date>" is when the assessment shown was MADE
//               (`updated_at`), not when the first one was (`created_at`)
//   the label   a Scout label made before the assessment shown says so
//               (PipelineTimeline `assessedAt`), and so does a resume
//               stored before it (the resume panel's stale line): neither
//               is the new assessment's
// 0110-10-14: the posting's date in the header: "posted 10 days ago (Sep 24,
// 2026)", "updated ..." or "first seen ..." (postingsModel.postedLine), from
// the Jobs row the page was opened from, else ONE GET /api/jobs?url=. When
// the board changed the posting on a later day, that is said beside it
// ("· updated 3 days ago"), never in place of the posting day.
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

// 7b: the posting's address on its board, beside every "Open posting" of a job stored under the company's own URL.
function OpenOnBoard({ board, className }) {
  if (!board) {
    return null;
  }
  return (
    <a className={className} href={board.url} target="_blank" rel="noopener noreferrer" data-role="open-on-board">
      {board.label} ↗
    </a>
  );
}

function JobDescription({ posting, pasted, board }) {
  // A job page opened from the Jobs list shows the whole stored text once GET /api/jobs has served it.
  const excerpt = posting.text_full ? jdExcerpt(posting.text, { target: Infinity, limit: Infinity }) : jdExcerpt(posting.text, { cut: Boolean(posting.text_cut) });
  if (!excerpt) {
    return (
      <section className="panel">
        <h3>Job description</h3>
        <p className="muted" data-role="jd-missing">
          {pasted ? "You pasted this posting's text. Pasted text is used for the assessment only and is never stored." : "The posting text was not captured for this row."}{" "}
          {posting.url && <OpenPosting url={posting.url}>Open the posting</OpenPosting>}
          {posting.url && board && " · "}
          {posting.url && <OpenOnBoard board={board} />}
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
          {posting.url && board && " · "}
          {posting.url && <OpenOnBoard board={board} />}
        </p>
      )}
    </section>
  );
}

// uat-bug-029: a posting whose requirements could not be read (POST
// /api/assess 422 posting_requirements_unreadable) stays not assessed, and
// the page says so as a note, not an error.
// 0.1.11.5 SP: an assessment asked for on a job page names the page's profile. Without it the server assesses the
// profile selected at that moment, which another tab or the CLI may have changed since the page was opened: the new
// assessment (and a resume that waits beside an edited one) then landed on a profile this page does not show.
function assessResume(profileId) {
  return profileId && profileId !== PASTED_RESUME_KEY ? { resume: { profile_id: profileId } } : {};
}

function AssessNow({ posting, profileId, origin, onAssessed, label = "Assess" }) {
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
          postAssess({ job: { job_url: posting.url }, ...assessResume(profileId), origin })
            .then((response) => {
              setState("idle");
              onAssessed(response);
            })
            .catch((err) => {
              setState("idle");
              if (isRequirementsUnreadable(err)) {
                setUnreadable(true);
              } else {
                setError(reassessErrorText(err)); // 0110-10-13: a typed cause says its facts and next action
              }
            });
        }}
      >
        {state === "saving" ? "Assessing…" : label}
      </button>
      {unreadable && (
        <div className="muted requirements-unreadable" data-role="requirements-unreadable">
          {REQUIREMENTS_UNREADABLE_TEXT}. It stays not assessed.
        </div>
      )}
      {error && <div className="field-error">{error}</div>}
    </span>
  );
}

// 0110-10-13: what one assessment sends, beside the Assess / Re-assess actions. Opened, it asks the no-call
// preview (POST /api/postings/assess without approve) for this job's summary: profile, resume source, answers
// and stories, whether the posting is fetched first. No model call either way.
function AssessSends({ jobUrl, profileId, target }) {
  const [lines, setLines] = useState(null);
  return (
    <details
      className="muted small"
      data-role="assess-sends"
      onToggle={(event) => {
        if (event.currentTarget.open && lines === null && jobUrl) {
          postAssessThese({ jobs: [jobUrl], again: true, ...(profileId ? { profile_id: profileId } : {}) })
            .then((response) => setLines(assessSummaryLines(response.model_input_summary)))
            .catch(() => setLines([]));
        }
      }}
    >
      <summary>{assessSendsLine(target, target ? modelTargetLabel(target) : null)}</summary>
      {(lines || []).map((line) => (
        <div key={line}>{line}</div>
      ))}
    </details>
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

export default function JobPage({
  job,
  jobId,
  runId,
  from,
  profileId,
  profileLabel,
  visaRequired,
  loading,
  onDemandHref,
  listedRow,
  onQuickUpdated,
  onApplicationsChanged,
  onTailored,
  onBatchChanged,
}) {
  // 0.1.11.5 (ASSESS-01, UI-01 part 3): "is it still assessing?". While an assess batch runs the page says how far
  // it is and whether this posting waits in it; when the posting gets its result (or the batch ends) the page's
  // assessments are read again (`onBatchChanged`).
  const batchSeen = useRef(null);
  const batchJob = useRef(null);
  const batchChanged = useRef(onBatchChanged);
  batchChanged.current = onBatchChanged;
  const batch = useAssessBatch({
    onProgress: (next) => {
      if (jobLeftBatch(batchSeen.current, next, batchJob.current) && batchChanged.current) {
        batchChanged.current();
      }
      batchSeen.current = next;
    },
    onEnd: () => {
      batchSeen.current = null;
      batchChanged.current && batchChanged.current();
    },
  });
  if (batch.running && batchSeen.current === null) {
    batchSeen.current = batch.status;
  }
  const [answers, setAnswers] = useState([]);
  const [modelTarget, setModelTarget] = useState(null);
  useEffect(() => {
    let current = true;
    getConfig()
      .then((config) => {
        if (!current || !config) {
          return;
        }
        // 0110-10-13: GET /api/config holds the settings under `config`.
        setModelTarget((config.config && config.config.default_model_target) || config.default_model_target || null);
      })
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
  }, [jobId, profileId]);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [jobId]);

  // 0110-10-14: the posting's dates. The Jobs row the page was opened from has them; opened by its link (a reload,
  // Assessments), the page asks for the job once. A past run's row keeps its own line (below).
  const rowDated = Boolean(listedRow && postingDate(listedRow));
  // 0.1.11.3: the company's catalog H-1B figure is a label of the Jobs row; a run's or an assessment's job has none of its own.
  const h1bFigure = job && h1bLabel(job.h1b) ? job.h1b : listedRow && h1bLabel(listedRow.h1b) ? listedRow.h1b : null;
  const askFullText = Boolean(job && job.fromPostings && /^https?:\/\//.test(job.id || ""));
  const askDates = askFullText || (Boolean(job) && !rowDated && !job.row && /^https?:\/\//.test(job.id || ""));
  // 0.1.11.4 R1: the same read says whether the board still lists the posting (`liveness`); every job with a link asks it.
  const askLive = Boolean(job) && /^https?:\/\//.test(job.id || "");
  // ONE read for both, asked again only for another job: a page opened by its link has no row until the list
  // arrives, and the row arriving (it has the dates) must not read the job a second time.
  const askJob = askDates || askLive;
  const [served, setServed] = useState(null); // GET /api/jobs?url='s answer for this job
  useEffect(() => {
    setServed(null);
    if (!askJob) {
      return undefined;
    }
    let current = true;
    getJob(jobId)
      .then((response) => {
        if (current) {
          setServed(response || null);
        }
      })
      .catch(() => {});
    return () => {
      current = false;
    };
  }, [jobId, askJob]);
  const servedDates = askDates && served && served.posting ? served.posting : null;
  const liveness = served && served.liveness ? served.liveness : null;

  const servedText = askFullText && servedDates && typeof servedDates.text === "string" && servedDates.text.trim() ? servedDates.text : null;
  const posting = useMemo(
    () =>
      job && servedText
        ? { ...job.posting, text: servedText, text_full: true, text_cut: false }
        : job && postingText && !job.posting.text
          ? { ...job.posting, text: postingText }
          : job
            ? job.posting
            : null,
    [job, postingText, servedText],
  );
  const assessment = job ? job.assessment : null;
  const jobUrl = posting && posting.url ? posting.url : null;
  batchJob.current = posting ? posting.normalized_url || jobId : jobId;
  const batchLine = jobBatchLine(batch.status, batchJob.current);
  const priorAnswers = useMemo(() => new Map(answers.map((answer) => [answer.question_id, answer])), [answers]);
  const assessOrigin = assessOriginFor(job);
  const assessByUrl = useCallback(() => postAssess({ job: { job_url: jobUrl }, ...assessResume(profileId), origin: assessOrigin }), [jobUrl, profileId, assessOrigin]);

  // 0.1.11.6 AN1: an assessment made from this page replaces the one shown only when it is this page's profile's.
  // Another profile's (a server older than the page) is never shown as this profile's: the stored ones are read again.
  const showAssessed = useCallback(
    (item) => {
      const made = item && item.resume ? item.resume.profile_id : null;
      if (made && profileId && made !== profileId) {
        batchChanged.current && batchChanged.current();
        return;
      }
      onQuickUpdated(item);
    },
    [profileId, onQuickUpdated],
  );

  // Hooks run on every render, a missing job included (its state is empty).
  const answerDrafts = useAnswerDrafts({
    assessment,
    jobIdentity: posting ? posting.normalized_url : null,
    // 0.1.11.6 AN1: the re-assessment an answer starts is this page's profile's (a pasted-resume assessment has none).
    profileId: job && job.pastedResume ? null : profileId,
    priorAnswers,
    onAnswered: showAssessed,
    onReassessUnavailable: jobUrl ? assessByUrl : undefined,
    stale: staleReasonWords(job),
  });
  const resume = useJobResume({ jobIdentity: job ? job.id : null, jobUrl, profileId, expectRecord: Boolean(job && job.assessmentSource === "quick" && job.quick && job.quick.resume_gate) });

  // uat-bug-018: a resume stored for this job makes its state "Resume
  // ready" at once, on this page and on its card.
  const tailoredJobId = job && resume.stored ? job.id : null;
  useEffect(() => {
    if (tailoredJobId && onTailored) {
      onTailored(tailoredJobId);
    }
  }, [tailoredJobId, onTailored]);

  // 0.1.11: the pick comes with the assessment. A new assessment of the job shown (Re-assess, an answer saved)
  // may have stored a new resume or a `proposed` one: the stored resume and its record are read again, once.
  const assessedStamp = job && job.assessmentSource === "quick" ? assessedAt(job) : "";
  const assessedSeen = useRef({ id: null, at: "" });
  const reloadResume = resume.reload;
  useEffect(() => {
    const before = assessedSeen.current;
    assessedSeen.current = { id: jobId, at: assessedStamp };
    if (before.id === jobId && before.at && assessedStamp && before.at !== assessedStamp) {
      reloadResume();
    }
  }, [jobId, assessedStamp, reloadResume]);

  if (!job) {
    return (
      <div>
        <BackToList from={from} />
        <section className="panel">
          <h2>{loading ? (from === "assessments" ? "Loading assessment…" : "Loading job…") : from === "assessments" ? "Job not found" : "We have no stored posting at this address"}</h2>
          {!loading && onDemandHref && (
            <p className="muted" data-role="on-demand-hint">
              This job was assessed on demand: <a href={onDemandHref}>open it under Assessments</a>.
            </p>
          )}
          {!loading && !onDemandHref && (
            <p className="muted" data-role="no-stored-posting">
              {from === "assessments" ? "No assessment with this address for this profile: " : "Nothing is stored for: "}
              <code>{jobId}</code>
            </p>
          )}
          {/* 0.1.11.7 FS2: a posting found by "Search all jobs" that no profile holds opens from its search row only. */}
          {!loading && !onDemandHref && from !== "assessments" && (
            <p className="muted" data-role="search-again-hint">
              If you found this posting with Search all jobs, search for it again on the Jobs page and open it from the results.
            </p>
          )}
          {!loading && !onDemandHref && from !== "assessments" && (
            <p>
              <a href={JOBS_HASH} data-role="back-to-jobs">
                Back to the Jobs list
              </a>
            </p>
          )}
        </section>
      </div>
    );
  }

  const mode = workModeLabel(posting);
  const pay = payLabel(posting.pay);
  const pasted = Boolean(job.quick && job.quick.job && job.quick.job.fetch_kind === "pasted" && job.status === "on_demand");
  const state = job.state || jobStateFor(job, null, tailoredJobId ? [tailoredJobId] : null);
  const applicationLabel = isApplicationState(state.state) ? applicationBadge(state) : null; // 0.1.11.3: "Applied · Oct 6", the latest status
  const posted = postedLine(rowDated ? listedRow : servedDates);
  // When the assessment shown was made (a re-assessment keeps the first one's `created_at`).
  const assessedTime = job.assessmentSource === "quick" ? assessedAt(job) : "";
  // The header's stale label, in the Jobs row's words; a reason that names what changed (a story, a resume line, the
  // posting text) says that in a line under it.
  const stale = assessment ? assessmentStaleFor(job) : null;
  const staleWordsNow = stale ? staleReasonWords(job) : null;
  const modelNotice = assessment && job.assessmentSource === "quick" ? modelNoticeLine(job.quick) : null; // the served item (job.quick) carries it, never a run row's own assessment
  // 0.1.11.2: ONE line for a thin match (fewer than 4 requirement rows), in place of the stored "Only 2 requirements were read" note.
  const storedNote = assessment && job.assessmentSource === "quick" ? requirementsNoteLine(job.quick) : null; // same served item as the model notice
  const thinLine = assessment ? thinPostingLine(job.verdict, assessment, storedNote) : null;
  const requirementsNote = thinLine || storedNote;
  const staleDetail = stale && ["story_bank_changed", "resume_changed", "posting_changed"].includes(stale.reason) ? staleAssessmentNote(job) : null;
  // 0.1.11 N6: the gate decides whether a resume is suggested (the record's, else the assessment's, else the verdict);
  // the stale list is the server's when it sends one. Nothing here recomputes.
  const fit = fitStateFor(job);
  const gate = assessment ? gateOf({ record: resume.record, quick: job.assessmentSource === "quick" ? job.quick : null, assessment }) : null;
  const staleList = staleItems(staleCodes({ served: resume.stale, job, stored: resume.stored, assessedAt: assessedTime }));
  const chip = headerChip(fit, { assessment, gate });
  const box = scoreBox(listedRow); // 0.1.11.5 (UI-01): an assessed job's fit and rank, from the Jobs row it was opened from
  const coverage = coverageRows({ assessment, record: resume.record, stored: resume.stored });
  // The page's ONE Re-assess, as the stale label and Apply offer it too.
  const reassess = { enabled: Boolean(assessment) && answerDrafts.gate.enabled && !answerDrafts.busy, reason: answerDrafts.gate.reason, onClick: answerDrafts.reassess };
  // 0.1.11.7 FS2: a posting found by "Search all jobs" that no profile holds: its Assess says whose assessment it will be.
  const searchAssessLabel = job.fromSearch && profileLabel ? `Assess · 1 model call · as ${profileLabel}` : undefined;
  const closed = closedBanner(liveness, listedRow, servedDates, posting);
  // 7b: the same read gives the posting's address on its board when the stored URL is the company's own page.
  const board = closed ? null : boardLink(liveness);
  const structured = Boolean(resume.record) || Boolean(assessment && Array.isArray(assessment.structured_suggestions) && assessment.structured_suggestions.length > 0);

  return (
    <div className="job-page">
      <BackToList from={from} />

      <section className="panel">
        {closed && (
          <div className="callout danger" data-role="posting-closed" data-since={closed.since || undefined} style={{ margin: "0 0 12px" }}>
            <strong>{closed.text}.</strong> Its board no longer lists it.{" "}
            {jobUrl ? (
              <a href={jobUrl} target="_blank" rel="noopener noreferrer" data-role="posting-closed-link">
                Open the posting to confirm
              </a>
            ) : (
              "Check it before you apply."
            )}
          </div>
        )}
        {board && board.down && (
          <div className="callout warn" role="status" data-role="company-page-down" style={{ margin: "0 0 12px" }}>
            {board.note}. <OpenOnBoard board={board} />
          </div>
        )}
        <div className="job-header">
          <div style={{ minWidth: 0, flex: 1 }}>
            <h2 className="job-title">{posting.title || "(untitled posting)"}</h2>
            <div className="job-sub">
              <strong style={{ color: "var(--text)" }}>{displayCompanyName(posting.company)}</strong>
              {posting.location && <span>{posting.location}</span>}
              {showQuickAssessChip(job) ? <QuickAssessChip fetchKind={job.quick && job.quick.job && job.quick.job.fetch_kind} /> : <ProviderBadge posting={posting} />}
              {posted ? (
                <span data-role="posted" data-kind={posted.kind} data-at={posted.at} title={posted.title}>
                  {posted.text} ({posted.date})
                  {posted.updated && (
                    <span data-role="posting-updated" data-at={posted.updated.at}>
                      {" · "}
                      {posted.updated.text} ({posted.updated.date})
                    </span>
                  )}
                </span>
              ) : job.status === "on_demand" || job.fromPostings ? null : (
                <span title={posting.published_at || undefined}>
                  posted {ageLabel(posting.published_at)}
                  {posting.published_at ? ` (${dateLabel(posting.published_at)})` : ""}
                </span>
              )}
              {job.status === "on_demand" && (
                <span data-role="assessed-at" data-at={assessedAt(job) || undefined} title={assessedAt(job) || undefined}>
                  {storedOrigin(job.quick) === ORIGIN_JOB_PAGE ? "assessed from its job page" : "assessed on demand"}
                  {assessedAt(job) ? ` ${dateLabel(assessedAt(job))}` : ""}
                  {job.pastedResume ? " against a pasted resume" : ""}
                </span>
              )}
            </div>
            <div className="job-facts">
              {mode && <span className="mode-chip">{mode}</span>}
              {pay && <span className="pay">{pay}</span>}
              <span className={`verdict-chip ${job.verdict} fit-${chip.state}`} data-role="job-chip" data-fit={chip.state} title={chip.title || undefined}>
                {chip.label}
              </span>
              {staleWordsNow && (
                <span className="tag stale-label" data-role="assessment-stale" data-reason={staleWordsNow} title="Re-assess to renew it: one model call">
                  old assessment: {staleWordsNow}
                </span>
              )}
              <HelpLink topic="verdict" />
              {(visaRequired || h1bFigure) && <SponsorshipBadge sponsorship={job.sponsorship} h1b={h1bFigure} />}
              {applicationLabel && (
                <span className={`state-pill tone-${applicationLabel.status === "rejected" ? "danger" : applicationLabel.status === "withdrawn" ? "plain" : "ok"}`} data-role="application-badge" data-status={applicationLabel.status} title={applicationLabel.title}>
                  {applicationLabel.label}
                </span>
              )}
              {job.status === "carried_forward" && <span className="tag">{unchangedSinceLabel(job.fromRunDate)}</span>}
            </div>
            {assessment && (
              <p className="muted small" data-role="verdict-wording">
                {VERDICT_WORDING}
              </p>
            )}
            {modelNotice && (
              <p className="muted small" data-role="model-notice">
                {modelNotice.text}
                {modelNotice.href && (
                  <>
                    {" "}
                    <a href={modelNotice.href} target="_blank" rel="noreferrer" data-role="model-notice-link">
                      {modelNotice.label}
                    </a>
                  </>
                )}
              </p>
            )}
            {staleDetail && (
              <p className="muted small" data-role="assessment-stale-detail">
                {staleDetail}
              </p>
            )}
            {assessment && assessment.not_a_match_reason && (
              <div className="callout danger" style={{ margin: "12px 0 0" }}>
                {assessment.not_a_match_reason}
              </div>
            )}
            {batchLine && (
              <div className="callout info" style={{ margin: "12px 0 0" }} role="status" data-testid="job-assess-batch">
                {batchLine}
              </div>
            )}
            {!assessment && (
              <div className="callout info" style={{ margin: "12px 0 0" }} title={job.notAssessedReason ? notAssessedReasonDetail(job.notAssessedReason) : undefined}>
                {notAssessedLine(job)}.
                {!jobWaitsInBatch(batch.status, batchJob.current) && job.status !== "assessing" && (job.status !== "acquired" || job.runEnded) && <AssessNow posting={posting} profileId={profileId} origin={assessOrigin} onAssessed={showAssessed} label={searchAssessLabel} />}
              </div>
            )}
          </div>
          <div className="header-side">
            {box ? (
              <div className="score-box" data-testid="score-box" title="Fit counts the must-have requirements twice. Rank is a first guess from the posting and your resume; it is not a verdict.">
                {box.fit && (
                  <>
                    <span className="score-box-fit" data-role="score-box-fit">
                      {box.fit.percent}
                    </span>
                    <span className="score-box-label">fit{box.fit.requirements ? ` · ${box.fit.requirements}` : ""}</span>
                  </>
                )}
                {box.rank && (
                  <span className="score-box-rank" data-role="score-box-rank">
                    {box.rank}
                  </span>
                )}
              </div>
            ) : (
              <RankBadge rank={job.rank} detail hideWhenNone={job.status === "on_demand"} />
            )}
          </div>
        </div>
        <div className="job-actions">
          {posting.url && board && board.down && <OpenOnBoard board={board} className="button small" />}
          {posting.url && (
            <a className="button secondary small" href={posting.url} target="_blank" rel="noreferrer" data-role="open-posting">
              Open posting ↗
            </a>
          )}
          {posting.url && board && !board.down && <OpenOnBoard board={board} className="button secondary small" />}
        </div>
        <JobStateActions jobId={job.id} state={state} pasted={pasted} onRecorded={handleApplicationRecorded} />
        {!pasted && <AssessSends jobUrl={posting.url} profileId={profileId} target={modelTarget} />}
      </section>

      <JobDescription posting={posting} pasted={pasted} board={board} />

      {requirementsNote && (
        <p className="muted small" data-role="requirements-note" data-thin={thinLine ? "true" : undefined}>
          {requirementsNote}
        </p>
      )}

      {assessment ? (
        <AssessmentBody
          assessment={assessment}
          jobIdentity={posting.normalized_url}
          controller={answerDrafts}
          showVerdict={false}
          questionsFirst
          alwaysActions
          reassessLabel={REASSESS_LABEL}
          coverage={coverage}
          onShowLine={resume.showLine}
          structuredSuggestions={structured}
        />
      ) : (
        <section className="panel">
          <h3>Requirements</h3>
          <RequirementActions reassess={{ ...reassessGate({ assessed: false, states: [] }), label: REASSESS_LABEL, helpName: "Re-assess", onClick: () => {} }} busy={Boolean(answerDrafts.busy)} />
          <p className="muted">Not assessed yet. The requirement table and its questions appear once the posting is assessed.</p>
        </section>
      )}

      <JobResumePanel
        state={resume}
        assessment={assessment}
        gate={gate}
        items={staleList}
        reassess={reassess}
        hasQuestions={answerDrafts.questions.length > 0}
        visaRequired={visaRequired}
      />

      {assessment && <SuggestionsPanel state={resume} assessment={assessment} jobUrl={jobUrl} />}

      <PipelineTimeline
        jobIdentity={job.id}
        profileId={profileId}
        assessed={Boolean(assessment)}
        assessedAt={assessedTime || null}
        refreshKey={`${assessment ? assessment.verdict || "assessed" : "none"}:${resume.changes}`}
        onPickDone={resume.reload}
      />

      {assessment && posting.url && <PrepPanel postingUrl={posting.url} profileId={profileId} />}
    </div>
  );
}

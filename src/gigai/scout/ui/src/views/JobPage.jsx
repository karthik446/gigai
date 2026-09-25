import { useCallback, useEffect, useState } from "react";
import { ApiError, getAnswers, getApplications, postApplication, postAssess } from "../api.js";
import AssessmentBody from "../components/AssessmentBody.jsx";
import JevBadge from "../components/JevBadge.jsx";
import VerdictChip from "../components/VerdictChip.jsx";
import SponsorshipBadge from "../components/SponsorshipBadge.jsx";
import ProviderBadge from "../components/ProviderBadge.jsx";
import QuickAssessChip from "../components/QuickAssessChip.jsx";
import PrepPanel from "../components/PrepPanel.jsx";
import { displayCompanyName, notAssessedReasonDetail, unchangedSinceLabel } from "../display.js";
import {
  VERDICT_LABELS,
  ageLabel,
  dateLabel,
  dateTimeLabel,
  jevReasonsLine,
  notAssessedLine,
  payLabel,
  triggerLabel,
  verdictHistoryFor,
  workModeLabel,
} from "../jobModel.js";
import Breadcrumb from "../components/Breadcrumb.jsx";
import { JOBS_HASH } from "../routing.js";

// Q4a: one posting's job page (#/jobs/<normalized_url>), per
// mockups/cards-and-job-page.html with the operator amendment: the
// requirement table is the SAME AssessmentBody the Quick assess and Pending
// answers views render (Requirement | Resume evidence | Status), with the
// class label and the unclear/unmet-first sort added there for every page,
// and the questions block directly under it.
//
// Data, all from existing routes:
//   header/JD   the run's posting row (GET /api/runs/{id}/results ->
//               rows[].posting; `text` is present when acquire fetched it)
//   assessment  jobModel's latest-of(run's own, quick store) -- see buildJobs
//   questions   POST /api/answers {question_id, answer, reassess:{job_identity}}
//               -> `reassessed` (a full AssessResponse incl. history) replaces
//               the quick item in place (onQuickUpdated), so verdict, table,
//               questions and history all update without a reload. A posting
//               assessed only by the run is not in the quick store yet, so
//               that reassess 404s (reassess_not_found): the first answer
//               then falls back to POST /api/assess {job_url} (AssessmentBody's
//               onReassessUnavailable), after which the store entry exists
//   assess      POST /api/assess {job:{job_url}} for a not-assessed row
//   applied     GET/POST /api/applications (external_ref = normalized_url)
//   answers     GET /api/answers: an already-recorded answer pre-fills its
//               question's box (the prompt decides what it means, operator
//               answer 5 -- nothing here maps an answer to a status)
//   history     jobModel.verdictHistoryFor: the run's own entry + the quick
//               store's history[] (Q4a's one backend addition)
//
// Phase 2 hooks: work_mode / pay / h1b_filings_fy2024 render only when the
// posting carries them; the tailored-resume panel (Q3's
// /api/tailored-resumes) mounts in the right column below the history
// (see the marked spot in the JSX) once that route lands.
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

function JobDescription({ posting }) {
  const [expanded, setExpanded] = useState(false);
  if (!posting.text) {
    return (
      <section className="panel">
        <h3>Job description</h3>
        <p className="muted">
          The posting text was not captured for this row.{" "}
          {posting.url && (
            <a href={posting.url} target="_blank" rel="noreferrer">
              Open the posting ↗
            </a>
          )}
        </p>
      </section>
    );
  }
  const long = posting.text.length > 1200;
  return (
    <section className="panel">
      <h3>Job description</h3>
      <div className={`jd-box${expanded || !long ? " expanded" : ""}`}>{posting.text}</div>
      {long && (
        <button type="button" className="button small secondary jd-toggle" onClick={() => setExpanded((value) => !value)}>
          {expanded ? "Show less" : "Show full description"}
        </button>
      )}
    </section>
  );
}

function VerdictHistory({ job }) {
  const entries = verdictHistoryFor(job);
  return (
    <section className="panel">
      <h3>Verdict history</h3>
      {entries.length === 0 ? (
        <p className="muted" style={{ fontSize: "0.85rem" }}>
          No assessment yet.
        </p>
      ) : (
        <ul className="history">
          {entries
            .slice()
            .reverse()
            .map((entry, index) => (
              <li key={`${entry.at || "unknown"}-${entry.trigger}-${index}`} className={entry.verdict || "no_verdict"}>
                <div>
                  <div>
                    <strong>{entry.verdict ? VERDICT_LABELS[entry.verdict] || entry.verdict : "Assessed"}</strong>{" "}
                    <span className="h-when" title={entry.at || undefined}>
                      {dateTimeLabel(entry.at) || "this run"}
                    </span>
                  </div>
                  <div className="h-trigger">{triggerLabel(entry.trigger)}</div>
                </div>
              </li>
            ))}
        </ul>
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

export default function JobPage({ job, jobId, profileId, visaRequired, loading, onQuickUpdated, onApplicationsChanged }) {
  const [answers, setAnswers] = useState([]);
  const [applications, setApplications] = useState([]);

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
  }, [reloadApplications, jobId]);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [jobId]);

  if (!job) {
    return (
      <div>
        <Breadcrumb crumbs={[{ label: "Jobs", href: JOBS_HASH }, { label: loading ? "Loading…" : "Job not found" }]} />
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

  const { posting, assessment } = job;
  const reasons = jevReasonsLine(job.rank);
  const mode = workModeLabel(posting);
  const pay = payLabel(posting.pay);
  const priorAnswers = new Map(answers.map((answer) => [answer.question_id, answer]));

  const crumbTitle = [displayCompanyName(posting.company), posting.title].filter(Boolean).join(" · ") || "(untitled posting)";

  return (
    <div>
      <Breadcrumb crumbs={[{ label: "Jobs", href: JOBS_HASH }, { label: crumbTitle }]} />

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
              {visaRequired && <SponsorshipBadge sponsorship={job.sponsorship} h1bFilings={posting.h1b_filings_fy2024} />}
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

      <div className="two-col">
        <div>
          <JobDescription posting={posting} />
          <section className="panel">
            <h3>Requirements</h3>
            {assessment ? (
              <>
                {job.assessmentSource === "run" && job.quick === null && job.status !== "carried_forward" && (
                  <p className="muted" style={{ fontSize: "0.82rem", margin: "0 0 8px" }}>
                    From this run's assessment. Answering a question re-assesses this posting with all your answers.
                  </p>
                )}
                <AssessmentBody
                  assessment={assessment}
                  jobIdentity={posting.normalized_url}
                  onAnswered={onQuickUpdated}
                  priorAnswers={priorAnswers}
                  onReassessUnavailable={posting.url ? () => postAssess({ job: { job_url: posting.url } }) : undefined}
                />
              </>
            ) : (
              <p className="muted">Not assessed yet. The requirement table and questions appear once the posting is assessed.</p>
            )}
          </section>
          {assessment && posting.url && <PrepPanel postingUrl={posting.url} profileId={profileId} />}
        </div>
        <div>
          <VerdictHistory job={job} />
          {/* Phase 2 (Q3): the tailored-resume panel mounts here, fed by
              POST /api/tailored-resumes {job_identity: posting.normalized_url,
              profile_id: profileId} once that route lands. */}
        </div>
      </div>
    </div>
  );
}

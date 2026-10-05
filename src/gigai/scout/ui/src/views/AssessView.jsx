import ResumeWarning from "../components/ResumeWarning.jsx";
import { useEffect, useState } from "react";
import { ApiError, postAssess } from "../api.js";
import { assessCauseText } from "../answersModel.js";
import Breadcrumb from "../components/Breadcrumb.jsx";
import { assessPrivacyNote, assessResume, assessingAgainst, canAssess, otherProfiles } from "../assessModel.js";
import { REQUIREMENTS_UNREADABLE_TEXT, isRequirementsUnreadable } from "../rankModel.js";
import { ORIGIN_QUICK_ASSESS } from "../jobModel.js";
import { ASSESSMENTS_HASH } from "../routing.js";

// P5/P9 → Q4a-nav: "+ Assess a job" (#/assess). POST /api/assess -- URL or
// pasted text, a profile or a pasted resume. Synchronous (operator decision
// 1): the request blocks for the model call; a 504 assess_timeout is shown
// plainly, not retried. The request says `origin: "quick_assess"`, which is
// what lists the result under Assessments (assess-origin-field).
//
// The result is not rendered here: `onAssessed(response)` hands the
// AssessResponse to the app, which merges it into the job model and opens
// its job page (the same JobPage every card opens), so an on-demand
// assessment reads exactly like a run's.
//
// uat-batch2:
//   uat-bug-016  reached from Assessments (the breadcrumb and Cancel go
//                back there)
//   uat-bug-013  the page assesses against the ACTIVE profile, the one the
//                top bar shows, and says so: "Assessing against: <profile>
//                · <resume label>". Another profile or a pasted resume is a
//                secondary choice behind "Use another profile or paste a
//                resume", not a toggle the operator has to pass first.
//
// The resume label is GET /api/config's (the selected profile's own
// resume): `config` is the app's own read of it (App.jsx re-reads it when
// the active profile changes), so this page adds no request of its own.
export default function AssessView({ profiles, selectedProfileId, config, configLoading, onAssessed }) {
  const [jobMode, setJobMode] = useState("url");
  const [jobUrl, setJobUrl] = useState("");
  const [jobText, setJobText] = useState("");
  // "active" | "profile" | "text" (assessModel.js)
  const [mode, setMode] = useState("active");
  const [choosing, setChoosing] = useState(false);
  const [otherProfileId, setOtherProfileId] = useState("");
  const [resumeText, setResumeText] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  // uat-bug-029: the posting's requirements could not be read; not an error.
  const [unreadable, setUnreadable] = useState(false);

  const others = otherProfiles(profiles, selectedProfileId);
  const against = assessingAgainst({ mode, profiles, activeProfileId: selectedProfileId, otherProfileId, config, configLoading });

  // The profile picked here became the active one (the top bar changed):
  // that is the default again.
  useEffect(() => {
    if (mode === "profile" && otherProfileId && otherProfileId === selectedProfileId) {
      setMode("active");
      setOtherProfileId("");
    }
  }, [mode, otherProfileId, selectedProfileId]);

  function backToActive() {
    setMode("active");
    setChoosing(false);
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setUnreadable(false);
    try {
      const job = jobMode === "url" ? { job_url: jobUrl.trim() } : { job_text: jobText };
      const resume = assessResume({ mode, activeProfileId: selectedProfileId, otherProfileId, resumeText });
      const result = await postAssess({ job, resume, origin: ORIGIN_QUICK_ASSESS });
      onAssessed(result);
    } catch (err) {
      if (isRequirementsUnreadable(err)) {
        setUnreadable(true);
      } else if (assessCauseText(err)) {
        setError(assessCauseText(err)); // 0110-10-13: a typed cause says its facts and next action
      } else if (err instanceof ApiError && err.status === 504) {
        setError("The model timed out assessing this posting. Try again, or a smaller/faster model target.");
      } else {
        setError(err.message || String(err));
      }
    } finally {
      setSubmitting(false);
    }
  }

  const ready = canAssess({ jobMode, jobUrl, jobText, mode, otherProfileId, resumeText });

  return (
    <div>
      <Breadcrumb crumbs={[{ label: "Assessments", href: ASSESSMENTS_HASH }, { label: "Assess a job" }]} />
      <div className="panel">
        <h2>Assess a job</h2>
        <p className="muted">Assess one job posting without running a full search. The result opens as a job page.</p>

        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <div className="toggle-row">
              <button type="button" className={`toggle-button${jobMode === "url" ? " active" : ""}`} onClick={() => setJobMode("url")}>
                Job URL
              </button>
              <button type="button" className={`toggle-button${jobMode === "text" ? " active" : ""}`} onClick={() => setJobMode("text")}>
                Paste text
              </button>
            </div>
          </div>

          {jobMode === "url" ? (
            <div className="form-group">
              <label className="form-label" htmlFor="quick-assess-url">
                Job posting URL
              </label>
              <input
                id="quick-assess-url"
                type="url"
                className="text-input"
                value={jobUrl}
                onChange={(event) => setJobUrl(event.target.value)}
                placeholder="https://…"
              />
            </div>
          ) : (
            <div className="form-group">
              <label className="form-label" htmlFor="quick-assess-text">
                Posting text
              </label>
              <textarea id="quick-assess-text" className="text-input" rows={6} value={jobText} onChange={(event) => setJobText(event.target.value)} />
            </div>
          )}

          <div className="assess-against" data-role="assessing-against" data-mode={mode}>
            <span title={against.tooltip}>
              Assessing against: <strong>{against.who}</strong>
              {against.resume ? ` · ${against.resume}` : ""}
            </span>
            {mode === "active" && !choosing && (
              <button type="button" className="link-button" onClick={() => setChoosing(true)} data-action="assess-other">
                Use another profile or paste a resume
              </button>
            )}
            {(mode !== "active" || choosing) && (
              <button type="button" className="link-button" onClick={backToActive} data-action="assess-active">
                {mode === "active" ? "Keep the active profile" : "Use the active profile"}
              </button>
            )}
          </div>

          {(mode !== "active" || choosing) && (
            <div className="assess-other" data-role="assess-other">
              {others.length > 0 && (
                <div className="form-group">
                  <label className="form-label" htmlFor="quick-assess-profile">
                    Another profile
                  </label>
                  <select
                    id="quick-assess-profile"
                    value={mode === "profile" ? otherProfileId : ""}
                    onChange={(event) => {
                      setOtherProfileId(event.target.value);
                      setMode(event.target.value ? "profile" : "active");
                    }}
                  >
                    <option value="">Choose a profile…</option>
                    {others.map((profile) => (
                      <option key={profile.profile_id} value={profile.profile_id}>
                        {profile.label}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <div className="form-group">
                <label className="form-label" htmlFor="quick-assess-resume-text">
                  {others.length > 0 ? "Or paste a resume" : "Paste a resume"} (used for this assessment only, never stored)
                </label>
                <ResumeWarning modelTarget={config && config.config ? config.config.default_model_target : undefined} />
                <textarea
                  id="quick-assess-resume-text"
                  className="text-input"
                  rows={6}
                  value={resumeText}
                  onChange={(event) => {
                    setResumeText(event.target.value);
                    if (event.target.value.trim()) {
                      setMode("text");
                    } else if (mode === "text") {
                      setMode("active");
                    }
                  }}
                />
              </div>
            </div>
          )}

          <p className="privacy-note">{assessPrivacyNote(mode)}</p>

          {unreadable && (
            <div className="callout info" data-role="requirements-unreadable">
              {REQUIREMENTS_UNREADABLE_TEXT}. Nothing was assessed; try the posting's own page on the company's job board, or paste its text.
            </div>
          )}
          {error && <div className="callout danger">{error}</div>}

          <div className="actions" style={{ justifyContent: "flex-start" }}>
            <button type="submit" className="button" disabled={submitting || !ready}>
              {submitting ? "Assessing…" : "Assess"}
            </button>
            <a className="button secondary" href={ASSESSMENTS_HASH}>
              Cancel
            </a>
          </div>
          {submitting && <p className="muted">Assessing with the model… this can take a minute.</p>}
        </form>
      </div>
    </div>
  );
}

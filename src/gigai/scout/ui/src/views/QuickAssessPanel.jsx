import { useState } from "react";
import { ApiError, postAssess } from "../api.js";
import AssessmentBody from "../components/AssessmentBody.jsx";
import SponsorshipBadge from "../components/SponsorshipBadge.jsx";

// P5/P9: Quick-assess. POST /api/assess -- URL or pasted text, a profile or
// pasted resume. Synchronous (operator decision 1): the request blocks for
// the model call; a 504 assess_timeout is shown plainly, not retried
// automatically. Runs independently of every other view (its own local
// state here; never touches App.jsx's run/profile state), so a slow assess
// call never blocks navigating to another tab.
export default function QuickAssessPanel({ profiles, selectedProfileId }) {
  const [jobMode, setJobMode] = useState("url");
  const [jobUrl, setJobUrl] = useState("");
  const [jobText, setJobText] = useState("");
  const [resumeMode, setResumeMode] = useState("profile");
  const [profileId, setProfileId] = useState(selectedProfileId || "");
  const [resumeText, setResumeText] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [response, setResponse] = useState(null);

  async function handleSubmit(event) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setResponse(null);
    try {
      const job = jobMode === "url" ? { job_url: jobUrl.trim() } : { job_text: jobText };
      const resume =
        resumeMode === "profile" ? { profile_id: profileId || null } : { resume_text: resumeText };
      const result = await postAssess({ job, resume });
      setResponse(result);
    } catch (err) {
      if (err instanceof ApiError && err.status === 504) {
        setError("The model timed out assessing this posting. Try again, or a smaller/faster model target.");
      } else {
        setError(err.message || String(err));
      }
    } finally {
      setSubmitting(false);
    }
  }

  const canSubmit =
    (jobMode === "url" ? jobUrl.trim() : jobText.trim()) &&
    (resumeMode === "profile" ? profileId : resumeText.trim());

  return (
    <div className="panel">
      <h2>Quick assess</h2>
      <p className="muted">Assess one job posting without running a full search.</p>
      <p className="privacy-note">Your resume is sent to Jev to rank postings.</p>

      <form onSubmit={handleSubmit}>
        <div className="form-group">
          <div className="toggle-row">
            <button
              type="button"
              className={`toggle-button${jobMode === "url" ? " active" : ""}`}
              onClick={() => setJobMode("url")}
            >
              Job URL
            </button>
            <button
              type="button"
              className={`toggle-button${jobMode === "text" ? " active" : ""}`}
              onClick={() => setJobMode("text")}
            >
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
            <textarea
              id="quick-assess-text"
              className="text-input"
              rows={6}
              value={jobText}
              onChange={(event) => setJobText(event.target.value)}
            />
          </div>
        )}

        <div className="form-group">
          <div className="toggle-row">
            <button
              type="button"
              className={`toggle-button${resumeMode === "profile" ? " active" : ""}`}
              onClick={() => setResumeMode("profile")}
            >
              Use a profile's resume
            </button>
            <button
              type="button"
              className={`toggle-button${resumeMode === "text" ? " active" : ""}`}
              onClick={() => setResumeMode("text")}
            >
              Paste resume text
            </button>
          </div>
        </div>

        {resumeMode === "profile" ? (
          <div className="form-group">
            <label className="form-label" htmlFor="quick-assess-profile">
              Profile
            </label>
            <select id="quick-assess-profile" value={profileId} onChange={(event) => setProfileId(event.target.value)}>
              <option value="">Selected profile (default)</option>
              {profiles.map((profile) => (
                <option key={profile.profile_id} value={profile.profile_id}>
                  {profile.label}
                </option>
              ))}
            </select>
          </div>
        ) : (
          <div className="form-group">
            <label className="form-label" htmlFor="quick-assess-resume-text">
              Resume text (used for this call only, never stored)
            </label>
            <textarea
              id="quick-assess-resume-text"
              className="text-input"
              rows={6}
              value={resumeText}
              onChange={(event) => setResumeText(event.target.value)}
            />
          </div>
        )}

        {error && <div className="callout danger">{error}</div>}

        <div className="actions" style={{ justifyContent: "flex-start" }}>
          <button type="submit" className="button" disabled={submitting || !canSubmit}>
            {submitting ? "Assessing…" : "Assess"}
          </button>
        </div>
      </form>

      {response && (
        <div className="posting-card" style={{ marginTop: 14 }}>
          <div className="posting-card-heading">
            <span>
              {response.job.title || "(pasted text)"} {response.job.company ? `· ${response.job.company}` : ""}
            </span>
            <SponsorshipBadge sponsorship={response.result.sponsorship} />
          </div>
          <AssessmentBody assessment={response.result} jobIdentity={response.job.job_identity} onAnswered={setResponse} />
        </div>
      )}
    </div>
  );
}

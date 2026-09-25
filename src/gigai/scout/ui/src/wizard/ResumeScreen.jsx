import { useRef, useState } from "react";
import TagListInput from "../components/TagListInput.jsx";
import { MODEL_TARGETS, MODEL_TARGET_HINTS } from "./wizardState.js";

const TEXT_FILE_PATTERN = /\.(txt|md|markdown|text)$/i;
const MAX_UPLOAD_BYTES = 2 * 1024 * 1024;

// Screen 1 -- name the profile, supply a resume (paste / upload / choose an
// existing one), pick the model that reads it, run the extraction, edit the
// resulting chips. Everything shown comes from GET /api/profiles, GET
// /api/config and POST /api/resume/extract (see wizardState.js).
export default function ResumeScreen({
  fields,
  setField,
  selectedProfile,
  resumes,
  extracting,
  extractError,
  onExtract,
}) {
  const [uploadError, setUploadError] = useState(null);
  const fileInput = useRef(null);

  function handleFile(event) {
    const file = event.target.files && event.target.files[0];
    setUploadError(null);
    if (!file) {
      return;
    }
    if (!TEXT_FILE_PATTERN.test(file.name) && !(file.type && file.type.startsWith("text/"))) {
      setUploadError("Plain text or Markdown only (.txt, .md). PDF/DOCX parsing is not available in this release.");
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setUploadError("That file is larger than 2 MB. Paste the relevant text instead.");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setField("resumeText", String(reader.result || ""));
      setField("uploadName", file.name);
      setField("extraction", null);
    };
    reader.onerror = () => setUploadError("Could not read that file.");
    reader.readAsText(file);
  }

  function setResumeMode(mode) {
    setField("resumeMode", mode);
    setField("extraction", null);
  }

  const canExtract =
    !extracting &&
    (fields.resumeMode === "existing" ? Boolean(fields.existingRef) : fields.resumeText.trim().length > 0);
  const extraction = fields.extraction;

  return (
    <section className="panel">
      <h2>{fields.profileMode === "update" ? "Update this profile" : "Name this profile"}</h2>
      <p className="muted">
        An interested profile pairs one resume with the job titles you want it to search for. Countries, companies
        and discovery settings are shared by every profile.
      </p>

      {selectedProfile && (
        <div className="form-group wz-radio-list" role="radiogroup" aria-label="Profile">
          <label className={`wz-radio-card${fields.profileMode === "update" ? " active" : ""}`}>
            <input
              type="radio"
              name="wz-profile-mode"
              checked={fields.profileMode === "update"}
              onChange={() => {
                setField("profileMode", "update");
                if (!fields.profileName.trim()) {
                  setField("profileName", selectedProfile.label || "");
                }
              }}
            />
            <div className="wz-radio-body">
              <strong>Update “{selectedProfile.label}”</strong>
              <span>The selected profile. Its titles and resume are replaced by what you set here.</span>
            </div>
          </label>
          <label className={`wz-radio-card${fields.profileMode === "new" ? " active" : ""}`}>
            <input
              type="radio"
              name="wz-profile-mode"
              checked={fields.profileMode === "new"}
              onChange={() => setField("profileMode", "new")}
            />
            <div className="wz-radio-body">
              <strong>Create a new profile</strong>
              <span>Keeps “{selectedProfile.label}” as it is; the new profile is not selected automatically.</span>
            </div>
          </label>
        </div>
      )}

      <div className="form-group">
        <label className="form-label" htmlFor="wz-profile-name">
          Profile name
        </label>
        <input
          id="wz-profile-name"
          type="text"
          className="text-input"
          value={fields.profileName}
          placeholder="e.g. Staff AI engineer"
          onChange={(event) => setField("profileName", event.target.value)}
        />
      </div>

      <h3>Resume</h3>
      <div className="wz-tabs" role="tablist">
        {[
          ["paste", "Paste text"],
          ["upload", "Upload file"],
          ["existing", "Choose existing"],
        ].map(([mode, label]) => (
          <button
            key={mode}
            type="button"
            role="tab"
            aria-selected={fields.resumeMode === mode}
            className={`wz-tab${fields.resumeMode === mode ? " active" : ""}`}
            onClick={() => setResumeMode(mode)}
          >
            {label}
          </button>
        ))}
      </div>

      {fields.resumeMode === "paste" && (
        <div className="form-group">
          <label className="form-label" htmlFor="wz-resume-text">
            Paste your resume text
          </label>
          <textarea
            id="wz-resume-text"
            className="text-input"
            value={fields.resumeText}
            placeholder="Paste the plain text of your resume…"
            onChange={(event) => {
              setField("resumeText", event.target.value);
              setField("uploadName", null);
              setField("extraction", null);
            }}
          />
          <small className="wz-hint">
            Used for the extraction below. To attach it to the profile, save it as a file and run the{" "}
            <code>gigai scout resume add</code> command shown at the end.
          </small>
        </div>
      )}

      {fields.resumeMode === "upload" && (
        <div className="form-group">
          <label className="form-label" htmlFor="wz-resume-file">
            Upload a file
          </label>
          <input
            id="wz-resume-file"
            ref={fileInput}
            type="file"
            accept=".txt,.md,.markdown,text/plain,text/markdown"
            onChange={handleFile}
          />
          {fields.uploadName && (
            <small className="wz-hint">
              Loaded {fields.uploadName} ({fields.resumeText.length} characters). The same file is what{" "}
              <code>gigai scout resume add</code> attaches at the end.
            </small>
          )}
          {!fields.uploadName && <small className="wz-hint">Plain text or Markdown. Read in your browser only.</small>}
          {uploadError && <div className="field-error">{uploadError}</div>}
        </div>
      )}

      {fields.resumeMode === "existing" && (
        <div className="form-group">
          <span className="form-label">Choose an existing resume</span>
          {resumes.length === 0 && (
            <p className="muted">
              No resume is stored yet. Paste or upload one, or run <code>gigai scout resume add &lt;file&gt;</code>.
            </p>
          )}
          {resumes.map((item) => {
            const active = fields.existingRef && fields.existingRef.key === item.key;
            return (
              <button
                key={item.key}
                type="button"
                className={`wz-file-item${active ? " active" : ""}`}
                aria-pressed={Boolean(active)}
                onClick={() => {
                  setField("existingRef", item);
                  setField("extraction", null);
                }}
              >
                <span className="wz-fname">{item.name}</span>
                {item.date && <span className="wz-fdate">{item.date}</span>}
              </button>
            );
          })}
        </div>
      )}

      <h3>Analysis model</h3>
      <div className="form-group">
        <label className="form-label" htmlFor="wz-model-target">
          Which model reads the resume?
        </label>
        <select
          id="wz-model-target"
          value={fields.modelTarget}
          onChange={(event) => {
            setField("modelTarget", event.target.value);
            setField("extraction", null);
          }}
        >
          {MODEL_TARGETS.map((target) => (
            <option key={target} value={target}>
              {target}
            </option>
          ))}
        </select>
        <small className="wz-hint">{MODEL_TARGET_HINTS[fields.modelTarget]}</small>
      </div>
      <div className="callout info">
        Your resume is used only for this extraction and for assessing postings. It never goes to web search.
      </div>

      <div style={{ margin: "14px 0" }}>
        <button type="button" className="button secondary" onClick={onExtract} disabled={!canExtract}>
          {extracting ? "Analyzing…" : extraction ? "Analyze again" : "Analyze resume"}
        </button>
      </div>

      {extracting && (
        <div className="wz-progress" role="status">
          <p className="muted">Extracting stack, seniority and titles with {fields.modelTarget}…</p>
          <div className="wz-progress-track">
            <div className="wz-progress-fill" />
          </div>
        </div>
      )}

      {extractError && <div className="callout danger">{extractError}</div>}

      {extraction && !extracting && (
        <div>
          <div className="callout ok">
            Extracted by the <strong>{extraction.extractor}</strong> extractor via{" "}
            <code>{extraction.resolved_target || extraction.model_target}</code>. Everything below is from your
            resume; edit any chip.
          </div>
          <TagListInput
            id="wz-stack"
            label="Tech stack"
            values={fields.stack}
            onChange={(values) => setField("stack", values)}
            placeholder="add…"
          />
          <TagListInput
            id="wz-seniority"
            label="Seniority"
            values={fields.seniority}
            onChange={(values) => setField("seniority", values.slice(-1))}
            placeholder="e.g. staff"
          />
          <TagListInput
            id="wz-suggested-titles"
            label="Suggested job titles"
            values={fields.suggestedTitles}
            onChange={(values) => setField("suggestedTitles", values)}
            placeholder="add…"
          />
          <small className="wz-hint">The suggested titles pre-fill the next screen the first time you open it.</small>
        </div>
      )}
    </section>
  );
}

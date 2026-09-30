import { useEffect, useRef, useState } from "react";
import ResumeWarning from "./ResumeWarning.jsx";
import useResumeCheck from "./useResumeCheck.js";
import { contactHeadsUp } from "../resumeWarning.js";
import { RESUME_MODES } from "../profileResumeModel.js";
import { RESUME_FILE_PATTERN, RESUME_MAX_BYTES, bytesToBase64 } from "../wizard/wizardState.js";

// 0110-016: the new-profile form's own resume: Paste text / Upload file /
// Choose existing (same words, limits and local contact check as the wizard's
// Resume step). `value` is profileResumeModel's initialNewResume() shape.
// Storing happens on Create; nothing here calls a model.
export default function NewProfileResume({ value, onChange, choices, modelTarget, onBlockedChange }) {
  const [uploadError, setUploadError] = useState(null);
  const fileInput = useRef(null);
  const stored = value.mode === "existing";
  const found = useResumeCheck(
    stored
      ? value.existingRef
        ? { resume_ref: { record_id: value.existingRef.record_id, revision_id: value.existingRef.revision_id } }
        : null
      : value.text.trim()
        ? { resume_text: value.text }
        : null,
  );
  const headsUp = contactHeadsUp(found, { stored });
  // "Continue anyway" applies to this text only; the form holds Create while it is pending.
  const [continuedText, setContinuedText] = useState(null);
  const needsContinue = Boolean(headsUp) && !stored && continuedText !== value.text;
  useEffect(() => {
    onBlockedChange(needsContinue);
  }, [needsContinue]);

  function handleFile(event) {
    const file = event.target.files && event.target.files[0];
    setUploadError(null);
    if (!file) {
      return;
    }
    if (!RESUME_FILE_PATTERN.test(file.name)) {
      setUploadError("Plain text or Markdown only (.txt, .md, .markdown).");
      return;
    }
    if (file.size > RESUME_MAX_BYTES) {
      setUploadError("That file is larger than 1 MB. Paste the relevant text instead.");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const bytes = new Uint8Array(reader.result);
      let text;
      try {
        text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      } catch {
        setUploadError("That file is not UTF-8 text. Save it as plain text or Markdown and try again.");
        return;
      }
      if (!text.trim()) {
        setUploadError("That file is empty.");
        return;
      }
      onChange({ ...value, text, uploadName: file.name, uploadBase64: bytesToBase64(bytes) });
    };
    reader.onerror = () => setUploadError("Could not read that file.");
    reader.readAsArrayBuffer(file);
  }

  return (
    <div data-role="new-profile-resume">
      <h3>Resume for this profile</h3>
      <ResumeWarning modelTarget={modelTarget} />
      <div className="wz-tabs" role="tablist">
        {RESUME_MODES.map(([mode, label]) => (
          <button
            key={mode}
            type="button"
            role="tab"
            aria-selected={value.mode === mode}
            className={`wz-tab${value.mode === mode ? " active" : ""}`}
            onClick={() => onChange({ ...value, mode })}
          >
            {label}
          </button>
        ))}
      </div>

      {value.mode === "paste" && (
        <div className="form-group">
          <label className="form-label" htmlFor="new-profile-resume-text">
            Paste this profile's resume text
          </label>
          <textarea
            id="new-profile-resume-text"
            className="text-input"
            value={value.text}
            placeholder="Paste the plain text of the resume…"
            onChange={(event) => onChange({ ...value, text: event.target.value, uploadName: null, uploadBase64: null })}
          />
          <small className="wz-hint">Stored on this machine as this profile's own resume. Nothing is sent to a model now.</small>
        </div>
      )}

      {value.mode === "upload" && (
        <div className="form-group">
          <label className="form-label" htmlFor="new-profile-resume-file">
            Upload a file
          </label>
          <input id="new-profile-resume-file" ref={fileInput} type="file" accept=".txt,.md,.markdown" onChange={handleFile} />
          {value.uploadName ? (
            <small className="wz-hint">
              Loaded {value.uploadName} ({value.text.length} characters). Stored on this machine as this profile's own
              resume.
            </small>
          ) : (
            <small className="wz-hint">Plain text or Markdown. Read in your browser only.</small>
          )}
          {uploadError && <div className="field-error">{uploadError}</div>}
        </div>
      )}

      {value.mode === "existing" && (
        <div className="form-group">
          <span className="form-label">Choose an existing resume</span>
          <small className="wz-hint">The profile will share that resume; it is not copied.</small>
          {choices.length === 0 && <p className="muted">No resume is stored yet. Paste or upload one.</p>}
          {choices.map((item) => {
            const active = value.existingRef && value.existingRef.key === item.key;
            return (
              <button
                key={item.key}
                type="button"
                className={`wz-file-item${active ? " active" : ""}`}
                aria-pressed={Boolean(active)}
                onClick={() => onChange({ ...value, existingRef: item })}
              >
                <span className="wz-fname">{item.name}</span>
                {item.date && <span className="wz-fdate">{item.date}</span>}
              </button>
            );
          })}
        </div>
      )}

      {headsUp && (
        <div className="callout warn" role="status" data-role="resume-heads-up">
          {headsUp}
          {needsContinue && (
            <div style={{ marginTop: 8 }}>
              <button type="button" className="button secondary small" onClick={() => setContinuedText(value.text)}>
                Continue anyway
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

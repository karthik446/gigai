import { useEffect, useState } from "react";
import { getResumeDisplay, putResumeDisplay } from "../api.js";
import {
  AUTO_FIT_HELP,
  PREVIEW_LINES,
  PRIVACY_NOTE,
  SPACING_MAX,
  SPACING_MIN,
  SPACING_STEP,
  buildPutBody,
  clampSpacing,
  draftFromResponse,
  previewGap,
  spacingDisabled,
  spacingLabel,
} from "../resumeDisplayModel.js";

// The fields and live preview, shared by this panel and the setup wizard's
// "Resume display" step (one implementation). `onChange(patch)` merges a
// patch into the draft. 0110-046: no name or contact fields: GigAI stores
// none; they are typed in the Generate PDF form for one PDF.
export function ResumeDisplayFields({ draft, onChange }) {
  return (
    <>
      <div className="form-group">
        <label className="form-label" htmlFor="resume-display-title">
          Title (this profile)
        </label>
        <input id="resume-display-title" type="text" className="text-input" value={draft.title} onChange={(event) => onChange({ title: event.target.value })} placeholder="e.g. Staff AI Engineer" />
      </div>
      <div className="form-group" data-role="spacing-control">
        <label className="form-label" htmlFor="resume-display-spacing">
          Spacing <span data-role="spacing-value">{spacingLabel(draft.spacing_scale)}</span>
        </label>
        <input
          id="resume-display-spacing"
          type="range"
          min={SPACING_MIN}
          max={SPACING_MAX}
          step={SPACING_STEP}
          value={clampSpacing(draft.spacing_scale)}
          disabled={spacingDisabled(draft)}
          onChange={(event) => onChange({ spacing_scale: clampSpacing(event.target.value) })}
        />
        <label className="checkbox-label" htmlFor="resume-display-auto-fit">
          <input id="resume-display-auto-fit" type="checkbox" checked={draft.auto_fit !== false} onChange={(event) => onChange({ auto_fit: event.target.checked })} /> Auto fit
        </label>
        <p className="muted">{AUTO_FIT_HELP}</p>
      </div>
      <h3>Preview</h3>
      <div className="resume-display-preview-row">
        <div className="md-preview" data-role="resume-display-preview" style={{ whiteSpace: "normal" }}>
          <div className="muted" style={{ fontWeight: 700, fontSize: "1.1rem" }}>
            Your name
          </div>
          {draft.title && <div>{draft.title}</div>}
          <div className="muted">Your contact details, typed when you generate a PDF</div>
        </div>
        <div className="spacing-preview" data-role="spacing-preview" aria-hidden="true" style={{ gap: previewGap(draft.spacing_scale) }}>
          {Array.from({ length: PREVIEW_LINES }, (_unused, index) => (
            <span key={index} className="spacing-preview-line" style={index === PREVIEW_LINES - 1 ? { width: "60%" } : undefined} />
          ))}
        </div>
      </div>
    </>
  );
}

// 0.1.10-003 1b / 0110-046: "Resume display" on the profile page: this
// profile's title and the PDF layout (GET/PUT /api/resume-display). The
// layout is stored once per machine; the title is this profile's.
export default function ResumeDisplayPanel({ profileId }) {
  const [draft, setDraft] = useState(null);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [savedNote, setSavedNote] = useState(false);

  useEffect(() => {
    let live = true;
    setDraft(null);
    setError(null);
    setSavedNote(false);
    getResumeDisplay(profileId)
      .then((response) => live && setDraft(draftFromResponse(response)))
      .catch((err) => live && setError(err.message || String(err)));
    return () => {
      live = false;
    };
  }, [profileId]);

  function change(patch) {
    setDraft((current) => ({ ...current, ...patch }));
    setSavedNote(false);
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const response = await putResumeDisplay(buildPutBody(draft, profileId));
      setDraft(draftFromResponse({ ...response, title: draft.title }));
      setSavedNote(true);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="panel" id="resume-display">
      <h2>Resume display</h2>
      <p className="muted">The title and layout of your resume PDF. {PRIVACY_NOTE}</p>
      {error && <div className="callout danger">{error}</div>}
      {!draft && !error && <p className="muted">Loading…</p>}
      {draft && (
        <>
          <ResumeDisplayFields draft={draft} onChange={change} />
          <div className="actions">
            {savedNote && <span className="muted">Saved.</span>}
            <button type="button" className="button small" onClick={save} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </>
      )}
    </section>
  );
}

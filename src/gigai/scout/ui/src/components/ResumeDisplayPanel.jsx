import { useEffect, useState } from "react";
import { getResumeDisplay, putResumeDisplay } from "../api.js";
import {
  AUTO_FIT_HELP,
  KIND_LABELS,
  KIND_PLACEHOLDERS,
  MAX_CONTACT,
  PREVIEW_LINES,
  PRIVACY_NOTE,
  SPACING_MAX,
  SPACING_MIN,
  SPACING_STEP,
  addEntry,
  availableKinds,
  buildPutBody,
  draftFromResponse,
  moveEntry,
  previewGap,
  previewHeader,
  removeEntry,
  setEntryKind,
  setEntryValue,
  clampSpacing,
  spacingDisabled,
  spacingLabel,
} from "../resumeDisplayModel.js";

// The fields, reorder controls and live preview, shared by this panel and the
// setup wizard's "Resume display" step (one implementation). `onChange(patch)`
// merges a patch into the draft.
export function ResumeDisplayFields({ draft, onChange }) {
  const preview = previewHeader(draft);
  const addable = availableKinds(draft.contact);
  return (
    <>
      <div className="form-group">
        <label className="form-label" htmlFor="resume-display-name">
          Name
        </label>
        <input id="resume-display-name" type="text" className="text-input" value={draft.name} onChange={(event) => onChange({ name: event.target.value })} />
      </div>
      <div className="form-group">
        <label className="form-label" htmlFor="resume-display-title">
          Title (this profile)
        </label>
        <input id="resume-display-title" type="text" className="text-input" value={draft.title} onChange={(event) => onChange({ title: event.target.value })} placeholder="e.g. Staff AI Engineer" />
      </div>
      {draft.contact.map((entry, index) => (
        <div className="action-item" key={index} data-contact-index={index}>
          <select className="text-input" aria-label="Kind" value={entry.kind} onChange={(event) => onChange({ contact: setEntryKind(draft.contact, index, event.target.value) })} style={{ maxWidth: 170 }}>
            {[entry.kind, ...availableKinds(draft.contact).filter((kind) => kind !== entry.kind)].map((kind) => (
              <option key={kind} value={kind}>
                {KIND_LABELS[kind]}
              </option>
            ))}
          </select>
          <input
            type="text"
            className="text-input"
            aria-label={KIND_LABELS[entry.kind]}
            value={entry.value}
            placeholder={KIND_PLACEHOLDERS[entry.kind]}
            onChange={(event) => onChange({ contact: setEntryValue(draft.contact, index, event.target.value) })}
          />
          <button type="button" className="button small secondary" aria-label="Move up" disabled={index === 0} onClick={() => onChange({ contact: moveEntry(draft.contact, index, -1) })}>
            ↑
          </button>
          <button type="button" className="button small secondary" aria-label="Move down" disabled={index === draft.contact.length - 1} onClick={() => onChange({ contact: moveEntry(draft.contact, index, 1) })}>
            ↓
          </button>
          <button type="button" className="button small secondary" aria-label="Remove" onClick={() => onChange({ contact: removeEntry(draft.contact, index) })}>
            Remove
          </button>
        </div>
      ))}
      {draft.contact.length < MAX_CONTACT && addable.length > 0 && (
        <div className="card-actions" style={{ marginTop: 8 }}>
          {addable.map((kind) => (
            <button key={kind} type="button" className="button small secondary" onClick={() => onChange({ contact: addEntry(draft.contact, kind) })}>
              + {KIND_LABELS[kind]}
            </button>
          ))}
        </div>
      )}
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
          <div style={{ fontWeight: 700, fontSize: "1.1rem" }}>{preview.name || <span className="muted">Your name</span>}</div>
          {preview.title && <div>{preview.title}</div>}
          {preview.contactLine && <div className="muted">{preview.contactLine}</div>}
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

// 0.1.10-003 1b: "Resume display" on the profile page: the name, title and
// contact line printed on a tailored-resume PDF (GET/PUT /api/resume-display).
// The name and contact line are stored once per machine; the title is this
// profile's. `suggested` prefills once and is only saved when the user does.
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
      setDraft({ ...draftFromResponse({ ...response, title: draft.title }), prefilled: false });
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
      <p className="muted">The header printed on your tailored-resume PDF. {PRIVACY_NOTE}</p>
      {error && <div className="callout danger">{error}</div>}
      {!draft && !error && <p className="muted">Loading…</p>}
      {draft && (
        <>
          {draft.prefilled && <div className="callout info">Filled in from your resume. Check it, then save.</div>}
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

import { ResumeDisplayFields } from "../components/ResumeDisplayPanel.jsx";
import { PRIVACY_NOTE } from "../resumeDisplayModel.js";

// Screen 2 (0110-013) -- "Resume display": the name, title and contact line
// printed on a tailored-resume PDF. It is the settings panel's own form
// (ResumeDisplayFields), filled from the resume header as there; skippable.
// Nothing is sent from here: Finish saves it with PUT /api/resume-display
// (wizardFinish.js), and never to a model endpoint.
export default function ResumeDisplayScreen({ fields, loadError, onChange, onSkip }) {
  const draft = fields.display;
  return (
    <section className="panel">
      <h2>Resume display</h2>
      <p className="muted">The header printed on your tailored-resume PDF. {PRIVACY_NOTE}</p>
      {loadError && <div className="callout danger">Could not read the saved header: {loadError}</div>}
      {!draft && !loadError && <p className="muted">Loading…</p>}
      {draft && (
        <>
          {fields.displaySkipped && <div className="callout info">Skipped: your saved header stays as it is. Edit anything to include it.</div>}
          {!fields.displaySkipped && draft.prefilled && <div className="callout info">Filled in from your resume. Check it; Finish saves it.</div>}
          <ResumeDisplayFields draft={draft} onChange={onChange} />
        </>
      )}
      <div className="actions" style={{ marginTop: 12 }}>
        <button type="button" className="button secondary" onClick={onSkip} data-role="skip-resume-display">
          Skip this step
        </button>
      </div>
    </section>
  );
}

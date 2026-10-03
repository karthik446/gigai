import { ResumeDisplayFields } from "../components/ResumeDisplayPanel.jsx";
import { PRIVACY_NOTE } from "../resumeDisplayModel.js";

// Screen 2 (0110-013) -- "Resume display": this profile's title and the PDF
// layout. It is the settings panel's own form (ResumeDisplayFields);
// skippable. 0110-046: no name
// or contact fields: GigAI stores none (they are typed at Generate PDF time).
// Nothing is sent from here: Finish saves it with PUT /api/resume-display
// (wizardFinish.js), and never to a model endpoint.
export default function ResumeDisplayScreen({ fields, loadError, onChange, onSkip }) {
  const draft = fields.display;
  return (
    <section className="panel">
      <h2>Resume display</h2>
      <p className="muted">The title and layout of your resume PDF. {PRIVACY_NOTE}</p>
      {loadError && <div className="callout danger">Could not read the saved settings: {loadError}</div>}
      {!draft && !loadError && <p className="muted">Loading…</p>}
      {draft && (
        <>
          {fields.displaySkipped && <div className="callout info">Skipped: your saved title and layout stay as they are. Edit anything to include them.</div>}
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

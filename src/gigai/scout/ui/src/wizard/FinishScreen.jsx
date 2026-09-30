import { reviewRows } from "./wizardState.js";

// Screen 4 -- the live review table and what is still missing before a
// search. The mockup's Local-only / OpenAI mode picker is omitted (scope doc
// F2: "Local-mode toggle: omit for 0.1.9"), and so are the discovery cadence
// and the budget per run (A2: Discover is hidden in 0.1.9).
//
// uat-bug-020: Finish stores the resume itself, so there is no list of
// commands to run. `hints` (wizardState.setupHints) holds one line per thing
// that is actually missing -- an API key that is not set, Ollama when it is
// the chosen model -- and the section is left out when nothing is.
export default function FinishScreen({ fields, resumes, hints, fieldErrors, saveError, saved }) {
  const errors = fieldErrors || {};
  const rows = reviewRows(fields, resumes);
  const missing = hints || [];

  return (
    <section className="panel">
      <h2>Review</h2>

      <table className="wz-summary">
        <tbody>
          {rows.map(([key, value]) => (
            <tr key={key}>
              <td className="k">{key}</td>
              <td>{value}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {missing.length > 0 && (
        <div data-role="setup-hints" style={{ marginTop: 16 }}>
          <h3>Not set up yet</h3>
          <ul className="wz-hints">
            {missing.map((line) => (
              <li key={line.id} data-hint={line.id}>
                {line.text}: <code>{line.command}</code>
                {line.note && <span className="wz-hint-note"> ({line.note})</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {errors._ && <div className="callout danger" style={{ marginTop: 12 }}>{errors._}</div>}
      {saveError && <div className="callout danger" style={{ marginTop: 12 }}>{saveError}</div>}

      {!saved && (
        <p className="muted" style={{ marginTop: 16 }}>
          <strong>Finish</strong> stores your resume on this machine and saves the profile and your preferences.
        </p>
      )}
      {saved && (
        <div className="callout ok" style={{ marginTop: 16 }}>
          Saved: profile <strong>{saved.profile.label}</strong>, its resume and your preferences.
        </div>
      )}
    </section>
  );
}

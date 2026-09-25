import { useState } from "react";
import { buildCommands, commandsAsText, reviewRows } from "./wizardState.js";

// Screen 4 -- cadence + budget, the live review table, then (after Finish
// saved the profile and the preferences) the exact commands to run with the
// real profile id. The mockup's Local-only / OpenAI mode picker is omitted
// (scope doc F2: "Local-mode toggle: omit for 0.1.9"); the command list
// follows the model target chosen on screen 1 instead.
export default function FinishScreen({ fields, setField, resumes, fieldErrors, saveError, saved, onDone }) {
  const [copyLabel, setCopyLabel] = useState("Copy all");
  const errors = fieldErrors || {};
  const rows = reviewRows(fields, resumes);
  const commands = buildCommands({
    modelTarget: fields.modelTarget,
    resumeMode: fields.resumeMode,
    uploadName: fields.uploadName,
    profileId: saved ? saved.profile.profile_id : null,
  });

  function copyAll() {
    const text = commandsAsText(commands);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard
        .writeText(text)
        .then(() => setCopyLabel("Copied!"))
        .catch(() => setCopyLabel("Select and copy manually"));
    } else {
      setCopyLabel("Select and copy manually");
    }
    setTimeout(() => setCopyLabel("Copy all"), 1500);
  }

  return (
    <section className="panel">
      <h2>Discovery + finish</h2>

      <div className="field-row">
        <div className="form-group field">
          <label className="form-label" htmlFor="wz-cadence">
            Discovery cadence (days)
          </label>
          <input
            id="wz-cadence"
            type="number"
            className="text-input"
            min={1}
            value={fields.cadenceDays}
            disabled={Boolean(saved)}
            onChange={(event) => setField("cadenceDays", Math.max(1, Math.floor(Number(event.target.value) || 1)))}
          />
          {errors.cadence_days && <div className="field-error">{errors.cadence_days}</div>}
        </div>
        <div className="form-group field">
          <label className="form-label" htmlFor="wz-budget">
            Budget per run ($)
          </label>
          <input
            id="wz-budget"
            type="number"
            className="text-input"
            min={0.01}
            step={0.01}
            value={fields.budgetUsdPerSession}
            disabled={Boolean(saved)}
            onChange={(event) => setField("budgetUsdPerSession", Number(event.target.value) || 0.01)}
          />
          {errors.budget_usd_per_session && <div className="field-error">{errors.budget_usd_per_session}</div>}
        </div>
      </div>

      <h3>Review</h3>
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

      {errors._ && <div className="callout danger" style={{ marginTop: 12 }}>{errors._}</div>}
      {saveError && <div className="callout danger" style={{ marginTop: 12 }}>{saveError}</div>}

      {saved && (
        <div className="callout ok" style={{ marginTop: 16 }}>
          Saved: profile <strong>{saved.profile.label}</strong> (<code>{saved.profile.profile_id}</code>) and your
          preferences.
        </div>
      )}

      <h3>Run these commands</h3>
      {!saved && (
        <p className="muted">
          Press <strong>Finish</strong> to save; the profile id in the <code>resume add</code> line fills in then.
        </p>
      )}
      <div className="wz-copy-row">
        <button type="button" className="button small secondary" onClick={copyAll}>
          {copyLabel}
        </button>
      </div>
      <div className="wz-commands">
        {commands.map((line) => (
          <div className="wz-command" key={line.text}>
            $ {line.text}
            {line.comment && <span className="wz-comment">{"   # " + line.comment}</span>}
          </div>
        ))}
      </div>

      {saved && (
        <div style={{ marginTop: 16 }}>
          <div className="callout ok">
            <strong>You're set:</strong> once you run these, Scout opens at <code>http://127.0.0.1:8765</code>.
          </div>
          <button type="button" className="button" onClick={onDone}>
            Done
          </button>
        </div>
      )}
    </section>
  );
}

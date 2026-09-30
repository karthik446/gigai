import { useEffect, useState } from "react";
import { getSecretsStatus, putConfigSources } from "../api.js";
import { EXA_TOGGLE_LABEL, exaIsOn, exaKeyHint } from "../exaSourceModel.js";

// uat-bug-033: Exa is optional (a model target is the only requirement; the
// bundled company list and stored boards are the source). Turning it on saves
// straight away; with no key set the key hint shows (keys are added from the
// CLI, as before). Off = no Exa requests.
export default function ExaSourceToggle({ config, reloadConfig }) {
  const [keys, setKeys] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const exaOn = exaIsOn(config);

  useEffect(() => {
    let current = true;
    getSecretsStatus()
      .then((response) => current && setKeys((response && response.keys) || null))
      .catch(() => current && setKeys(null));
    return () => {
      current = false;
    };
  }, []);

  async function handleChange(event) {
    const next = event.target.checked;
    setSaving(true);
    setError(null);
    try {
      await putConfigSources({ exa: next });
      await reloadConfig();
    } catch (caught) {
      setError(caught.message || String(caught));
    } finally {
      setSaving(false);
    }
  }

  const hint = exaKeyHint({ exaOn, keys });
  return (
    <section className="panel" id="settings-exa">
      <h2>Search sources</h2>
      <p className="muted">
        Runs search the bundled company list and stored company boards. Exa is an optional extra.
      </p>
      <label>
        <input type="checkbox" checked={exaOn} disabled={saving || !config} onChange={handleChange} /> {EXA_TOGGLE_LABEL}
      </label>
      {hint && (
        <p className="callout warn" data-role="exa-key-hint">
          {hint.text}: run <code>{hint.command}</code>
        </p>
      )}
      {error && <div className="callout danger">Could not save: {error}</div>}
    </section>
  );
}

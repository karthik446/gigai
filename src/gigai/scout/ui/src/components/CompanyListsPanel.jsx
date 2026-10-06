import { useEffect, useState } from "react";
import TagListInput from "./TagListInput.jsx";
import { putSetup } from "../api.js";
import { companyListsBody, companyListsChanged, companyListsOf } from "../companyListsModel.js";

// 0.1.11.2 (UAT-008): the exclude / always-watch lists, moved out of onboarding. Both are shared by every profile.
// Add-by-URL is the form below this panel (AddCompanyForm).
export default function CompanyListsPanel({ prefs, onSaved }) {
  const [lists, setLists] = useState(() => companyListsOf(prefs));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [savedNote, setSavedNote] = useState(false);

  useEffect(() => {
    setLists(companyListsOf(prefs));
  }, [prefs]);

  if (!prefs) {
    return null;
  }

  async function save() {
    setSaving(true);
    setError(null);
    setSavedNote(false);
    try {
      await putSetup(companyListsBody(prefs, lists));
      setSavedNote(true);
      if (onSaved) {
        onSaved();
      }
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="panel" id="settings-company-lists" data-role="company-lists">
      <h2>Companies</h2>
      <p className="muted">Both lists are shared by every profile and drive weekly discovery as well as matching.</p>
      <TagListInput
        id="settings-exclude"
        label="Exclude"
        values={lists.exclude}
        onChange={(values) => setLists((prev) => ({ ...prev, exclude: values }))}
        placeholder="not interested / current employer…"
      />
      <TagListInput
        id="settings-watch"
        label="Always watch"
        values={lists.watch}
        onChange={(values) => setLists((prev) => ({ ...prev, watch: values }))}
        placeholder="optional…"
      />
      {error && <div className="callout danger">{error}</div>}
      <div className="actions" style={{ justifyContent: "flex-start" }}>
        <button type="button" className="button" data-action="save-company-lists" onClick={save} disabled={saving || !companyListsChanged(prefs, lists)}>
          {saving ? "Saving…" : "Save companies"}
        </button>
        {savedNote && <span className="muted" data-role="company-lists-saved">Saved.</span>}
      </div>
    </section>
  );
}

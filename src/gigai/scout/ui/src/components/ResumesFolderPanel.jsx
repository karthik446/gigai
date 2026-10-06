import { useEffect, useState } from "react";
import { getResumesFolder, putResumesFolder } from "../api.js";
import { folderChanged, folderLine, folderRequest } from "../resumesFolderModel.js";

// 0110-10-05 A: the resumes folder (GET/PUT /api/resumes-folder): master.md and the PDFs made without a
// header. 0.1.11.4 J3: legacy for job resumes (new picks go to the jobs folder, JobsFolderPanel). Save sends the typed path; "Use
// the default" sends an empty one. Files already written are not moved.
export default function ResumesFolderPanel() {
  const [folder, setFolder] = useState(null);
  const [typed, setTyped] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let current = true;
    getResumesFolder()
      .then((response) => current && setFolder(response))
      .catch((caught) => current && setError(`Could not load the folder: ${caught.detail || caught.message || String(caught)}`));
    return () => {
      current = false;
    };
  }, []);

  async function save(body) {
    setSaving(true);
    setError(null);
    try {
      setFolder(await putResumesFolder(body));
      setTyped("");
    } catch (caught) {
      setError(`Could not save: ${caught.detail || caught.message || String(caught)}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="panel" id="settings-resumes-folder">
      <h2>Resumes folder</h2>
      <p className="muted">
        This is the older folder. Job resumes you picked before now are still here, left as they were; new picks go to the Jobs folder
        above. The Master resume file (master.md) and PDFs made without a header are still saved here.
      </p>
      {folder && (
        <p data-testid="resumes-folder" data-source={folder.source}>
          <code>{folderLine(folder)}</code>
        </p>
      )}
      <div className="actions" style={{ justifyContent: "flex-start" }}>
        <input
          type="text"
          className="text-input"
          aria-label="Another folder"
          data-testid="resumes-folder-input"
          placeholder="Another folder, e.g. ~/Resumes"
          value={typed}
          disabled={saving}
          onChange={(event) => setTyped(event.target.value)}
        />
        <button type="button" className="button secondary" data-testid="resumes-folder-save" disabled={saving || !folderChanged(folder, typed)} onClick={() => save(folderRequest(typed))}>
          Save
        </button>
        {folder && folder.source === "setting" && (
          <button type="button" className="button secondary" data-testid="resumes-folder-default" disabled={saving} onClick={() => save(folderRequest(""))}>
            Use the default
          </button>
        )}
      </div>
      {error && (
        <div className="callout danger" role="alert" data-testid="resumes-folder-error">
          {error}
        </div>
      )}
    </section>
  );
}

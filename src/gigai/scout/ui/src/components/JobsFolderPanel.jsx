import { useEffect, useState } from "react";
import { getJobsFolder, openJobsFolder, putJobsFolder } from "../api.js";
import { folderChanged, folderLine, folderRequest, openFolderNote } from "../resumesFolderModel.js";

// 0.1.11.4 J3: the jobs folder (GET/PUT /api/jobs-folder): one folder per application,
// <company>/<role>/resume.md, where new picks go. Same mechanism as the resumes folder: Save sends the
// typed path, "Use the default" sends an empty one, and the server's plain refusal is shown as it is.
// "Open folder" asks the server to show the folder in the computer's file manager (it sends no path).
export default function JobsFolderPanel() {
  const [folder, setFolder] = useState(null);
  const [typed, setTyped] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [note, setNote] = useState("");

  useEffect(() => {
    let current = true;
    getJobsFolder()
      .then((response) => current && setFolder(response))
      .catch((caught) => current && setError(`Could not load the folder: ${caught.detail || caught.message || String(caught)}`));
    return () => {
      current = false;
    };
  }, []);

  async function save(body) {
    setSaving(true);
    setError(null);
    setNote("");
    try {
      setFolder(await putJobsFolder(body));
      setTyped("");
    } catch (caught) {
      setError(`Could not save: ${caught.detail || caught.message || String(caught)}`);
    } finally {
      setSaving(false);
    }
  }

  function open() {
    setNote("");
    openJobsFolder()
      .then((response) => setNote(openFolderNote(response, null)))
      .catch((caught) => setNote(openFolderNote(null, caught)));
  }

  return (
    <section className="panel" id="settings-jobs-folder">
      <h2>Jobs folder</h2>
      <p className="muted">
        Each job you pick a resume for gets its own folder here: <code>&lt;company&gt;/&lt;role&gt;/resume.md</code>. A file you change
        there stays yours: a newer one gets a new name. Folders already made are not moved when you choose another folder.
      </p>
      {folder && (
        <p data-testid="jobs-folder" data-source={folder.source}>
          <code>{folderLine(folder)}</code>{" "}
          <button type="button" className="link-button" data-testid="jobs-folder-open" disabled={!folder.exists} onClick={open}>
            Open folder
          </button>
        </p>
      )}
      {note && (
        <p className="muted small" data-testid="jobs-folder-note">
          {note}
        </p>
      )}
      <div className="actions" style={{ justifyContent: "flex-start" }}>
        <input
          type="text"
          className="text-input"
          aria-label="Another jobs folder"
          data-testid="jobs-folder-input"
          placeholder="Another folder, e.g. ~/Applications"
          value={typed}
          disabled={saving}
          onChange={(event) => setTyped(event.target.value)}
        />
        <button type="button" className="button secondary" data-testid="jobs-folder-save" disabled={saving || !folderChanged(folder, typed)} onClick={() => save(folderRequest(typed))}>
          Save
        </button>
        {folder && folder.source === "setting" && (
          <button type="button" className="button secondary" data-testid="jobs-folder-default" disabled={saving} onClick={() => save(folderRequest(""))}>
            Use the default
          </button>
        )}
      </div>
      {error && (
        <div className="callout danger" role="alert" data-testid="jobs-folder-error">
          {error}
        </div>
      )}
    </section>
  );
}

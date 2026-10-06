// 0110-10-05: the resumes folder and the "edited" mark, as the job page and
// Settings show them. Pure functions over GET /api/resumes-folder
// ({path, shown, source, default, exists, files?: {markdown, pdf}}) and a
// TailorResponse's optional `edited` ({written_by, edited_at, source}).

// The folder the way the user types it, with what said so.
export function folderLine(folder) {
  if (!folder || !folder.shown) {
    return "";
  }
  return folder.source === "setting" ? folder.shown : `${folder.shown} (the default)`;
}

// Where this job's tailored markdown is in the folder, or "" when it is not
// there (the folder could not be written, or the file was removed).
export function folderFilePath(folder) {
  const name = folder && folder.files && folder.files.markdown;
  if (!folder || !folder.shown || !name) {
    return "";
  }
  return `${folder.shown.replace(/\/+$/, "")}/${name}`;
}

// 0.1.11.4 J3: where this job's resume is in the jobs folder, from GET /api/jobs-folder?profile_id&job_identity
// ({path, shown, ..., job: {shown, relative, files: {resume}} | null}), as the user types it
// (~/Documents/GigAI/jobs/<company>/<role>/resume.md); "" when GigAI has made no folder or file for the job.
export function jobFilePath(folder) {
  const job = folder && folder.job;
  const name = job && job.files && job.files.resume;
  if (!job || !job.shown || !name) {
    return "";
  }
  return `${job.shown.replace(/\/+$/, "")}/${name}`;
}

// The line under the path after "Open folder": the server's plain sentence, or why it could not be asked.
export function openFolderNote(response, error) {
  if (error) {
    return `Could not open the folder: ${error.detail || error.message || String(error)}`;
  }
  return response && response.message ? String(response.message) : "";
}

// PUT /api/resumes-folder's body for what was typed: an empty field is the
// default folder.
export function folderRequest(typed) {
  return { path: String(typed || "").trim() };
}

// True when Save has something to send: a path that is not the shown one.
export function folderChanged(folder, typed) {
  const value = String(typed || "").trim();
  return Boolean(value) && Boolean(folder) && value !== folder.shown && value !== folder.path;
}

// "Edited by your agent" / "Edited by you", with the writer's source when
// there is one; "" for a resume a model tailored (no `edited`).
export function editedLine(response) {
  const edited = response && response.edited;
  if (!edited || typeof edited !== "object") {
    return "";
  }
  const who = edited.written_by === "agent" ? "Edited by your agent" : "Edited by you";
  return edited.source ? `${who} · source: ${edited.source}` : who;
}

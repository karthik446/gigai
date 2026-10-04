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

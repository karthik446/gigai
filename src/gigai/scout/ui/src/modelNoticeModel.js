// 0.1.11 UINOTICE: the one line under the verdict when the model that made an
// assessment is not one GigAI's accuracy results are for. The assessment API's
// served item (`model_notice`: {text, link: {label, path}, ...}) writes the sentence; this
// only reads it, as a pure function (no React) so the node-backed test can pin
// it. The link is the docs page `path`, shown under its words (`label`).
export const DOCS_URL = "https://karthik446.github.io/gigai/latest/";
const DOC_PATH = /^[a-z0-9][a-z0-9/-]*$/;

// {text, label, href} for a served assessment item that carries a notice, else null (a
// missing or malformed notice shows nothing; a bad link shows the text alone).
export function modelNoticeLine(item) {
  const notice = item && item.model_notice;
  if (!notice || typeof notice.text !== "string" || !notice.text.trim()) {
    return null;
  }
  const link = notice.link;
  const linked = link && typeof link.label === "string" && link.label.trim() && typeof link.path === "string" && DOC_PATH.test(link.path);
  return { text: notice.text.trim(), label: linked ? link.label.trim() : null, href: linked ? `${DOCS_URL}${link.path}/` : null };
}

// 0.1.11 (orchestrator #96): the served item's `requirements_note` (a thin answer stored after one retry), as the one
// line near the requirements table; null when absent or blank.
export function requirementsNoteLine(item) {
  const note = item && item.requirements_note;
  return typeof note === "string" && note.trim() ? note.trim() : null;
}

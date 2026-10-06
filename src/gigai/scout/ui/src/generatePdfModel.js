// 0110-046: the Generate PDF form, pure (no React, no fetch, no storage) so
// tests/api_e2e/test_ui_generate_pdf_model.py can run it under node.
//
// GigAI never stores the user's name or contact details. The values live
// in the form's React state while it is open and travel ONLY in the one render
// request (POST /api/tailored-resumes/pdf or POST /api/resume/pdf, `header`);
// the server fills the PDF's header and drops them. Nothing here, or in the
// form, writes them to localStorage, sessionStorage, a cookie or the URL. The
// `autocomplete` tokens let the BROWSER offer its own autofill, if the user
// allows it; GigAI keeps nothing.

// A link field is type "text" with inputMode "url": a browser's type="url"
// check would refuse "linkedin.com/in/you" (no scheme).
export const FIELDS = [
  { key: "name", label: "Name", autocomplete: "name", type: "text", placeholder: "Your name" },
  { key: "email", label: "Email", autocomplete: "email", type: "email", placeholder: "you@example.com" },
  { key: "phone", label: "Phone", autocomplete: "tel", type: "tel", placeholder: "+1 555 123 4567" },
  { key: "location", label: "Location", autocomplete: "address-level2", type: "text", placeholder: "City, State" },
  { key: "linkedin", label: "LinkedIn", autocomplete: "url", type: "text", inputMode: "url", placeholder: "linkedin.com/in/you" },
  { key: "link", label: "Other link", autocomplete: "url", type: "text", inputMode: "url", placeholder: "github.com/you or your site" },
  // 0.1.11.3 item 6: optional, its own header line; `startValues` prefills it from the profile's sponsorship answer.
  {
    key: "work_authorization",
    label: "Work authorization (optional)",
    autocomplete: "off",
    type: "text",
    placeholder: "e.g. H-1B, requires sponsorship",
    hint: "Printed as a line of the PDF's header only. Leave it empty for no line.",
  },
];

export const MAX_VALUE = 200;

export function emptyValues() {
  return Object.fromEntries(FIELDS.map((field) => [field.key, ""]));
}

// 0.1.11.3 item 6: what the form starts with EVERY time it opens. The profile
// stores only yes/no ("Visa sponsorship required"), so a yes prefills the
// "Work authorization" line with a plain sentence the user edits to the exact
// wording (e.g. "H-1B, requires sponsorship"). Like the other values, the
// edit is never remembered: the next form starts from the profile again.
export const WORK_AUTHORIZATION_PREFILL = "Requires visa sponsorship";

export function startValues({ visaRequired = false, file = null } = {}) {
  const start = { ...emptyValues(), work_authorization: visaRequired ? WORK_AUTHORIZATION_PREFILL : "" };
  const filled = fileValues(file);
  if (!filled) {
    return start;
  }
  // 0.1.11.3 item 13: the header file's values, then the profile's answer for
  // the "Work authorization" line only when the file has no such key at all
  // (a key that is there and empty means "no line").
  for (const field of FIELDS) {
    if (field.key !== "work_authorization" || file.has_work_authorization === true) {
      start[field.key] = typeof filled[field.key] === "string" ? filled[field.key] : "";
    }
  }
  const links = linkRows(filled.links);
  return links.length > 0 ? { ...start, links } : start;
}

// 0.1.11.3 item 13: the person's own header file (default
// ~/Documents/GigAI/header.json), as POST /api/pdf-header answers it:
// {state: filled | missing | invalid, shown, message, warning,
// has_work_authorization, values}. The server reads the file for THIS form
// only; what the person leaves in the form is what prints (form edits > the
// file > the profile's sponsorship answer). Nothing is written back unless
// the person presses "Save these details to <path>" (item 14, below).
//
// 0.1.11.3 item 14: a value of the file that is empty or starts with REPLACE
// (a template placeholder) is skipped by the server: it never reaches this
// form. `placeholders` names the skipped fields, `notice` is the server's one
// sentence about them, `name_note` says "<path> has no name yet." State
// "placeholder": nothing in the file is filled in, so nothing is filled here.
export const MAX_LINKS = 6;

export function fileValues(file) {
  return file && file.state === "filled" && file.values && typeof file.values === "object" ? file.values : null;
}

function linkRows(links) {
  if (!Array.isArray(links)) {
    return [];
  }
  return links
    .filter((row) => row && typeof row === "object")
    .map((row) => ({
      label: typeof row.label === "string" ? row.label.trim().slice(0, MAX_VALUE) : "",
      url: typeof row.url === "string" ? row.url.trim().slice(0, MAX_VALUE) : "",
    }))
    .slice(0, MAX_LINKS);
}

// What the form says about the file: "Filled from <path>" when it was read,
// else the server's one plain sentence (what is wrong, where the file goes),
// plus its warning when other users of the computer can read the file.
export function headerSource(file) {
  if (!file || typeof file.state !== "string") {
    return null;
  }
  const text = (value) => (typeof value === "string" && value ? value : null);
  // The path the Save button names and writes: the one the server reads.
  const shown = text(file.shown);
  const nameNote = text(file.name_note);
  if (fileValues(file)) {
    return { filled: true, text: `Filled from ${shown || "your header file"}`, warning: text(file.warning), shown, notice: text(file.notice), nameNote };
  }
  // The message of a file that is all placeholders IS the notice: said once.
  return { filled: false, text: text(file.message), warning: null, shown, notice: null, nameNote: file.state === "placeholder" ? nameNote : null };
}

// 0.1.11.3 item 14: what "Save these details to <path>" sends (POST
// /api/pdf-header/save): the header FILE's shape, from what is in the form
// now. The LinkedIn field is a link labelled LinkedIn, the "Other link" field
// a link labelled Link, then the file's own link rows; a link with no url is
// left out. work_authorization is always sent: empty means "no line".
export const REPLACE_QUESTION = "Replace the existing header.json?";

export function headerFileBody(values) {
  const body = headerBody(values);
  const links = [
    ...(body.linkedin ? [{ label: "LinkedIn", url: body.linkedin }] : []),
    ...(body.link ? [{ label: "Link", url: body.link }] : []),
    ...(body.links || []),
  ];
  return { name: body.name, email: body.email, phone: body.phone, location: body.location, links, work_authorization: body.work_authorization };
}

// Something to save: at least one detail typed.
export function canSave(values) {
  const body = headerFileBody(values);
  return [body.name, body.email, body.phone, body.location, body.work_authorization].some((value) => value.length > 0) || body.links.length > 0;
}

// What the form says after a save request: {tone, text, ask}. `ask` is true
// when a file is already there: the form then asks REPLACE_QUESTION.
export function saveResult(answer) {
  const text = answer && typeof answer.message === "string" ? answer.message : "";
  if (answer && answer.state === "saved") {
    return { tone: "saved", text, ask: false };
  }
  if (answer && answer.state === "exists") {
    return { tone: "ask", text, ask: true };
  }
  return { tone: "failed", text: text || "Your details were not saved.", ask: false };
}

// The render request's `header`: every field, trimmed and capped (the server
// refuses a longer value without echoing it).
export function headerBody(values) {
  const source = values || {};
  const body = Object.fromEntries(
    FIELDS.map((field) => {
      const value = typeof source[field.key] === "string" ? source[field.key].trim() : "";
      return [field.key, value.slice(0, MAX_VALUE)];
    }),
  );
  // The header file's other links (item 13): the rows that still hold a URL.
  const links = linkRows(source.links).filter((row) => row.url);
  return links.length > 0 ? { ...body, links } : body;
}

// Generate PDF needs at least a name: a header with no name is what an agent's
// headerless PDF already is.
export function canGenerate(values) {
  return headerBody(values).name.length > 0;
}

// The one-time contact cleanup's report (GET /api/privacy/cleanup) as the
// notice to show, or null: only when something was removed and the report
// was not shown yet. The text is the server's (counts only, never a value).
export function cleanupNotice(report) {
  if (!report || report.removed_any !== true || report.shown !== false || typeof report.text !== "string") {
    return null;
  }
  return report.text;
}

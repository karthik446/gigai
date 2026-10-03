// 0110-046: the Generate PDF form, pure (no React, no fetch, no storage) so
// tests/api_e2e/test_ui_generate_pdf_model.py can run it under node.
//
// GigAI never stores the user's name or contact details. The six values live
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
];

export const MAX_VALUE = 200;

export function emptyValues() {
  return Object.fromEntries(FIELDS.map((field) => [field.key, ""]));
}

// The render request's `header`: every field, trimmed and capped (the server
// refuses a longer value without echoing it).
export function headerBody(values) {
  const source = values || {};
  return Object.fromEntries(
    FIELDS.map((field) => {
      const value = typeof source[field.key] === "string" ? source[field.key].trim() : "";
      return [field.key, value.slice(0, MAX_VALUE)];
    }),
  );
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

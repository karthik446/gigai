import { useState } from "react";
import { FIELDS, canGenerate, emptyValues, headerBody } from "../generatePdfModel.js";
import { PRIVACY_PDF_LINE, PRIVACY_PROMISE } from "../wording.js";

function saveBlob(blob, fileName) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// 0110-046: the Generate PDF form. Six fields with the standard autocomplete
// tokens (so the browser may offer its own autofill) and one button. The
// values live in this component's state only while it is mounted and go in
// the one render request `render(header)` sends; GigAI stores none of them
// (no localStorage, sessionStorage, cookie or URL). `render` answers
// {blob, fileName} (api.js postTailoredResumePdf / postResumePdf); the file is
// saved under the server's name: <company>-<role>-<date>.pdf, never yours.
export default function GeneratePdfForm({ render, disabled = false }) {
  const [values, setValues] = useState(emptyValues);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [savedAs, setSavedAs] = useState(null);
  const [note, setNote] = useState(null);

  async function submit(event) {
    event.preventDefault();
    if (!canGenerate(values)) {
      return;
    }
    setBusy(true);
    setError(null);
    setSavedAs(null);
    setNote(null);
    try {
      const { blob, fileName, note: fitNote } = await render(headerBody(values));
      saveBlob(blob, fileName);
      setSavedAs(fileName);
      setNote(fitNote || null);
    } catch (err) {
      setError(err.detail || err.message || String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="generate-pdf-form" data-role="generate-pdf-form" autoComplete="on" onSubmit={submit}>
      <p className="muted small" data-role="pdf-privacy">
        <strong>{PRIVACY_PROMISE}</strong> {PRIVACY_PDF_LINE}
      </p>
      <div className="generate-pdf-fields">
        {FIELDS.map((field) => (
          <div className="form-group" key={field.key}>
            <label className="form-label" htmlFor={`generate-pdf-${field.key}`}>
              {field.label}
            </label>
            <input
              id={`generate-pdf-${field.key}`}
              name={field.autocomplete === "url" ? field.key : field.autocomplete}
              type={field.type}
              inputMode={field.inputMode}
              autoComplete={field.autocomplete}
              className="text-input"
              placeholder={field.placeholder}
              value={values[field.key]}
              onChange={(event) => setValues((current) => ({ ...current, [field.key]: event.target.value }))}
            />
          </div>
        ))}
      </div>
      {error && (
        <div className="callout danger" role="alert">
          Could not make the PDF. {error}
        </div>
      )}
      {note && (
        <div className="callout warn" role="status" data-role="pdf-fit-note">
          {note}
        </div>
      )}
      <div className="actions">
        {savedAs && (
          <span className="muted" data-role="pdf-saved">
            Saved as {savedAs}.
          </span>
        )}
        <button type="submit" className="button small" disabled={disabled || busy || !canGenerate(values)} data-role="generate-pdf">
          {busy ? "Preparing…" : "Generate PDF"}
        </button>
      </div>
    </form>
  );
}

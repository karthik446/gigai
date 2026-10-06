import { useEffect, useRef, useState } from "react";
import { postPdfHeader, postPdfHeaderSave } from "../api.js";
import { FIELDS, REPLACE_QUESTION, canGenerate, canSave, fileValues, headerBody, headerFileBody, headerSource, saveResult, startValues } from "../generatePdfModel.js";
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

// 0110-046: the Generate PDF form. Its fields carry the standard autocomplete
// tokens (so the browser may offer its own autofill) and one button. The
// values live in this component's state only while it is mounted and go in
// the one render request `render(header)` sends; GigAI stores none of them
// (no localStorage, sessionStorage, cookie or URL). `render` answers
// {blob, fileName} (api.js postTailoredResumePdf / postResumePdf); the file is
// saved under the server's name: <company>-<role>-<date>.pdf, never yours.
//
// 0.1.11.3 item 6: the optional "Work authorization" line starts, every time
// the form opens, as the profile's sponsorship answer in plain words
// (`visaRequired`). The user edits it for this PDF; it prints in the PDF's
// header only and, like the other values, is not remembered.
//
// 0.1.11.3 item 13: when the person keeps a header file of their own (default
// ~/Documents/GigAI/header.json), the form asks the server for it each time it
// opens (POST /api/pdf-header) and starts from its values: "Filled from
// <path>", every field still editable. What is in the form when the button is
// pressed is what prints: form edits > the file > the profile's answer. The
// file is only read; a missing or unusable one is one plain line here.
//
// 0.1.11.3 item 14: "Save these details to <path>" writes what is in the form
// to that same file, once, when the person clicks it (POST
// /api/pdf-header/save). A file that is already there is never written over
// without the question "Replace the existing header.json?". The values go in
// that request and that file only. A file that still holds REPLACE
// placeholders fills only its real fields; the form names what was skipped.
//
// 0.1.11.3 item 15: when the server says the PDF does not fit its pages
// (`note`) and the caller gives `shorten` (a stored job's resume), the note
// has one button, "Shorten automatically". `shorten()` answers the server's
// own sentence about what was left out; the person then generates again.
export default function GeneratePdfForm({ render, disabled = false, visaRequired = false, shorten = null }) {
  const [values, setValues] = useState(() => startValues({ visaRequired }));
  const [source, setSource] = useState(null);
  const touched = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [savedAs, setSavedAs] = useState(null);
  const [note, setNote] = useState(null);
  const [shortening, setShortening] = useState(false);
  const [shortened, setShortened] = useState(null);
  const [shortenError, setShortenError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(null);

  async function saveDetails(replace) {
    setSaving(true);
    try {
      const result = saveResult(await postPdfHeaderSave(headerFileBody(values), { replace }));
      setSaved(result);
      if (result.tone === "saved") {
        // What the form said about the file when it opened (missing, placeholders) is no longer true.
        setSource((current) => (current ? { filled: false, text: null, warning: null, shown: current.shown, notice: null, nameNote: null } : current));
      }
    } catch (err) {
      setSaved({ tone: "failed", text: err.detail || err.message || String(err), ask: false });
    } finally {
      setSaving(false);
    }
  }

  useEffect(() => {
    let live = true;
    postPdfHeader().then((file) => {
      if (!live) {
        return;
      }
      // Typed into before the answer came: the form keeps what was typed, and does not claim the file filled it.
      const typed = touched.current;
      if (!typed) {
        setValues(startValues({ visaRequired, file }));
      }
      const said = headerSource(file);
      // Typed over: the path stays (the Save button names it), the claim that the file filled the form does not.
      setSource(typed && fileValues(file) && said ? { filled: false, text: null, warning: null, shown: said.shown, notice: null, nameNote: null } : said);
    });
    return () => {
      live = false;
    };
    // Once per open form: the file is read when the form opens, not while the person types.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function edit(change) {
    touched.current = true;
    setValues(change);
  }

  const linkRows = Array.isArray(values.links) ? values.links : [];

  async function submit(event) {
    event.preventDefault();
    if (!canGenerate(values)) {
      return;
    }
    setBusy(true);
    setError(null);
    setSavedAs(null);
    setNote(null);
    setShortened(null);
    setShortenError(null);
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

  async function shortenNow() {
    setShortening(true);
    setShortenError(null);
    try {
      setShortened(await shorten());
      setNote(null);
      setSavedAs(null);
    } catch (err) {
      setShortenError(err.message || String(err));
    } finally {
      setShortening(false);
    }
  }

  return (
    <form className="generate-pdf-form" data-role="generate-pdf-form" autoComplete="on" onSubmit={submit}>
      <p className="muted small" data-role="pdf-privacy">
        <strong>{PRIVACY_PROMISE}</strong> {PRIVACY_PDF_LINE}
      </p>
      {source && source.text && (
        <p className="muted small" data-role="pdf-header-source" data-filled={source.filled ? "true" : "false"}>
          {source.text}
          {source.filled ? ". Edit anything below before you generate; the file is not changed." : ""}
        </p>
      )}
      {source && source.warning && (
        <div className="callout warn" role="status" data-role="pdf-header-warning">
          {source.warning}
        </div>
      )}
      {source && (source.notice || source.nameNote) && (
        <div className="callout warn" role="status" data-role="pdf-header-placeholders">
          {[source.notice, source.nameNote].filter(Boolean).join(" ")}
        </div>
      )}
      <div className="generate-pdf-fields">
        {FIELDS.filter((field) => field.key !== "work_authorization").map((field) => (
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
              onChange={(event) => edit((current) => ({ ...current, [field.key]: event.target.value }))}
            />
          </div>
        ))}
        {linkRows.map((row, index) => (
          <div className="form-group" key={`link-${index}`}>
            <label className="form-label" htmlFor={`generate-pdf-links-${index}`}>
              {row.label || "Link"}
            </label>
            <input
              id={`generate-pdf-links-${index}`}
              name={`links-${index}`}
              type="text"
              inputMode="url"
              autoComplete="off"
              className="text-input"
              data-role="generate-pdf-file-link"
              value={row.url}
              onChange={(event) =>
                edit((current) => ({
                  ...current,
                  links: current.links.map((item, at) => (at === index ? { ...item, url: event.target.value } : item)),
                }))
              }
            />
          </div>
        ))}
        {FIELDS.filter((field) => field.key === "work_authorization").map((field) => (
          <div className="form-group" key={field.key}>
            <label className="form-label" htmlFor={`generate-pdf-${field.key}`}>
              {field.label}
            </label>
            <input
              id={`generate-pdf-${field.key}`}
              name={field.autocomplete === "url" || field.autocomplete === "off" ? field.key : field.autocomplete}
              type={field.type}
              inputMode={field.inputMode}
              autoComplete={field.autocomplete}
              className="text-input"
              placeholder={field.placeholder}
              value={values[field.key]}
              onChange={(event) => edit((current) => ({ ...current, [field.key]: event.target.value }))}
            />
            {field.hint && (
              <div className="muted small" data-role={`generate-pdf-hint-${field.key}`}>
                {field.hint}
              </div>
            )}
          </div>
        ))}
      </div>
      {source && source.shown && (
        <div className="actions" data-role="pdf-header-save-row">
          {saved && saved.ask ? (
            <span role="alertdialog" data-role="pdf-header-replace">
              <span className="muted small">{REPLACE_QUESTION}</span>{" "}
              <button type="button" className="button small" disabled={saving} data-role="pdf-header-replace-yes" onClick={() => saveDetails(true)}>
                Replace
              </button>{" "}
              <button type="button" className="button small secondary" disabled={saving} data-role="pdf-header-replace-no" onClick={() => setSaved(null)}>
                Cancel
              </button>
            </span>
          ) : (
            <button type="button" className="button small secondary" disabled={saving || !canSave(values)} data-role="pdf-header-save" onClick={() => saveDetails(false)}>
              {saving ? "Saving…" : `Save these details to ${source.shown}`}
            </button>
          )}
          {saved && !saved.ask && (
            <span className="muted small" role="status" data-role="pdf-header-saved" data-state={saved.tone}>
              {saved.text}
            </span>
          )}
        </div>
      )}
      {error && (
        <div className="callout danger" role="alert">
          Could not make the PDF. {error}
        </div>
      )}
      {note && (
        <div className="callout warn" role="status" data-role="pdf-fit-note">
          {note}
          {shorten && (
            <>
              {" "}
              <button type="button" className="button small" disabled={shortening || busy} data-action="shorten-resume" onClick={shortenNow}>
                {shortening ? "Shortening…" : "Shorten automatically"}
              </button>
            </>
          )}
        </div>
      )}
      {shortenError && (
        <div className="callout danger" role="alert" data-role="pdf-shorten-error">
          The resume was not shortened. {shortenError}
        </div>
      )}
      {shortened && (
        <div className="callout" role="status" data-role="pdf-shortened">
          {shortened}
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

import { useEffect, useRef, useState } from "react";
import { postResumePreview } from "../api.js";
import { clampSpacing, spacingLabel } from "../resumeDisplayModel.js";
import {
  AUTO_TEXT,
  HEADER_DEBOUNCE_MS,
  LOADING_TEXT,
  NO_HEADER_TEXT,
  SAVED_TEXT,
  SLIDER_DEBOUNCE_MS,
  UPDATING_TEXT,
  headerKey,
  pagesLine,
  previewOf,
  sliderRange,
} from "../resumePreviewModel.js";

// 0.1.11.5 (a): the job's resume AS IT WILL PRINT, with one spacing slider and
// the page count beside it.
//
//   reads    POST /api/tailored-resumes/preview {profile_id, job_identity,
//            header?}: the PDF's own render as one picture a page. Asked once
//            when the panel opens with a stored resume, then only when the
//            slider or the header changes: never on a page read or a poll.
//   writes   a slider move sends `spacing_scale`: the server saves it as THIS
//            job's spacing (nothing else of the resume, never the master) and
//            answers the preview at it. No Save button.
//   shows    the pages that came back last stay on screen while the next ones
//            are made ("Updating the preview"): never a blank. An answer that
//            is not for the latest request is dropped, and a request that is no
//            longer wanted is cancelled.
//
// `header` is what Generate PDF would send now (the open form's values), or
// null while the form is closed or has no name: the preview is then the
// headerless PDF, with the blank block it keeps for the header.
// `onSpacing(value)` tells the panel the spacing on screen, which Generate PDF
// sends.
// 0.1.11.5 (b): `content` is the stored resume's text as the panel holds it.
// ANY change of it (a point edited, removed or added, Restore, Cut for length
// again, a line choice) asks for the preview again AT ONCE, from this one
// place: no button has to. It sends no spacing of its own, so it saves none.
// `side` is shown under the slider (the list of points, ResumePoints.jsx).
export default function ResumePreview({ profileId, jobIdentity, header = null, onSpacing = null, content = "", side = null, children = null }) {
  const [preview, setPreview] = useState(null);
  const [slider, setSlider] = useState(null); // the slider's value while the person moves it; null: the server's
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const sequence = useRef(0);
  const asked = useRef(false);
  const shownContent = useRef(content);
  const wanted = headerKey(header);
  const headerRef = useRef(header);
  headerRef.current = header;
  const notify = useRef(onSpacing);
  notify.current = onSpacing;

  useEffect(() => {
    if (!profileId || !jobIdentity) {
      return undefined;
    }
    const mine = ++sequence.current;
    const controller = typeof AbortController === "function" ? new AbortController() : null;
    // The first preview is asked at once; a slider move or a typed header value waits until it rests.
    const rewritten = shownContent.current !== content; // the resume's lines changed: nothing to wait for
    shownContent.current = content;
    const wait = !asked.current || rewritten ? 0 : slider !== null ? SLIDER_DEBOUNCE_MS : HEADER_DEBOUNCE_MS;
    setBusy(true);
    const timer = setTimeout(() => {
      asked.current = true;
      postResumePreview({ profileId, jobIdentity, header: headerRef.current, spacingScale: slider, signal: controller ? controller.signal : undefined })
        .then((answer) => {
          if (sequence.current !== mine) {
            return; // a newer request is out: this answer is stale
          }
          const next = previewOf(answer);
          if (!next) {
            throw new Error("The preview could not be read.");
          }
          setPreview(next);
          setError(null);
          setBusy(false);
          if (notify.current) {
            notify.current(next.spacing);
          }
        })
        .catch((err) => {
          if (sequence.current !== mine || (err && err.code === "aborted")) {
            return;
          }
          setError(err.detail || err.message || String(err));
          setBusy(false);
        });
    }, wait);
    return () => {
      clearTimeout(timer);
      if (controller) {
        controller.abort();
      }
    };
  }, [profileId, jobIdentity, wanted, slider, content]);

  const range = sliderRange(preview);
  const value = slider !== null ? slider : preview ? preview.spacing : null;
  const line = pagesLine(preview);
  const state = error ? "error" : !preview ? "loading" : busy ? "updating" : "ready";
  const move = (event) => {
    const next = clampSpacing(event.target.value);
    setSlider(next);
    if (notify.current) {
      notify.current(next);
    }
  };

  return (
    <div className="resume-preview" data-testid="resume-preview" data-state={state} data-with-points={side ? "true" : undefined} data-pages={preview ? preview.pages : undefined} data-spacing={preview ? preview.spacing.toFixed(2) : undefined} data-header={header ? "true" : "false"}>
      <div className="resume-preview-side">
        <div className="form-group" data-role="preview-spacing-control">
          <label className="form-label" htmlFor="resume-preview-spacing">
            Spacing <span data-role="preview-spacing-value">{value === null ? "" : spacingLabel(value)}</span>
          </label>
          <input
            id="resume-preview-spacing"
            type="range"
            data-role="preview-spacing"
            min={range.min}
            max={range.max}
            step={range.step}
            value={value === null ? range.min : value}
            disabled={value === null}
            onChange={move}
          />
          <div className="resume-preview-ends muted small" aria-hidden="true">
            <span>Tighter</span>
            <span>Looser</span>
          </div>
        </div>
        {line && (
          <p className="resume-preview-count" data-role="preview-pages" data-over={line.over ? "true" : "false"}>
            <strong>{line.text}</strong>
            {line.hint && <span data-role="preview-pages-hint"> {line.hint}</span>}
          </p>
        )}
        <p className="muted small" role="status" aria-live="polite" data-role="preview-status">
          {state === "loading" ? LOADING_TEXT : state === "updating" ? UPDATING_TEXT : ""}
        </p>
        {preview && (
          <p className="muted small" data-role="preview-saved" data-saved={preview.saved || slider !== null ? "true" : "false"}>
            {preview.saved || slider !== null ? SAVED_TEXT : AUTO_TEXT}
          </p>
        )}
        {!header && preview && (
          <p className="muted small" data-role="preview-no-header">
            {NO_HEADER_TEXT}
          </p>
        )}
        {error && (
          <div className="callout danger" role="alert" data-role="preview-error">
            The preview could not be made. {error}
          </div>
        )}
        {side}
      </div>
      <div className="resume-preview-pages clean-wrap" data-role="preview-sheets" aria-busy={state === "updating" || state === "loading"}>
        {preview &&
          preview.images.map((image, index) => (
            <img key={index} className="resume-preview-page" data-role="preview-page" src={image} alt={`Page ${index + 1} of ${preview.pages} of the resume as it will print`} />
          ))}
        {!preview && !error && <div className="resume-preview-wait muted">{LOADING_TEXT}…</div>}
        {/* The resume's text, for a screen reader and for search: the pictures hold none. */}
        {children && <div className="visually-hidden">{children}</div>}
      </div>
    </div>
  );
}

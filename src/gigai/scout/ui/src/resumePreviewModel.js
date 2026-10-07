// 0.1.11.5 (a): the job page's RESUME PREVIEW, pure (no React, no fetch, no
// storage) so tests/api_e2e/test_ui_resume_preview_model.py can run it under
// node.
//
// The preview is the PDF's own render (POST /api/tailored-resumes/preview:
// the same template, header, spacing and fit as POST /api/tailored-resumes/pdf)
// as one picture a page, with ONE spacing slider beside it and the page count.
// A slider move asks for the preview again at that spacing; the server saves
// it as THIS job's spacing (a small file of its own beside the job's stored
// resume, which is not written: never the master, never the saved layout) and
// Generate PDF uses it.
import { SPACING_MAX, SPACING_MIN, SPACING_STEP, clampSpacing } from "./resumeDisplayModel.js";

// How long the slider (or a typed header value) rests before the preview is asked for again.
export const SLIDER_DEBOUNCE_MS = 250;
export const HEADER_DEBOUNCE_MS = 500;
export const UPDATING_TEXT = "Updating the preview";
export const LOADING_TEXT = "Making the preview";
export const SAVED_TEXT = "Saved for this job. Generate PDF uses this spacing.";
export const AUTO_TEXT = "Fitted automatically. Move the slider to set this job's spacing; it is saved for this job.";
export const NO_HEADER_TEXT = "Shown without your name and contact details: the space for them is kept. Open Generate PDF to see them here.";

// "1 page" / "2 pages".
export function pagesLabel(pages) {
  return `${pages} page${pages === 1 ? "" : "s"}`;
}

// The slider's range, from the server's answer (`spacing`: {min, max, step}) or the layout model's.
export function sliderRange(preview) {
  const range = preview && preview.spacing && typeof preview.spacing === "object" ? preview.spacing : {};
  const number = (value, fallback) => (typeof value === "number" && Number.isFinite(value) ? value : fallback);
  return { min: number(range.min, SPACING_MIN), max: number(range.max, SPACING_MAX), step: number(range.step, SPACING_STEP) };
}

// The server's answer as the page holds it, or null when it is not a preview.
export function previewOf(answer) {
  if (!answer || typeof answer !== "object" || !Array.isArray(answer.images) || typeof answer.pages !== "number") {
    return null;
  }
  const type = typeof answer.image_type === "string" ? answer.image_type : "image/png";
  return {
    pages: answer.pages,
    maxPages: typeof answer.max_pages === "number" ? answer.max_pages : null,
    spacing: clampSpacing(answer.spacing_scale),
    saved: answer.saved === true,
    note: typeof answer.note === "string" && answer.note ? answer.note : null,
    images: answer.images.filter((image) => typeof image === "string" && image).map((image) => `data:${type};base64,${image}`),
    range: sliderRange(answer),
  };
}

// What the page says under the count: {text, over}. `over`: more pages than the resume's limit.
export function pagesLine(preview) {
  if (!preview) {
    return null;
  }
  const count = pagesLabel(preview.pages);
  if (preview.maxPages === null || preview.pages <= preview.maxPages) {
    return { text: count, over: false, hint: null };
  }
  const tightest = preview.spacing <= preview.range.min;
  return {
    text: count,
    over: true,
    hint: tightest
      ? `This is the tightest spacing: the resume does not reach ${pagesLabel(preview.maxPages)}. Shorten it, or keep it at ${count}.`
      : `Move the slider left to reach ${pagesLabel(preview.maxPages)}.`,
  };
}

// The header a preview request carries: the form's values when it has a name
// (generatePdfModel.canGenerate: what Generate PDF would send), else none.
export function previewHeader(body) {
  return body && typeof body.name === "string" && body.name.length > 0 ? body : null;
}

// Two headers print the same: no new preview is asked for.
export function headerKey(header) {
  return header ? JSON.stringify(header) : "";
}

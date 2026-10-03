// 0.1.10-003 1b: the "Resume display" settings model, pure functions (no
// React) so tests/api_e2e/test_ui_resume_display_model.py can run them under
// node. Mirrors src/gigai/scout/resume_display.py.
//
// 0110-046: GigAI stores no name or contact details. What is saved here is the
// per-profile title printed under the name and the PDF layout (spacing, auto
// fit); the name and contact items are typed in the Generate PDF form for one
// PDF (generatePdfModel.js) and never saved.

export const SPACING_MIN = 0.7;
export const SPACING_MAX = 1.4;
export const SPACING_DEFAULT = 1.0;
export const SPACING_STEP = 0.05;
export const AUTO_FIT_HELP = "Adjusts spacing (never font size) so your resume fills its pages; turn off to use the slider as set.";

export const PRIVACY_NOTE = "Stored on this machine only; never sent to a model. Your name and contact details are not stored: you type them when you generate a PDF.";

function text(value) {
  return typeof value === "string" ? value.trim() : "";
}

// A spacing value as sent: a number clamped to 0.7..1.4 and rounded to 2
// decimals; anything that is not a finite number is the default.
export function clampSpacing(value) {
  const number = typeof value === "number" ? value : typeof value === "string" && value.trim() !== "" ? Number(value) : NaN;
  if (!Number.isFinite(number)) {
    return SPACING_DEFAULT;
  }
  return Math.round(Math.min(SPACING_MAX, Math.max(SPACING_MIN, number)) * 100) / 100;
}

// "1.00x" for the slider's readout.
export function spacingLabel(value) {
  return `${clampSpacing(value).toFixed(2)}x`;
}

// The slider is greyed (value kept) while Auto fit is on.
export function spacingDisabled(draft) {
  return !!(draft && draft.auto_fit !== false);
}

// The schematic preview: the vertical gap (px) between its grey lines for a
// spacing value. Monotonic: a bigger value never gives a smaller gap.
export const PREVIEW_LINES = 4;
export function previewGap(value) {
  return Math.round(clampSpacing(value) * 8 * 100) / 100;
}

// The draft the form edits: {title, spacing_scale, auto_fit, prefilled}. A
// saved title is never overwritten by `suggested` (the server only sends a
// suggestion while this profile has no title). A response from an older
// server may still carry name/contact: they are ignored.
export function draftFromResponse(response) {
  const body = response || {};
  const suggested = body.suggested || null;
  let title = text(body.title);
  let prefilled = false;
  if (!title && suggested && text(suggested.title)) {
    title = text(suggested.title);
    prefilled = true;
  }
  const spacing_scale = typeof body.spacing_scale === "number" ? clampSpacing(body.spacing_scale) : SPACING_DEFAULT;
  const auto_fit = typeof body.auto_fit === "boolean" ? body.auto_fit : true;
  return { title, spacing_scale, auto_fit, prefilled };
}

// The PUT body: only this profile's title is sent (the server merges titles
// per profile; an empty one clears it). Never a name or contact item.
export function buildPutBody(draft, profileId) {
  const body = {
    spacing_scale: clampSpacing(draft.spacing_scale),
    auto_fit: draft.auto_fit !== false,
  };
  if (profileId) {
    body.titles = { [profileId]: text(draft.title) };
  }
  return body;
}

// The saved layout on one line, for a row that names it: the title, then
// "Auto fit" or the spacing. "" when there is no draft.
export function layoutLine(draft) {
  if (!draft) {
    return "";
  }
  const spacing = draft.auto_fit !== false ? "Auto fit" : `Spacing ${spacingLabel(draft.spacing_scale)}`;
  return [text(draft.title), spacing].filter(Boolean).join(" · ");
}

// 0110-026 F2: the search keywords of one run, as pure functions (no React)
// so the rules run under node.
//
// POST /api/run takes an optional `keywords` list (contracts.search_keywords):
// at most 20, each at most 100 characters with at least one letter or digit,
// trimmed, no repeats (case-insensitive). The form keeps to the same limits
// so the server never has to refuse one. A keyword is a phrase; a posting is
// kept when its stored description holds any one of them, and a posting whose
// text was never stored is kept and counted.
//
// What the run did with them comes back at GET /api/runs/{id}/progress ->
// boards.keywords {terms, applied, reason, message, matched, dropped,
// text_not_checked}; the key is absent when the search had none.
import { formatCount } from "./runText.js";

export const MAX_KEYWORDS = 20;
export const MAX_KEYWORD_LENGTH = 100;
export const KEYWORDS_HELP =
  "Optional. Keeps postings whose description mentions any one of these (each is an exact phrase). A posting with no stored description is kept.";

const WORD = /[\p{L}\p{N}_]/u;

function fold(value) {
  return value.toLowerCase();
}

// One typed keyword, as the server will store it: inner whitespace collapsed.
export function normalizeKeyword(text) {
  return String(text == null ? "" : text).split(/\s+/).filter(Boolean).join(" ");
}

// Add what was typed (commas and new lines separate several) to `values`:
//   {values, error}  `error` is "" or why the LAST refused piece was refused.
// A repeat is dropped silently; a piece the server would refuse is not added.
export function addKeywords(values, text) {
  const next = [...values];
  let error = "";
  for (const piece of String(text == null ? "" : text).split(/[,\n]/)) {
    const keyword = normalizeKeyword(piece);
    if (!keyword) {
      continue;
    }
    if (!WORD.test(keyword)) {
      error = "A keyword needs a letter or a digit.";
      continue;
    }
    if (keyword.length > MAX_KEYWORD_LENGTH) {
      error = `A keyword can be at most ${MAX_KEYWORD_LENGTH} characters.`;
      continue;
    }
    if (next.some((value) => fold(value) === fold(keyword))) {
      continue;
    }
    if (next.length >= MAX_KEYWORDS) {
      error = `At most ${MAX_KEYWORDS} keywords.`;
      break;
    }
    next.push(keyword);
  }
  return { values: next, error };
}

export function removeKeyword(values, index) {
  return values.filter((_unused, i) => i !== index);
}

// The run request with this search's keywords. No keywords: the body is
// returned as it is, with no `keywords` key (a run without them is sealed
// exactly as before).
export function runBodyWithKeywords(body, keywords) {
  const { values } = addKeywords([], (keywords || []).join("\n"));
  return values.length > 0 ? { ...body, keywords: values } : body;
}

// The run summary's keywords line, from GET /progress's `boards`:
//   {text, ignored}  or null when the search had no keywords
// applied: "Keywords: rust, payments · text not checked for 12 postings"
// ignored: the server's own message (it says why), quietly.
export function keywordsLine(boards) {
  const keywords = boards && typeof boards === "object" ? boards.keywords : null;
  if (!keywords || typeof keywords !== "object") {
    return null;
  }
  const terms = Array.isArray(keywords.terms) ? keywords.terms.filter((term) => typeof term === "string" && term) : [];
  const list = terms.join(", ");
  if (!keywords.applied) {
    const message =
      typeof keywords.message === "string" && keywords.message.trim() ? keywords.message.trim() : "Keywords were not applied to this search.";
    return { text: list ? `Keywords: ${list} · ${message}` : message, ignored: true };
  }
  const unchecked = typeof keywords.text_not_checked === "number" && keywords.text_not_checked > 0 ? Math.floor(keywords.text_not_checked) : 0;
  const rest = unchecked ? ` · text not checked for ${formatCount(unchecked)} posting${unchecked === 1 ? "" : "s"}` : "";
  return { text: `Keywords: ${list}${rest}`, ignored: false };
}

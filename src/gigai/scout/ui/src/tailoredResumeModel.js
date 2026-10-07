// Q4b-ui (v0.1.9): the tailored-resume PREVIEW rules, pure functions (no
// React) so tests/api_e2e/test_ui_tailored_resume_model.py can run them
// under node exactly like jobModel.js.
//
// Input is one TailorResponse from POST/GET /api/tailored-resumes
// (src/gigai/scout/tailored_resume.py): `result.header[]` copy lines and
// `result.sections[]`, each line `{kind: "copy"|"rewritten"|"custom", text, refs[]}`
// with every ref carrying the cited source text
// (`{kind:"resume", line, text}` / `{kind:"answer", question_id, text}`).
//
// Nothing here invents text: every content line's `text` is the response's
// own; the only client-side strings are the markdown scaffolding
// (`# `, `## Summary`, `### `, `- `), which mirrors render_markdown() so the
// preview reads like the response's `markdown` (never re-rendered here; the
// PDF is rendered server-side).

const ENTRY_SECTIONS = new Set(["experience", "projects", "education"]);

function capitalize(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

// A copied resume line without its own markdown/bullet markers (a mirror of
// tailored_resume._display), so the scaffolding added below (`# `, `### `,
// `- `) never doubles the line's own: a resume whose entry headings are
// already `### Role` shows `### Role`, never `### ### Role`.
const EMPHASIS_PAIRS = /\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__/g;
const LEADING_MARKERS = /^(?:[#>*\-•–—]+\s*)+/;

export function displayText(text) {
  const raw = String(text || "");
  const stripped = raw.replace(EMPHASIS_PAIRS, (_m, a, b) => a || b).replace(LEADING_MARKERS, "").trim();
  return stripped || raw.trim();
}

// 0.1.11.4 item 9: a role none of whose lines is shown is listed on ONE line, in a block "Earlier experience" at the
// end of Experience (a mirror of tailored_resume.heading_only_line / heading_only / render_markdown). In the stored
// result such a role is an Experience entry with its heading lines (the master's own, each with its master id) and
// no bullet.
export const EARLIER_HEADING = "Earlier experience";
const ROLE_DATES = /\b(?:19|20)\d\d\b|\bPresent\b/i;

function rolePart(text) {
  const shown = displayText(text).split(/\s+/).filter(Boolean).join(" ");
  const at = shown.lastIndexOf(" | ");
  return at >= 0 && ROLE_DATES.test(shown.slice(at + 3)) ? [shown.slice(0, at), shown.slice(at + 3)] : [shown, ""];
}

// "Senior Full Stack Developer, USDA | Feb 2016 - Jan 2017": the title (the first line under the employer), the
// employer, and the first dates any of the heading lines names. A part the resume does not give is left out.
export function headingOnlyLine(heading) {
  const parts = (Array.isArray(heading) ? heading : []).filter((line) => String(line || "").trim() !== "").map(rolePart);
  if (parts.length === 0) {
    return "";
  }
  const what = [parts.length > 1 ? parts[1][0] : "", parts[0][0]].filter(Boolean).join(", ");
  const dates = (parts.find(([, when]) => when) || ["", ""])[1];
  return [what, dates].filter(Boolean).join(" | ");
}

// The entries of an Experience section that print as one line each: no bullet, and a heading that is a line of the
// MASTER (its ref carries the master id). An entry with no bullet in a resume tailored from a profile's own resume
// (0.1.10) prints as it always did, its heading in its place.
export function headingOnlyEntries(section) {
  if (!section || section.heading !== "experience") {
    return [];
  }
  return (section.entries || []).filter((entry) => {
    const heading = (entry && entry.heading) || [];
    const refs = heading.length > 0 && Array.isArray(heading[0].refs) ? heading[0].refs : [];
    return heading.length > 0 && (entry.bullets || []).length === 0 && refs.some((ref) => ref && ref.kind === "resume" && typeof ref.item_id === "string" && ref.item_id !== "");
  });
}

// --- what changed (uat-bug-044) ------------------------------------------
// Every rewritten line cites the resume lines it came from and each ref
// carries that source text, so the originals are derived from the response
// itself: no new field, no model call.

function words(text) {
  return String(text || "").split(/\s+/).filter(Boolean);
}

// The resume lines a rewritten line cites, as `{label, text}` (answers are
// not resume lines and are left to the sources list).
export function originalLines(line) {
  return (line.refs || [])
    .filter((ref) => ref && ref.kind === "resume")
    .map((ref) => ({ label: sourceLabel(ref), text: displayText(ref.text) }));
}

// Word-level diff of the new line against the original text: the new line's
// words as `{text, added}` segments, `added` when the word is not part of a
// longest common subsequence with the original (case-sensitive, whitespace
// split; deterministic, O(n*m) on lines of at most a few hundred words).
export function diffSegments(original, updated) {
  const a = words(original);
  const b = words(updated);
  const table = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const out = [];
  let i = 0;
  let j = 0;
  while (j < b.length) {
    if (i < a.length && a[i] === b[j]) {
      out.push({ text: b[j], added: false });
      i += 1;
      j += 1;
    } else if (i < a.length && table[i + 1][j] >= table[i][j + 1]) {
      i += 1;
    } else {
      out.push({ text: b[j], added: true });
      j += 1;
    }
  }
  return out;
}

// Inline `**bold**` / `__bold__` as `{text, bold}` segments. Text is never
// turned into markup here: the panel renders every segment as a React text
// child (escaped by React); there is no innerHTML anywhere in the panel.
export function inlineSegments(text) {
  const out = [];
  const pattern = /\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__/g;
  const raw = String(text || "");
  let last = 0;
  let match = pattern.exec(raw);
  while (match) {
    if (match.index > last) {
      out.push({ text: raw.slice(last, match.index), bold: false });
    }
    out.push({ text: match[1] || match[2], bold: true });
    last = match.index + match[0].length;
    match = pattern.exec(raw);
  }
  if (last < raw.length) {
    out.push({ text: raw.slice(last), bold: false });
  }
  return out;
}

// A line's text as it prints: a copy without its own markers, anything else as written.
function shownText(line) {
  return line && line.kind === "copy" ? displayText(line.text) : String((line && line.text) || "");
}

// 0110-006: the optional keys a line may carry (`id`, `origin`, `reason`,
// `alternative`); an older stored line has none of them and shows no buttons.
// 0110-032: `kind: "custom"` is a line the operator (or their agent) typed; it
// cites nothing and `edited_from` holds the line it replaced.
function contentLine(line, display, where, role, plain, heading = false) {
  const kind = line.kind === "copy" || line.kind === "custom" ? line.kind : "rewritten";
  const alternative = line.alternative && typeof line.alternative === "object" ? line.alternative : null;
  const editedFrom = kind === "custom" && line.edited_from && typeof line.edited_from === "object" ? line.edited_from : null;
  const row = {
    edited: kind === "custom",
    editedFrom,
    id: typeof line.id === "string" ? line.id : null,
    origin: line.origin === "fallback" || line.origin === "user" ? line.origin : "model",
    reason: line.reason && typeof line.reason === "object" ? line.reason : null,
    alternative,
    heading,
    kind,
    text: line.text,
    display,
    role,
    plain,
    refs: Array.isArray(line.refs) ? line.refs : [],
    where,
  };
  if (kind === "rewritten") {
    row.original = originalLines(row);
    row.diff = diffSegments(row.original.map((item) => item.text).join(" "), plain);
  }
  if (kind === "custom") {
    // What changed is shown against the line the edit replaced.
    row.original = editedFrom ? [{ label: "Before the edit", text: shownText(editedFrom) }] : [];
    row.diff = diffSegments(row.original.map((item) => item.text).join(" "), plain);
  }
  return row;
}

// The `## ` sections of the stored markdown that the structured result has no section for (0.1.11.4 UI1 item 11): the
// stored file is what the PDF prints, so the preview shows them too, as copied lines (the trailing refs comment dropped).
function markdownOnlySections(result, markdown) {
  if (typeof markdown !== "string" || markdown === "") {
    return [];
  }
  const known = new Set((result && Array.isArray(result.sections) ? result.sections : []).map((section) => String(section.heading).toLowerCase()));
  const found = [];
  let current = null;
  markdown.split("\n").forEach((raw) => {
    const text = raw.replace(/<!--.*?-->/g, "").trim();
    if (text.startsWith("## ")) {
      const heading = text.slice(3).trim();
      current = known.has(heading.toLowerCase()) ? null : { heading, rows: [] };
      if (current) {
        found.push(current);
      }
    } else if (current && text !== "") {
      if (text.startsWith("### ")) {
        current.rows.push({ role: "entry", text: text.slice(4).trim() });
      } else if (text.startsWith("- ")) {
        current.rows.push({ role: "bullet", text: text.slice(2).trim() });
      } else {
        current.rows.push({ role: "text", text });
      }
    }
  });
  return found.filter((section) => section.rows.length > 0);
}

// The preview rows, in render_markdown()'s order: `{kind, text, display, refs, where}`
// for content lines, `{kind:"heading", display}` for section headings and
// `{kind:"blank"}` between blocks. `where` names the line the way the
// validator's messages do ("summary line 1", "experience entry 2 bullet 3").
export function previewLines(result, markdown = "") {
  const out = [];
  if (!result || typeof result !== "object") {
    return out;
  }
  const header = Array.isArray(result.header) ? result.header : [];
  header.forEach((line, index) => {
    const shown = displayText(line.text);
    out.push(contentLine(line, index === 0 ? `# ${shown}` : shown, `header line ${index + 1}`, index === 0 ? "title" : "text", shown, true));
  });
  if (header.length > 0) {
    out.push({ kind: "blank" });
  }
  (Array.isArray(result.sections) ? result.sections : []).forEach((section) => {
    out.push({ kind: "heading", display: `## ${capitalize(section.heading)}`, text: capitalize(section.heading) });
    out.push({ kind: "blank" });
    // 0.1.11.4 UI1 item 11: a section that holds entries (an Other section's earlier roles) prints them, and one that
    // holds lines prints those, whatever its heading: nothing the stored resume holds is left out of the preview.
    const entries = section.entries || [];
    const earlier = headingOnlyEntries(section);
    if (ENTRY_SECTIONS.has(section.heading) || entries.length > 0) {
      entries.forEach((entry, entryIndex) => {
        if (earlier.includes(entry)) {
          return; // listed below, on one line (EARLIER_HEADING)
        }
        (entry.heading || []).forEach((line, index) => {
          const shown = displayText(line.text);
          out.push(
            contentLine(
              line,
              index === 0 ? `### ${shown}` : shown,
              `${section.heading} entry ${entryIndex + 1} heading ${index + 1}`,
              index === 0 ? "entry" : "text",
              shown,
              true,
            ),
          );
        });
        if ((entry.bullets || []).length > 0) {
          out.push({ kind: "blank" });
        }
        (entry.bullets || []).forEach((line, index) => {
          // A copied bullet prints through displayText (a mirror of the
          // server's _display), so a bullet that carries its own marker never shows `- - `.
          const shown = line.kind === "copy" ? displayText(line.text) : line.text;
          out.push(contentLine(line, `- ${shown}`, `${section.heading} entry ${entryIndex + 1} bullet ${index + 1}`, "bullet", shown));
        });
        out.push({ kind: "blank" });
      });
      if (earlier.length > 0) {
        // The roles with no line shown: one line each, after the roles that show lines, the way the markdown and the PDF print them.
        out.push({ kind: "heading", level: 3, earlier: true, display: `### ${EARLIER_HEADING}`, text: EARLIER_HEADING });
        out.push({ kind: "blank" });
        earlier.forEach((entry) => {
          const shown = headingOnlyLine(entry.heading.map((line) => line.text));
          const refs = entry.heading.flatMap((line) => (Array.isArray(line.refs) ? line.refs : []));
          const row = contentLine({ kind: "copy", text: shown, refs }, shown, `${section.heading} entry ${entries.indexOf(entry) + 1} heading`, "text", shown, true);
          row.earlier = true;
          out.push(row);
        });
        out.push({ kind: "blank" });
      }
    }
    if (!ENTRY_SECTIONS.has(section.heading)) {
      const lines = section.lines || [];
      lines.forEach((line, index) => {
        const shown = line.kind === "copy" ? displayText(line.text) : line.text;
        out.push(contentLine(line, `- ${shown}`, `${section.heading} line ${index + 1}`, "bullet", shown));
      });
      if (lines.length > 0 || entries.length === 0) {
        out.push({ kind: "blank" });
      }
    }
  });
  markdownOnlySections(result, markdown).forEach((section) => {
    out.push({ kind: "heading", display: `## ${section.heading}`, text: section.heading });
    out.push({ kind: "blank" });
    section.rows.forEach((row, index) => {
      const line = { kind: "copy", text: row.text };
      out.push(contentLine(line, row.role === "bullet" ? `- ${row.text}` : row.text, `${section.heading} line ${index + 1}`, row.role, row.text, row.role !== "bullet"));
    });
    out.push({ kind: "blank" });
  });
  while (out.length > 0 && out[out.length - 1].kind === "blank") {
    out.pop();
  }
  return out;
}

// Words that look like terms (a digit, `+`, `#`, an inner dot or capital, all
// caps, or Capitalized not at the start of the line) in the rewritten lines that none of the line's cited sources
// contain. The posting-term set the guards used is not stored on the
// response, so this is derived from the provenance alone (no model call).
const TECHNICAL = /[0-9+#]|.[.][^.]|^[A-Za-z]+[a-z][A-Z]|^[A-Z]{2,}$/;

export function addedKeywords(lines) {
  const seen = new Map();
  lines
    .filter((line) => line.kind === "rewritten")
    .forEach((line) => {
      const source = new Set((line.refs || []).flatMap((ref) => words(ref.text)).map((word) => word.replace(/^[^\w+#]+|[^\w+#]+$/g, "").toLowerCase()));
      (line.diff || []).forEach((segment, position) => {
        const word = segment.text.replace(/^[^\w+#]+|[^\w+#]+$/g, "");
        if (segment.added && word.length > 1 && /^[\w.+#-]+$/.test(word) && (TECHNICAL.test(word) || (position > 0 && /^[A-Z][a-z]/.test(word))) && !source.has(word.toLowerCase()) && !seen.has(word.toLowerCase())) {
          seen.set(word.toLowerCase(), word);
        }
      });
    });
  return [...seen.values()];
}

// Entry headings and the header are not rewritable lines (the server's
// tailor_line_stats leaves them out too), so they stay out of the counts.
// `keptOriginal` is the copies the no-loss check put back (a fallback).
// `edited` is the lines showing the operator's own text (0110-032): neither
// copied nor rewritten, and not "unsourced" (no source is claimed for them).
export function previewStats(lines) {
  const content = lines.filter((line) => (line.kind === "copy" || line.kind === "rewritten" || line.kind === "custom") && !line.heading);
  const copied = content.filter((line) => line.kind === "copy").length;
  const edited = content.filter((line) => line.kind === "custom").length;
  const keptOriginal = content.filter((line) => line.kind === "copy" && line.origin === "fallback").length;
  const rewritten = content.length - copied - edited;
  const citingAnswers = content.filter((line) => line.refs.some((ref) => ref.kind === "answer")).length;
  const unsourced = content.filter((line) => line.kind !== "custom" && line.refs.length === 0).length;
  const stats = { total: content.length, copied, keptOriginal, rewritten, citingAnswers, unsourced, keywords: addedKeywords(content) };
  // Present only when a line is edited, so a resume without edits counts as before.
  return edited > 0 ? { ...stats, edited } : stats;
}

// "N of M lines rewritten · K kept as your original (the rewrite dropped facts) · C copied;
// New words (not in the cited lines): a, b" -- the counts are previewStats' (M = every
// rewritable line, N + K + C = M; the K part shows only when K > 0).
export function changeSummary(stats) {
  const kept = stats.keptOriginal || 0;
  const parts = [`${stats.rewritten} of ${stats.total} line${stats.total === 1 ? "" : "s"} rewritten`];
  if (kept > 0) {
    parts.push(`${kept} kept as your original (the rewrite dropped facts)`);
  }
  parts.push(`${stats.copied - kept} copied`);
  if ((stats.edited || 0) > 0) {
    parts.push(`${stats.edited} edited`);
  }
  const base = parts.join(" · ");
  const keywords = stats.keywords || [];
  return keywords.length > 0 ? `${base}; New words (not in the cited lines): ${keywords.join(", ")}` : base;
}

export function statsLine(stats) {
  const parts = [
    `${stats.total} line${stats.total === 1 ? "" : "s"}`,
    `${stats.copied} copied verbatim`,
    `${stats.rewritten} rewritten${stats.citingAnswers > 0 ? ` (${stats.citingAnswers} citing your answers)` : ""}`,
  ];
  if ((stats.edited || 0) > 0) {
    parts.push(`${stats.edited} edited`);
  }
  if (stats.unsourced > 0) {
    parts.push(`${stats.unsourced} unsourced`);
  }
  return parts.join(" · ");
}

// "Resume line 12" / "Your answer · <the question's prompt>" -- the ref's
// identity, in words; the cited text is shown separately. `promptFor(id)`
// (optional) resolves an answer ref's question_id to its prompt text
// (orchestrator rule: a question is shown by its prompt, the id only as
// secondary detail); without one the id is all there is.
export function sourceLabel(ref, promptFor) {
  if (!ref || typeof ref !== "object") {
    return "";
  }
  if (ref.kind === "resume") {
    // tailor-r2: a ref whose line runs on into the next ones carries
    // `continued_lines`; its `text` is already the joined span.
    const continued = Array.isArray(ref.continued_lines) ? ref.continued_lines.filter((n) => Number.isInteger(n)) : [];
    if (continued.length > 0) {
      return `Resume lines ${ref.line}–${Math.max(ref.line, ...continued)}`;
    }
    return `Resume line ${ref.line}`;
  }
  if (ref.kind === "answer") {
    const prompt = typeof promptFor === "function" ? promptFor(ref.question_id) : null;
    return `Your answer · ${prompt || ref.question_id}`;
  }
  return ref.kind || "";
}

// "for M3: event-driven" / "for the summary" / "from your answer": why the
// model rewrote a line, from the line's own `reason` (never invented here).
// A quoted posting phrase reads mid-sentence after "for": lowercase its first
// letter only when the first word is upper-case then lower-case only (not
// "I"), so acronyms such as "AWS" and "SQL" keep their case.
function lowerFirstWord(phrase) {
  const first = /^[A-Z][a-z]*(?![A-Za-z])/.exec(phrase);
  return first && first[0] !== "I" ? phrase[0].toLowerCase() + phrase.slice(1) : phrase;
}

export function reasonLabel(reason) {
  if (!reason || typeof reason !== "object") {
    return "";
  }
  const phrase = typeof reason.posting_phrase === "string" ? lowerFirstWord(reason.posting_phrase) : reason.posting_phrase;
  const detail = [reason.requirement, phrase].filter((part) => typeof part === "string" && part).join(": ");
  if (detail) {
    return `for ${detail}`;
  }
  if (reason.kind === "answer") {
    return "from your answer";
  }
  return reason.kind === "summary" ? "for the summary" : "";
}

// What the no-loss check found missing from a rewrite, as "rule: a, b" strings.
export function lostLabels(alternative) {
  const lost = alternative && typeof alternative.lost === "object" && alternative.lost ? alternative.lost : {};
  return Object.entries(lost)
    .filter(([, items]) => Array.isArray(items) && items.length > 0)
    .map(([rule, items]) => `${rule}: ${items.join(", ")}`);
}

// The one button a line offers, or null: `{use, label}` for PUT /api/tailored-resumes/lines.
//   shown rewrite with an original  -> Keep original
//   fallback (the original is shown) -> Use rewrite anyway
//   operator's choice                -> Undo
export function lineAction(line) {
  if (!line || !line.id || !line.alternative || line.heading) {
    return null;
  }
  if (line.origin === "user") {
    return { use: line.kind === "copy" ? "rewritten" : "original", label: "Undo" };
  }
  if (line.kind === "rewritten" && line.alternative.kind === "copy") {
    return { use: "original", label: "Keep original" };
  }
  if (line.origin === "fallback" && line.kind === "copy" && line.alternative.kind === "rewritten") {
    return { use: "rewritten", label: "Use rewrite anyway" };
  }
  return null;
}

// 0110-032: the ways back from an edited line, as `{use, label}` for the same
// PUT: the versions the line it replaced has (its own kind, and its alternative's).
export function editedActions(line) {
  if (!line || !line.id || !line.edited || !line.editedFrom || line.heading) {
    return [];
  }
  const base = line.editedFrom;
  const kinds = [base.kind, base.alternative && typeof base.alternative === "object" ? base.alternative.kind : null];
  const out = [];
  if (kinds.includes("copy")) {
    out.push({ use: "original", label: "Use original" });
  }
  if (kinds.includes("rewritten")) {
    out.push({ use: "rewritten", label: "Use rewrite" });
  }
  return out;
}

// Every button a line offers: the ways back from an edit, else lineAction's one.
export function lineActions(line) {
  if (line && line.edited) {
    return editedActions(line);
  }
  const action = lineAction(line);
  return action ? [action] : [];
}

// The hover text for one preview line: one "<label>: <cited text>" per ref.
export function sourcesHover(line, promptFor) {
  if (line && line.edited) {
    return "Edited: your own text, no source cited.";
  }
  return (line.refs || []).map((ref) => `${sourceLabel(ref, promptFor)}: ${ref.text}`).join("\n");
}

// The list route serves newest first; keep that but never trust the order
// blindly (the filter is by identity, an older CLI run could sit beside a
// newer UI one).
export function latestStored(items) {
  const list = Array.isArray(items) ? items.slice() : [];
  list.sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
  return list[0] || null;
}

// A quiet re-read (the background pipeline stored a resume) never replaces
// what the page holds with an older one, or with nothing: a resume tailored
// or edited on the page while the read was on its way stays.
export function newerStored(held, loaded) {
  if (!loaded || !held) {
    return loaded || held || null;
  }
  return String(loaded.updated_at || "") > String(held.updated_at || "") ? loaded : held;
}

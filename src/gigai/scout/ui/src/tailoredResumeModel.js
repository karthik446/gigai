// Q4b-ui (v0.1.9): the tailored-resume PREVIEW rules, pure functions (no
// React) so tests/api_e2e/test_ui_tailored_resume_model.py can run them
// under node exactly like jobModel.js.
//
// Input is one TailorResponse from POST/GET /api/tailored-resumes
// (src/gigai/scout/tailored_resume.py): `result.header[]` copy lines and
// `result.sections[]`, each line `{kind: "copy"|"rewritten", text, refs[]}`
// with every ref carrying the cited source text
// (`{kind:"resume", line, text}` / `{kind:"answer", question_id, text}`).
//
// Nothing here invents text: every content line's `text` is the response's
// own; the only client-side strings are the markdown scaffolding
// (`# `, `## Summary`, `### `, `- `), which mirrors render_markdown() so the
// preview reads like the .md the "Download" button saves (that file is the
// response's `markdown` verbatim, never re-rendered here).

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

function contentLine(line, display, where, role, plain) {
  const kind = line.kind === "copy" ? "copy" : "rewritten";
  const row = {
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
  return row;
}

// The preview rows, in render_markdown()'s order: `{kind, text, display, refs, where}`
// for content lines, `{kind:"heading", display}` for section headings and
// `{kind:"blank"}` between blocks. `where` names the line the way the
// validator's messages do ("summary line 1", "experience entry 2 bullet 3").
export function previewLines(result) {
  const out = [];
  if (!result || typeof result !== "object") {
    return out;
  }
  const header = Array.isArray(result.header) ? result.header : [];
  header.forEach((line, index) => {
    const shown = displayText(line.text);
    out.push(contentLine(line, index === 0 ? `# ${shown}` : shown, `header line ${index + 1}`, index === 0 ? "title" : "text", shown));
  });
  if (header.length > 0) {
    out.push({ kind: "blank" });
  }
  (Array.isArray(result.sections) ? result.sections : []).forEach((section) => {
    out.push({ kind: "heading", display: `## ${capitalize(section.heading)}`, text: capitalize(section.heading) });
    out.push({ kind: "blank" });
    if (ENTRY_SECTIONS.has(section.heading)) {
      (section.entries || []).forEach((entry, entryIndex) => {
        (entry.heading || []).forEach((line, index) => {
          const shown = displayText(line.text);
          out.push(
            contentLine(
              line,
              index === 0 ? `### ${shown}` : shown,
              `${section.heading} entry ${entryIndex + 1} heading ${index + 1}`,
              index === 0 ? "entry" : "text",
              shown,
            ),
          );
        });
        if ((entry.bullets || []).length > 0) {
          out.push({ kind: "blank" });
        }
        (entry.bullets || []).forEach((line, index) => {
          out.push(contentLine(line, `- ${line.text}`, `${section.heading} entry ${entryIndex + 1} bullet ${index + 1}`, "bullet", line.text));
        });
        out.push({ kind: "blank" });
      });
    } else {
      (section.lines || []).forEach((line, index) => {
        const shown = line.kind === "copy" ? displayText(line.text) : line.text;
        out.push(contentLine(line, `- ${shown}`, `${section.heading} line ${index + 1}`, "bullet", shown));
      });
      out.push({ kind: "blank" });
    }
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

export function previewStats(lines) {
  const content = lines.filter((line) => line.kind === "copy" || line.kind === "rewritten");
  const copied = content.filter((line) => line.kind === "copy").length;
  const rewritten = content.length - copied;
  const citingAnswers = content.filter((line) => line.refs.some((ref) => ref.kind === "answer")).length;
  const unsourced = content.filter((line) => line.refs.length === 0).length;
  return { total: content.length, copied, rewritten, citingAnswers, unsourced, keywords: addedKeywords(content) };
}

// "N of M lines rewritten, K copied as-is; added keywords: a, b" -- the
// counts are previewStats' (M = every content line, N + K = M).
export function changeSummary(stats) {
  const base = `${stats.rewritten} of ${stats.total} line${stats.total === 1 ? "" : "s"} rewritten, ${stats.copied} copied as-is`;
  const keywords = stats.keywords || [];
  return keywords.length > 0 ? `${base}; added keywords: ${keywords.join(", ")}` : base;
}

export function statsLine(stats) {
  const parts = [
    `${stats.total} line${stats.total === 1 ? "" : "s"}`,
    `${stats.copied} copied verbatim`,
    `${stats.rewritten} rewritten${stats.citingAnswers > 0 ? ` (${stats.citingAnswers} citing your answers)` : ""}`,
  ];
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

// The hover text for one preview line: one "<label>: <cited text>" per ref.
export function sourcesHover(line, promptFor) {
  return (line.refs || []).map((ref) => `${sourceLabel(ref, promptFor)}: ${ref.text}`).join("\n");
}

function slug(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

// "tailored-resume-acme-software-engineer.md", from the response's own
// job fields (never the posting text; the server's markdown_path basename
// is a digest, not a name a person would keep).
export function downloadName(response) {
  const job = (response && response.job) || {};
  const parts = [slug(job.company), slug(job.title)].filter(Boolean);
  return `tailored-resume${parts.length ? `-${parts.join("-")}` : ""}.md`;
}

// The list route serves newest first; keep that but never trust the order
// blindly (the filter is by identity, an older CLI run could sit beside a
// newer UI one).
export function latestStored(items) {
  const list = Array.isArray(items) ? items.slice() : [];
  list.sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
  return list[0] || null;
}

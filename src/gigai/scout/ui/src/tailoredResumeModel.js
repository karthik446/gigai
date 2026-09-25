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

function contentLine(line, display, where) {
  return {
    kind: line.kind === "copy" ? "copy" : "rewritten",
    text: line.text,
    display,
    refs: Array.isArray(line.refs) ? line.refs : [],
    where,
  };
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
    out.push(contentLine(line, index === 0 ? `# ${line.text}` : line.text, `header line ${index + 1}`));
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
          out.push(
            contentLine(line, index === 0 ? `### ${line.text}` : line.text, `${section.heading} entry ${entryIndex + 1} heading ${index + 1}`),
          );
        });
        if ((entry.bullets || []).length > 0) {
          out.push({ kind: "blank" });
        }
        (entry.bullets || []).forEach((line, index) => {
          out.push(contentLine(line, `- ${line.text}`, `${section.heading} entry ${entryIndex + 1} bullet ${index + 1}`));
        });
        out.push({ kind: "blank" });
      });
    } else {
      (section.lines || []).forEach((line, index) => {
        out.push(contentLine(line, `- ${line.text}`, `${section.heading} line ${index + 1}`));
      });
      out.push({ kind: "blank" });
    }
  });
  while (out.length > 0 && out[out.length - 1].kind === "blank") {
    out.pop();
  }
  return out;
}

export function previewStats(lines) {
  const content = lines.filter((line) => line.kind === "copy" || line.kind === "rewritten");
  const copied = content.filter((line) => line.kind === "copy").length;
  const rewritten = content.length - copied;
  const citingAnswers = content.filter((line) => line.refs.some((ref) => ref.kind === "answer")).length;
  const unsourced = content.filter((line) => line.refs.length === 0).length;
  return { total: content.length, copied, rewritten, citingAnswers, unsourced };
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

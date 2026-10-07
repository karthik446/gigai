// 0.1.10.9 master P5: the master resume in the UI, as pure functions (no
// React, no fetch) so the rules run under node.
//
// The master resume is the user's ONE document of every role, bullet, project
// and skill, with a stable id on every line (GET /api/master). The Master
// page (#/master) edits it one line at a time; a profile shows a selection of
// it (GET /api/master/selection); a resume tailored from it lists what it
// picked and what it left out (`selection` on the tailored resume).
//
// Two writers share the master (the user here, and the user's agent), so
// every write sends the `revision` the page read. A 409 revision_conflict
// carries the revision the master is at now: the page reads the master
// again and says so, and nothing was written.

const text = (value) => (typeof value === "string" ? value : "");
const list = (value) => (Array.isArray(value) ? value : []);
const day = (value) => text(value).slice(0, 10);
const plural = (count, word, many) => `${count} ${count === 1 ? word : many || `${word}s`}`;

export const SECTION_LABELS = {
  summary: "Summary",
  experience: "Experience",
  projects: "Projects",
  skills: "Skills",
  education: "Education",
  other: "Other",
};
// The sections whose lines live under an entry (a role, a project, a school).
export const ENTRY_SECTIONS = new Set(["experience", "projects", "education"]);
// The order the page lists sections in: roles first (the page is "by role").
export const SECTION_ORDER = ["experience", "projects", "summary", "skills", "education", "other"];
// What a new line is called, by section.
export const LINE_WORD = { summary: "summary", skills: "skills line", other: "line" };

// --- strength --------------------------------------------------------------

// A line's evidence strength is derived by the server, never typed: backed
// (a story or an answer is linked), quantified (the line states a number),
// stated. The mark is a letter, with the words on hover and for a reader.
const STRENGTHS = {
  backed: { mark: "B", label: "Backed", title: "Backed: a story or an answer of yours is linked to this line" },
  quantified: { mark: "#", label: "Quantified", title: "Quantified: the line states a number" },
  stated: { mark: "·", label: "Stated", title: "Stated: no number and no linked story or answer" },
};

export function strengthMark(item) {
  return STRENGTHS[item && item.strength] || STRENGTHS.stated;
}

// --- the page: by role ------------------------------------------------------

// [{section, label, entries: [{entry, lines}], lines}] in SECTION_ORDER; an
// entry section lists its entries (each with its lines, in the master's
// order), any other section its lines. A section the master does not have
// is listed too, empty, so a first line can be added to it.
export function masterSections(master) {
  if (!master) {
    return [];
  }
  const items = list(master.items);
  const byId = new Map(items.map((item) => [item.id, item]));
  return SECTION_ORDER.map((section) => {
    const entries = list(master.entries)
      .filter((entry) => entry.section === section)
      .map((entry) => ({ entry, lines: list(entry.bullets).map((id) => byId.get(id)).filter(Boolean) }));
    const lines = ENTRY_SECTIONS.has(section) ? [] : items.filter((item) => item.section === section);
    return { section, label: SECTION_LABELS[section], entries, lines };
  });
}

// 0.1.11.4 item 9d: a master with no school in it. A resume picked from it prints no education, so the page says so
// (the CLI's `master show` says the same words) and offers the form that adds one.
export const NO_EDUCATION_TEXT = "Your master has no education.";
export const NO_EDUCATION_WHY = "A resume picked from it prints no degree.";
export const ADD_EDUCATION_LABEL = "Add education";

// True when the master holds no Education entry (false with no master: there is nothing to say yet).
export function masterHasNoEducation(master) {
  return Boolean(master) && !list(master.entries).some((entry) => entry && entry.section === "education");
}

// "Staff Software Engineer | Jun 2019 - Jan 2023" under a heading.
export function entryWhen(entry) {
  return list(entry && entry.sublines).join(" · ");
}

// "Revision 3 · written by you · 2026-10-04 · 71 lines, 13 entries, 67 skills"
export function revisionLine(master) {
  if (!master) {
    return "";
  }
  const counts = master.counts || {};
  const size = [plural(counts.items || 0, "line"), plural(counts.entries || 0, "entry", "entries"), plural(counts.skills || 0, "skill")].join(", ");
  return [`Revision ${master.revision}`, `written by ${master.written_by === "agent" ? "your agent" : "you"}`, day(master.updated_at), size].filter(Boolean).join(" · ");
}

// "Shown by Staff Engineer, Staff AI Engineer" for a line some profile's
// selection shows; "" when none does.
export function shownByLabel(id, shownBy, profiles) {
  const ids = list(shownBy && shownBy[id]);
  if (ids.length === 0) {
    return "";
  }
  const labels = new Map(list(profiles).map((profile) => [profile.profile_id, profile.label]));
  return `Shown by ${ids.map((profileId) => labels.get(profileId) || profileId).join(", ")}`;
}

// A write refused because the master moved on: what to say (the page then
// reads the master again). null for any other error.
export function conflictOf(err) {
  if (!err || err.code !== "revision_conflict") {
    return null;
  }
  const current = err.current && typeof err.current === "object" ? err.current : null;
  const who = current && current.written_by === "agent" ? "your agent" : "someone";
  const at = current ? ` (it is at revision ${current.revision} now)` : "";
  return { revision: current ? current.revision : null, message: `Not saved: ${who} changed the master after this page read it${at}. It is shown as it is now; make the change again.` };
}

export function errorText(err) {
  return text(err && err.detail) || text(err && err.message) || String(err);
}

// What a write did to the profiles, in one line; "" when nothing.
export function afterWriteLine(profiles) {
  const synced = list(profiles && profiles.synced);
  const offers = list(profiles && profiles.offers);
  const parts = [];
  if (synced.length > 0) {
    parts.push(`${synced.map((change) => change.label).join(", ")}: the resume now shows this change.`);
  }
  if (offers.length > 0) {
    parts.push(`${offers.map((offer) => `${offer.label}: ${offer.offer}`).join(" ")}`);
  }
  return parts.join(" ");
}

// A new line the master already has in other words is ASKED about: the
// server wrote nothing (status near_duplicate) and names the lines it looks
// like, the closest first. What to say; "" for any other reply.
export function nearDuplicateLine(response) {
  const near = response && response.status === "near_duplicate" ? list(response.near_duplicates)[0] : null;
  if (!near) {
    return "";
  }
  const alike = typeof near.similarity === "number" ? `${Math.round(near.similarity * 100)}% alike` : "";
  const numbers = near.same_numbers === false ? "with different numbers" : "";
  const how = [alike, numbers].filter(Boolean).join(", ");
  return `Not added: the master already has a line that says nearly this${how ? ` (${how})` : ""}: "${text(near.text)}". If it is the same fact, edit that line instead; to keep both, choose Add it anyway.`;
}

// --- the file in the resumes folder (0.1.10.9 master P8) ---------------------

// The master is also a file the user may edit: master.md in the resumes
// folder, written by GigAI after every change (GET /api/master `file`).
// GigAI never reads it by itself, so the page says how it stands and offers
// the one button: {state, text, button}. null when there is nothing to say
// (no master and no file).
export function fileLine(file) {
  if (!file || typeof file !== "object") {
    return null;
  }
  const name = text(file.name) || "master.md";
  if (file.not_imported) {
    const beside = file.beside ? `; the master as it is now is in ${file.beside}` : "";
    const moved = file.behind ? ` The master changed here since that file was written (revision ${file.revision}, now ${file.master_revision}${beside}).` : "";
    return { state: "changed", text: `${name} has changes not imported yet (${text(file.path)}).${moved}`, button: "Import the file" };
  }
  if (file.action === "write") {
    const what = file.state === "missing" ? `${name} is not in your resumes folder (${text(file.folder)}).` : `${name} in your resumes folder holds revision ${file.revision}, not ${file.master_revision}.`;
    return { state: file.state === "missing" ? "missing" : "behind", text: what, button: "Write the file" };
  }
  if (file.state !== "current") {
    return null;
  }
  return { state: "current", text: `Also a file you can edit: ${text(file.path)}. Change it in your own editor, then import it here.`, button: "Import the file" };
}

// What POST /api/master/sync did, in one line.
export function syncLine(response) {
  if (!response) {
    return "";
  }
  const name = text(response.file && response.file.name) || "master.md";
  if (response.status === "written") {
    return `Wrote ${name} to your resumes folder. Nothing was imported.`;
  }
  if (response.status !== "imported") {
    return `Nothing to import: ${name} says what the master holds.`;
  }
  const changes = response.changes || {};
  const ids = response.ids || {};
  const retired = changes.removed > 0 ? " History can put a retired line back." : "";
  const kept = `${plural(ids.kept || 0, "id")} kept, ${ids.assigned || 0} new${ids.restored ? `, ${ids.restored} restored` : ""}`;
  const after = afterWriteLine(response.profiles);
  return `Imported ${name} as revision ${response.master ? response.master.revision : "?"}: ${changes.added || 0} added, ${changes.changed || 0} changed, ${changes.removed || 0} retired; ${kept}.${retired}${after ? ` ${after}` : ""}`;
}

// An import the server refused because the file was not written from the
// master as it is now: what to ask, and the revision that imports it anyway.
// null for any other error.
export function importAnywayOf(err) {
  const current = err && err.current && typeof err.current === "object" ? err.current : null;
  if (!err || !current || (err.code !== "revision_conflict" && err.code !== "revision_required")) {
    return null;
  }
  const why =
    err.code === "revision_conflict"
      ? `the master changed here or through your agent after master.md was written (it is at revision ${current.revision} now). Importing the file as it is retires what was added since`
      : "GigAI did not write this master.md, so it does not know which revision it was written from. Importing it as it is retires every line the file does not hold";
  return { revision: current.revision, message: `Not imported: ${why}. A retired line can be put back from History.`, button: "Import it anyway" };
}

// After a write made on this page: where the revision went when master.md
// was not replaced; "" otherwise.
export function fileWriteLine(file) {
  if (!file || typeof file !== "object") {
    return "";
  }
  if (file.error) {
    return "The master could not be written into your resumes folder; it is stored.";
  }
  return file.not_imported ? `${text(file.name) || "master.md"} has changes not imported yet, so it was left as it is: this revision is in ${text(file.wrote)} beside it.` : "";
}

// --- history ---------------------------------------------------------------

// One row per revision, newest first: "3 · 2026-10-04 · your agent · 72 lines · +1 −0 ~0".
export function historyRows(history) {
  return list(history && history.revisions).map((entry) => ({
    key: entry.revision_id || String(entry.revision),
    revision: entry.revision,
    text: [
      `Revision ${entry.revision}`,
      day(entry.updated_at),
      entry.written_by === "agent" ? "your agent" : "you",
      plural(entry.items || 0, "line"),
      `${entry.added || 0} added, ${entry.removed || 0} retired, ${entry.changed || 0} changed`,
    ].join(" · "),
  }));
}

// The retired lines and entries, as the last revision that held them had
// them: {id, what, text, where, note}.
export function retiredRows(history) {
  return list(history && history.retired).map((gone) => ({
    id: gone.id,
    what: gone.what === "entry" ? "entry" : "line",
    text: gone.what === "entry" ? [gone.text, ...list(gone.sublines)].filter(Boolean).join(" · ") : text(gone.text),
    where: gone.what === "entry" ? SECTION_LABELS[gone.section] || "" : gone.entry_heading || SECTION_LABELS[gone.section] || "",
    note: `retired in revision ${gone.retired_in}`,
  }));
}

// --- the profiles' selections ---------------------------------------------

// One profile against the master: {line, offer, action}. `action` is the
// button to offer: refresh (select again), sync (print the resume again) or
// null. `offer` is the server's own sentence ("3 new master lines: refresh?").
// The line ends with the server's sentence for what a resume for a job is
// made from (`tailoring_basis_line`, the one rule: 0110-10-10 item 3), so a
// profile that "shows its own resume" still says its jobs' resumes are picked
// from the master. The page decides nothing about that itself.
export function selectionRow(status) {
  if (!status) {
    return null;
  }
  if (status.pending) {
    return { line: "Making its first selection from your master…", offer: "", action: null, state: "pending" };
  }
  const basis = text(status.tailoring_basis_line) ? ` ${text(status.tailoring_basis_line)}` : "";
  if (!status.has_selection) {
    return { line: `Shows its own resume, not a selection of the master.${basis}`, offer: "", action: { use: "refresh", label: "Select from the master" }, state: "none" };
  }
  const made = status.made_from_revision ? `revision ${status.made_from_revision}` : "an earlier master";
  const size = `${plural(status.shown || 0, "entry and line", "entries and lines")}, ${plural(status.skills || 0, "skill")}, made from ${made}`;
  if (status.attached === false) {
    return { line: `${size}. Its resume was replaced after that, so it no longer shows this selection.${basis}`, offer: "", action: { use: "refresh", label: "Select from the master again" }, state: "detached" };
  }
  if (status.stale) {
    const edited = list(status.changed).length;
    const gone = list(status.retired).length + list(status.skills_retired).length;
    return { line: `${size}. The master changed under it (${plural(edited, "shown line")} edited, ${gone} retired).${basis}`, offer: text(status.offer), action: { use: "sync", label: "Print its resume again" }, state: "stale" };
  }
  return { line: `${size}.${basis}`, offer: text(status.offer), action: status.offer ? { use: "refresh", label: "Refresh" } : null, state: status.offer ? "offer" : "current" };
}

// What a refresh did, in one line.
export function refreshLine(change) {
  if (!change) {
    return "Nothing to do: the resume already says what the master says.";
  }
  if (change.action === "synced") {
    return `Printed again from the master: ${plural(list(change.changed).length, "line")} edited, ${list(change.retired).length} retired.`;
  }
  const size = `${plural(change.shown || 0, "entry and line", "entries and lines")} and ${plural(change.skills || 0, "skill")}${change.pages ? ` on ${plural(change.pages, "page")}` : ""}`;
  const moved = change.action === "refreshed" ? `; ${list(change.added).length} came in, ${list(change.removed).length} went` : "";
  const fit = change.fits === false ? " It does not fit 2 pages after every cut the rules allow." : "";
  return `${change.action === "first" ? "First selection" : "Refreshed"}: ${size}${moved}. The profile's resume is now this selection.${fit}`;
}

// --- the migration ---------------------------------------------------------

// What the merge found, in one line.
export function migrationSummary(payload) {
  const plan = payload && payload.migration;
  if (!plan) {
    return "";
  }
  return `${plural(plan.resumes || 0, "resume")}, ${plural(plan.lines_in || 0, "line")} in: ${plural(plan.exact_duplicates || 0, "exact duplicate")} and ${plural(
    list(plan.near_duplicates).length,
    "near-duplicate",
  )} folded, ${plural(list(plan.questions).length, "conflict")}. The master would hold ${plural(plan.lines_out || 0, "line")} and ${plural(plan.entries || 0, "entry", "entries")}.`;
}

// What became of EVERY line of the resumes (`migration.source_lines`): "Of
// 131 lines of resume text ...: 112 kept, 17 folded into a line the master
// holds, 2 left out." "" when the server did not count them.
export function sourceLinesLine(payload) {
  const lines = payload && payload.migration && payload.migration.source_lines;
  if (!lines || typeof lines !== "object") {
    return "";
  }
  return `Of ${plural(lines.in || 0, "line")} of resume text (headings, role lines and wrapped lines counted): ${lines.kept || 0} kept, ${lines.folded || 0} folded into a line the master already holds, ${lines.left_out || 0} left out.`;
}

// The lines the merge leaves out, one row per resume and reason:
// {key, resume, lines: "line 1, 3", count, reason, why}. The text of a line
// is never sent: the row names the resume (its profiles), the line numbers
// in the stored resume, and why.
export function leftOutRows(payload) {
  const lines = payload && payload.migration && payload.migration.source_lines;
  const rows = [];
  list(lines && lines.resumes).forEach((resume, index) => {
    const who = list(resume.profiles).join(", ");
    list(resume.left_out).forEach((row) => {
      const numbers = list(row.lines);
      rows.push({
        key: `${index}:${row.reason}`,
        resume: who,
        lines: `${numbers.length === 1 ? "line" : "lines"} ${numbers.join(", ")}`,
        count: numbers.length,
        reason: text(row.reason),
        why: text(row.why),
      });
    });
  });
  return rows;
}

// 0.1.11.4 item 9d: the lines of a resume that LOOK like education and did not become an Education entry, when the
// master would have none (`source_lines.resumes[].education`): [{key, section, text}]. `text` is the server's own
// sentence ("education: lines 31-32 of the resume of Staff Engineer were kept in Other, not as a degree"): line
// numbers and a section, never the text of a line.
export function educationRows(payload) {
  const lines = payload && payload.migration && payload.migration.source_lines;
  const rows = [];
  list(lines && lines.resumes).forEach((resume, index) => {
    list(resume.education).forEach((row) => {
      if (row && text(row.message)) {
        rows.push({ key: `${index}:${row.first}-${row.last}`, section: text(row.section), text: text(row.message) });
      }
    });
  });
  return rows;
}

// The page's state for a migration answer: blocked (why), nothing (every
// profile already has its selection), questions (answer them), ready.
export function migrationState(payload) {
  if (!payload) {
    return "loading";
  }
  if (payload.status === "blocked") {
    return "blocked";
  }
  if (!payload.migration) {
    return "nothing";
  }
  return list(payload.questions).length > 0 ? "questions" : "ready";
}

export const ANSWER_LABELS = { a: "Keep A", b: "Keep B", both: "Keep both lines" };

// True when every open question has one of its choices.
export function answersComplete(questions, answers) {
  return list(questions).every((question) => list(question.choices).includes((answers || {})[question.question_id]));
}

// "Experience / Lumenfold" for a question.
export function questionWhere(question) {
  return [SECTION_LABELS[question.section] || question.section, question.entry].filter(Boolean).join(" / ");
}

// --- Picked / Left out on the job page ------------------------------------

function displayText(value) {
  return text(value).replace(/^(?:[#>*\-•–—]+\s*)+/, "").trim();
}

// The master line a shown line stands for: the id on its first resume ref
// (an edited line: the line it replaced). null for a line that is no master
// line (the Skills line code assembled, an answer's line, typed text).
export function lineItemId(line) {
  const base = line && line.kind === "custom" && line.edited_from ? line.edited_from : line;
  const ref = list(base && base.refs).find((candidate) => candidate && candidate.kind === "resume" && candidate.item_id);
  return ref ? ref.item_id : null;
}

function shownLines(result) {
  const out = [];
  list(result && result.sections).forEach((section) => {
    list(section.lines).forEach((line) => out.push({ line, section: section.heading, entry: null }));
    list(section.entries).forEach((entry) => {
      const heading = list(entry.heading)[0];
      list(entry.bullets).forEach((line) => out.push({ line, section: section.heading, entry: heading ? displayText(heading.text) : null }));
    });
  });
  return out;
}

// Picked / Left out for one tailored resume, grouped by role:
//   {available, pickedBy, picked: [group], leftOut: [group], counts}
//   group = {key, label, lines: [{id, text, code, reason, known}]}
// A picked line's text is the resume's own; a left-out line's text comes
// from the master the page read (`master`, GET /api/master) and is null
// (known: false) for a line that master no longer has. `available` is false
// for a resume that was not tailored from a master.
export function pickedLeftOut(response, master) {
  const selection = response && response.selection;
  if (!selection || typeof selection !== "object") {
    return { available: false, pickedBy: null, picked: [], leftOut: [], counts: { picked: 0, leftOut: 0 } };
  }
  const items = new Map(list(master && master.items).map((item) => [item.id, item]));
  const entries = new Map(list(master && master.entries).map((entry) => [entry.id, entry]));
  const shown = new Map();
  shownLines(response.result).forEach(({ line, section, entry }) => {
    const id = lineItemId(line);
    if (id && !shown.has(id)) {
      shown.set(id, { text: line.kind === "copy" ? displayText(line.text) : text(line.text), section, entry });
    }
  });
  const groups = (lines, textOf) => {
    const out = [];
    const byKey = new Map();
    lines.forEach((entry) => {
      const item = items.get(entry.id);
      const found = textOf(entry.id, item);
      const section = (found && found.section) || (item && item.section) || "other";
      const heading = (found && found.entry) || (item && item.entry_id && entries.get(item.entry_id) ? entries.get(item.entry_id).heading : null);
      const key = heading ? `${section}:${heading}` : section;
      if (!byKey.has(key)) {
        byKey.set(key, { key, label: heading || SECTION_LABELS[section] || section, lines: [] });
        out.push(byKey.get(key));
      }
      byKey.get(key).lines.push({ id: entry.id, text: found ? found.text : null, known: Boolean(found), code: entry.code, reason: text(entry.reason) });
    });
    return out;
  };
  // 0.1.11.5 (b): the two lists follow the STORED resume. The selection is the pick's record, and a change that
  // is not an Add or a Remove (Restore / Cut for length again) moves lines without writing it: a line the resume
  // prints is Picked and one it does not print is Left out, whichever list the pick recorded it in.
  const printed = printedItemIds(response.result);
  const now = { picked: [], leftOut: [] };
  list(selection.picked).forEach((line) => (printed.has(line.id) ? now.picked.push(line) : now.leftOut.push({ ...line, ...NOT_SHOWN })));
  list(selection.left_out).forEach((line) => (printed.has(line.id) ? now.picked.push({ ...line, ...SHOWN_AGAIN }) : now.leftOut.push(line)));
  const picked = groups(now.picked, (id, item) => shown.get(id) || (item ? { text: item.text, section: item.section, entry: null } : null));
  const leftOut = groups(now.leftOut, (_id, item) => (item ? { text: item.text, section: item.section, entry: null } : null));
  return {
    available: true,
    pickedBy: selection.picked_by === "code" ? "code" : "model",
    picked,
    leftOut,
    counts: { picked: now.picked.length, leftOut: now.leftOut.length },
  };
}

// What Picked / Left out says of a line the stored resume shows, or does not show, against the pick's own record.
export const SHOWN_AGAIN = { code: "shown_again", reason: "put back on this resume after the pick" };
export const NOT_SHOWN = { code: "not_shown", reason: "not on this resume now: it was cut for length after the pick" };

// The master line ids a stored resume prints: a copied or edited line's own id, every id a reworded line cites.
export function printedItemIds(result) {
  const ids = new Set();
  shownLines(result).forEach(({ line }) => {
    const base = line && line.kind === "custom" && line.edited_from ? line.edited_from : line;
    list(base && base.refs).forEach((ref) => ref && ref.kind === "resume" && ref.item_id && ids.add(ref.item_id));
  });
  return ids;
}

// What a STORED tailored resume was made from, as it recorded it (never
// worked out again here): "master" when `sources.master` names the master
// revision its lines were picked from (`selection`, stored with it, lists
// them), else "profile_resume", or "pasted_resume" for a resume no profile
// holds. The header (`madeFrom`) and Picked / Left out's line
// (`pickedByLine`, shown for a stored `selection`) both follow this record,
// so they name the same basis (0110-10-10 item 3).
export function recordedBasis(response) {
  const sources = response && response.sources;
  const fromMaster = (sources && sources.master && typeof sources.master === "object") || (response && response.selection && typeof response.selection === "object");
  if (fromMaster) {
    return "master";
  }
  return response && response.resume && response.resume.profile_id ? "profile_resume" : "pasted_resume";
}

// The header's "from ...": {basis, lead, name}; the page prints `lead` and
// `name` in bold. A resume picked from the master says so and names the
// profile it was picked for; the profile's own resume is named only when
// that resume is what the lines came from.
export function madeFrom(response, profileLabel) {
  const basis = recordedBasis(response);
  const profile = response && response.resume && response.resume.profile_id ? text(profileLabel) || response.resume.profile_id : "";
  if (basis === "master") {
    const revision = response.sources && response.sources.master ? response.sources.master.revision : null;
    return { basis, lead: `from your master resume${revision ? ` (revision ${revision})` : ""}, picked for profile`, name: profile };
  }
  return { basis, lead: "from resume", name: basis === "pasted_resume" ? "a pasted resume" : profile };
}

// "Picked by the model inside the lines GigAI offered it." / the fallback.
export function pickedByLine(view) {
  if (!view || !view.available) {
    return "";
  }
  return view.pickedBy === "code"
    ? "Picked by GigAI's own rules: the model call did not give a usable answer."
    : "GigAI picked the candidate lines from your whole master; the model ordered them; GigAI fitted the result to 2 pages.";
}

// An Add that needs room: what the page asks. null when the change applied.
export function roomQuestion(change) {
  if (!change || change.applied !== false) {
    return null;
  }
  const cuts = list(change.would_cut);
  const names = cuts.map((cut) => (cut.kind === "role" ? `the role ${cut.role}` : `"${cut.text}" (${cut.role})`));
  const lines = cuts.length === 0 ? "No line can go to make room." : `To keep ${change.max_pages} pages, ${cuts.length === 1 ? "this line" : "these lines"} would be cut: ${names.join("; ")}.`;
  return {
    text: `With this line the resume is ${plural(change.pages, "page")}. ${lines}`,
    canCut: cuts.length > 0,
    cutLabel: cuts.length === 1 ? "Add it and cut that line" : "Add it and cut those lines",
    keepLabel: `Keep both (${plural(change.pages, "page")})`,
  };
}

// What an applied Add or Remove did, in one line.
export function changeLine(change) {
  if (!change || !change.applied) {
    return "";
  }
  if (!change.changed) {
    return "That line is already on the resume.";
  }
  if (change.use === "remove") {
    return "Removed from this resume. It is under Left out; your master is unchanged.";
  }
  const cuts = list(change.cut);
  const room = cuts.length > 0 ? ` To keep ${change.max_pages} pages, ${plural(cuts.length, "line")} went: Restore under "Cut for length" puts ${cuts.length === 1 ? "it" : "them"} back.` : "";
  const over = change.pages && change.pages > change.max_pages ? ` The resume is now ${plural(change.pages, "page")}.` : "";
  return `Added to this resume.${room}${over}`;
}

// "Save this wording to your master": the master line an EDITED resume line
// replaced, with the edited text; null when the line is not an edit of a
// master line.
export function saveWordingTarget(line) {
  if (!line || !line.edited || !line.editedFrom) {
    return null;
  }
  const id = lineItemId({ kind: "custom", edited_from: line.editedFrom });
  const wording = text(line.text).trim();
  return id && wording ? { id, text: wording } : null;
}

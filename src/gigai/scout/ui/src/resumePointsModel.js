// 0.1.11.5 (b): the POINTS of one job's resume, beside its preview, as pure
// functions (no React, no fetch, no storage) so
// tests/api_e2e/test_ui_resume_points_model.py can run them under node.
//
// The list is the stored job resume's own lines (GET /api/tailored-resumes):
// the Summary and each role's bullets, under their role. Per point the person
// can change its words, take it off, and put on a line of the master this
// resume leaves out. Every change is one request to a route that was there
// already, saved at once for THIS job's resume and never written to the
// master:
//
//   edit     PUT /api/tailored-resumes/lines      {line_id, use: "custom", text}
//   remove   PUT /api/tailored-resumes/selection  {use: "remove", item_id}
//   add      PUT /api/tailored-resumes/selection  {use: "add", item_id, fit: "keep"}
//
// An Add is sent with `fit: "keep"`: nothing is cut to make room, the person
// fits the page with the spacing slider and sees the page count beside it.
// There is no route for a point typed from nothing, so the list offers none:
// a line is added from the master, then its words are changed.
import { ENTRY_SECTIONS, SECTION_LABELS, pickedLeftOut, lineItemId } from "./masterModel.js";
import { displayText } from "./tailoredResumeModel.js";

const text = (value) => (typeof value === "string" ? value : "");
const list = (value) => (Array.isArray(value) ? value : []);

// The sections whose lines are points. Skills are not (they follow the lines shown) and Education has no bullets.
const POINT_SECTIONS = new Set(["summary", "experience", "projects", "other"]);
// The kinds of master line an Add can put on a resume as a point (the server refuses a Skills line by id).
const ADDABLE_KINDS = new Set(["bullet", "other"]);
// A line the person took off this resume (tailor_selection_edit.REMOVED).
const REMOVED_BY_YOU = "removed_by_you";
// The picker shows its search box from this many lines on.
export const SEARCH_FROM = 8;
// tailored_resume.MAX_CUSTOM_TEXT_CHARS.
export const MAX_POINT_CHARS = 400;

export const SAVING_TEXT = "Saving";
export const SAVED_TEXT = "Saved for this job. Your master is unchanged.";
export const EMPTY_TEXT = "Not saved: a point cannot be empty. Use Remove to take it off this resume.";
export const NO_SELECTION_TEXT = "This resume was not picked from your master: its points can be reworded here, not added or removed.";
export const NOTHING_LEFT_TEXT = "Every line of your master is on this resume.";
export const NO_MATCH_TEXT = "No left-out line has those words.";

// A line's words as the box shows them: a copied line without its own markers, anything else as written.
function shown(line) {
  return line && line.kind === "copy" ? displayText(line.text) : text(line && line.text).trim();
}

function point(line) {
  return { id: text(line && line.id) || null, itemId: lineItemId(line), text: shown(line), edited: Boolean(line && line.kind === "custom") };
}

// The points of a stored resume, in the order it prints them:
//   [{key, label, lines: [{id, itemId, text, edited}]}]
// `id` is the line's own id on this resume (an edit names it); `itemId` the master line it stands for (a Remove
// names it), null for a line that came from no master line. A role that shows no bullet has no group.
export function pointGroups(stored) {
  const out = [];
  list(stored && stored.result && stored.result.sections).forEach((section) => {
    if (!POINT_SECTIONS.has(section.heading)) {
      return;
    }
    const label = SECTION_LABELS[section.heading] || section.heading;
    list(section.entries).forEach((entry, index) => {
      const heading = list(entry.heading)[0];
      const lines = list(entry.bullets).map(point);
      if (lines.length > 0) {
        out.push({ key: `${section.heading}:${index}`, label: heading ? displayText(heading.text) : label, lines });
      }
    });
    const lines = ENTRY_SECTIONS.has(section.heading) ? [] : list(section.lines).map(point);
    if (lines.length > 0) {
      out.push({ key: section.heading, label, lines });
    }
  });
  return out;
}

export function pointCount(groups) {
  return list(groups).reduce((sum, group) => sum + group.lines.length, 0);
}

// "Point 2 of Larkspur Table Systems": what a screen reader says of a point's box and its Remove.
export function pointLabel(group, index) {
  return `Point ${index + 1} of ${group.label}`;
}

// True when points can be added and removed: the resume was picked from the master (it lists Picked / Left out).
export function canMovePoints(stored) {
  return Boolean(stored && stored.selection && typeof stored.selection === "object");
}

// The master's lines this resume leaves out, for the "Add a point" picker:
//   {total, groups: [{key, label, lines: [{id, text, removed}]}]}
// By role, the roles in the master's own order (its newest first); in a role, a line the person took off this
// resume comes first (`removed`: its button says "Put back"). `query` keeps the lines that hold every word of it,
// in the line or in its role's name. `total` counts the lines before the search.
export function leftOutChoices(stored, master, query = "") {
  const items = new Map(list(master && master.items).map((item) => [item.id, item]));
  const order = new Map(list(master && master.entries).map((entry, index) => [entry.id, index]));
  const place = (line) => {
    const item = items.get(line.id);
    return item && order.has(item.entry_id) ? order.get(item.entry_id) : -1; // a line under no role (Other) first
  };
  const words = text(query).toLowerCase().split(/\s+/).filter(Boolean);
  let total = 0;
  const groups = pickedLeftOut(stored, master)
    .leftOut.map((group) => {
      const lines = group.lines.filter((line) => line.known && ADDABLE_KINDS.has(text(items.get(line.id) && items.get(line.id).kind)));
      total += lines.length;
      const kept = lines
        .filter((line) => words.every((word) => `${line.text} ${group.label}`.toLowerCase().includes(word)))
        .map((line) => ({ id: line.id, text: line.text, removed: line.code === REMOVED_BY_YOU }));
      kept.sort((a, b) => Number(b.removed) - Number(a.removed)); // stable: the master's order within each
      return { key: group.key, label: group.label, lines: kept, at: lines.length > 0 ? place(lines[0]) : 0 };
    })
    .filter((group) => group.lines.length > 0);
  groups.sort((a, b) => a.at - b.at);
  return { total, groups: groups.map(({ at: _at, ...group }) => group) };
}

// What a refused change says, in the page's own words (never an id, never the server's sentence).
const REFUSALS = {
  master_line_not_found: "Not added: that line is no longer in your master. Nothing was changed.",
  selection_line_not_shown: "Not removed: that point is no longer on this resume. Nothing was changed.",
  selection_line_unsupported: "Not added: a Skills line cannot be added as a point. The skills follow the points shown.",
  selection_unavailable: NO_SELECTION_TEXT,
  master_not_found: "Not added: there is no master resume to take the line from.",
  tailored_resume_changed: "Not saved: this resume was replaced by a newer one a moment ago. It is shown now; make the change again.",
  tailored_resume_not_found: "Not saved: no resume is stored for this job any more.",
  personal_info_refused: "Not saved: a point cannot hold a name or contact details. You type those in Generate PDF.",
};

export function pointErrorText(err) {
  const code = text(err && err.code);
  if (REFUSALS[code]) {
    return REFUSALS[code];
  }
  if (code === "invalid_value" && /longer than/.test(text(err && (err.detail || err.message)))) {
    return `Not saved: a point is at most ${MAX_POINT_CHARS} characters.`;
  }
  if (code === "invalid_value" && /no line/.test(text(err && (err.detail || err.message)))) {
    return "Not saved: that point is no longer on this resume. Nothing was changed.";
  }
  return "Not saved: the change could not be stored. Nothing was changed; try again.";
}

// The words to send for an edit, or null when there is nothing to send: the box holds what the point already says.
export function editOf(line, draft) {
  const next = text(draft).split(/\s+/).filter(Boolean).join(" ");
  return next === "" || next === text(line && line.text) ? null : next;
}

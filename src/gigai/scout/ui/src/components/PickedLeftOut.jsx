import { useCallback, useEffect, useRef, useState } from "react";
import { getMaster, getTailoredResumes, putTailoredResumeSelection } from "../api.js";
import { changeLine, errorText, pickedByLine, pickedLeftOut, recordedBasis, roomQuestion } from "../masterModel.js";
import { latestStored } from "../tailoredResumeModel.js";
import { ORIGIN_OLD_TAILOR, ORIGIN_OLD_TAILOR_YOURS, pickedNotes, pickedReason, rowsById, skillsView } from "../jobResumeModel.js";

// 0.1.10.9 master P5: Picked / Left out, on the job page's resume panel, for
// a resume picked from the master resume (`stored.selection`).
//
//   Picked (n)    the master lines this resume shows, by role, each with why;
//                 Remove takes one off THIS job's resume
//   Left out (m)  every other line of the master, by role, each with why;
//                 Add shows one on this resume
//
// 0.1.11 N6 (SPEC section 6, item 5), extended:
//
//   Picked        a line's reason names the requirements it supports, by id
//                 ("supports req-3fa91c, req-77b0aa"), and what Scout added
//                 ("added by Scout: the only evidence for <requirement>",
//                 "room left on the page"): the suggestion record's
//                 (jobResumeModel.pickedReason). Without a record the stored
//                 resume's own sentence stays
//   Changed (k)   each line whose wording is not the master's, beside the
//                 master line it replaced, with its sources, who wrote it
//                 and Restore (PUT /api/tailored-resumes/lines `original`)
//   Skills        the groups shown and, when any were cut for length, which
//   `state.focus` a master line another part of the page asks to see (a
//                 requirement whose evidence is "not in the resume"): Left
//                 out opens with that line marked, and Add puts it back
//
// An Add that pushes a resume that fitted over 2 pages is ASKED about: the
// server names the lines that would go to keep 2 pages and stores nothing;
// "Add it and cut that line" makes room, "Keep both" keeps both.
//
// The reasons and ids come from the stored resume; a left-out line's text
// comes from the master (read once, when a list is first opened). Every text
// goes into the tree as a React text child. A resume that was not picked
// from a master and has no changed line renders nothing.
function Group({ group, action, busy, onAct, reasonFor, focus }) {
  return (
    <li className="master-entry" data-group={group.key}>
      <strong>{group.label}</strong>
      <ul className="master-lines">
        {group.lines.map((line) => (
          <li
            key={line.id}
            className={`master-line${focus === line.id ? " focused" : ""}`}
            data-item-id={line.id}
            data-code={line.code}
            data-focused={focus === line.id ? "true" : undefined}
            ref={focus === line.id ? (node) => node && typeof node.scrollIntoView === "function" && node.scrollIntoView({ block: "center" }) : undefined}
          >
            <div className="master-line-body">
              <span data-role="line-text">{line.known ? line.text : "A line your master no longer has."}</span>
              <div className="muted small" data-role="reason">
                {reasonFor ? reasonFor(line) : line.reason}
              </div>
            </div>
            {line.known && (
              <span className="master-line-actions">
                <button className="link-button" data-action={action.use} disabled={busy} onClick={() => onAct(action.use, line.id)}>
                  {action.label}
                </button>
              </span>
            )}
          </li>
        ))}
      </ul>
    </li>
  );
}

function Changed({ lines, busy, onRestore }) {
  return (
    <ul className="story-list" data-role="changed">
      {lines.map((line, index) => (
        <li key={line.id || index} className="master-line" data-line-id={line.id || undefined} data-by={line.mine ? "user" : "old-tailor"}>
          <div className="master-line-body">
            {line.entry && <div className="muted small">{line.entry}</div>}
            <span data-role="line-text">{line.text}</span>
            {line.before && (
              <div className="muted small" data-role="line-before">
                Your master says: {line.before}
              </div>
            )}
            <div className="muted small" data-role="line-sources">
              {line.mine ? `Changed in chat by ${line.by}` : "Reworded by the old tailor"}
              {line.sources.length > 0 ? ` · sources: ${line.sources.map((source) => source.label).join(", ")}` : " · no source cited"}
            </div>
          </div>
          {line.restore && (
            <span className="master-line-actions">
              <button className="link-button" data-action="restore-line" disabled={busy} title="Show the master line again on this job's resume" onClick={() => onRestore(line.id, line.restore)}>
                Restore
              </button>
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

function Skills({ view }) {
  return (
    <div data-role="skills">
      <p data-role="skills-shown">
        <strong>Shown ({view.shown.length}):</strong> {view.shown.join(" · ") || "none"}
      </p>
      {view.cut.length > 0 && (
        <p data-role="skills-cut">
          <strong>Cut for length ({view.cut.length}):</strong> {view.cut.map((skill) => skill.name).join(" · ")}
        </p>
      )}
      {view.other.length > 0 && (
        <p className="muted small" data-role="skills-other">
          Not shown ({view.other.length}): {view.other.map((skill) => (skill.reason ? `${skill.name} (${skill.reason})` : skill.name)).join(" · ")}
        </p>
      )}
    </div>
  );
}

export default function PickedLeftOut({ stored, state, record = null, assessment = null, origin = null, changed = [], busy: outerBusy = false, onRestoreLine = null }) {
  const [open, setOpen] = useState(null); // null | "picked" | "left-out" | "changed" | "skills"
  const [master, setMaster] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [note, setNote] = useState(null);
  const [question, setQuestion] = useState(null); // {itemId, ...roomQuestion}
  const hasSelection = Boolean(stored && stored.selection);
  const busy = saving || outerBusy;
  const needsMaster = open === "picked" || open === "left-out";

  useEffect(() => {
    if (!needsMaster || master || !hasSelection) {
      return undefined;
    }
    let current = true;
    getMaster()
      .then((response) => current && setMaster(response.master || { items: [], entries: [] }))
      .catch((err) => current && setError(errorText(err)));
    return () => {
      current = false;
    };
  }, [needsMaster, master, hasSelection]);

  // A line another part of the page asks to see: Left out opens (each request once).
  const focus = state.focus || null;
  const shownFocus = useRef(null);
  useEffect(() => {
    if (focus && hasSelection && shownFocus.current !== focus.at) {
      shownFocus.current = focus.at;
      setOpen("left-out");
    }
  }, [focus, hasSelection]);

  const act = useCallback(
    (use, itemId, fit) => {
      setSaving(true);
      setError(null);
      setNote(null);
      putTailoredResumeSelection({ profileId: state.profileId, jobIdentity: state.jobIdentity, updatedAt: stored.updated_at, use, itemId, fit })
        .then((response) => {
          const { selection_change: change, ...resume } = response;
          const asks = roomQuestion(change);
          if (asks) {
            setQuestion({ itemId, ...asks });
            return;
          }
          setQuestion(null);
          setNote(changeLine(change));
          if (change && change.changed) {
            state.setStored(resume);
          }
        })
        .catch((err) => {
          setError(errorText(err));
          if (err.code === "tailored_resume_changed") {
            getTailoredResumes({ profileId: state.profileId, jobIdentity: state.jobIdentity })
              .then((response) => state.setStored(latestStored(response.items)))
              .catch(() => {});
          }
        })
        .finally(() => setSaving(false));
    },
    [stored, state],
  );

  if (!hasSelection && changed.length === 0) {
    return null;
  }
  const view = pickedLeftOut(stored, master);
  const notes = pickedNotes(record, assessment);
  const rows = rowsById(assessment);
  const skills = skillsView(stored);
  const oldTailor = origin === ORIGIN_OLD_TAILOR || origin === ORIGIN_OLD_TAILOR_YOURS;
  const tab = (name, label, count) => (
    <button type="button" className={`button small ${open === name ? "" : "secondary"}`} aria-pressed={open === name} data-action={`show-${name}`} onClick={() => setOpen(open === name ? null : name)}>
      {label}
      {count === null ? "" : ` (${count})`}
    </button>
  );
  return (
    <div className="picked-left-out" data-testid="picked-left-out" data-picked-by={view.pickedBy || undefined}>
      <div className="resume-change-bar">
        <div className="resume-summary" data-role="picked-by" data-basis={recordedBasis(stored)}>
          {oldTailor ? pickedByLine(view) : ""}
        </div>
        <div className="view-toggle" role="group" aria-label="Picked and left out">
          {hasSelection && tab("picked", "Picked", view.counts.picked)}
          {hasSelection && tab("left-out", "Left out", view.counts.leftOut)}
          {changed.length > 0 && tab("changed", "Changed", changed.length)}
          {hasSelection && tab("skills", "Skills", null)}
        </div>
      </div>
      {error && (
        <div className="callout danger" role="alert" data-role="selection-error">
          {error}
        </div>
      )}
      {note && (
        <div className="callout info" data-role="selection-note">
          {note}
        </div>
      )}
      {question && (
        <div className="callout" data-role="room-question">
          <span>{question.text}</span>{" "}
          {question.canCut && (
            <button type="button" className="button small" data-action="add-cut" disabled={busy} onClick={() => act("add", question.itemId, "cut")}>
              {question.cutLabel}
            </button>
          )}{" "}
          <button type="button" className="button small secondary" data-action="add-keep" disabled={busy} onClick={() => act("add", question.itemId, "keep")}>
            {question.keepLabel}
          </button>{" "}
          <button type="button" className="button small secondary" data-action="add-cancel" disabled={busy} onClick={() => setQuestion(null)}>
            Cancel
          </button>
        </div>
      )}
      {needsMaster && !master && !error && <p className="muted">Reading your master…</p>}
      {open === "picked" && master && (
        <ul className="story-list" data-role="picked">
          {view.picked.map((group) => (
            <Group key={group.key} group={group} action={{ use: "remove", label: "Remove" }} busy={busy} onAct={act} reasonFor={(line) => pickedReason(line.id, notes, rows, line.reason, { keep: line.code === "added_by_you" })} />
          ))}
        </ul>
      )}
      {open === "left-out" && master && (
        <ul className="story-list" data-role="left-out">
          {view.leftOut.map((group) => (
            <Group key={group.key} group={group} action={{ use: "add", label: "Add" }} busy={busy} onAct={act} focus={focus ? focus.line : null} />
          ))}
        </ul>
      )}
      {open === "changed" && <Changed lines={changed} busy={busy} onRestore={onRestoreLine} />}
      {open === "skills" && <Skills view={skills} />}
    </div>
  );
}

import { useCallback, useEffect, useState } from "react";
import { getMaster, getTailoredResumes, putTailoredResumeSelection } from "../api.js";
import { changeLine, errorText, pickedByLine, pickedLeftOut, roomQuestion } from "../masterModel.js";
import { latestStored } from "../tailoredResumeModel.js";

// 0.1.10.9 master P5: Picked / Left out, on the job page's Tailored resume
// panel, for a resume tailored from the master resume (`stored.selection`).
//
//   Picked (n)    the master lines this resume shows, by role, each with why;
//                 Remove takes one off THIS job's resume
//   Left out (m)  every other line of the master, by role, each with why;
//                 Add shows one on this resume
//
// An Add that pushes a resume that fitted over 2 pages is ASKED about: the
// server names the lines that would go to keep 2 pages and stores nothing;
// "Add it and cut that line" makes room, "Keep both" keeps both.
//
// The reasons and ids come from the stored resume; a left-out line's text
// comes from the master (read once, when a list is first opened). Every text
// goes into the tree as a React text child. A resume that was not tailored
// from a master renders nothing.
function Group({ group, action, busy, onAct }) {
  return (
    <li className="master-entry" data-group={group.key}>
      <strong>{group.label}</strong>
      <ul className="master-lines">
        {group.lines.map((line) => (
          <li key={line.id} className="master-line" data-item-id={line.id} data-code={line.code}>
            <div className="master-line-body">
              <span data-role="line-text">{line.known ? line.text : "A line your master no longer has."}</span>
              <div className="muted small" data-role="reason">
                {line.reason}
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

export default function PickedLeftOut({ stored, state }) {
  const [open, setOpen] = useState(null); // null | "picked" | "left-out"
  const [master, setMaster] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [note, setNote] = useState(null);
  const [question, setQuestion] = useState(null); // {itemId, ...roomQuestion}
  const hasSelection = Boolean(stored && stored.selection);

  useEffect(() => {
    if (!open || master || !hasSelection) {
      return undefined;
    }
    let current = true;
    getMaster()
      .then((response) => current && setMaster(response.master || { items: [], entries: [] }))
      .catch((err) => current && setError(errorText(err)));
    return () => {
      current = false;
    };
  }, [open, master, hasSelection]);

  const act = useCallback(
    (use, itemId, fit) => {
      setBusy(true);
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
        .finally(() => setBusy(false));
    },
    [stored, state],
  );

  if (!hasSelection) {
    return null;
  }
  const view = pickedLeftOut(stored, master);
  const tab = (name, label, count) => (
    <button type="button" className={`button small ${open === name ? "" : "secondary"}`} aria-pressed={open === name} data-action={`show-${name}`} onClick={() => setOpen(open === name ? null : name)}>
      {label} ({count})
    </button>
  );
  return (
    <div className="picked-left-out" data-testid="picked-left-out" data-picked-by={view.pickedBy}>
      <div className="resume-change-bar">
        <div className="resume-summary" data-role="picked-by">
          {pickedByLine(view)}
        </div>
        <div className="view-toggle" role="group" aria-label="Picked and left out">
          {tab("picked", "Picked", view.counts.picked)}
          {tab("left-out", "Left out", view.counts.leftOut)}
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
      {open && !master && !error && <p className="muted">Reading your master…</p>}
      {open === "picked" && master && (
        <ul className="story-list" data-role="picked">
          {view.picked.map((group) => (
            <Group key={group.key} group={group} action={{ use: "remove", label: "Remove" }} busy={busy} onAct={act} />
          ))}
        </ul>
      )}
      {open === "left-out" && master && (
        <ul className="story-list" data-role="left-out">
          {view.leftOut.map((group) => (
            <Group key={group.key} group={group} action={{ use: "add", label: "Add" }} busy={busy} onAct={act} />
          ))}
        </ul>
      )}
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { getMaster, getTailoredResumes, putTailoredResumeLine, putTailoredResumeSelection } from "../api.js";
import { latestStored } from "../tailoredResumeModel.js";
import {
  EMPTY_TEXT,
  MAX_POINT_CHARS,
  NOTHING_LEFT_TEXT,
  NO_MATCH_TEXT,
  NO_SELECTION_TEXT,
  SAVED_TEXT,
  SAVING_TEXT,
  SEARCH_FROM,
  canMovePoints,
  editOf,
  leftOutChoices,
  pointCount,
  pointErrorText,
  pointGroups,
  pointLabel,
} from "../resumePointsModel.js";

// 0.1.11.5 (b): the POINTS of this job's resume, beside its preview
// (ResumePreview.jsx). Not click-to-edit on the pages: a list.
//
//   shows    the stored resume's Summary and each role's bullets, under their
//            role, each in a small box (resumePointsModel.pointGroups)
//   edit     type in a point's box; Enter, or leaving the box, saves it (Shift
//            + Enter is a new line in the box, Escape puts the words back)
//   remove   "Remove" takes the point off this resume; it is then under
//            "Add a point", first in its role, as "Put back"
//   add      "Add a point" lists the lines of the master this resume leaves
//            out, by role (the master is read once, when the list is first
//            opened); one click puts a line on, under its own role
//
// Every change is ONE request to a route that was there already, saved at once
// for this job's resume (no Save button) and never written to the master. The
// answer is the stored resume: `state.setStored` holds it, so the preview is
// made again and the page count follows (JobResumePanel hands the preview the
// stored resume's text). Changes are sent one after the other, in the order
// they were made.
function Point({ line, label, movable, onEdit, onRemove }) {
  const [draft, setDraft] = useState(line.text);
  const sent = useRef(null); // the words last sent for this point: Enter then leaving the box is one request
  useEffect(() => {
    setDraft(line.text);
    sent.current = null;
  }, [line.text]);
  const commit = () => {
    if (draft.trim() === "") {
      setDraft(line.text);
      onEdit(line, null);
      return;
    }
    const next = editOf(line, draft);
    if (next !== null && next !== sent.current) {
      sent.current = next;
      onEdit(line, next);
    }
  };
  return (
    <li className="resume-point" data-role="point" data-line-id={line.id || undefined} data-item-id={line.itemId || undefined} data-edited={line.edited ? "true" : "false"}>
      <textarea
        className="resume-point-text"
        data-role="point-text"
        aria-label={label}
        rows={3}
        maxLength={MAX_POINT_CHARS}
        value={draft}
        readOnly={!line.id}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            commit();
          } else if (event.key === "Escape") {
            setDraft(line.text);
          }
        }}
      />
      <span className="resume-point-actions">
        {line.edited && (
          <span className="muted small" data-role="point-edited">
            Your words
          </span>
        )}
        {movable && line.itemId && (
          <button type="button" className="link-button" data-action="remove-point" aria-label={`Remove ${label}`} onClick={() => onRemove(line)}>
            Remove
          </button>
        )}
      </span>
    </li>
  );
}

export default function ResumePoints({ stored, state }) {
  const [master, setMaster] = useState(null);
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState(null); // {kind: "saving" | "saved" | "error", text}
  const [masterError, setMasterError] = useState(null);
  const latest = useRef({ stored, state });
  latest.current = { stored, state };
  const queue = useRef(Promise.resolve());
  const waiting = useRef(0);
  const movable = canMovePoints(stored);

  useEffect(() => {
    if (!adding || master || !movable) {
      return undefined;
    }
    let current = true;
    getMaster()
      .then((response) => current && setMaster(response.master || { items: [], entries: [] }))
      .catch(() => current && setMasterError("Your master could not be read. Try again."));
    return () => {
      current = false;
    };
  }, [adding, master, movable]);

  // One change of the stored resume: `make(stored, key)` is the request. Sent after the ones before it.
  const send = useCallback((make) => {
    waiting.current += 1;
    setStatus({ kind: "saving", text: SAVING_TEXT });
    queue.current = queue.current.then(() => {
      const { stored: held, state: page } = latest.current;
      const key = { profileId: page.profileId, jobIdentity: page.jobIdentity, updatedAt: held.updated_at };
      return make(key)
        .then((response) => {
          const { selection_change: change, ...resume } = response;
          if (!change || change.changed) {
            latest.current = { ...latest.current, stored: resume };
            page.setStored(resume);
          }
          waiting.current -= 1;
          if (waiting.current === 0) {
            setStatus({ kind: "saved", text: SAVED_TEXT });
          }
        })
        .catch((err) => {
          waiting.current -= 1;
          setStatus({ kind: "error", text: pointErrorText(err) });
          if (err && err.code === "tailored_resume_changed") {
            return getTailoredResumes({ profileId: key.profileId, jobIdentity: key.jobIdentity })
              .then((response) => page.setStored(latestStored(response.items)))
              .catch(() => {});
          }
          return undefined;
        });
    });
  }, []);

  const edit = useCallback(
    (line, words) => {
      if (words === null) {
        setStatus({ kind: "error", text: EMPTY_TEXT });
        return;
      }
      send((key) => putTailoredResumeLine({ ...key, lineId: line.id, use: "custom", text: words }));
    },
    [send],
  );
  const move = useCallback((use, itemId) => send((key) => putTailoredResumeSelection({ ...key, use, itemId, fit: use === "add" ? "keep" : undefined })), [send]);

  const groups = pointGroups(stored);
  const choices = adding && master ? leftOutChoices(stored, master, query) : null;
  return (
    <div className="resume-points" data-testid="resume-points" data-points={pointCount(groups)} data-state={status ? status.kind : "idle"}>
      <h4 className="resume-points-title">Points ({pointCount(groups)})</h4>
      <p className="muted small" data-role="points-help">
        Change a point's words, take it off, or add one. Each change is saved for this job only: your master is never changed.
      </p>
      <p className={status && status.kind === "error" ? "callout danger small" : "muted small"} role={status && status.kind === "error" ? "alert" : "status"} aria-live="polite" data-role="points-status">
        {status ? status.text : ""}
      </p>
      {movable ? (
        <div className="resume-points-add">
          <button type="button" className="button small secondary" data-action="add-point" aria-expanded={adding} onClick={() => setAdding((open) => !open)}>
            Add a point
          </button>
          {adding && !master && !masterError && <p className="muted small">Reading your master…</p>}
          {adding && masterError && (
            <p className="callout danger small" role="alert">
              {masterError}
            </p>
          )}
          {choices && (
            <div className="resume-points-choices" data-role="left-out-choices" data-total={choices.total}>
              {choices.total >= SEARCH_FROM && (
                <input type="search" className="resume-points-search" data-role="point-search" aria-label="Search the lines left out" placeholder="Search the lines left out" value={query} onChange={(event) => setQuery(event.target.value)} />
              )}
              {choices.total === 0 && <p className="muted small">{NOTHING_LEFT_TEXT}</p>}
              {choices.total > 0 && choices.groups.length === 0 && <p className="muted small">{NO_MATCH_TEXT}</p>}
              {choices.groups.map((group) => (
                <div key={group.key} data-role="choice-group">
                  <div className="resume-points-role">{group.label}</div>
                  <ul className="story-list">
                    {group.lines.map((line) => (
                      <li key={line.id} className="resume-point-choice" data-role="choice" data-item-id={line.id} data-removed={line.removed ? "true" : "false"}>
                        <span data-role="choice-text">{line.text}</span>
                        <button type="button" className="link-button" data-action="add-choice" onClick={() => move("add", line.id)}>
                          {line.removed ? "Put back" : "Add"}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        <p className="muted small" data-role="points-no-selection">
          {NO_SELECTION_TEXT}
        </p>
      )}
      {groups.map((group) => (
        <div key={group.key} data-role="point-group">
          <div className="resume-points-role">{group.label}</div>
          <ul className="story-list">
            {group.lines.map((line, index) => (
              <Point key={line.id || `${group.key}-${index}`} line={line} label={pointLabel(group, index)} movable={movable} onEdit={edit} onRemove={(removed) => move("remove", removed.itemId)} />
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

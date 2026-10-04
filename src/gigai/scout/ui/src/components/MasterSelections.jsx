import { useCallback, useEffect, useRef, useState } from "react";
import { getMasterSelection, postMasterSelection } from "../api.js";
import { errorText, refreshLine, selectionRow } from "../masterModel.js";

// 0.1.10.9 master P5: each profile's selection of the master resume, and the
// one button it may need (Settings, under Profiles; and the Master page).
//
// A selection is sticky: lines the master gained since it was made are only
// OFFERED ("3 new master lines: refresh?"). Refresh selects again from the
// whole master and makes the result the profile's resume; a profile whose
// shown line the master edited or retired gets "Print its resume again".
// With no master resume this renders nothing.
//
// `version` re-reads the statuses (the Master page bumps it after a write);
// a profile whose first selection is still being made (`pending`) is read
// again every few seconds until it lands.
const PENDING_POLL_MS = 2500;

export default function MasterSelections({ version = 0, onChanged = null }) {
  const [body, setBody] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);
  const [notes, setNotes] = useState({});
  const alive = useRef(true);
  const wasPending = useRef(false);
  // Held in a ref: the caller's function may be a new one on every render, and a read must not start for that.
  const changed = useRef(onChanged);
  changed.current = onChanged;

  const load = useCallback(() => {
    getMasterSelection()
      .then((response) => {
        if (!alive.current) {
          return;
        }
        setBody(response);
        setError(null);
        const pending = (response.pending || []).length > 0;
        if (wasPending.current && !pending && changed.current) {
          changed.current(); // a first selection landed: the profile's resume is now its own
        }
        wasPending.current = pending;
      })
      .catch((err) => alive.current && setError(errorText(err)));
  }, []);

  useEffect(() => {
    alive.current = true;
    load();
    return () => {
      alive.current = false;
    };
  }, [load, version]);

  const pending = body ? (body.pending || []).length > 0 : false;
  useEffect(() => {
    if (!pending) {
      return undefined;
    }
    const timer = setInterval(load, PENDING_POLL_MS);
    return () => clearInterval(timer);
  }, [pending, load]);

  const act = useCallback(
    (profileId, use) => {
      setBusy(profileId);
      setError(null);
      postMasterSelection({ profileId, use })
        .then((response) => {
          if (!alive.current) {
            return;
          }
          setNotes((current) => ({ ...current, [profileId]: refreshLine((response.changes || [])[0] || null) }));
          load();
          if (changed.current) {
            changed.current();
          }
        })
        .catch((err) => alive.current && setError(errorText(err)))
        .finally(() => alive.current && setBusy(null));
    },
    [load],
  );

  if (!body || !body.master) {
    return error ? <div className="callout danger">Could not read the profiles' selections: {error}</div> : null;
  }
  const profiles = (body.profiles || []).filter((status) => status.state !== "deleted");
  return (
    <section className="panel" data-role="master-selections">
      <h2>Profiles and your master resume</h2>
      <p className="muted">
        Each profile shows a selection of your master resume (revision {body.master.revision}). A selection stays as it is until you refresh it; new
        lines of the master are offered, never added by themselves.
      </p>
      {error && <div className="callout danger">{error}</div>}
      <ul className="story-list">
        {profiles.map((status) => {
          const row = selectionRow(status);
          return (
            <li key={status.profile_id} className="story-entry" data-profile-id={status.profile_id} data-selection-state={row.state}>
              <div className="story-entry-head">
                <strong>{status.label}</strong>
                {status.state === "archived" && <span className="tag">archived</span>}
              </div>
              <p className="muted small" data-role="selection-line">
                {row.line}
              </p>
              {row.offer && (
                <p data-role="selection-offer">
                  <strong>{row.offer}</strong>
                </p>
              )}
              {notes[status.profile_id] && (
                <div className="callout info" data-role="selection-note">
                  {notes[status.profile_id]}
                </div>
              )}
              {row.action && (
                <div className="card-actions">
                  <button className="button small secondary" data-action={`selection-${row.action.use}`} disabled={busy !== null} onClick={() => act(status.profile_id, row.action.use)}>
                    {busy === status.profile_id ? "Working…" : row.action.label}
                  </button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

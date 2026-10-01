import { useEffect, useState } from "react";
import { getBackgroundSettings, putBackgroundSettings } from "../api.js";
import {
  AUTO_REFRESH_HELP,
  BACKFILL_HELP,
  CHECK_TIMES_HELP,
  MODEL_TAGS_HELP,
  SNAPSHOT_HELP,
  UNREADABLE_WARNING,
  backfillModelOptions,
  buildPatch,
  draftFromResponse,
  checkTimesError,
  formError,
  hasCheckTimes,
  isUnreadable,
  overrideNotes,
  saveErrorText,
  scheduleSummary,
  taggingPausedNote,
  withDefaultCheckTimes,
} from "../backgroundSettingsModel.js";

// 0110-024 P4 / 0110-025 R4 / 0110-026 S3: Settings' "Background updates",
// over GET / PUT /api/settings/background. The form edits what the settings
// file says; nothing is saved until Save, and Save sends only what changed.
// An environment override is named beside the switch it decides.
// 0110-033: the times of day the boards are checked at (sources.check_times),
// one line for weekdays and one for weekend days, with what is in effect now.
export default function BackgroundUpdatesPanel() {
  const [response, setResponse] = useState(null);
  const [draft, setDraft] = useState(null);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [savedNote, setSavedNote] = useState(false);

  useEffect(() => {
    let live = true;
    getBackgroundSettings()
      .then((loaded) => {
        if (live) {
          setResponse(loaded);
          setDraft(draftFromResponse(loaded));
        }
      })
      .catch((err) => live && setError(saveErrorText(err)));
    return () => {
      live = false;
    };
  }, []);

  function change(patch) {
    setDraft((current) => ({ ...current, ...patch }));
    setSavedNote(false);
  }

  const patch = draft && response ? buildPatch(draft, response) : null;
  const invalid = draft ? formError(draft, response) : "";
  const timesShown = hasCheckTimes(response);
  const timesInvalid = draft && timesShown ? checkTimesError(draft) : "";
  const schedule = response ? scheduleSummary(response) : "";
  const unreadable = isUnreadable(response);

  async function save() {
    if (!patch || invalid) {
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const saved = await putBackgroundSettings(patch);
      setResponse(saved);
      setDraft(draftFromResponse(saved));
      setSavedNote(true);
    } catch (err) {
      setError(saveErrorText(err));
    } finally {
      setSaving(false);
    }
  }

  const notes = response ? overrideNotes(response) : { sources: "", tagging: "", snapshot: "" };
  const paused = draft ? taggingPausedNote(draft, response) : "";
  const off = unreadable || saving;

  return (
    <section className="panel" id="settings-background" data-role="background-settings">
      <h2>Background updates</h2>
      <p className="muted">What Scout does by itself while it is open. These apply to this Scout project on this machine.</p>
      {error && <div className="callout danger">{error}</div>}
      {unreadable && (
        <div className="callout warn" data-role="background-unreadable">
          {UNREADABLE_WARNING}
        </div>
      )}
      {!draft && !error && <p className="muted">Loading…</p>}
      {draft && (
        <>
          <div className="background-setting" data-setting="auto-refresh">
            <label className="checkbox-label" htmlFor="background-auto-refresh">
              <input id="background-auto-refresh" type="checkbox" checked={draft.autoRefresh} disabled={off} onChange={(event) => change({ autoRefresh: event.target.checked })} /> Check
              the company boards automatically
            </label>
            <small className="muted">{AUTO_REFRESH_HELP}</small>
            {notes.sources && <small className="override-note" data-role="override-sources">{notes.sources}</small>}
            {timesShown && (
              <div className="background-setting-sub" data-setting="check-times">
                <label className="form-label" htmlFor="background-check-times-weekdays">
                  Check on weekdays at
                </label>
                <input
                  id="background-check-times-weekdays"
                  type="text"
                  className="text-input"
                  value={draft.weekdayTimes}
                  disabled={off}
                  onChange={(event) => change({ weekdayTimes: event.target.value })}
                />
                <label className="form-label" htmlFor="background-check-times-weekends">
                  Check on weekend days at
                </label>
                <input
                  id="background-check-times-weekends"
                  type="text"
                  className="text-input"
                  value={draft.weekendTimes}
                  disabled={off}
                  onChange={(event) => change({ weekendTimes: event.target.value })}
                />
                <small className="muted">{CHECK_TIMES_HELP}</small>
                <button type="button" className="link-button" disabled={off} onClick={() => change(withDefaultCheckTimes(draft, response))} data-action="check-times-default">
                  Use the default times
                </button>
                {timesInvalid && <div className="field-error" data-role="check-times-error">{timesInvalid}</div>}
                {schedule && <small className="muted" data-role="check-times-effective">{schedule}</small>}
              </div>
            )}
          </div>

          <div className="background-setting" data-setting="model-tags">
            <label className="checkbox-label" htmlFor="background-model-tags">
              <input id="background-model-tags" type="checkbox" checked={draft.modelEnabled} disabled={off} onChange={(event) => change({ modelEnabled: event.target.checked })} /> Tag
              titles with the model
            </label>
            <small className="muted">
              {MODEL_TAGS_HELP} {paused}
            </small>
            {notes.tagging && <small className="override-note" data-role="override-tagging">{notes.tagging}</small>}
          </div>

          <div className="background-setting" data-setting="backfill">
            <label className="checkbox-label" htmlFor="background-backfill">
              <input
                id="background-backfill"
                type="checkbox"
                checked={draft.backfillEnabled}
                disabled={off || !draft.modelEnabled}
                onChange={(event) => change({ backfillEnabled: event.target.checked })}
              />{" "}
              Tag the rest in the background
            </label>
            <small className="muted">
              {BACKFILL_HELP} {!draft.modelEnabled ? "Needs “Tag titles with the model”." : paused}
            </small>
            <div className="background-setting-sub">
              <label htmlFor="background-backfill-model">Tag the rest with</label>
              <select id="background-backfill-model" value={draft.backfillModel} disabled={off || !draft.modelEnabled || !draft.backfillEnabled} onChange={(event) => change({ backfillModel: event.target.value })}>
                {backfillModelOptions(response, draft).map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="background-setting" data-setting="snapshot">
            <label className="checkbox-label" htmlFor="background-snapshot">
              <input id="background-snapshot" type="checkbox" checked={draft.snapshotEnabled} disabled={off} onChange={(event) => change({ snapshotEnabled: event.target.checked })} /> Use
              the shared starter snapshot
            </label>
            <small className="muted">{SNAPSHOT_HELP}</small>
            {notes.snapshot && <small className="override-note" data-role="override-snapshot">{notes.snapshot}</small>}
            <details className="background-advanced">
              <summary>Advanced: where the starter snapshot is read from</summary>
              <label className="form-label" htmlFor="background-manifest-url">
                Manifest address
              </label>
              <input
                id="background-manifest-url"
                type="url"
                className="text-input"
                value={draft.manifestUrl}
                disabled={off}
                placeholder="empty = the default address"
                onChange={(event) => change({ manifestUrl: event.target.value })}
              />
              <button type="button" className="link-button" disabled={off || draft.manifestUrl === ""} onClick={() => change({ manifestUrl: "" })} data-action="manifest-default">
                Use the default address
              </button>
              {invalid && !timesInvalid && <div className="field-error">{invalid}</div>}
            </details>
          </div>

          <div className="actions">
            {savedNote && <span className="muted" data-role="background-saved">Saved.</span>}
            <button type="button" className="button small" onClick={save} disabled={off || !patch || Boolean(invalid)} data-action="save-background-settings">
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </>
      )}
    </section>
  );
}

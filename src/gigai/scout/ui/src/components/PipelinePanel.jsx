import { useCallback, useEffect, useState } from "react";
import { getBackgroundSettings, getPipeline, postPipelineApproval, putBackgroundSettings } from "../api.js";
import { UNREADABLE_WARNING, isUnreadable, saveErrorText } from "../backgroundSettingsModel.js";
import { dateTimeLabel } from "../jobModel.js";
import {
  approvalRows,
  capRows,
  decisionBody,
  errorRows,
  hasPipelineSettings,
  laneRows,
  modelOptions,
  pipelineDraft,
  pipelineFormError,
  pipelinePatch,
  statusLine,
} from "../pipelineModel.js";
import { jobHash } from "../routing.js";

// 0.1.10.7 M4b: Settings' "Background pipeline", beside Background updates.
//   GET /api/pipeline                 the lanes, today's model calls against
//                                     their caps, the approvals that wait, the
//                                     last errors (codes)
//   POST /api/pipeline/approvals/{id} Approve / Deny one approval
//   GET / PUT /api/settings/background  the `pipeline` and `rank` blocks: the
//                                     switch, the caps, the Scout ATS minimum
//                                     of the Scout label, the model per step
// The form edits what the settings file says; Save sends only what changed.
// System data only: counts, codes and job links.
export default function PipelinePanel() {
  const [overview, setOverview] = useState(null);
  const [response, setResponse] = useState(null);
  const [draft, setDraft] = useState(null);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [savedNote, setSavedNote] = useState(false);
  const [deciding, setDeciding] = useState(null);
  const [decisionError, setDecisionError] = useState(null);

  const readOverview = useCallback(
    () =>
      getPipeline()
        .then(setOverview)
        .catch((err) => setError(err.detail || err.message || String(err))),
    [],
  );

  useEffect(() => {
    let live = true;
    readOverview();
    getBackgroundSettings()
      .then((loaded) => {
        if (live) {
          setResponse(loaded);
          setDraft(pipelineDraft(loaded));
        }
      })
      .catch((err) => live && setError(saveErrorText(err)));
    return () => {
      live = false;
    };
  }, [readOverview]);

  const change = (patch) => {
    setDraft((current) => ({ ...current, ...patch }));
    setSavedNote(false);
  };

  const unreadable = isUnreadable(response);
  const formShown = Boolean(draft) && hasPipelineSettings(response);
  const invalid = formShown ? pipelineFormError(draft) : "";
  const patch = formShown && !invalid ? pipelinePatch(draft, response) : null;
  const off = unreadable || saving;

  async function save() {
    if (!patch) {
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const saved = await putBackgroundSettings(patch);
      setResponse(saved);
      setDraft(pipelineDraft(saved));
      setSavedNote(true);
      readOverview();
    } catch (err) {
      setError(saveErrorText(err));
    } finally {
      setSaving(false);
    }
  }

  const decide = (id, approve) => {
    setDeciding(id);
    setDecisionError(null);
    postPipelineApproval(id, decisionBody(approve))
      .then(() => readOverview())
      .catch((err) => setDecisionError(err.detail || err.message || String(err)))
      .finally(() => setDeciding(null));
  };

  const caps = capRows(overview);
  const lanes = laneRows(overview);
  const approvals = approvalRows(overview);
  const errors = errorRows(overview);
  const number = (id, key, label) => (
    <div className="pipeline-field">
      <label className="form-label" htmlFor={id}>
        {label}
      </label>
      <input id={id} type="number" min={0} value={draft[key]} disabled={off} onChange={(event) => change({ [key]: event.target.value })} />
    </div>
  );
  const model = (id, key, label) => (
    <div className="pipeline-field">
      <label className="form-label" htmlFor={id}>
        {label}
      </label>
      <select id={id} value={draft[key]} disabled={off} onChange={(event) => change({ [key]: event.target.value })}>
        {modelOptions().map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );

  return (
    <section className="panel" id="settings-pipeline" data-testid="background-panel">
      <h2>Background pipeline</h2>
      <p className="muted">
        After a job is assessed (or when you ask for it on the job page), Scout picks its resume from your master, scores it and sets the Scout label. No model is called for that; only an
        assessment that has become old is made again, one model call.
      </p>
      {error && <div className="callout danger">{error}</div>}
      {unreadable && <div className="callout warn">{UNREADABLE_WARNING}</div>}
      {!overview && !error && <p className="muted">Loading…</p>}

      {overview && (
        <>
          <p className="pipeline-status" data-role="pipeline-status">
            {statusLine(overview)}
          </p>

          <div className="pipeline-caps" data-role="pipeline-caps">
            {caps.map((cap) => (
              <div key={cap.id} className={`cap-meter state-${cap.state}`} data-cap={cap.id} data-state={cap.state}>
                <div className="cap-label">{cap.label}</div>
                <div className="cap-value">{cap.text}</div>
                <div className="progress-bar-track" role="progressbar" aria-label={cap.label} aria-valuemin={0} aria-valuemax={cap.limit} aria-valuenow={cap.used}>
                  <div className="progress-bar-fill" style={{ width: `${cap.limit ? Math.min(100, Math.round((100 * cap.used) / cap.limit)) : 0}%` }} />
                </div>
                {cap.note && <div className="cap-note">{cap.note}</div>}
              </div>
            ))}
          </div>

          <h3>Lanes</h3>
          {lanes.length === 0 ? (
            <p className="muted">No step has been queued yet.</p>
          ) : (
            <ul className="lane-list" data-role="pipeline-lanes">
              {lanes.map((lane) => (
                <li key={lane.lane} data-lane={lane.lane} data-state={lane.state}>
                  <code>{lane.lane}</code> <span>{lane.text}</span>
                  {lane.note && <span className="lane-note"> · {lane.note}</span>}
                </li>
              ))}
            </ul>
          )}

          <h3>Approvals</h3>
          {decisionError && <div className="field-error">{decisionError}</div>}
          <ul className="approvals-list" data-testid="approvals-list">
            {approvals.length === 0 && <li className="muted">Nothing is waiting for your approval.</li>}
            {approvals.map((approval) => (
              <li key={approval.id} className="approval-row" data-role="approval">
                <div>
                  <strong>{approval.text}</strong>
                  <div className="muted small">
                    {approval.trigger}
                    {approval.createdAt ? ` · ${dateTimeLabel(approval.createdAt)}` : ""}
                  </div>
                </div>
                <div className="approval-actions">
                  <button type="button" className="button small" disabled={deciding !== null} data-action="approve" onClick={() => decide(approval.id, true)}>
                    {deciding === approval.id ? "Saving…" : "Approve"}
                  </button>
                  <button type="button" className="button small secondary" disabled={deciding !== null} data-action="deny" onClick={() => decide(approval.id, false)}>
                    Deny
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      {formShown && (
        <>
          <h3>Settings</h3>
          <div className="background-setting" data-setting="pipeline-enabled">
            <label className="checkbox-label" htmlFor="pipeline-enabled">
              <input id="pipeline-enabled" type="checkbox" checked={draft.enabled} disabled={off} onChange={(event) => change({ enabled: event.target.checked })} /> Run the pipeline
              in the background
            </label>
          </div>
          <div className="pipeline-fields">
            {number("pipeline-jobs-per-trigger", "jobsPerTrigger", "Jobs per trigger (the rest wait for approval)")}
            {number("pipeline-calls-per-day", "callsPerDay", "Pipeline model calls a day")}
            {number("pipeline-label-min-ats", "labelMinAts", "Scout ATS minimum for the Scout label")}
            {number("pipeline-rank-calls", "rankCallsPerDay", "Rank calls a day")}
            {number("pipeline-rank-warn", "rankWarnAt", "Rank warning level")}
          </div>
          <div className="pipeline-fields models">
            {model("pipeline-model-assess", "assessModel", "Re-assess an old assessment with")}
          </div>
          {invalid && <div className="field-error">{invalid}</div>}
          <div className="actions">
            {savedNote && (
              <span className="muted" data-role="pipeline-saved">
                Saved.
              </span>
            )}
            <button type="button" className="button small" onClick={save} disabled={off || !patch} data-action="save-pipeline-settings">
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </>
      )}

      {overview && (
        <>
          <h3>Last errors</h3>
          {errors.length === 0 ? (
            <p className="muted">None.</p>
          ) : (
            <div className="table-scroll">
              <table className="data-table" data-role="pipeline-errors">
                <thead>
                  <tr>
                    <th>Step</th>
                    <th>Code</th>
                    <th>Attempt</th>
                    <th>When</th>
                    <th>Job</th>
                  </tr>
                </thead>
                <tbody>
                  {errors.map((item) => (
                    <tr key={item.key}>
                      <td>{item.step}</td>
                      <td>
                        <code>{item.code}</code>
                      </td>
                      <td>{item.attempt}</td>
                      <td>{dateTimeLabel(item.at)}</td>
                      <td>
                        <a href={jobHash(item.job)}>Open</a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}

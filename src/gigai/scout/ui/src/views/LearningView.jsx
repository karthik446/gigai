import { useCallback, useEffect, useRef, useState } from "react";
import { getConfig, getLearningPathways, postLearningPathwayCancel, postLearningPathwayResume, postLearningPathways } from "../api.js";
import {
  EMPTY_NOTE,
  IMPORT_COMMAND,
  LEARNING_POLL_MS,
  ROLE_PLACEHOLDER,
  WHAT_IS_A_COURSE,
  anyLive,
  canSubmitRole,
  cleanedRole,
  displayRows,
  generateDialog,
} from "../learningModel.js";
import { modelTargetLabel } from "../modelTargets.js";
import LearningRequestDialog from "../components/LearningRequestDialog.jsx";

// 0.1.11.10 Part A slice 1 (read) plus Part B G6 (request): the Learning pathways tab (#/learning). A pathway is one
// role a course was built for, imported or requested from this tab; this page lists them, newest first, lets the
// operator type a role and request a new course (an estimate dialog first; nothing is generated before "Generate"),
// shows a running course's plain progress with Stop, and Resume for a failed/interrupted one.
export default function LearningView() {
  const [pathways, setPathways] = useState(null);
  const [error, setError] = useState(null);
  const [role, setRole] = useState("");
  const [modelLabel, setModelLabel] = useState(null);
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState(null);
  const [dialog, setDialog] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [dialogError, setDialogError] = useState(null);
  const [stoppingIds, setStoppingIds] = useState(() => new Set());
  const pollRef = useRef(null);

  const load = useCallback(() => {
    return getLearningPathways()
      .then((body) => {
        setPathways(body.pathways || []);
        setError(null);
        return body.pathways || [];
      })
      .catch((err) => {
        setError(err.message || String(err));
        return [];
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    let current = true;
    getConfig()
      .then((config) => {
        if (!current || !config) {
          return;
        }
        const target = (config.config && config.config.default_model_target) || config.default_model_target || null;
        setModelLabel(target ? modelTargetLabel(target) : null);
      })
      .catch(() => {});
    return () => {
      current = false;
    };
  }, []);

  // Poll every 2 s only while a course of this list is queued/running; stop otherwise. Cleared on unmount (leaving
  // the tab stops the poll).
  useEffect(() => {
    if (!anyLive(pathways)) {
      return undefined;
    }
    pollRef.current = window.setInterval(() => {
      load();
    }, LEARNING_POLL_MS);
    return () => {
      if (pollRef.current) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [pathways, load]);

  const askForEstimate = useCallback(() => {
    const typed = cleanedRole(role);
    if (!typed) {
      return;
    }
    setAsking(true);
    setAskError(null);
    postLearningPathways({ role_text: typed })
      .then((response) => {
        setDialog(generateDialog(response, modelLabel));
        setDialogError(null);
      })
      .catch((err) => setAskError(err.message || String(err)))
      .finally(() => setAsking(false));
  }, [role, modelLabel]);

  const closeDialog = useCallback(() => {
    setDialog(null);
    setDialogError(null);
  }, []);

  const approve = useCallback(() => {
    if (!dialog) {
      return;
    }
    setSubmitting(true);
    postLearningPathways(dialog.approveBody)
      .then(() => {
        setDialog(null);
        setDialogError(null);
        setRole("");
        return load();
      })
      .catch((err) => setDialogError(err.message || String(err)))
      .finally(() => setSubmitting(false));
  }, [dialog, load]);

  const stop = useCallback(
    (pathwayId) => {
      setStoppingIds((current) => new Set(current).add(pathwayId));
      postLearningPathwayCancel(pathwayId)
        .then(() => load())
        .catch(() => {
          setStoppingIds((current) => {
            const next = new Set(current);
            next.delete(pathwayId);
            return next;
          });
        });
    },
    [load],
  );

  const resume = useCallback(
    (pathwayId) => {
      postLearningPathwayResume(pathwayId)
        .then(() => load())
        .catch((err) => setError(err.message || String(err)));
    },
    [load],
  );

  const loaded = pathways !== null;
  const rows = loaded ? displayRows({ pathways }, { stoppingIds }) : [];

  return (
    <div data-role="learning">
      <section className="panel">
        <h2>Learning pathways</h2>
        <p className="muted">{WHAT_IS_A_COURSE}</p>
        <div className="form-group" data-role="learning-request-form">
          <label className="form-label" htmlFor="learning-role">
            Role
          </label>
          <input
            id="learning-role"
            type="text"
            className="text-input"
            value={role}
            onChange={(event) => setRole(event.target.value)}
            placeholder={ROLE_PLACEHOLDER}
          />
          <button type="button" className="button" onClick={askForEstimate} disabled={asking || !canSubmitRole(role)} data-action="learning-generate">
            Generate course
          </button>
        </div>
        {askError && (
          <div className="callout danger" data-role="learning-request-error">
            {askError}
          </div>
        )}
        {error && (
          <div className="callout danger">
            Could not load learning pathways: {error}{" "}
            <button className="button small secondary" onClick={load}>
              Retry
            </button>
          </div>
        )}
        {!loaded && !error && <p className="muted">Loading…</p>}
      </section>

      {dialog && (
        <LearningRequestDialog dialog={dialog} submitting={submitting} error={dialogError} onApprove={approve} onCancel={closeDialog} />
      )}

      {loaded && rows.length === 0 && (
        <section className="panel" data-role="learning-empty">
          <p className="muted">{EMPTY_NOTE}</p>
          <p className="muted small">
            To import a finished course: <code>{IMPORT_COMMAND}</code>
          </p>
        </section>
      )}

      {loaded && rows.length > 0 && (
        <ul className="learning-list" data-role="learning-list">
          {rows.map((row) => (
            <li key={row.id} className="learning-entry" data-testid="learning-row" data-pathway-id={row.id}>
              <div className="learning-entry-head">
                <strong>{row.roleText}</strong>
                <span className="muted small">{row.requestedAtLabel}</span>
              </div>
              <p className="muted small" data-role="learning-status">
                {row.statusText}
              </p>
              {row.progressLine && (
                <p className="muted small" data-role="learning-progress">
                  {row.progressLine}
                </p>
              )}
              {row.progressCounters && (
                <p className="muted small" data-role="learning-progress-counters">
                  {row.progressCounters}
                </p>
              )}
              <p className="muted small" data-role="learning-cost">
                {row.costText}
              </p>
              {row.lessons !== null && (
                <p className="muted small" data-role="learning-lessons">
                  {row.lessons} lesson{row.lessons === 1 ? "" : "s"}
                  {row.sizeLabel ? `, ${row.sizeLabel}` : ""}
                </p>
              )}
              {row.sourceText && (
                <p className="muted small" data-role="learning-source">
                  {row.sourceText}
                </p>
              )}
              {row.errorText && (
                <p className="muted small" data-role="learning-error">
                  {row.errorText}
                </p>
              )}
              {row.canStop && (
                <button type="button" className="button small secondary" onClick={() => stop(row.id)} data-action="learning-stop">
                  Stop
                </button>
              )}
              {row.stopping && (
                <p className="muted small" data-role="learning-stopping">
                  Stopping…
                </p>
              )}
              {row.canResume && (
                <button type="button" className="button small secondary" onClick={() => resume(row.id)} data-action="learning-resume">
                  Resume
                </button>
              )}
              {row.canOpen && (
                <a className="button small secondary" data-action="open-course" href={row.url} target="_blank" rel="noopener">
                  Open course
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

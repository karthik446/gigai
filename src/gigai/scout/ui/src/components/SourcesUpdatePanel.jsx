import { useCallback, useEffect, useRef, useState } from "react";
import { getSourcesUpdate, startSourcesUpdate } from "../api.js";
import { relativeTimeLabel } from "../display.js";
import { SOURCES_POLL_MS, isRunning, looksStuck, sourcesProgress, sourcesResult, startErrorText, storedLine } from "../sourcesModel.js";

// uat-batch2 (N11-C): GET /api/sources/update, read when the page opens and
// polled every SOURCES_POLL_MS while an update runs (its own or one started
// from the CLI). `status` is the whole response, null until the first read;
// `unavailable` is true when the read failed (an older server has no such
// route), in which case the callers show nothing rather than an error.
export function useSourcesStatus({ enabled = true } = {}) {
  const [status, setStatus] = useState(null);
  const [unavailable, setUnavailable] = useState(false);
  const timer = useRef(null);
  const live = useRef(true);

  const stop = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const read = useCallback(() => {
    stop();
    return getSourcesUpdate()
      .then((response) => {
        if (!live.current) {
          return null;
        }
        setStatus(response);
        setUnavailable(false);
        if (isRunning(response)) {
          timer.current = setTimeout(read, SOURCES_POLL_MS);
        }
        return response;
      })
      .catch(() => {
        if (live.current) {
          setUnavailable(true);
        }
        return null;
      });
  }, [stop]);

  useEffect(() => {
    live.current = true;
    if (enabled) {
      read();
    }
    return () => {
      live.current = false;
      stop();
    };
  }, [enabled, read, stop]);

  return { status, unavailable, read };
}

function ProgressBar({ progress }) {
  return (
    <div
      className={`progress-bar-track${progress.determinate ? "" : " indeterminate"}`}
      role="progressbar"
      aria-label="Update sources progress"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={progress.determinate ? progress.percent : undefined}
    >
      <div className="progress-bar-fill" style={progress.determinate ? { width: `${progress.percent}%` } : undefined} />
    </div>
  );
}

// uat-batch2 (N11-C): Settings' "Update sources". One button; while an
// update runs it is off and the bar follows boards.checked / boards.total
// (indeterminate while total is still 0). When it ends the line is the
// server's own summary; a partial update says how many boards have never
// been checked, "Run again to continue" and how many boards are left; a
// failed one shows the server's error message.
export default function SourcesUpdatePanel() {
  const { status, unavailable, read } = useSourcesStatus();
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState(null);

  const start = useCallback(
    (force, fullRefresh = false) => {
      setStarting(true);
      setStartError(null);
      startSourcesUpdate({ force, fullRefresh })
        .then(() => read())
        .catch((error) => {
          setStartError(startErrorText(error));
          // 409: one is already running (started from the CLI, or another
          // tab); follow it.
          return read();
        })
        .finally(() => setStarting(false));
    },
    [read],
  );

  const update = status ? status.update : null;
  const index = status ? status.index : null;
  const running = isRunning(status);
  const progress = sourcesProgress(update);
  const result = sourcesResult(update);
  const stuck = looksStuck(update);
  const stored = storedLine(index);
  const lastChecked = index && index.last_checked_at ? relativeTimeLabel(index.last_checked_at) : null;

  return (
    <section className="panel" id="settings-sources" data-role="sources-update" data-status={update ? update.status : "none"}>
      <h2>Sources</h2>
      <p className="muted">
        Update sources checks every company board on your watchlist and stores its postings on this machine. It uses the network and no
        model.
      </p>

      {unavailable && !status && <p className="muted">The sources status could not be loaded from this server.</p>}

      {/* An empty index is said once, by the server's own message below. */}
      {stored && (
        <p className="sources-state" data-role="sources-index">
          {stored}
          {lastChecked ? ` · last updated ${lastChecked}` : ""}.
        </p>
      )}

      {index && index.needs_update && index.message && !running && (
        <div className="callout info" data-role="sources-needs-update">
          {index.message}
        </div>
      )}

      {running && progress && (
        <div data-role="sources-progress">
          <ProgressBar progress={progress} />
          <p className="muted sources-line">{progress.line}</p>
        </div>
      )}

      {!running && result && (
        <div className={`callout ${result.tone === "ok" ? "success" : result.tone === "danger" ? "danger" : "warn"}`} data-role="sources-result">
          <strong>{result.line}</strong>
          {result.detail && <div className="sources-detail">{result.detail}</div>}
          {result.backlog && (
            <div className="sources-detail" data-role="sources-backlog">
              {result.backlog}
            </div>
          )}
          {result.next && <div className="sources-next">{result.next}</div>}
        </div>
      )}

      {startError && (
        <div className="field-error" data-role="sources-start-error">
          {startError}
        </div>
      )}

      <div className="actions" style={{ justifyContent: "flex-start", marginTop: 12 }}>
        <button type="button" className="button" onClick={() => start(false)} disabled={running || starting || !status} data-action="update-sources">
          {running ? "Updating sources…" : starting ? "Starting…" : "Update sources"}
        </button>
        <button type="button" className="button secondary" onClick={() => start(false, true)} disabled={running || starting || !status} data-action="full-refresh-sources" title="Check every company board again, even ones checked recently">
          Full refresh
        </button>
        {stuck && (
          <span className="muted sources-stuck">
            No progress for a while.{" "}
            <button type="button" className="link-button" onClick={() => start(true)} disabled={starting} data-action="update-sources-force">
              Start over
            </button>
          </span>
        )}
      </div>
    </section>
  );
}

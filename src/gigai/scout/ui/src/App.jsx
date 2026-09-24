import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, buildRunRequest, getConfig, getRunResults, getRunStatus, startRun } from "./api.js";
import ConfigPanel from "./components/ConfigPanel.jsx";
import RunConfirmDialog from "./components/RunConfirmDialog.jsx";
import NodeStatusList from "./components/NodeStatusList.jsx";
import ResultsView from "./components/ResultsView.jsx";

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "blocked", "cancelled", "interrupted"]);
const POLL_INTERVAL_MS = 2000;

function useConfig() {
  const [state, setState] = useState({ loading: true, config: null, error: null });

  const reload = useCallback(() => {
    setState({ loading: true, config: null, error: null });
    getConfig()
      .then((config) => setState({ loading: false, config, error: null }))
      .catch((error) => setState({ loading: false, config: null, error: error.message || String(error) }));
  }, []);

  useEffect(reload, [reload]);

  return { ...state, reload };
}

export default function App() {
  const { loading: configLoading, config: configResponse, error: configError, reload: reloadConfig } = useConfig();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [runSubmitting, setRunSubmitting] = useState(false);
  const [runError, setRunError] = useState(null);
  const [runId, setRunId] = useState(null);
  const [runStatus, setRunStatus] = useState(null);
  const [results, setResults] = useState(null);
  const [resultsError, setResultsError] = useState(null);
  const pollTimer = useRef(null);

  const stopPolling = useCallback(() => {
    if (pollTimer.current) {
      clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const pollStatus = useCallback(
    (id) => {
      getRunStatus(id)
        .then((status) => {
          setRunStatus(status);
          if (TERMINAL_STATUSES.has(status.status)) {
            getRunResults(id)
              .then((response) => setResults(response.payload))
              .catch((error) => setResultsError(error.message || String(error)));
          } else {
            pollTimer.current = setTimeout(() => pollStatus(id), POLL_INTERVAL_MS);
          }
        })
        .catch((error) => {
          // A failed status poll must not spin forever pretending to still be running:
          // surface it and stop, rather than silently retrying on a broken connection.
          stopPolling();
          setResultsError(error.message || String(error));
        });
    },
    [stopPolling],
  );

  function openDialog() {
    setRunError(null);
    setDialogOpen(true);
  }

  function closeDialog() {
    setDialogOpen(false);
    setRunError(null);
  }

  async function handleConfirm({ selectionCap, modelTarget }) {
    if (!configResponse) {
      return;
    }
    setRunSubmitting(true);
    setRunError(null);
    try {
      const body = buildRunRequest({
        configDigest: configResponse.config_digest,
        selectionCap,
        modelTarget,
      });
      const response = await startRun(body);
      setDialogOpen(false);
      setRunId(response.run_id);
      setRunStatus({ run_id: response.run_id, status: response.status, node_receipts: response.node_receipts });
      setResults(null);
      setResultsError(null);
      stopPolling();
      if (!TERMINAL_STATUSES.has(response.status)) {
        pollTimer.current = setTimeout(() => pollStatus(response.run_id), POLL_INTERVAL_MS);
      } else {
        getRunResults(response.run_id)
          .then((resultsResponse) => setResults(resultsResponse.payload))
          .catch((error) => setResultsError(error.message || String(error)));
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setRunError(`${error.message} Reloading configuration…`);
        reloadConfig();
      } else {
        setRunError(error.message || String(error));
      }
    } finally {
      setRunSubmitting(false);
    }
  }

  const hasResume = Boolean(configResponse && configResponse.resume_preview);
  const canRun = Boolean(configResponse) && hasResume;

  return (
    <div>
      <header className="app-header">
        <h1>Scout · find jobs</h1>
      </header>

      {configLoading && <p>Loading configuration…</p>}

      {configError && (
        <div className="callout danger">
          Could not load configuration: {configError}{" "}
          <button className="button small secondary" onClick={reloadConfig}>
            Retry
          </button>
        </div>
      )}

      {configResponse && (
        <>
          <ConfigPanel
            config={configResponse.config}
            resumePreview={configResponse.resume_preview}
            resumeMissingHint={configResponse.resume_missing_hint}
          />

          <div className="panel">
            <button className="button" onClick={openDialog} disabled={!canRun}>
              Run workflow
            </button>
            {!hasResume && <p className="muted">Add a resume (see above) to enable a run.</p>}
          </div>
        </>
      )}

      {dialogOpen && configResponse && (
        <RunConfirmDialog
          config={configResponse.config}
          onConfirm={handleConfirm}
          onCancel={closeDialog}
          submitting={runSubmitting}
          error={runError}
        />
      )}

      {runId && runStatus && <NodeStatusList status={runStatus.status} nodeReceipts={runStatus.node_receipts} />}

      {resultsError && <div className="callout danger">Could not load results: {resultsError}</div>}

      {results && <ResultsView payload={results} />}
    </div>
  );
}

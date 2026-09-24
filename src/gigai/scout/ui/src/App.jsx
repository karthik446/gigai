import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  buildRunRequest,
  getConfig,
  getDiscoverLatest,
  getRunProgress,
  getRunResults,
  getRunStatus,
  getSetup,
  putSetup,
  startDiscovery,
  startRun,
} from "./api.js";
import { mergeRows, rowsFromProgress } from "./boardRows.js";
import ConfigPanel from "./components/ConfigPanel.jsx";
import RunConfirmDialog from "./components/RunConfirmDialog.jsx";
import NodeStatusList from "./components/NodeStatusList.jsx";
import ProgressBoard from "./components/ProgressBoard.jsx";
import ResultsView from "./components/ResultsView.jsx";
import SetupInterviewForm from "./components/SetupInterviewForm.jsx";
import DiscoverPanel from "./components/DiscoverPanel.jsx";

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "blocked", "cancelled", "interrupted"]);
const POLL_INTERVAL_MS = 2000;
// B4: the progress poll is a separate, faster cadence than the run-status
// poll -- "load ui sooner… we load as we get" means cards should update at
// roughly the rate work actually happens, not at the coarser status cadence.
const PROGRESS_POLL_INTERVAL_MS = 1500;
// S2-B: the Discover panel's own poll, while a discovery session is running.
const DISCOVER_POLL_INTERVAL_MS = 4000;

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

// S2-B: GET /api/setup either returns saved prefs (200) or a 404
// prefs_missing carrying a pre-fill derived from find-jobs.json (see
// present_api.py's _handle_get_setup). `prefsMissing` distinguishes "first
// run, show the interview before anything else" from "prefs saved,
// interview only reachable via the Preferences link".
function useSetup() {
  const [state, setState] = useState({ loading: true, prefs: null, prefill: null, prefsMissing: false, error: null });

  const reload = useCallback(() => {
    setState({ loading: true, prefs: null, prefill: null, prefsMissing: false, error: null });
    getSetup()
      .then((response) => setState({ loading: false, prefs: response.prefs, prefill: null, prefsMissing: false, error: null }))
      .catch((error) => {
        if (error instanceof ApiError && error.status === 404 && error.code === "prefs_missing") {
          setState({ loading: false, prefs: null, prefill: error.prefill || {}, prefsMissing: true, error: null });
        } else {
          setState({ loading: false, prefs: null, prefill: null, prefsMissing: false, error: error.message || String(error) });
        }
      });
  }, []);

  useEffect(reload, [reload]);

  return { ...state, reload };
}

export default function App() {
  const { loading: configLoading, config: configResponse, error: configError, reload: reloadConfig } = useConfig();
  const setupState = useSetup();

  const [editingSetup, setEditingSetup] = useState(false);
  const [setupSaving, setSetupSaving] = useState(false);
  const [setupError, setSetupError] = useState(null);
  const [setupFieldErrors, setSetupFieldErrors] = useState(null);

  const [discoverLatest, setDiscoverLatest] = useState(null);
  const [discoverLoading, setDiscoverLoading] = useState(false);
  const [discoverStarting, setDiscoverStarting] = useState(false);
  const [discoverError, setDiscoverError] = useState(null);
  const discoverPollTimer = useRef(null);

  const stopDiscoverPolling = useCallback(() => {
    if (discoverPollTimer.current) {
      clearTimeout(discoverPollTimer.current);
      discoverPollTimer.current = null;
    }
  }, []);

  const pollDiscoverLatest = useCallback(
    (delayMs) => {
      discoverPollTimer.current = setTimeout(() => {
        getDiscoverLatest()
          .then((latest) => {
            setDiscoverLatest(latest);
            if (latest.running) {
              pollDiscoverLatest(DISCOVER_POLL_INTERVAL_MS);
            }
          })
          .catch(() => {
            // Best-effort, same reasoning as the run-progress poll: a
            // transient failure here should back off and retry, not stop
            // reflecting a session that may still be running.
            pollDiscoverLatest(DISCOVER_POLL_INTERVAL_MS);
          });
      }, delayMs);
    },
    [],
  );

  // Once prefs exist (setup is done), load the Discover panel's last-run
  // state and keep polling if a session is already running (e.g. the UI
  // was reloaded mid-session).
  useEffect(() => {
    if (setupState.prefsMissing || setupState.loading || !setupState.prefs) {
      return undefined;
    }
    setDiscoverLoading(true);
    getDiscoverLatest()
      .then((latest) => {
        setDiscoverLatest(latest);
        setDiscoverLoading(false);
        if (latest.running) {
          pollDiscoverLatest(DISCOVER_POLL_INTERVAL_MS);
        }
      })
      .catch((error) => {
        setDiscoverLoading(false);
        setDiscoverError(error.message || String(error));
      });
    return stopDiscoverPolling;
  }, [setupState.prefsMissing, setupState.loading, setupState.prefs, pollDiscoverLatest, stopDiscoverPolling]);

  async function handleSaveSetup(fields) {
    setSetupSaving(true);
    setSetupError(null);
    setSetupFieldErrors(null);
    try {
      await putSetup(fields);
      setEditingSetup(false);
      setupState.reload();
    } catch (error) {
      if (error instanceof ApiError && error.status === 400) {
        setSetupFieldErrors(error.field_errors || null);
        setSetupError(error.field_errors ? null : error.message || String(error));
      } else {
        setSetupError(error.message || String(error));
      }
    } finally {
      setSetupSaving(false);
    }
  }

  async function handleStartDiscovery() {
    setDiscoverStarting(true);
    setDiscoverError(null);
    try {
      await startDiscovery();
      stopDiscoverPolling();
      pollDiscoverLatest(0);
    } catch (error) {
      setDiscoverError(error.message || String(error));
    } finally {
      setDiscoverStarting(false);
    }
  }

  const [dialogOpen, setDialogOpen] = useState(false);
  const [runSubmitting, setRunSubmitting] = useState(false);
  const [runError, setRunError] = useState(null);
  const [runId, setRunId] = useState(null);
  const [runStatus, setRunStatus] = useState(null);
  const [progress, setProgress] = useState(null);
  const [boardRows, setBoardRows] = useState([]);
  const [results, setResults] = useState(null);
  const [resultsError, setResultsError] = useState(null);
  const pollTimer = useRef(null);
  const progressPollTimer = useRef(null);
  const boardRowsRef = useRef([]);

  const stopPolling = useCallback(() => {
    if (pollTimer.current) {
      clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  const stopProgressPolling = useCallback(() => {
    if (progressPollTimer.current) {
      clearTimeout(progressPollTimer.current);
      progressPollTimer.current = null;
    }
  }, []);

  useEffect(() => () => {
    stopPolling();
    stopProgressPolling();
  }, [stopPolling, stopProgressPolling]);

  // B4: progressive cards -- poll /progress independently of /runs/{id}
  // status, so a card appears the moment its posting is acquired and fills
  // in as its assessment finishes, instead of waiting for the whole run
  // (operator: "we load as we get"). Stops once the run reaches a terminal
  // status (the caller of startProgressPolling controls that via `active`).
  const pollProgress = useCallback((id) => {
    getRunProgress(id)
      .then((snapshot) => {
        setProgress(snapshot);
        const nextRows = mergeRows(boardRowsRef.current, rowsFromProgress(snapshot));
        boardRowsRef.current = nextRows;
        setBoardRows(nextRows);
        progressPollTimer.current = setTimeout(() => pollProgress(id), PROGRESS_POLL_INTERVAL_MS);
      })
      .catch(() => {
        // A failed progress poll is never fatal to the run itself (it's a
        // best-effort, non-authoritative view) -- back off and retry rather
        // than surfacing an error the way a failed status/results poll does.
        progressPollTimer.current = setTimeout(() => pollProgress(id), PROGRESS_POLL_INTERVAL_MS);
      });
  }, []);

  const pollStatus = useCallback(
    (id) => {
      getRunStatus(id)
        .then((status) => {
          setRunStatus(status);
          if (TERMINAL_STATUSES.has(status.status)) {
            stopProgressPolling();
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
          stopProgressPolling();
          setResultsError(error.message || String(error));
        });
    },
    [stopPolling, stopProgressPolling],
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
      setProgress(null);
      boardRowsRef.current = [];
      setBoardRows([]);
      stopPolling();
      stopProgressPolling();
      if (!TERMINAL_STATUSES.has(response.status)) {
        pollTimer.current = setTimeout(() => pollStatus(response.run_id), POLL_INTERVAL_MS);
        progressPollTimer.current = setTimeout(() => pollProgress(response.run_id), 0);
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
  const runActive = Boolean(runId && runStatus && !TERMINAL_STATUSES.has(runStatus.status));

  // S2-B: `gigai scout run` opens this UI; if no discovery prefs exist yet,
  // the interview shows first, ahead of the rest of the app (CHANGE #2).
  const showFirstRunInterview = !setupState.loading && setupState.prefsMissing && !setupState.error;

  return (
    <div>
      <header className="app-header">
        <h1>Scout · find jobs</h1>
      </header>

      {setupState.loading && <p>Loading setup…</p>}

      {setupState.error && (
        <div className="callout danger">
          Could not load setup: {setupState.error}{" "}
          <button className="button small secondary" onClick={setupState.reload}>
            Retry
          </button>
        </div>
      )}

      {showFirstRunInterview && (
        <SetupInterviewForm
          initialPrefs={setupState.prefill || {}}
          onSave={handleSaveSetup}
          onCancel={null}
          saving={setupSaving}
          error={setupError}
          fieldErrors={setupFieldErrors}
          isFirstRun
        />
      )}

      {editingSetup && setupState.prefs && (
        <SetupInterviewForm
          initialPrefs={setupState.prefs}
          onSave={handleSaveSetup}
          onCancel={() => {
            setEditingSetup(false);
            setSetupError(null);
            setSetupFieldErrors(null);
          }}
          saving={setupSaving}
          error={setupError}
          fieldErrors={setupFieldErrors}
          isFirstRun={false}
        />
      )}

      {/* The rest of the app (config, run, discover) is gated behind the
          first-run interview -- CHANGE #2's "asked ONCE... shows the
          interview first". Once prefs exist it's never blocking again;
          editing happens in-place above via the Preferences link. */}
      {!showFirstRunInterview && !editingSetup && (
        <>
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
                {setupState.prefs && (
                  <p className="muted">
                    <button className="button small secondary" onClick={() => setEditingSetup(true)}>
                      Preferences
                    </button>
                  </p>
                )}
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

          {runId && runStatus && (
            <NodeStatusList
              status={runStatus.status}
              nodeReceipts={runStatus.node_receipts}
              progressSteps={progress?.steps}
            />
          )}

          {resultsError && <div className="callout danger">Could not load results: {resultsError}</div>}

          {/* B4: while the run is active, cards render straight from the
              progressive /progress poll; once results land, ResultsView takes
              over (same PostingsBoard underneath, so this is not a layout
              swap -- see ResultsView.jsx). */}
          {!results && runActive && <ProgressBoard rows={boardRows} progress={progress} />}

          {results && <ResultsView payload={results} />}

          {setupState.prefs && !discoverLoading && (
            <DiscoverPanel
              latest={discoverLatest}
              running={Boolean(discoverLatest && discoverLatest.running)}
              cadenceDays={setupState.prefs.cadence_days || 7}
              onStart={handleStartDiscovery}
              starting={discoverStarting}
              error={discoverError}
            />
          )}
        </>
      )}
    </div>
  );
}

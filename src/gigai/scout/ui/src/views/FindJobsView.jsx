import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  buildRunRequest,
  getAssessments,
  getRunProgress,
  getRunResults,
  getRunStatus,
  postRank,
  startRun,
} from "../api.js";
import { mergeRows, rowsFromProgress, rowsFromResults } from "../boardRows.js";
import ConfigPanel from "../components/ConfigPanel.jsx";
import RunConfirmDialog from "../components/RunConfirmDialog.jsx";
import NodeStatusList from "../components/NodeStatusList.jsx";
import JobsGrid from "../components/JobsGrid.jsx";
import JobPage from "./JobPage.jsx";
import { useRuns } from "../hooks.js";
import { relativeTimeLabel } from "../display.js";
import { buildJobs } from "../jobModel.js";
import { useHashRoute } from "../routing.js";

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "blocked", "cancelled", "interrupted"]);
const POLL_INTERVAL_MS = 2000;
const PROGRESS_POLL_INTERVAL_MS = 1500;

// P9/P9c (F3): Find jobs, for the selected profile.
//
// Q4a (v0.1.9): the postings list is now the card grid (JobsGrid) and each
// card opens a job page (JobPage) by hash route (#/jobs/<normalized_url>,
// routing.js), so the browser back button returns to the grid with the
// run, filters and cards intact (this view stays mounted for both).
// The cards merge three existing reads (jobModel.buildJobs): the run's
// rows + assessments, the Jev rank scores, and the quick-assess store
// (GET /api/assessments?profile_id=…) where every job-page re-assessment
// lands -- a card's verdict chip is always the latest of the two.
//
// When this tab opens with no run loaded (fresh load, back from another
// tab, a deep link to a job page), the newest succeeded run for the
// profile is loaded automatically (GET /api/runs, newest first) -- the
// same read the past-run picker does, so a job page survives a reload.
//
// P9c: past-run picker wired to GET /api/runs?profile_id=<this profile>
// -- selecting an entry loads that run's real, sealed
// GET /api/runs/{id}/results (never a stub).
//
// Still DROPPED from the mockup (no backing API): "New since last run"
// (no field computes it). Phase 2 fields (work_mode / pay / H-1B count,
// tailored resume) render only once their APIs carry them -- see
// jobModel.js / JobPage.jsx.
function PastRunPicker({ runs, onSelect, disabled }) {
  if (runs.length === 0) {
    return null;
  }
  return (
    <div className="form-group" style={{ maxWidth: 360 }}>
      <label className="form-label" htmlFor="past-run-picker">
        View a past run
      </label>
      <select
        id="past-run-picker"
        onChange={(event) => {
          if (event.target.value) {
            onSelect(event.target.value);
          }
          event.target.value = "";
        }}
        disabled={disabled}
        defaultValue=""
      >
        <option value="" disabled>
          Select a past run…
        </option>
        {runs.map((run) => (
          <option key={run.run_id} value={run.run_id}>
            {relativeTimeLabel(run.created_at)} · {run.counts.found} found / {run.counts.assessed} assessed (
            {run.status})
          </option>
        ))}
      </select>
    </div>
  );
}

export default function FindJobsView({ profile, config, reloadConfig }) {
  const route = useHashRoute();
  const profileId = profile ? profile.profile_id : null;
  const runsState = useRuns(profileId);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [runSubmitting, setRunSubmitting] = useState(false);
  const [runError, setRunError] = useState(null);
  const [runId, setRunId] = useState(null);
  const [runStatus, setRunStatus] = useState(null);
  const [progress, setProgress] = useState(null);
  const [boardRows, setBoardRows] = useState([]);
  const [results, setResults] = useState(null);
  const [resultsError, setResultsError] = useState(null);
  const [resultsLoading, setResultsLoading] = useState(false);
  const [rankScores, setRankScores] = useState([]);
  const [quickItems, setQuickItems] = useState([]);

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

  useEffect(
    () => () => {
      stopPolling();
      stopProgressPolling();
    },
    [stopPolling, stopProgressPolling],
  );

  // Reset run state when the selected profile changes -- a run belongs to
  // whichever profile was selected when it started; switching profiles
  // should not show a stale run's cards under a different profile's label.
  useEffect(() => {
    stopPolling();
    stopProgressPolling();
    setRunId(null);
    setRunStatus(null);
    setProgress(null);
    boardRowsRef.current = [];
    setBoardRows([]);
    setResults(null);
    setResultsError(null);
    setResultsLoading(false);
    setRankScores([]);
    setQuickItems([]);
  }, [profileId, stopPolling, stopProgressPolling]);

  // Q4a: the quick-assess store for this profile (job-page re-assessments,
  // "Assess this posting", CLI/quick-assess of the same URL). Loaded with
  // every results load; a job-page mutation merges its response in place.
  const loadQuickItems = useCallback(() => {
    if (!profileId) {
      return;
    }
    getAssessments({ profileId })
      .then((response) => setQuickItems(response.items || []))
      .catch(() => setQuickItems([]));
  }, [profileId]);

  const handleQuickUpdated = useCallback((item) => {
    if (!item || !item.job) {
      return;
    }
    setQuickItems((prev) => {
      const rest = prev.filter((existing) => existing.job.job_identity !== item.job.job_identity);
      return [item, ...rest];
    });
  }, []);

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
        progressPollTimer.current = setTimeout(() => pollProgress(id), PROGRESS_POLL_INTERVAL_MS);
      });
  }, []);

  function loadRankScores(id) {
    postRank(id, {})
      .then((response) => setRankScores(response.scores))
      .catch(() => setRankScores([]));
  }

  const loadResults = useCallback(
    (id) => {
      setResultsLoading(true);
      return getRunResults(id)
        .then((response) => {
          setResults(response.payload);
          setResultsLoading(false);
          loadRankScores(id);
          loadQuickItems();
        })
        .catch((error) => {
          setResultsLoading(false);
          setResultsError(error.message || String(error));
        });
    },
    [loadQuickItems],
  );

  const pollStatus = useCallback(
    (id) => {
      getRunStatus(id)
        .then((status) => {
          setRunStatus(status);
          if (TERMINAL_STATUSES.has(status.status)) {
            stopProgressPolling();
            loadResults(id);
            // The just-finished run's created_at feeds the verdict history.
            runsState.reload();
          } else {
            pollTimer.current = setTimeout(() => pollStatus(id), POLL_INTERVAL_MS);
          }
        })
        .catch((error) => {
          stopPolling();
          stopProgressPolling();
          setResultsError(error.message || String(error));
        });
    },
    [stopPolling, stopProgressPolling, loadResults, runsState.reload],
  );

  function openDialog() {
    setRunError(null);
    setDialogOpen(true);
  }
  function closeDialog() {
    setDialogOpen(false);
    setRunError(null);
  }

  // P9c: load a PAST run's real, sealed results -- never starts a new run.
  // Stops any live poll first, so an in-progress run's cards can't keep
  // arriving and overwrite what the operator just chose to view.
  const viewPastRun = useCallback(
    (pastRunId) => {
      stopPolling();
      stopProgressPolling();
      setRunError(null);
      setResultsError(null);
      setRunId(pastRunId);
      boardRowsRef.current = [];
      setBoardRows([]);
      setProgress(null);
      setRankScores([]);
      setResultsLoading(true);
      getRunStatus(pastRunId)
        .then((status) => {
          setRunStatus(status);
          return loadResults(pastRunId);
        })
        .catch((error) => {
          setResultsLoading(false);
          setResultsError(error.message || String(error));
        });
    },
    [stopPolling, stopProgressPolling, loadResults],
  );

  // Q4a: nothing loaded yet -> show the newest succeeded run for this
  // profile (the grid is empty otherwise, and a job page deep link would
  // have nothing to find).
  useEffect(() => {
    if (runId || runsState.loading || runsState.error) {
      return;
    }
    const newest = runsState.runs.find((run) => run.status === "succeeded");
    if (newest) {
      viewPastRun(newest.run_id);
    }
  }, [runId, runsState.loading, runsState.error, runsState.runs, viewPastRun]);

  async function handleConfirm({ selectionCap, modelTarget }) {
    if (!config) {
      return;
    }
    setRunSubmitting(true);
    setRunError(null);
    try {
      const body = buildRunRequest({ configDigest: config.config_digest, selectionCap, modelTarget });
      const response = await startRun(body);
      setDialogOpen(false);
      setRunId(response.run_id);
      setRunStatus({ run_id: response.run_id, status: response.status, node_receipts: response.node_receipts });
      setResults(null);
      setResultsError(null);
      setProgress(null);
      setRankScores([]);
      boardRowsRef.current = [];
      setBoardRows([]);
      stopPolling();
      stopProgressPolling();
      if (!TERMINAL_STATUSES.has(response.status)) {
        pollTimer.current = setTimeout(() => pollStatus(response.run_id), POLL_INTERVAL_MS);
        progressPollTimer.current = setTimeout(() => pollProgress(response.run_id), 0);
      } else {
        loadResults(response.run_id);
        runsState.reload();
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

  const hasResume = Boolean(config && config.resume_preview);
  const canRun = Boolean(config) && hasResume;
  const runActive = Boolean(runId && runStatus && !TERMINAL_STATUSES.has(runStatus.status));
  const rows = results ? rowsFromResults(results) : boardRows;
  const visaRequired = Boolean(config && config.config && config.config.visa_sponsorship_required);
  const currentRun = runsState.runs.find((run) => run.run_id === runId) || null;
  const runCreatedAt = currentRun ? currentRun.created_at : null;

  const jobs = useMemo(
    () => buildJobs({ rows, rankScores, quickItems, runCreatedAt }),
    [rows, rankScores, quickItems, runCreatedAt],
  );

  if (!profile) {
    return (
      <div className="panel">
        <p className="muted">Select a profile first.</p>
      </div>
    );
  }

  if (route.view === "job") {
    const job = jobs.find((candidate) => candidate.id === route.jobId) || null;
    return (
      <JobPage
        job={job}
        jobId={route.jobId}
        profileId={profile.profile_id}
        visaRequired={visaRequired}
        loading={resultsLoading || (runsState.loading && !runId)}
        onQuickUpdated={handleQuickUpdated}
      />
    );
  }

  const runLabel = currentRun ? `run ${relativeTimeLabel(currentRun.created_at)}` : runActive ? "run in progress" : "";

  return (
    <div>
      <section className="panel">
        <h2>
          Run find jobs <span className="muted">{profile.label}</span>
        </h2>
        <p className="privacy-note">Your resume is sent to Jev to rank postings.</p>

        {config && (
          <ConfigPanel
            config={config.config}
            resumePreview={config.resume_preview}
            resumeLabel={config.resume_label}
            resumeCreatedAt={config.resume_created_at}
            resumeMissingHint={config.resume_missing_hint}
          />
        )}

        <div className="actions" style={{ justifyContent: "flex-start", marginTop: 12 }}>
          <button className="button" onClick={openDialog} disabled={!canRun}>
            Run find jobs: {profile.label}
          </button>
        </div>
        {!hasResume && <p className="muted">Add a resume (see above) to enable a run.</p>}

        <div style={{ marginTop: 12 }}>
          <PastRunPicker runs={runsState.runs} onSelect={viewPastRun} disabled={runActive} />
        </div>
      </section>

      {dialogOpen && config && (
        <RunConfirmDialog config={config.config} onConfirm={handleConfirm} onCancel={closeDialog} submitting={runSubmitting} error={runError} />
      )}

      {runId && runStatus && <NodeStatusList status={runStatus.status} nodeReceipts={runStatus.node_receipts} progressSteps={progress?.steps} />}

      {resultsError && <div className="callout danger">Could not load results: {resultsError}</div>}

      {(results || runActive) && (
        <JobsGrid
          jobs={jobs}
          visaRequired={visaRequired}
          runLabel={runLabel}
          emptyMessage={runActive ? "Waiting for the first postings…" : "This run found no postings."}
        />
      )}
      {!results && !runActive && !resultsLoading && !runsState.loading && runsState.runs.length === 0 && (
        <p className="muted">No find-jobs run yet for this profile. Run one above to see its postings here.</p>
      )}
    </div>
  );
}

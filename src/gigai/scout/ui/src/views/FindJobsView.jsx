import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, buildRunRequest, getAssessments, getRunProgress, getRunResults, getRunStatus, postRank, startRun } from "../api.js";
import { mergeRows, rowsFromProgress, rowsFromResults } from "../boardRows.js";
import RunConfirmDialog from "../components/RunConfirmDialog.jsx";
import NodeStatusList from "../components/NodeStatusList.jsx";
import JobsGrid from "../components/JobsGrid.jsx";
import JobsSummaryStrip from "../components/JobsSummaryStrip.jsx";
import Breadcrumb from "../components/Breadcrumb.jsx";
import JobPage from "./JobPage.jsx";
import { relativeTimeLabel } from "../display.js";
import { buildJobs, dateTimeLabel } from "../jobModel.js";
import { ASSESS_HASH, RUNS_HASH, SETTINGS_HASH, runHash } from "../routing.js";

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "blocked", "cancelled", "interrupted"]);
const POLL_INTERVAL_MS = 2000;
const PROGRESS_POLL_INTERVAL_MS = 1500;

// P9/P9c (F3) → Q4a → Q4a-nav: the postings host, for the selected profile.
//
// Q4a (v0.1.9): the postings list is the card grid (JobsGrid) and each card
// opens a job page (JobPage) by hash route (#/jobs/<normalized_url>,
// routing.js). The cards merge three existing reads (jobModel.buildJobs):
// the run's rows + assessments, the Jev rank scores, and the quick-assess
// store (GET /api/assessments?profile_id=…) where every job-page
// re-assessment and every "+ Assess a job" lands -- a card's verdict chip
// is always the latest of the two, and an on-demand assessment no run
// carries is a card of its own.
//
// Q4a-nav: this view stays MOUNTED for the whole app (App.jsx renders it
// on every route; it draws nothing on the routes it does not own), so a
// live run keeps polling while the operator reads Questions or Settings,
// and the run/filters/cards survive every navigation. It owns three routes:
//
//   #/jobs        the grid: the Dashboard's summary strip, "Run find jobs"
//                 (the consent dialog), "+ Assess a job", the past-run picker
//   #/jobs/<id>   one posting's JobPage, from the same job model
//   #/runs/<id>   one run's page: "Runs › <run>", status + node receipts,
//                 counts, and that run's grid (loaded via the same
//                 GET /api/runs/{id}/results read as the picker)
//
// When nothing is loaded yet (fresh load, a deep link), the newest
// succeeded run for the profile is loaded automatically (GET /api/runs,
// newest first) -- so a job page survives a reload. A run page loads its
// own run instead.
//
// Still DROPPED from the mockup (no backing API): "New since last run".
// Phase 2 fields (work_mode / pay / H-1B count, tailored resume) render
// only once their APIs carry them -- see jobModel.js / JobPage.jsx.
function PastRunPicker({ runs, currentRunId, onSelect, disabled }) {
  if (runs.length === 0) {
    return null;
  }
  return (
    <label className="past-run-picker">
      <span className="chip-group-label">Showing</span>
      <select value={currentRunId || ""} onChange={(event) => event.target.value && onSelect(event.target.value)} disabled={disabled}>
        {!currentRunId && (
          <option value="" disabled>
            Select a run…
          </option>
        )}
        {runs.map((run) => (
          <option key={run.run_id} value={run.run_id}>
            run {relativeTimeLabel(run.created_at)} · {run.counts.found} found / {run.counts.assessed} assessed ({run.status})
          </option>
        ))}
      </select>
    </label>
  );
}

export default function FindJobsView({
  route,
  profile,
  config,
  reloadConfig,
  runsState,
  applicationsState,
  questions,
  externalQuickItem,
}) {
  const profileId = profile ? profile.profile_id : null;
  const ownsRoute = route.view === "jobs" || route.view === "job" || route.view === "run";

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
  // "Assess this posting", "+ Assess a job", CLI). Loaded when the profile
  // changes and with every results load; a mutation merges its response in
  // place (handleQuickUpdated) so nothing waits for a re-read.
  const loadQuickItems = useCallback(() => {
    if (!profileId) {
      return;
    }
    getAssessments({ profileId })
      .then((response) => setQuickItems(response.items || []))
      .catch(() => setQuickItems([]));
  }, [profileId]);

  useEffect(loadQuickItems, [loadQuickItems]);

  // Q4a-nav: this view stays mounted while the operator answers on
  // Questions (or assesses elsewhere), so re-read the store each time one
  // of its own routes comes back into view -- a cheap GET, and the only way
  // a card/job page reflects an answer given on another page.
  useEffect(() => {
    if (ownsRoute) {
      loadQuickItems();
    }
  }, [route.view, ownsRoute, loadQuickItems]);

  const questionsReload = questions ? questions.reload : null;
  const handleQuickUpdated = useCallback(
    (item) => {
      if (!item || !item.job) {
        return;
      }
      setQuickItems((prev) => {
        const rest = prev.filter((existing) => existing.job.job_identity !== item.job.job_identity);
        return [item, ...rest];
      });
      // The Questions badge counts open questions in this same store.
      if (questionsReload) {
        questionsReload();
      }
    },
    [questionsReload],
  );

  // Q4a-nav: an AssessResponse from "+ Assess a job" (App.jsx hands it
  // over, then opens #/jobs/<job_identity>).
  useEffect(() => {
    if (externalQuickItem) {
      handleQuickUpdated(externalQuickItem);
    }
  }, [externalQuickItem, handleQuickUpdated]);

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

  const runsReload = runsState.reload;
  const pollStatus = useCallback(
    (id) => {
      getRunStatus(id)
        .then((status) => {
          setRunStatus(status);
          if (TERMINAL_STATUSES.has(status.status)) {
            stopProgressPolling();
            loadResults(id);
            // The just-finished run's created_at feeds the verdict history
            // and the summary strip.
            runsReload();
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
    [stopPolling, stopProgressPolling, loadResults, runsReload],
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
      setResults(null);
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

  const runActive = Boolean(runId && runStatus && !TERMINAL_STATUSES.has(runStatus.status));
  const routeRunId = route.view === "run" ? route.params.runId : null;

  // Q4a-nav: a run page shows ITS run -- load it unless it is already the
  // loaded one (a live run included: never interrupt its polling).
  useEffect(() => {
    if (!profileId || !routeRunId || routeRunId === runId) {
      return;
    }
    viewPastRun(routeRunId);
  }, [profileId, routeRunId, runId, viewPastRun]);

  // Q4a: nothing loaded yet -> show the newest succeeded run for this
  // profile (the grid is empty otherwise, and a job page deep link would
  // have nothing to find).
  useEffect(() => {
    if (runId || routeRunId || runsState.loading || runsState.error) {
      return;
    }
    const newest = runsState.runs.find((run) => run.status === "succeeded");
    if (newest) {
      viewPastRun(newest.run_id);
    }
  }, [runId, routeRunId, runsState.loading, runsState.error, runsState.runs, viewPastRun]);

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
        runsReload();
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
  const rows = results ? rowsFromResults(results) : boardRows;
  const visaRequired = Boolean(config && config.config && config.config.visa_sponsorship_required);
  const currentRun = runsState.runs.find((run) => run.run_id === runId) || null;
  const runCreatedAt = currentRun ? currentRun.created_at : null;
  const newestRun = runsState.runs[0] || null;

  const jobs = useMemo(() => buildJobs({ rows, rankScores, quickItems, runCreatedAt }), [rows, rankScores, quickItems, runCreatedAt]);
  const onDemandCount = useMemo(() => jobs.filter((job) => job.status === "on_demand").length, [jobs]);

  if (!ownsRoute) {
    return null;
  }

  if (!profile) {
    return (
      <div className="panel">
        <p className="muted">
          Select a profile first (or create one in <a href={SETTINGS_HASH}>Settings</a>).
        </p>
      </div>
    );
  }

  if (route.view === "job") {
    const jobId = route.params.jobId;
    const job = jobs.find((candidate) => candidate.id === jobId) || null;
    return (
      <JobPage
        job={job}
        jobId={jobId}
        profileId={profile.profile_id}
        profileLabel={profile.label}
        visaRequired={visaRequired}
        loading={resultsLoading || (runsState.loading && !runId)}
        onQuickUpdated={handleQuickUpdated}
        onApplicationsChanged={applicationsState.reload}
      />
    );
  }

  const runLabel = currentRun ? `run ${relativeTimeLabel(currentRun.created_at)}` : runActive ? "run in progress" : "";
  const gridLabel = onDemandCount > 0 ? `${runLabel}${runLabel ? " · " : ""}${onDemandCount} assessed on demand` : runLabel;
  const grid = (
    <>
      {resultsError && <div className="callout danger">Could not load results: {resultsError}</div>}
      {(results || runActive || onDemandCount > 0) && (
        <JobsGrid
          jobs={jobs}
          visaRequired={visaRequired}
          runLabel={gridLabel}
          emptyMessage={runActive ? "Waiting for the first postings…" : "This run found no postings."}
        />
      )}
    </>
  );

  if (route.view === "run") {
    const shownRun = currentRun || (runId === routeRunId && runStatus ? { run_id: runId, created_at: null, counts: null, status: runStatus.status } : null);
    const crumb = shownRun && shownRun.created_at ? `run ${relativeTimeLabel(shownRun.created_at)}` : `run ${routeRunId}`;
    return (
      <div>
        <Breadcrumb crumbs={[{ label: "Runs", href: RUNS_HASH }, { label: crumb }]} />
        <section className="panel">
          <h2>
            Run <span className="muted">{shownRun && shownRun.created_at ? dateTimeLabel(shownRun.created_at) : routeRunId}</span>
          </h2>
          <p className="muted small">
            <code>{routeRunId}</code>
            {profile && ` · ${profile.label}`}
          </p>
          {shownRun && shownRun.counts && (
            <div className="summary-strip compact">
              <div className="stat-tile">
                <div className="stat-label">Found</div>
                <div className="stat-value">{shownRun.counts.found}</div>
              </div>
              <div className="stat-tile">
                <div className="stat-label">New</div>
                <div className="stat-value">{shownRun.counts.new}</div>
              </div>
              <div className="stat-tile">
                <div className="stat-label">Assessed</div>
                <div className="stat-value">{shownRun.counts.assessed}</div>
              </div>
              <div className="stat-tile">
                <div className="stat-label">Matched</div>
                <div className="stat-value">{shownRun.counts.matched}</div>
              </div>
            </div>
          )}
          {resultsLoading && <p className="muted">Loading run…</p>}
        </section>
        {runId === routeRunId && runStatus && (
          <NodeStatusList status={runStatus.status} nodeReceipts={runStatus.node_receipts} progressSteps={progress?.steps} rotation={progress?.rotation} boards={progress?.boards} />
        )}
        {runId === routeRunId && grid}
      </div>
    );
  }

  return (
    <div>
      <JobsSummaryStrip
        lastRun={newestRun}
        runsLoading={runsState.loading}
        questionsCount={questions ? questions.count : null}
        applications={applicationsState.applications}
        applicationsLoading={applicationsState.loading}
      />

      <section className="panel jobs-header">
        <div className="jobs-header-row">
          <h2>
            Jobs <span className="muted">{profile.label}</span>
          </h2>
          <div className="jobs-header-actions">
            <a className="button secondary" href={ASSESS_HASH} data-action="assess">
              + Assess a job
            </a>
            <button className="button" onClick={openDialog} disabled={!canRun || runActive} data-action="run">
              {runActive ? "Run in progress…" : "Run find jobs"}
            </button>
          </div>
        </div>
        <div className="jobs-header-meta">
          <p className="privacy-note" style={{ margin: 0 }}>
            Your resume is sent to Jev to rank postings.
          </p>
          {!hasResume && config && (
            <p className="muted" style={{ margin: 0 }}>
              Add a resume in <a href={SETTINGS_HASH}>Settings</a> to enable a run.
            </p>
          )}
          <PastRunPicker runs={runsState.runs} currentRunId={runId} onSelect={viewPastRun} disabled={runActive} />
        </div>
      </section>

      {dialogOpen && config && (
        <RunConfirmDialog config={config.config} onConfirm={handleConfirm} onCancel={closeDialog} submitting={runSubmitting} error={runError} />
      )}

      {runId && runStatus && (runActive || runStatus.status !== "succeeded") && (
        <NodeStatusList status={runStatus.status} nodeReceipts={runStatus.node_receipts} progressSteps={progress?.steps} rotation={progress?.rotation} boards={progress?.boards} />
      )}

      {grid}

      {!results && !runActive && !resultsLoading && !runsState.loading && runsState.runs.length === 0 && onDemandCount === 0 && (
        <div className="panel">
          <p className="muted" style={{ margin: 0 }}>
            No find-jobs run yet for this profile. Run one above to see its postings here, or assess a single posting with
            "+ Assess a job".
          </p>
        </div>
      )}
      {!runsState.loading && runId && newestRun && runId !== newestRun.run_id && !runActive && (
        <p className="muted small">
          Showing an older run. <a href={runHash(runId)}>Open its run page</a> or{" "}
          <button type="button" className="link-button" onClick={() => viewPastRun(newestRun.run_id)}>
            back to the latest run
          </button>
          .
        </p>
      )}
    </div>
  );
}

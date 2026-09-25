import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  buildRunRequest,
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
import FindJobsPostingsBoard from "../components/FindJobsPostingsBoard.jsx";

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "blocked", "cancelled", "interrupted"]);
const POLL_INTERVAL_MS = 2000;
const PROGRESS_POLL_INTERVAL_MS = 1500;

// P9/F3: Find jobs, for the selected profile.
//
// DROPPED from the mockup (no backing API):
//  - Past-run picker: no route lists a profile's (or any) past run_ids;
//    GET /api/runs/{run_id} needs one you already hold. This view only
//    ever shows the run it just started, in this session.
//  - "New since last run" filter: no field computes it anywhere.
//  - "Stack overlap with profile" on cards: no field computes it; Jev's
//    `reasons`/`mismatch_flags` (category ids, never prose) are shown
//    instead, since those DO exist (RankScore, jev_contracts.py).
//
// KEPT, on real data: run control + live step status (reused from App.jsx's
// existing polling logic), posting cards with verdict + rank score/flags +
// sponsorship + not-assessed reason (PostingCard.jsx, extended), the Jev
// "no + strong mismatch" hidden-by-default filter (RankScore.hidden_by_default),
// and a "Prep for interview" command panel (gigai scout prep, S27).
export default function FindJobsView({ profile, config, reloadConfig }) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [runSubmitting, setRunSubmitting] = useState(false);
  const [runError, setRunError] = useState(null);
  const [runId, setRunId] = useState(null);
  const [runStatus, setRunStatus] = useState(null);
  const [progress, setProgress] = useState(null);
  const [boardRows, setBoardRows] = useState([]);
  const [results, setResults] = useState(null);
  const [resultsError, setResultsError] = useState(null);
  const [rankScores, setRankScores] = useState([]);

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
    setRankScores([]);
  }, [profile?.profile_id, stopPolling, stopProgressPolling]);

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

  const pollStatus = useCallback(
    (id) => {
      getRunStatus(id)
        .then((status) => {
          setRunStatus(status);
          if (TERMINAL_STATUSES.has(status.status)) {
            stopProgressPolling();
            getRunResults(id)
              .then((response) => {
                setResults(response.payload);
                loadRankScores(id);
              })
              .catch((error) => setResultsError(error.message || String(error)));
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
        getRunResults(response.run_id)
          .then((resultsResponse) => {
            setResults(resultsResponse.payload);
            loadRankScores(response.run_id);
          })
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

  const hasResume = Boolean(config && config.resume_preview);
  const canRun = Boolean(config) && hasResume;
  const runActive = Boolean(runId && runStatus && !TERMINAL_STATUSES.has(runStatus.status));
  const rows = results ? rowsFromResults(results) : boardRows;

  if (!profile) {
    return (
      <div className="panel">
        <p className="muted">Select a profile first.</p>
      </div>
    );
  }

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
      </section>

      {dialogOpen && config && (
        <RunConfirmDialog config={config.config} onConfirm={handleConfirm} onCancel={closeDialog} submitting={runSubmitting} error={runError} />
      )}

      {runId && runStatus && <NodeStatusList status={runStatus.status} nodeReceipts={runStatus.node_receipts} progressSteps={progress?.steps} />}

      {resultsError && <div className="callout danger">Could not load results: {resultsError}</div>}

      {(results || runActive) && (
        <FindJobsPostingsBoard rows={rows} cap={null} rankScores={rankScores} profileId={profile.profile_id} />
      )}
    </div>
  );
}

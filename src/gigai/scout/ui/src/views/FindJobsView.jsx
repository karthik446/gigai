import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  buildRunRequest,
  getAssessments,
  getJevSettings,
  getRunProgress,
  getRunResults,
  getRuns,
  getRunStatus,
  postRank,
  startRun,
  storedRankScores,
} from "../api.js";
import { mergeRows, rowsFromProgress, rowsFromResults } from "../boardRows.js";
import RunConfirmDialog from "../components/RunConfirmDialog.jsx";
import NodeStatusList from "../components/NodeStatusList.jsx";
import JobsGrid from "../components/JobsGrid.jsx";
import JobsSummaryStrip from "../components/JobsSummaryStrip.jsx";
import Breadcrumb from "../components/Breadcrumb.jsx";
import JobPage from "./JobPage.jsx";
import AssessmentsView from "./AssessmentsView.jsx";
import { useSourcesStatus } from "../components/SourcesUpdatePanel.jsx";
import { relativeTimeLabel } from "../display.js";
import { PASTED_RESUME_KEY, addRunPostings, assessmentJobs, buildJobs, dateTimeLabel, isScored, runJobs as onlyRunJobs, usedPastedResume } from "../jobModel.js";
import { canScoreWithJev, createRankPass, jevCardSkipWords, jevNoticeText, jevRankingOn, jevRunConsentLine, mergeRankScores } from "../jevModel.js";
import { needAnswersCount, withJobStates } from "../jobStateModel.js";
import { rankStatusLine, rankUsageLine, runFailure } from "../runText.js";
import { indexNotice } from "../sourcesModel.js";
import { ASSESSMENTS_HASH, RUNS_HASH, SETTINGS_HASH, runHash } from "../routing.js";

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
// is always the latest of the two. An on-demand assessment is a card on
// Assessments; a run posting's assessment, however it was started, is on
// its Jobs card only (uat-batch2-r1, jobModel.postingHome).
//
// uat-bug-018: every job carries its derived state (job.state, from
// jobStateModel.withJobStates over the served `job_state`s, the
// applications list and the tailored resumes made on this page). The grids
// filter on it, and the number of jobs that need the operator's answers
// goes up to the top bar (`onNeedAnswers`), per list.
//
// Q4a-nav: this view stays MOUNTED for the whole app (App.jsx renders it
// on every route; it draws nothing on the routes it does not own), so a
// live run keeps polling while the operator reads Applications or Settings,
// and the run/filters/cards survive every navigation. It owns five routes:
//
//   #/jobs        the grid: the Dashboard's summary strip, "Run find jobs"
//                 (the consent dialog), the past-run picker. Search (run)
//                 results only (uat-bug-016)
//   #/jobs/<id>   one posting's JobPage, from the same job model
//   #/runs/<id>   one run's page: "Runs › <run>", status + node receipts,
//                 counts, and that run's grid (loaded via the same
//                 GET /api/runs/{id}/results read as the picker)
//   #/assessments       the on-demand assessments, newest first, and
//                       "+ Assess a job" (uat-bug-016, AssessmentsView);
//                       never a run posting (uat-batch2-r1)
//   #/assessments/<id>  the same JobPage, opened from Assessments
//
// When nothing is loaded yet (fresh load, a deep link), the newest
// succeeded run for the profile is loaded automatically (GET /api/runs,
// newest first) -- so a job page survives a reload. A run page loads its
// own run instead.
//
// uat-bug-012: every response is checked against the run the view is
// showing NOW (shownRunId). Before this a slow load of run A (the newest
// succeeded run, loaded automatically) could land after the operator had
// opened run B's page, so B's page showed A's cards under B's name. A run
// page also shows ONLY its run's postings (no on-demand cards), and a run
// that did not succeed says where it failed (runText.runFailure).
//
// uat-batch2: Jobs says when the stored company postings are missing or
// out of date (GET /api/sources/update -> index.needs_update, N11-C) with a
// link to Settings' "Update sources"; a run page says how many postings
// matched but were not imported (progress.not_imported_count, uat-bug-011).
//
// ui-pass (uat-bug-021): the page never asks Jev on its own. A run's cards
// show the scores already stored (each results page's `rank_score`,
// api.storedRankScores); nothing POSTs /rank when a run opens. "Score with
// Jev" (only with a Jev key, ranking on, room in today's budget and an
// unscored posting: jevModel.canScoreWithJev) is the one call that may
// spend: POST /rank with `start` set, then the same route read ({}) while the
// pass runs, each answer's scores merged into the grid as they arrive
// (jevModel.createRankPass). The run's own Jev line is on its status panel
// (NodeStatusList `rankStatus`), and a card with no score says why in its
// "– Jev" tooltip (jevModel.jevCardSkipWords). The privacy line "Your
// resume is sent to Jev" shows only when it is true: a key and ranking on.
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
  profilesLoading,
  config,
  reloadConfig,
  runsState,
  applicationsState,
  externalQuickItem,
  runPostingIds,
  onRunPostingIds,
  onNeedAnswers,
}) {
  const profileId = profile ? profile.profile_id : null;
  const ownsRoute = route.view === "jobs" || route.view === "job" || route.view === "run" || route.view === "assessments" || route.view === "assessment";

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
  // run-reads-fast (uat-bug-022): what the loaded run's results say about
  // the run itself ({created_at, counts}), so its labels do not wait for
  // the runs list; and whether the run to open on is still being looked up.
  const [runMeta, setRunMeta] = useState(null);
  const [newestLoading, setNewestLoading] = useState(true);
  // True while pages after the first are still coming: a job page for a
  // posting that is not in yet says "Loading", not "not found".
  const [pagesLoading, setPagesLoading] = useState(false);
  const [rankScores, setRankScores] = useState([]);
  // ui-pass: GET /api/jev/settings (key, on/off, budget, today's usage), and
  // what the page's own "Score with Jev" pass last said (POST /rank's
  // rank_status and usage). Null until read / until a click.
  const [jevSettings, setJevSettings] = useState(null);
  const [rankStatus, setRankStatus] = useState(null);
  const [jevUsage, setJevUsage] = useState(null);
  const [rankStarting, setRankStarting] = useState(false);
  const [rankError, setRankError] = useState(null);
  const [quickItems, setQuickItems] = useState([]);
  // Assessments made against a PASTED resume: the store files them under no
  // profile, so the profile's list never has them. Cards on Assessments
  // only; they never touch a run posting's verdict (jobModel.assessmentJobs).
  const [pastedItems, setPastedItems] = useState([]);
  // True until the first read of the quick-assess store for this profile
  // ends: an assessment's job page says "Loading…" meanwhile, not "not found".
  const [quickLoading, setQuickLoading] = useState(true);
  // uat-bug-018: the jobs a tailored resume was made for on this page since
  // the lists were read (their served state does not say "tailored" yet).
  const [tailoredIds, setTailoredIds] = useState(() => new Set());
  const handleTailored = useCallback((jobId) => {
    setTailoredIds((known) => (known.has(jobId) ? known : new Set(known).add(jobId)));
  }, []);
  // N11-C: read while Jobs is the page shown (and polled while an update
  // runs), so the message goes away once "Update sources" has run.
  const sources = useSourcesStatus({ enabled: route.view === "jobs" });

  const pollTimer = useRef(null);
  const progressPollTimer = useRef(null);
  const rankPass = useRef(null);
  const boardRowsRef = useRef([]);
  // The run this view shows, set in the same tick as setRunId: a response
  // for any other run is dropped (uat-bug-012).
  const shownRunId = useRef(null);
  const showRun = useCallback((id) => {
    shownRunId.current = id;
    setRunId(id);
  }, []);

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
  // Ends the reads of a "Score with Jev" pass (the pass itself runs on in
  // the server) and forgets what it said: it was about the run shown before.
  const stopRankPass = useCallback(() => {
    if (rankPass.current) {
      rankPass.current.stop();
      rankPass.current = null;
    }
    setRankStatus(null);
    setJevUsage(null);
    setRankStarting(false);
    setRankError(null);
  }, []);

  useEffect(
    () => () => {
      stopPolling();
      stopProgressPolling();
      if (rankPass.current) {
        rankPass.current.stop();
      }
    },
    [stopPolling, stopProgressPolling],
  );

  const loadJevSettings = useCallback(() => {
    getJevSettings()
      .then(setJevSettings)
      .catch(() => setJevSettings(null));
  }, []);

  // Reset run state when the selected profile changes -- a run belongs to
  // whichever profile was selected when it started; switching profiles
  // should not show a stale run's cards under a different profile's label.
  useEffect(() => {
    stopPolling();
    stopProgressPolling();
    stopRankPass();
    showRun(null);
    setRunStatus(null);
    setProgress(null);
    boardRowsRef.current = [];
    setBoardRows([]);
    setResults(null);
    setRunMeta(null);
    setNewestLoading(true);
    setPagesLoading(false);
    setResultsError(null);
    setResultsLoading(false);
    setRankScores([]);
    setQuickItems([]);
    setPastedItems([]);
    setQuickLoading(true);
    setTailoredIds(new Set());
    onRunPostingIds(new Set());
  }, [profileId, stopPolling, stopProgressPolling, stopRankPass, showRun, onRunPostingIds]);

  // Q4a: the quick-assess store for this profile (job-page re-assessments,
  // "Assess this posting", "+ Assess a job", CLI). Loaded when the profile
  // changes and with every results load; a mutation merges its response in
  // place (handleQuickUpdated) so nothing waits for a re-read.
  const loadQuickItems = useCallback(() => {
    if (!profileId) {
      return;
    }
    const pasted = getAssessments({ profileId: PASTED_RESUME_KEY })
      .then((response) => setPastedItems(response.items || []))
      .catch(() => setPastedItems([]));
    const own = getAssessments({ profileId })
      .then((response) => setQuickItems(response.items || []))
      .catch(() => setQuickItems([]));
    Promise.all([own, pasted]).finally(() => setQuickLoading(false));
  }, [profileId]);

  useEffect(loadQuickItems, [loadQuickItems]);

  // Q4a-nav: this view stays mounted while the operator assesses
  // elsewhere, so re-read the store each time one of its own routes comes
  // back into view -- a cheap GET, and the only way a card/job page
  // reflects an assessment made on another page.
  useEffect(() => {
    if (ownsRoute) {
      loadQuickItems();
    }
  }, [route.view, ownsRoute, loadQuickItems]);

  // ui-pass: the Jev settings, re-read with the same rule (Settings may
  // have turned ranking off or changed the budget meanwhile).
  useEffect(() => {
    if (ownsRoute) {
      loadJevSettings();
    }
  }, [route.view, ownsRoute, loadJevSettings]);

  const handleQuickUpdated = useCallback(
    (item) => {
      if (!item || !item.job) {
        return;
      }
      const merge = (prev) => {
        const rest = prev.filter((existing) => existing.job.job_identity !== item.job.job_identity);
        return [item, ...rest];
      };
      if (usedPastedResume(item)) {
        setPastedItems(merge);
      } else {
        setQuickItems(merge);
      }
    },
    [],
  );

  // Q4a-nav: an AssessResponse from "+ Assess a job" (App.jsx hands it
  // over, then opens #/assessments/<job_identity>).
  useEffect(() => {
    if (externalQuickItem) {
      handleQuickUpdated(externalQuickItem);
    }
  }, [externalQuickItem, handleQuickUpdated]);

  const pollProgress = useCallback((id) => {
    getRunProgress(id)
      .then((snapshot) => {
        if (shownRunId.current !== id) {
          return;
        }
        setProgress(snapshot);
        const nextRows = mergeRows(boardRowsRef.current, rowsFromProgress(snapshot));
        boardRowsRef.current = nextRows;
        setBoardRows(nextRows);
        progressPollTimer.current = setTimeout(() => pollProgress(id), PROGRESS_POLL_INTERVAL_MS);
      })
      .catch(() => {
        if (shownRunId.current === id) {
          progressPollTimer.current = setTimeout(() => pollProgress(id), PROGRESS_POLL_INTERVAL_MS);
        }
      });
  }, []);

  // The click: the only thing on this page that may spend.
  function scoreWithJev() {
    const id = runId;
    if (rankPass.current) {
      rankPass.current.stop();
    }
    setRankStarting(true);
    setRankError(null);
    const pass = createRankPass({
      runId: id,
      postRank,
      isCurrent: () => shownRunId.current === id,
      intervalMs: POLL_INTERVAL_MS,
      onResponse: (response) => {
        setRankStarting(false);
        setRankScores((known) => mergeRankScores(known, response.scores));
        setRankStatus(response.rank_status || null);
        setJevUsage(response.usage || null);
      },
      onError: (error) => {
        setRankStarting(false);
        setRankError(error.message || String(error));
      },
    });
    rankPass.current = pass;
    pass.start();
  }

  const loadResults = useCallback(
    (id) => {
      setResultsLoading(true);
      setPagesLoading(true);
      // run-reads-fast: the results come a page at a time, the top of the
      // grid first. The first page is drawn as soon as it is in; the later
      // ones add their cards to it.
      let drawn = false;
      return getRunResults(id, {
        onPage: (response) => {
          if (shownRunId.current !== id) {
            return false; // another run is shown now: stop reading this one
          }
          setResults(response.payload);
          // The scores already stored for these rows: a read, never a Jev call.
          setRankScores(storedRankScores(response));
          if (!drawn) {
            drawn = true;
            setRunMeta({ created_at: response.created_at, counts: response.counts });
            setResultsLoading(false);
            loadQuickItems();
          }
          return true;
        },
      })
        .then(() => {
          if (shownRunId.current !== id) {
            return;
          }
          setPagesLoading(false);
          // The run's own pass may have spent: today's usage is re-read.
          loadJevSettings();
        })
        .catch((error) => {
          if (shownRunId.current !== id) {
            return;
          }
          setPagesLoading(false);
          setResultsLoading(false);
          setResultsError(error.message || String(error));
        });
    },
    [loadQuickItems, loadJevSettings],
  );

  const runsReload = runsState.reload;
  const pollStatus = useCallback(
    (id) => {
      getRunStatus(id)
        .then((status) => {
          if (shownRunId.current !== id) {
            return;
          }
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
          if (shownRunId.current !== id) {
            return;
          }
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
      stopRankPass();
      setRunError(null);
      setResultsError(null);
      showRun(pastRunId);
      setRunStatus(null);
      boardRowsRef.current = [];
      setBoardRows([]);
      setProgress(null);
      setRankScores([]);
      setResults(null);
      setRunMeta(null);
      setResultsLoading(true);
      getRunStatus(pastRunId)
        .then((status) => {
          if (shownRunId.current !== pastRunId) {
            return undefined;
          }
          setRunStatus(status);
          // One read of the run's progress files: what a finished run's page
          // says about its board pass and the postings it did not import.
          getRunProgress(pastRunId)
            .then((snapshot) => shownRunId.current === pastRunId && setProgress(snapshot))
            .catch(() => {});
          return loadResults(pastRunId);
        })
        .catch((error) => {
          if (shownRunId.current !== pastRunId) {
            return;
          }
          setResultsLoading(false);
          setResultsError(error.message || String(error));
        });
    },
    [stopPolling, stopProgressPolling, stopRankPass, loadResults, showRun],
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
  // have nothing to find). run-reads-fast: that run is asked for on its
  // own (one small read), so the grid never waits for the whole runs list.
  useEffect(() => {
    if (!profileId || runId || routeRunId) {
      setNewestLoading(false);
      return undefined;
    }
    let current = true;
    setNewestLoading(true);
    getRuns({ profileId, status: "succeeded", limit: 1 })
      .then((response) => {
        if (!current) {
          return;
        }
        setNewestLoading(false);
        if (response.runs.length > 0 && shownRunId.current === null) {
          viewPastRun(response.runs[0].run_id);
        }
      })
      .catch(() => current && setNewestLoading(false));
    return () => {
      current = false;
    };
  }, [profileId, runId, routeRunId, viewPastRun]);

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
      showRun(response.run_id);
      setRunStatus({ run_id: response.run_id, status: response.status, node_receipts: response.node_receipts });
      setResults(null);
      setRunMeta(null);
      setResultsError(null);
      setProgress(null);
      setRankScores([]);
      stopRankPass();
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
  const rows = useMemo(() => (results ? rowsFromResults(results) : boardRows), [results, boardRows]);
  const visaRequired = Boolean(config && config.config && config.config.visa_sponsorship_required);
  const currentRun = runsState.runs.find((run) => run.run_id === runId) || null;
  const runCreatedAt = currentRun ? currentRun.created_at : runMeta ? runMeta.created_at : null;
  const newestRun = runsState.runs[0] || null;

  const applications = applicationsState.applications;
  const jobs = useMemo(
    () => withJobStates(buildJobs({ rows, rankScores, quickItems, runCreatedAt }), applications, tailoredIds),
    [rows, rankScores, quickItems, runCreatedAt, applications, tailoredIds],
  );
  // Jobs and a run page show the run's postings only (uat-bug-016): an
  // on-demand assessment belongs to no run; it is a card on Assessments.
  const runJobs = useMemo(() => onlyRunJobs(jobs), [jobs]);
  // uat-batch2-r1: the postings of every run loaded for this profile since
  // the page was opened (App.jsx holds the set). An assessment of one of
  // them is that posting's, under Jobs.
  useEffect(() => {
    onRunPostingIds((known) => addRunPostings(known, rows));
  }, [rows, onRunPostingIds]);
  const assessed = useMemo(
    () => withJobStates(assessmentJobs(quickItems, jobs, pastedItems, runPostingIds), applications, tailoredIds),
    [quickItems, jobs, pastedItems, runPostingIds, applications, tailoredIds],
  );
  const jobsWaiting = useMemo(() => needAnswersCount(runJobs), [runJobs]);
  const unscoredCount = useMemo(() => runJobs.filter((job) => !isScored(job.rank)).length, [runJobs]);
  const assessmentsWaiting = useMemo(() => needAnswersCount(assessed), [assessed]);
  useEffect(() => {
    if (onNeedAnswers) {
      onNeedAnswers({ jobs: jobsWaiting, assessments: assessmentsWaiting });
    }
  }, [jobsWaiting, assessmentsWaiting, onNeedAnswers]);

  if (!ownsRoute) {
    return null;
  }

  if (!profile && profilesLoading) {
    return (
      <div className="panel">
        <p className="muted" style={{ margin: 0 }}>
          Loading profiles…
        </p>
      </div>
    );
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

  if (route.view === "job" || route.view === "assessment") {
    const jobId = route.params.jobId;
    const fromAssessments = route.view === "assessment";
    // An assessment's page reads the on-demand list first (a pasted-resume
    // assessment of a run posting's address is found there, not the run's
    // card). Either address still opens any job: an old link to a posting
    // that has since moved to the other list keeps working.
    const pool = fromAssessments ? assessed.concat(jobs) : jobs.concat(assessed);
    const job = pool.find((candidate) => candidate.id === jobId) || null;
    return (
      <JobPage
        job={job}
        jobId={jobId}
        from={fromAssessments ? "assessments" : "jobs"}
        profileId={profile.profile_id}
        profileLabel={profile.label}
        visaRequired={visaRequired}
        runId={runId}
        loading={fromAssessments ? quickLoading : resultsLoading || pagesLoading || quickLoading || (newestLoading && !runId)}
        onQuickUpdated={handleQuickUpdated}
        onApplicationsChanged={applicationsState.reload}
        onTailored={handleTailored}
      />
    );
  }

  if (route.view === "assessments") {
    return <AssessmentsView jobs={assessed} loading={quickLoading} profileLabel={profile.label} visaRequired={visaRequired} />;
  }

  const runLabel = currentRun ? `run ${relativeTimeLabel(currentRun.created_at)}` : runActive ? "run in progress" : "";
  const jevSkipWords = jevCardSkipWords(rankStatus, progress?.rank_status, jevSettings);
  const scoreOffered =
    Boolean(runId && results) &&
    !runActive &&
    !pagesLoading &&
    !rankStarting &&
    canScoreWithJev({ settings: jevSettings, usage: jevUsage, rankStatus, unscored: unscoredCount });
  const jevUsageLine = jevSettings ? rankUsageLine(jevUsage || jevSettings.usage) : null;
  const jevBar =
    runId && results && jevRankingOn(jevSettings) ? (
      <div className="jev-bar" data-role="jev-bar">
        {scoreOffered && (
          <button type="button" className="button small secondary" data-action="score-with-jev" onClick={scoreWithJev}>
            Score with Jev
          </button>
        )}
        {rankStarting && <span className="muted">Jev: starting…</span>}
        {rankStatus && (
          <span className="muted" data-role="rank-route-status" data-rank-status={rankStatus.status}>
            {rankStatusLine(rankStatus)}
          </span>
        )}
        {rankError && <span className="muted">Jev: {rankError}</span>}
        {jevUsageLine && (
          <span className="muted" data-role="jev-usage">
            {jevUsageLine}
          </span>
        )}
      </div>
    ) : null;
  const jevNotice = jevNoticeText(jevSettings);
  const notice = indexNotice(sources.status, { atsEnabled: Boolean(config && config.config && config.config.sources && config.config.sources.ats) });
  const grid = (
    <>
      {resultsError && <div className="callout danger">Could not load results: {resultsError}</div>}
      {jevBar}
      {(results || runActive) && (
        <JobsGrid
          jobs={runJobs}
          visaRequired={visaRequired}
          runLabel={runLabel}
          emptyMessage={runActive ? "Waiting for the first postings…" : "This run found no postings."}
          jevSkipWords={jevSkipWords}
        />
      )}
    </>
  );

  if (route.view === "run") {
    const shownRun = currentRun || (runId === routeRunId && runStatus ? { run_id: runId, created_at: null, counts: null, status: runStatus.status } : null);
    const crumb = shownRun && shownRun.created_at ? `run ${relativeTimeLabel(shownRun.created_at)}` : `run ${routeRunId}`;
    const loaded = runId === routeRunId && runStatus && runStatus.run_id === routeRunId;
    const failure = loaded ? runFailure(runStatus, results ? runJobs.length : null) : null;
    const lastGood = runsState.runs.find((run) => run.status === "succeeded" && run.run_id !== routeRunId) || null;
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
        {failure && (
          <div className="callout danger" data-role="run-failure">
            <strong>{failure.line}</strong>
            {failure.message && <div className="run-failure-message">{failure.message}</div>}
            {lastGood && (
              <div className="run-failure-next">
                <a href={runHash(lastGood.run_id)}>Open the last successful run</a> ({relativeTimeLabel(lastGood.created_at)}).
              </div>
            )}
          </div>
        )}
        {loaded && (
          <NodeStatusList
            status={runStatus.status}
            nodeReceipts={runStatus.node_receipts}
            progressSteps={progress?.steps}
            rotation={progress?.rotation}
            boards={progress?.boards}
            notImported={progress?.not_imported_count}
            rankStatus={progress?.rank_status}
          />
        )}
        {loaded && resultsError && <div className="callout danger">Could not load results: {resultsError}</div>}
        {loaded && !(failure && runJobs.length === 0) && jevBar}
        {loaded && (results || runActive) && !(failure && runJobs.length === 0) && (
          <JobsGrid
            jobs={runJobs}
            visaRequired={visaRequired}
            runLabel={runLabel}
            emptyMessage={runActive ? "Waiting for the first postings…" : "This run found no postings."}
            jevSkipWords={jevSkipWords}
          />
        )}
      </div>
    );
  }

  return (
    <div>
      <JobsSummaryStrip
        lastRun={newestRun}
        runsLoading={runsState.loading}
        needAnswersCount={results || runActive ? jobsWaiting : null}
        applications={applicationsState.applications}
        applicationsLoading={applicationsState.loading}
      />

      <section className="panel jobs-header">
        <div className="jobs-header-row">
          <h2>
            Jobs <span className="muted">{profile.label}</span>
          </h2>
          <div className="jobs-header-actions">
            <button className="button" onClick={openDialog} disabled={!canRun || runActive} data-action="run">
              {runActive ? "Run in progress…" : "Run find jobs"}
            </button>
          </div>
        </div>
        <div className="jobs-header-meta">
          {jevNotice && (
            <p className="privacy-note" data-role="jev-notice" style={{ margin: 0 }}>
              {jevNotice}
            </p>
          )}
          {!hasResume && config && (
            <p className="muted" style={{ margin: 0 }}>
              Add a resume in <a href={SETTINGS_HASH}>Settings</a> to enable a run.
            </p>
          )}
          <PastRunPicker runs={runsState.runs} currentRunId={runId} onSelect={viewPastRun} disabled={runActive} />
        </div>
      </section>

      {dialogOpen && config && (
        <RunConfirmDialog
          config={config.config}
          onConfirm={handleConfirm}
          onCancel={closeDialog}
          submitting={runSubmitting}
          error={runError}
          jevLine={jevRunConsentLine(jevSettings)}
        />
      )}

      {notice && (
        <div className="callout info" data-role="index-notice" data-index-status={notice.status || undefined}>
          {notice.message} {notice.running ? "An update is running now. " : ""}
          <a href={SETTINGS_HASH}>{notice.running ? "See its progress in Settings" : "Open Settings to run Update sources"}</a>.
        </div>
      )}

      {runId && runStatus && (runActive || runStatus.status !== "succeeded") && (
        <NodeStatusList
          status={runStatus.status}
          nodeReceipts={runStatus.node_receipts}
          progressSteps={progress?.steps}
          rotation={progress?.rotation}
          boards={progress?.boards}
          notImported={progress?.not_imported_count}
          rankStatus={progress?.rank_status}
        />
      )}

      {grid}

      {!results && !runActive && !resultsLoading && !runsState.loading && runsState.runs.length === 0 && (
        <div className="panel">
          <p className="muted" style={{ margin: 0 }}>
            No find-jobs run yet for this profile. Run one above to see its postings here, or assess a single posting under{" "}
            <a href={ASSESSMENTS_HASH}>Assessments</a>.
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

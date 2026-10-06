import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getAssessments,
  getPostings,
  getRunProgress,
  JOBS_PAGE_ROWS,
  createResultsPager,
  getRuns,
  getRunStatus,
  postAssessAll,
  postPostedWindow,
  postRank,
  storedRankScores,
} from "../api.js";
import { mergeRows, rowsFromProgress, rowsFromResults } from "../boardRows.js";
import NodeStatusList from "../components/NodeStatusList.jsx";
import JobsGrid from "../components/JobsGrid.jsx";
import Breadcrumb from "../components/Breadcrumb.jsx";
import ModelAverage from "../components/ModelAverage.jsx";
import JobPage from "./JobPage.jsx";
import AssessmentsView from "./AssessmentsView.jsx";
import JobsView, { expireJobsList } from "./JobsView.jsx";
import { relativeTimeLabel } from "../display.js";
import { PASTED_RESUME_KEY, addRunPostings, assessmentJobs, buildJobs, dateTimeLabel, runJobs as onlyRunJobs, usedPastedResume, withRunEnd } from "../jobModel.js";
import { createRankPass, isRanked, mergeRankScores, rankButtonLabel, rankPassLine, rankPassRunning } from "../rankModel.js";
import { needAnswersCount, withJobStates } from "../jobStateModel.js";
import { runFailure } from "../runText.js";
import {
  assessAllButtonLabel,
  assessAllRunning,
  createAssessAllPoll,
  estimateSourceLine,
  jobLine,
  planLine,
  privacyLine,
  skipReasonText,
  staleLine,
} from "../assessAllModel.js";
import { MAX_LOOKUP_ROWS, postingJob, postingsQuery, EMPTY_FILTER } from "../postingsModel.js";
import { RUNS_HASH, SETTINGS_HASH, assessmentHash, runHash } from "../routing.js";
import { onDemandItemFor, resolveJobId } from "../jobAddress.js";

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "blocked", "cancelled", "interrupted"]);
const POLL_INTERVAL_MS = 2000;
const PROGRESS_POLL_INTERVAL_MS = 1500;

// P9/P9c (F3) → Q4a → Q4a-nav: the postings host, for the selected profile.
//
// Q4a (v0.1.9): the postings list is the card grid (JobsGrid) and each card
// opens a job page (JobPage) by hash route (#/jobs/<normalized_url>,
// routing.js). The cards merge three existing reads (jobModel.buildJobs):
// the run's rows + assessments, the model's ranks, and the quick-assess
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
//   #/jobs        0.1.10.7 M4b: Jobs by posting (JobsView): the stored postings
//                 every active profile matches, with no run. This view starts
//                 no run any more: the "Run find jobs" button, its consent
//                 dialog and the past-run picker are gone
//   #/jobs/<id>   one posting's JobPage, from the same job model; a posting
//                 no loaded run and no stored assessment carries is built
//                 from its row of the Jobs list (postingsModel.postingJob)
//   #/runs/<id>   one PAST run's page (history): "Past runs › <run>", status + node receipts,
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
// SCOPE-ADD-3 D: the run ranks every posting that passed its filters with
// the operator's own model, a batch at a time. While the run goes, the grid
// is GET /progress's postings, each with its `rank` once its batch has
// landed, so the first ranked batch shows at once and the grid re-orders by
// score as more land (JobsGrid holds the order while the operator is on the
// list). Once the run ends the grid is GET /results' rows, each with its
// `rank` (score, reasons, blockers). The run's status panel says "Ranked N
// of M · Assessing X of Y" (NodeStatusList). A finished run's grid has a
// Rank / Re-rank button: POST /rank {start: true} starts (or joins) a
// re-rank pass by the same model, whose scores re-order the grid as they
// land, with Cancel ({cancel: true}); when it ends the results are read
// again for the new reasons (rankModel.createRankPass).
//
// uat-bug-042: a finished run's grid also has "Assess all new (N)": POST
// /assess-all reads the plan (N new postings not assessed yet, the run's
// model, K at a time, a minute figure only when a call was timed); the click
// shows that line first and "Start" queues them all in the background
// (rank order, K at a time, cancellable; a start after a cancel skips what
// finished). While it runs the store is re-read as results land, so cards
// and the header's Assessed / Matched / Need your answers move with it.
//
// 0110-019: the grid's "Posted" chips filter the cards by posting date with
// no request. POST /posted-window {} says how many days the shown run
// searched; when the chosen chip is wider, the grid's "Find postings from
// the last N days" posts {days}: the stored boards are searched (no
// download, no new run), the older postings join this run's rows, and only
// those are ranked and assessed. The results are read again, and the rank
// pass and the "assess all" job it started are followed like any other.
//
// Still DROPPED from the mockup (no backing API): "New since last run".
// Phase 2 fields (work_mode / pay / H-1B count, tailored resume) render
// only once their APIs carry them -- see jobModel.js / JobPage.jsx.
export default function FindJobsView({
  route,
  profile,
  profilesLoading,
  config,
  runsState,
  applicationsState,
  externalQuickItem,
  runPostingIds,
  onRunPostingIds,
  onNeedAnswers,
  onSelectProfile,
}) {
  const profileId = profile ? profile.profile_id : null;
  const ownsRoute = route.view === "jobs" || route.view === "job" || route.view === "run" || route.view === "assessments" || route.view === "assessment";

  const [runId, setRunId] = useState(null);
  const [runStatus, setRunStatus] = useState(null);
  const [progress, setProgress] = useState(null);
  const [boardRows, setBoardRows] = useState([]);
  const [results, setResults] = useState(null);
  // N33: the run's row count as the server counts it (the grid says "Showing
  // 50 of 500"), and the reader that asks for the next page when the grid
  // wants more rows (createResultsPager: each page read once).
  const [resultsTotal, setResultsTotal] = useState(null);
  const resultsPager = useRef(null);
  const pendingRowReads = useRef(0);
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
  // The rows' stored scores (RankScore), with a re-rank pass's answers
  // merged in as they land.
  const [rankScores, setRankScores] = useState([]);
  // What POST /rank last said about a re-rank pass of the shown run
  // (`rank_record`), null until read; whether a click is on its way.
  const [rankRecord, setRankRecord] = useState(null);
  const [rankStarting, setRankStarting] = useState(false);
  const [rankError, setRankError] = useState(null);
  // uat-bug-042: what POST /assess-all last said about the shown run
  // ({plan, job, counts, skip_reason}); whether the confirm line is open.
  const [assessAll, setAssessAll] = useState(null);
  const [assessAllConfirm, setAssessAllConfirm] = useState(false);
  const [assessAllError, setAssessAllError] = useState(null);
  const assessAllPoll = useRef(null);
  // 0110-019: what POST /posted-window last said about the shown run;
  // whether a search is on its way.
  const [postedWindow, setPostedWindow] = useState(null);
  const [findingOlder, setFindingOlder] = useState(false);
  const [findOlderError, setFindOlderError] = useState(null);
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
  // 0.1.10.7 M4b: the rows the Jobs list has read (JobsView hands them up),
  // by job identity, for every profile: a job page of a posting no run and no
  // stored assessment carries is built from its row. `postingsWaiting` is the
  // unfiltered list's "needs your answers" count (the top bar's number).
  const [postingRows, setPostingRows] = useState(() => new Map());
  const [postingsWaiting, setPostingsWaiting] = useState(0);
  const [postingLookup, setPostingLookup] = useState({ id: null, done: false }); // the one-off read of the list for a job page
  const addPostingRows = useCallback((rows) => {
    setPostingRows((known) => {
      const next = new Map(known);
      (rows || []).forEach((row) => next.set(row.job_identity, row));
      return next;
    });
  }, []);

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
  // Ends the reads of a re-rank pass (the pass itself runs on in the
  // server) and forgets what it said: it was about the run shown before.
  const stopRankPass = useCallback(() => {
    if (rankPass.current) {
      rankPass.current.pass.stop();
      rankPass.current = null;
    }
    setRankRecord(null);
    setRankStarting(false);
    setRankError(null);
    if (assessAllPoll.current) {
      assessAllPoll.current.poll.stop();
      assessAllPoll.current = null;
    }
    setAssessAll(null);
    setAssessAllConfirm(false);
    setAssessAllError(null);
    setPostedWindow(null);
    setFindingOlder(false);
    setFindOlderError(null);
  }, []);

  useEffect(
    () => () => {
      stopPolling();
      stopProgressPolling();
      if (rankPass.current) {
        rankPass.current.pass.stop();
      }
      if (assessAllPoll.current) {
        assessAllPoll.current.poll.stop();
      }
    },
    [stopPolling, stopProgressPolling],
  );

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
    setResultsTotal(null);
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

  // One re-rank pass of run `id`: `action` is "read" (on load), "start"
  // (the Rank / Re-rank click) or "cancel". Answers for another run are
  // dropped; each answer's scores re-order the grid as they land, and when a
  // pass it saw running ends the results are read again (`reloadResults`,
  // set below: the rows then carry the new reasons).
  const reloadResults = useRef(null);
  const followRankPass = useCallback((id, action) => {
    if (!rankPass.current || rankPass.current.runId !== id) {
      if (rankPass.current) {
        rankPass.current.stop();
      }
      const pass = createRankPass({
        runId: id,
        postRank,
        isCurrent: () => shownRunId.current === id,
        intervalMs: POLL_INTERVAL_MS,
        onResponse: (response) => {
          setRankStarting(false);
          setRankError(null);
          const record = response && response.rank_record ? response.rank_record : null;
          setRankRecord(record);
          if (rankPassRunning(record)) {
            setRankScores((known) => mergeRankScores(known, response.scores));
          }
        },
        onEnd: () => {
          if (reloadResults.current) {
            reloadResults.current(id);
          }
        },
        onError: (error) => {
          setRankStarting(false);
          setRankError(error.message || String(error));
        },
      });
      rankPass.current = { runId: id, pass };
    }
    const { pass } = rankPass.current;
    if (action === "start") {
      setRankStarting(true);
      setRankError(null);
      pass.start();
    } else if (action === "cancel") {
      pass.cancel();
    } else {
      pass.read();
    }
  }, []);

  // uat-bug-042: "Assess all new" for run `id`: `action` is "read", "start"
  // or "cancel". Each answer that shows more results landed re-reads the
  // quick-assess store (the cards' verdicts); answers for another run are
  // dropped.
  const assessAllDone = useRef(-1);
  const followAssessAll = useCallback(
    (id, action) => {
      if (!assessAllPoll.current || assessAllPoll.current.runId !== id) {
        if (assessAllPoll.current) {
          assessAllPoll.current.poll.stop();
        }
        assessAllDone.current = -1;
        const poll = createAssessAllPoll({
          runId: id,
          post: postAssessAll,
          isCurrent: () => shownRunId.current === id,
          intervalMs: POLL_INTERVAL_MS,
          onResponse: (response) => {
            setAssessAllError(null);
            setAssessAll(response);
            const job = response ? response.job : null;
            const landed = job ? (job.assessed || 0) + (job.failed || 0) : 0;
            if (job && landed !== assessAllDone.current) {
              if (assessAllDone.current >= 0) {
                loadQuickItems();
              }
              assessAllDone.current = landed;
            }
          },
          onEnd: () => loadQuickItems(),
          onError: (error) => setAssessAllError(error.message || String(error)),
        });
        assessAllPoll.current = { runId: id, poll };
      }
      const { poll } = assessAllPoll.current;
      if (action === "start") {
        setAssessAllConfirm(false);
        poll.start();
      } else if (action === "cancel") {
        poll.cancel();
      } else {
        poll.read();
      }
    },
    [loadQuickItems],
  );

  const loadResults = useCallback(
    (id) => {
      setResultsLoading(true);
      setPagesLoading(true);
      // N33: the first page (the top of the grid) is read and drawn; later
      // pages come when the grid asks for them (wantResultRows). A re-read
      // after a re-rank pass (the order changed) reads as many rows as were
      // loaded, from the top.
      const previous = resultsPager.current;
      const keep = previous && previous.runId === id ? previous.pager.loaded() : 0;
      if (previous) {
        previous.pager.stop();
      }
      let drawn = false;
      const pager = createResultsPager(id, {
        onPage: (response) => {
          if (shownRunId.current !== id) {
            return false; // another run is shown now: stop reading this one
          }
          if (!resultsPager.current || resultsPager.current.pager !== pager) {
            return false; // a newer read of this run took over
          }
          setResults(response.payload);
          setResultsTotal(response.total);
          // The scores already stored for these rows: a read, never a model call.
          setRankScores(storedRankScores(response));
          if (!drawn) {
            drawn = true;
            setRunMeta({ created_at: response.created_at, counts: response.counts, assess_cap: response.assess_cap ?? null });
            setResultsLoading(false);
            loadQuickItems();
          }
          return true;
        },
      });
      resultsPager.current = { runId: id, pager };
      return pager
        .ensure(Math.max(keep, JOBS_PAGE_ROWS))
        .then(() => {
          if (shownRunId.current !== id || resultsPager.current.pager !== pager) {
            return;
          }
          setPagesLoading(false);
          // Is a re-rank of this run running (a click on another tab, or
          // before a reload)? One read ({} never starts a pass); a running
          // one is followed until it ends.
          followRankPass(id, "read");
          followAssessAll(id, "read");
          // How many days this run searched (the "Posted" chips' button).
          postPostedWindow(id, {})
            .then((response) => shownRunId.current === id && setPostedWindow((known) => (known && known.run_id === id && known.assess ? { ...response, assess: known.assess } : response)))
            .catch(() => {});
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
    [loadQuickItems, followRankPass, followAssessAll],
  );

  // N33: the grid (or a job page for a posting not loaded yet) wants `count`
  // rows of the run: the pager reads the pages it lacks and no other.
  const wantResultRows = useCallback((count) => {
    const current = resultsPager.current;
    if (!current || current.runId !== shownRunId.current) {
      return;
    }
    const total = current.pager.total();
    if (total === null || current.pager.loaded() >= Math.min(count, total)) {
      return;
    }
    pendingRowReads.current += 1;
    setPagesLoading(true);
    current
      .pager.ensure(count)
      .catch((error) => {
        if (resultsPager.current === current) {
          setResultsError(error.message || String(error));
        }
      })
      .finally(() => {
        pendingRowReads.current -= 1;
        if (pendingRowReads.current <= 0 && resultsPager.current === current) {
          pendingRowReads.current = 0;
          setPagesLoading(false);
        }
      });
  }, []);

  reloadResults.current = loadResults;

  // 0110-019: "Find postings from the last N days" for the shown run. The
  // rows it added are read with the results; the rank pass it started is
  // followed by loadResults' own read, and the assess job that follows the
  // pass by the read after the pass ends.
  const findOlder = useCallback(
    (days) => {
      const id = shownRunId.current;
      if (!id) {
        return;
      }
      setFindingOlder(true);
      setFindOlderError(null);
      postPostedWindow(id, { days })
        .then((response) => {
          if (shownRunId.current !== id) {
            return;
          }
          setPostedWindow(response);
          if (response.search && response.search.added > 0) {
            loadResults(id);
          }
        })
        .catch((error) => shownRunId.current === id && setFindOlderError(error.message || String(error)))
        .finally(() => shownRunId.current === id && setFindingOlder(false));
    },
    [loadResults],
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
            // One last read: the counts as the run left them.
            getRunProgress(id)
              .then((snapshot) => shownRunId.current === id && setProgress(snapshot))
              .catch(() => {});
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

  // P9c: load a PAST run's real, sealed results -- never starts a new run.
  // Stops any live poll first, so an in-progress run's cards can't keep
  // arriving and overwrite what the operator just chose to view.
  const viewPastRun = useCallback(
    (pastRunId) => {
      stopPolling();
      stopProgressPolling();
      stopRankPass();
      setResultsError(null);
      showRun(pastRunId);
      setRunStatus(null);
      boardRowsRef.current = [];
      setBoardRows([]);
      setProgress(null);
      setRankScores([]);
      setResults(null);
      setResultsTotal(null);
      setRunMeta(null);
      setResultsLoading(true);
      getRunStatus(pastRunId)
        .then((status) => {
          if (shownRunId.current !== pastRunId) {
            return undefined;
          }
          setRunStatus(status);
          if (!TERMINAL_STATUSES.has(status.status)) {
            // A run still going (started through the deprecated POST /api/run,
            // never from this UI): its page follows it until it ends.
            setResultsLoading(false);
            pollTimer.current = setTimeout(() => pollStatus(pastRunId), POLL_INTERVAL_MS);
            progressPollTimer.current = setTimeout(() => pollProgress(pastRunId), 0);
            return undefined;
          }
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
    [stopPolling, stopProgressPolling, stopRankPass, loadResults, showRun, pollStatus, pollProgress],
  );

  const runActive = Boolean(runId && runStatus && !TERMINAL_STATUSES.has(runStatus.status));
  const runEnded = Boolean(runId && runStatus && TERMINAL_STATUSES.has(runStatus.status));
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

  const rows = useMemo(() => (results ? rowsFromResults(results) : boardRows), [results, boardRows]);
  const visaRequired = Boolean(config && config.config && config.config.visa_sponsorship_required);
  const currentRun = runsState.runs.find((run) => run.run_id === runId) || null;
  const runCreatedAt = currentRun ? currentRun.created_at : runMeta ? runMeta.created_at : null;

  const applications = applicationsState.applications;
  const assessCap = runMeta ? runMeta.assess_cap : null;
  const jobs = useMemo(
    () => withRunEnd(withJobStates(buildJobs({ rows, rankScores, quickItems, runCreatedAt }), applications, tailoredIds), { ended: runEnded, assessCap }),
    [rows, rankScores, quickItems, runCreatedAt, applications, tailoredIds, runEnded, assessCap],
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
  // N33: a job page for a posting that is not on the loaded pages reads the
  // rest of the run (the pager asks for the pages it lacks).
  // 0.1.11.2: the address is read the way the store keeps it (a trailing slash before the query, tracking parameters).
  const routeAddress = route.view === "job" || route.view === "assessment" ? route.params.jobId : null;
  const knownJobIds = useMemo(() => new Set(jobs.map((job) => job.id).concat(assessed.map((job) => job.id), [...postingRows.keys()])), [jobs, assessed, postingRows]);
  const jobRouteId = routeAddress === null ? null : resolveJobId(routeAddress, knownJobIds);
  const jobInLoaded =
    jobRouteId === null || jobs.some((job) => job.id === jobRouteId) || assessed.some((job) => job.id === jobRouteId) || postingRows.has(jobRouteId);
  useEffect(() => {
    if (!jobInLoaded && resultsTotal !== null && rows.length < resultsTotal) {
      wantResultRows(resultsTotal);
    }
  }, [jobInLoaded, resultsTotal, rows.length, wantResultRows]);
  // 0.1.10.7 M4b: a job page opened by its link (a reload) for a posting the
  // newest run and the stored assessments do not carry: one read of the Jobs
  // list finds its row, once the run and the store have been read.
  const settled = !resultsLoading && !pagesLoading && !quickLoading && !newestLoading;
  useEffect(() => {
    if (jobRouteId === null || jobInLoaded || !settled || postingLookup.id === jobRouteId) {
      return;
    }
    setPostingLookup({ id: jobRouteId, done: false });
    getPostings(postingsQuery(EMPTY_FILTER, { limit: MAX_LOOKUP_ROWS }))
      // 0110-9-01: a 202 "preparing" answer has no rows yet.
      .then((response) => {
        const found = response.postings ? response.postings.rows : [];
        addPostingRows(found);
        if (found.some((row) => row.job_identity === jobRouteId)) {
          return undefined;
        }
        // 0.1.11.4 R1: a posting its board no longer lists is under Removed; its page still opens and says it is closed.
        return getPostings(postingsQuery({ ...EMPTY_FILTER, removed: true }, { limit: MAX_LOOKUP_ROWS })).then((removed) =>
          addPostingRows(removed.postings ? removed.postings.rows : []),
        );
      })
      .catch(() => {})
      .finally(() => setPostingLookup((current) => (current.id === jobRouteId ? { id: jobRouteId, done: true } : current)));
  }, [jobRouteId, jobInLoaded, settled, postingLookup.id, addPostingRows]);
  const anyRanked = useMemo(() => runJobs.some((job) => isRanked(job.rank)), [runJobs]);
  const assessmentsWaiting = useMemo(() => needAnswersCount(assessed), [assessed]);
  useEffect(() => {
    if (onNeedAnswers) {
      onNeedAnswers({ jobs: postingsWaiting, assessments: assessmentsWaiting });
    }
  }, [postingsWaiting, assessmentsWaiting, onNeedAnswers]);

  if (!ownsRoute) {
    return null;
  }

  // 0.1.10.7 M4b: Jobs is by posting, across every active profile: it needs no selected profile.
  if (route.view === "jobs") {
    return (
      <JobsView
        selectedProfileId={profileId}
        onSelectProfile={onSelectProfile}
        applicationsState={applicationsState}
        onRows={addPostingRows}
        onCounts={setPostingsWaiting}
      />
    );
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
    const jobId = jobRouteId;
    const fromAssessments = route.view === "assessment";
    // An assessment's page reads the on-demand list first (a pasted-resume
    // assessment of a run posting's address is found there, not the run's
    // card). Either address still opens any job: an old link to a posting
    // that has since moved to the other list keeps working.
    const pool = fromAssessments ? assessed.concat(jobs) : jobs.concat(assessed);
    const listed = postingRows.get(jobId);
    const job = pool.find((candidate) => candidate.id === jobId) || (listed ? withJobStates([postingJob(listed)], applications, tailoredIds)[0] : null);
    // 0.1.11.2: no job page, but an on-demand assessment of this address: the page says so and links to it.
    const onDemand = !job && !fromAssessments ? onDemandItemFor(quickItems, jobId) : null;
    return (
      <JobPage
        onDemandHref={onDemand ? assessmentHash(onDemand.id) : null}
        job={job}
        jobId={jobId}
        from={fromAssessments ? "assessments" : "jobs"}
        profileId={profile.profile_id}
        profileLabel={profile.label}
        visaRequired={visaRequired}
        runId={runId}
        listedRow={listed || null}
        loading={fromAssessments ? quickLoading : resultsLoading || pagesLoading || quickLoading || (newestLoading && !runId) || !(postingLookup.id === jobId && postingLookup.done)}
        onQuickUpdated={handleQuickUpdated}
        onApplicationsChanged={() => {
          expireJobsList(); // the Jobs list's rows carry the application badge
          applicationsState.reload();
        }}
        onTailored={handleTailored}
      />
    );
  }

  if (route.view === "assessments") {
    return <AssessmentsView jobs={assessed} loading={quickLoading} profileLabel={profile.label} visaRequired={visaRequired} />;
  }

  const runLabel = currentRun ? `run ${relativeTimeLabel(currentRun.created_at)}` : runActive ? "run in progress" : "";
  const assessAllJob = assessAll && assessAll.run_id === runId ? assessAll.job : null;
  const assessAllPlan = assessAll && assessAll.run_id === runId ? assessAll.plan : null;
  const assessAllBusy = assessAllRunning(assessAllJob);
  const assessAllBar =
    runId && results && !runActive && (assessAllBusy || assessAllJob || (assessAllPlan && assessAllPlan.count > 0) || (assessAll && assessAll.skip_reason)) ? (
      <div className="rank-bar" data-role="assess-all-bar">
        {!assessAllBusy && assessAllPlan && assessAllPlan.count > 0 && !assessAllConfirm && (
          <button
            type="button"
            className="button small secondary"
            data-action="assess-all"
            onClick={() => setAssessAllConfirm(true)}
            title="Assess every new posting of this run that is not assessed yet, likely fits first, and again every one assessed with older settings."
          >
            {assessAllButtonLabel(assessAllPlan)}
          </button>
        )}
        {!assessAllBusy && assessAllPlan && assessAllPlan.count > 0 && !assessAllConfirm && (
          <ModelAverage kind="assess" target={assessAllPlan.model_target} />
        )}
        {!assessAllBusy && assessAllConfirm && assessAllPlan && (
          <span data-role="assess-all-confirm">
            <span data-role="assess-all-plan">{planLine(assessAllPlan)}</span>{" "}
            {staleLine(assessAllPlan) && (
              <span className="muted" data-role="assess-all-stale">
                {staleLine(assessAllPlan)}{" "}
              </span>
            )}
            <span className="muted" data-role="assess-all-estimate">
              {estimateSourceLine(assessAllPlan)}
            </span>{" "}
            {privacyLine(assessAllPlan.model_target) && (
              <span className="muted" data-role="assess-all-privacy">
                {privacyLine(assessAllPlan.model_target)}
              </span>
            )}{" "}
            <button type="button" className="button small" data-action="assess-all-start" onClick={() => followAssessAll(runId, "start")}>
              Start
            </button>{" "}
            <button type="button" className="button small secondary" onClick={() => setAssessAllConfirm(false)}>
              Not now
            </button>
          </span>
        )}
        {assessAllBusy && (
          <button type="button" className="button small secondary" data-action="cancel-assess-all" onClick={() => followAssessAll(runId, "cancel")}>
            Cancel assessing
          </button>
        )}
        {assessAllJob && (
          <span className="muted" data-role="assess-all-progress" data-assess-all-status={assessAllJob.status}>
            {jobLine(assessAllJob)}
          </span>
        )}
        {assessAll && assessAll.skip_reason && <span className="muted">{skipReasonText(assessAll.skip_reason)}</span>}
        {assessAllError && <span className="muted">Assess all new: {assessAllError}</span>}
      </div>
    ) : null;
  const passRunning = rankPassRunning(rankRecord);
  const passLine = rankPassLine(rankRecord);
  const rankBar =
    runId && results && !runActive ? (
      <div className="rank-bar" data-role="rank-bar">
        {!passRunning && (
          <button
            type="button"
            className="button small secondary"
            data-action="rank"
            disabled={pagesLoading || rankStarting}
            onClick={() => followRankPass(runId, "start")}
            title="Rank this run's postings again with your model: likely fits first, likely no-matches last."
          >
            {rankStarting ? "Starting…" : rankButtonLabel(anyRanked)}
          </button>
        )}
        {passRunning && (
          <button type="button" className="button small secondary" data-action="cancel-rank" onClick={() => followRankPass(runId, "cancel")}>
            Cancel re-rank
          </button>
        )}
        {passLine && (
          <span className="muted" data-role="rank-pass" data-rank-record-status={rankRecord.status}>
            {passLine}
          </span>
        )}
        {rankError && <span className="muted">Rank: {rankError}</span>}
      </div>
    ) : null;
  if (route.view === "run") {
    const shownRun = currentRun || (runId === routeRunId && runStatus ? { run_id: runId, created_at: null, counts: null, status: runStatus.status } : null);
    const crumb = shownRun && shownRun.created_at ? `run ${relativeTimeLabel(shownRun.created_at)}` : `run ${routeRunId}`;
    const loaded = runId === routeRunId && runStatus && runStatus.run_id === routeRunId;
    const failure = loaded ? runFailure(runStatus, results ? runJobs.length : null) : null;
    const lastGood = runsState.runs.find((run) => run.status === "succeeded" && run.run_id !== routeRunId) || null;
    return (
      <div>
        <Breadcrumb crumbs={[{ label: "Past runs", href: RUNS_HASH }, { label: crumb }]} />
        <section className="panel">
          <h2>
            Past run <span className="muted">{shownRun && shownRun.created_at ? dateTimeLabel(shownRun.created_at) : routeRunId}</span>
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
            rank={progress?.rank}
            assessCounts={progress?.assess_counts}
            rankStatus={progress?.rank_status}
          />
        )}
        {loaded && resultsError && <div className="callout danger">Could not load results: {resultsError}</div>}
        {loaded && !(failure && runJobs.length === 0) && rankBar}
        {loaded && !(failure && runJobs.length === 0) && assessAllBar}
        {loaded && (results || runActive) && !(failure && runJobs.length === 0) && (
          <JobsGrid
            jobs={runJobs}
            visaRequired={visaRequired}
            runLabel={runLabel}
            emptyMessage={runActive ? "Waiting for the first postings…" : "This run found no postings."}
            total={runActive ? null : resultsTotal}
            onWantRows={runActive ? null : wantResultRows}
            loadingMore={pagesLoading}
            postedWindow={postedWindow && postedWindow.run_id === runId ? postedWindow : null}
            onFindOlder={runActive ? null : findOlder}
            findingOlder={findingOlder}
            findOlderError={findOlderError}
          />
        )}
      </div>
    );
  }

  return null;
}

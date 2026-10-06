import { useCallback, useEffect, useState } from "react";
import { ApiError, getConfig, getPrivacyCleanup, getSetup, getSourcesUpdate, putPrivacyCleanupShown } from "./api.js";
import { cleanupNotice } from "./generatePdfModel.js";
import { useApplications, useProfiles, useRuns } from "./hooks.js";
import { finishLanding } from "./sourcesStripModel.js";
import SetupWizard from "./wizard/index.js";
import TopBar from "./components/TopBar.jsx";
import FindJobsView from "./views/FindJobsView.jsx";
import AssessView from "./views/AssessView.jsx";
import ApplicationsView from "./views/ApplicationsView.jsx";
import RunsView from "./views/RunsView.jsx";
import SettingsView from "./views/SettingsView.jsx";
import PdfView from "./views/PdfView.jsx";
import AnswersStoriesView from "./views/AnswersStoriesView.jsx";
import MasterView from "./views/MasterView.jsx";
import { JOBS_HASH, SETTINGS_HASH, navViewFor, navigate, postingHash, routeFor, useHashRoute } from "./routing.js";

function useConfig() {
  const [state, setState] = useState({ loading: true, config: null, error: null });

  const reload = useCallback(() => {
    setState({ loading: true, config: null, error: null });
    getConfig()
      .then((config) => setState({ loading: false, config, error: null }))
      .catch((error) => setState({ loading: false, config: null, error: error.message || String(error) }));
  }, []);

  useEffect(reload, [reload]);

  // uat-batch2: re-read without the "loading" state (which unmounts the
  // views that wait for a config): the selected profile changed, and with
  // it the resume and the config digest a run sends.
  const refresh = useCallback(
    () =>
      getConfig()
        .then((config) => setState({ loading: false, config, error: null }))
        .catch(() => {
          /* the config already shown stays; a run's own 409 reloads it */
        }),
    [],
  );

  return { ...state, reload, refresh };
}

// S2-B: GET /api/setup either returns saved prefs (200) or a 404
// prefs_missing carrying a pre-fill derived from find-jobs.json (see
// present_api.py's _handle_get_setup). `prefsMissing` distinguishes "first
// run, show the interview before anything else" from "prefs saved,
// interview only reachable via Settings".
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

  // 0.1.11.2: re-read the saved prefs without the loading state (a loading state unmounts the page that saved them).
  const refresh = useCallback(() => {
    getSetup()
      .then((response) => setState({ loading: false, prefs: response.prefs, prefill: null, prefsMissing: false, error: null }))
      .catch(() => {});
  }, []);

  useEffect(reload, [reload]);

  return { ...state, reload, refresh };
}

// P9 (v0.1.9): App.jsx routes across the app views. Q4a-nav: the tab row,
// the profile card and the "Preferences" button are gone; one persistent
// top bar (components/TopBar.jsx) and routing.js's ROUTES table drive
// everything:
//
//   jobs / job / run   FindJobsView (mounted on EVERY route so the job
//                      model survives; it draws nothing on the other
//                      routes). 0.1.10.7 M4b: Jobs is by posting (JobsView,
//                      no run); a run page is a past run, history only
//   assessments /      FindJobsView too (uat-bug-016): the on-demand
//   assessment         assessments, newest first, and their job page
//   applications       ApplicationsView: the jobs that are applied or
//                      beyond (GET /api/applications, uat-bug-018)
//   runs               RunsView: past runs, read-only history (GET /api/runs?profile_id=…)
//   settings           SettingsView (preferences + wizard launch, profiles,
//                      discover, add company)
//   assess             AssessView; its AssessResponse is handed to
//                      FindJobsView and its job page opens
//                      (#/assessments/<id>; #/jobs/<id> when the address
//                      assessed is a run posting's)
//
// uat-bug-018: there is no Questions view. "Needs your answers" is a job
// state; FindJobsView counts the jobs in it for Jobs and for Assessments
// (`needAnswers`) and the top bar shows the two counts.
//
// The first-run interview (P9b's SetupWizard) still shows before anything
// else when no prefs exist (CHANGE #2); editing prefs later renders the
// same wizard in place of the app, from Settings.
export default function App() {
  const { loading: configLoading, config: configResponse, error: configError, reload: reloadConfig, refresh: refreshConfig } = useConfig();
  // True between a profile switch and the config re-read that follows it:
  // the config shown is still the previous profile's.
  const [configStale, setConfigStale] = useState(false);
  const setupState = useSetup();
  const profilesState = useProfiles();
  const route = useHashRoute();
  const runsState = useRuns(profilesState.selectedProfileId);
  const applicationsState = useApplications();

  const [editingSetup, setEditingSetup] = useState(false);
  // The last "+ Assess a job" response, handed to FindJobsView's job model.
  const [assessedItem, setAssessedItem] = useState(null);
  // uat-batch2-r1: the postings of the runs loaded so far (FindJobsView
  // fills it). An assessment of one of them lives under Jobs; any other
  // under Assessments (routing.postingHash).
  const [runPostingIds, setRunPostingIds] = useState(() => new Set());
  // uat-bug-018: how many jobs need the operator's answers, per list.
  const [needAnswers, setNeedAnswers] = useState({ jobs: 0, assessments: 0 });

  // S2-B: `gigai scout run` opens this UI; if no discovery prefs exist yet,
  // the interview shows first, ahead of every other view (CHANGE #2).
  const showFirstRunInterview = !setupState.loading && setupState.prefsMissing && !setupState.error;

  const selectedProfile = profilesState.profiles.find((profile) => profile.profile_id === profilesState.selectedProfileId) || null;

  // Q4a-nav: an unknown hash lands on Jobs and the address bar says so.
  useEffect(() => {
    if (!route.known) {
      window.location.replace(`${window.location.pathname}${window.location.search}${JOBS_HASH}`);
    }
  }, [route.known]);

  // Each route names the tab; a job/run page keeps its section's name
  // (an assessment's job page reads "Assessments").
  useEffect(() => {
    const entry = routeFor(route.view === "assessment" ? navViewFor(route.view) : route.view);
    document.title = entry && route.view !== "jobs" ? `Scout · ${entry.label}` : "Scout";
  }, [route.view]);

  // Every view starts at the top (a job page also does this on its own
  // when its id changes).
  useEffect(() => {
    if (route.view !== "job" && route.view !== "assessment") {
      window.scrollTo(0, 0);
    }
  }, [route.view]);

  function handleSelectProfile(profileId) {
    profilesState
      .switchTo(profileId)
      .then(() => {
        setConfigStale(true);
        return refreshConfig().finally(() => setConfigStale(false));
      })
      .catch(() => {
        /* surfaced via profilesState.error on the next reload; the switcher
           itself stays on the previous selection rather than guessing. */
      });
  }

  const handleAssessed = useCallback(
    (response) => {
      setAssessedItem(response);
      navigate(postingHash(response, runPostingIds));
    },
    [runPostingIds],
  );

  // 0110-046: a one-time notice under the top bar ("We removed your contact
  // lines; ..." after the wizard stored a resume). Dismissed, it is gone.
  const [notice, setNotice] = useState(null);

  // 0110-046: the one-time contact cleanup's report, shown once (counts only),
  // then marked shown. A failed read shows nothing; the CLI's `gigai scout
  // privacy` prints the same report.
  useEffect(() => {
    let live = true;
    getPrivacyCleanup()
      .then((report) => {
        const text = cleanupNotice(report);
        if (live && text) {
          setNotice(text);
          putPrivacyCleanupShown().catch(() => {});
        }
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  const wizardDone = (done) => {
    setNotice((done && done.contactRemoved) || null);
    setEditingSetup(false);
    setupState.reload();
    // P0-2: PUT /api/setup also rewrites find-jobs.json, so the config --
    // and its config_digest that a run POST sends -- goes stale the moment
    // the wizard's Finish save succeeds.
    reloadConfig();
    profilesState.reload();
  };

  if (setupState.loading) {
    return (
      <div className="app-loading">
        <p>Loading setup…</p>
      </div>
    );
  }

  if (setupState.error) {
    return (
      <div>
        <div className="callout danger">
          Could not load setup: {setupState.error}{" "}
          <button className="button small secondary" onClick={setupState.reload}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (showFirstRunInterview) {
    return (
      <SetupWizard
        onDone={(done) => {
          wizardDone(done);
          navigate(JOBS_HASH);
        }}
      />
    );
  }

  if (editingSetup && setupState.prefs) {
    return (
      <SetupWizard
        onDone={(done) => {
          wizardDone(done);
          // uat-bug-048: an empty store lands on Jobs (step 1 highlighted there).
          getSourcesUpdate()
            .then((status) => navigate(finishLanding(status, { fromSettings: true }).toJobs ? JOBS_HASH : SETTINGS_HASH))
            .catch(() => navigate(SETTINGS_HASH));
        }}
        onCancel={() => setEditingSetup(false)}
      />
    );
  }

  return (
    <div className="app">
      <TopBar
        currentView={route.view}
        needAnswers={needAnswers}
        profiles={profilesState.profiles}
        selectedProfileId={profilesState.selectedProfileId}
        onSelectProfile={handleSelectProfile}
        profilesLoading={profilesState.loading}
        profilesError={profilesState.error}
      />

      <main className="app-main" data-view={route.view}>
        {notice && (
          <div className="callout info" role="status" data-role="app-notice">
            {notice}{" "}
            <button type="button" className="button small secondary" onClick={() => setNotice(null)}>
              Dismiss
            </button>
          </div>
        )}
        {profilesState.error && <div className="callout danger">Could not load profiles: {profilesState.error}</div>}

        {configError && route.view !== "settings" && (
          <div className="callout danger">
            Could not load configuration: {configError}{" "}
            <button className="button small secondary" onClick={reloadConfig}>
              Retry
            </button>
          </div>
        )}

        {/* Mounted on every route (see the header comment); renders only
            for jobs / job / run / assessments / assessment. */}
        {!configLoading && (
          <FindJobsView
            route={route}
            profile={selectedProfile}
            profilesLoading={profilesState.loading}
            config={configResponse}
            runsState={runsState}
            applicationsState={applicationsState}
            externalQuickItem={assessedItem}
            runPostingIds={runPostingIds}
            onRunPostingIds={setRunPostingIds}
            onNeedAnswers={setNeedAnswers}
            onSelectProfile={handleSelectProfile}
          />
        )}

        {route.view === "applications" && (
          <ApplicationsView
            applications={applicationsState.applications}
            loading={applicationsState.loading}
            error={applicationsState.error}
            reload={applicationsState.reload}
          />
        )}

        {route.view === "runs" && (
          <RunsView profile={selectedProfile} runs={runsState.runs} loading={runsState.loading} error={runsState.error} reload={runsState.reload} />
        )}

        {route.view === "settings" && (
          <SettingsView
            config={configResponse}
            configLoading={configLoading}
            configError={configError}
            reloadConfig={reloadConfig}
            prefs={setupState.prefs}
            onEditPreferences={() => setEditingSetup(true)}
            onPrefsSaved={() => {
              setupState.refresh();
            }}
            profiles={profilesState.profiles}
            selectedProfileId={profilesState.selectedProfileId}
            onSelectProfile={handleSelectProfile}
            reloadProfiles={profilesState.reload}
            defaultSearchSettings={profilesState.defaultSearchSettings}
          />
        )}

        {route.view === "pdf" && <PdfView target={route.params.pdfTarget} />}

        {route.view === "answers" && <AnswersStoriesView />}

        {route.view === "master" && <MasterView reloadProfiles={profilesState.reload} />}

        {route.view === "assess" && (
          <AssessView
            profiles={profilesState.profiles}
            selectedProfileId={profilesState.selectedProfileId}
            config={configStale ? null : configResponse}
            configLoading={configLoading || configStale}
            onAssessed={handleAssessed}
          />
        )}
      </main>
    </div>
  );
}

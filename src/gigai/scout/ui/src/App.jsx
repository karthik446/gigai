import { useCallback, useEffect, useState } from "react";
import { ApiError, getConfig, getSetup } from "./api.js";
import { useApplications, usePendingQuestions, useProfiles, useRuns } from "./hooks.js";
import SetupWizard from "./wizard/index.js";
import TopBar from "./components/TopBar.jsx";
import FindJobsView from "./views/FindJobsView.jsx";
import AssessView from "./views/AssessView.jsx";
import PendingAnswersView from "./views/PendingAnswersView.jsx";
import ApplicationsView from "./views/ApplicationsView.jsx";
import RunsView from "./views/RunsView.jsx";
import SettingsView from "./views/SettingsView.jsx";
import { JOBS_HASH, SETTINGS_HASH, jobHash, navigate, routeFor, useHashRoute } from "./routing.js";

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

  useEffect(reload, [reload]);

  return { ...state, reload };
}

// P9 (v0.1.9): App.jsx routes across the app views. Q4a-nav: the tab row,
// the profile card and the "Preferences" button are gone; one persistent
// top bar (components/TopBar.jsx) and routing.js's ROUTES table drive
// everything:
//
//   jobs / job / run   FindJobsView (mounted on EVERY route so a live run
//                      keeps polling and the grid's state survives; it
//                      draws nothing on the other routes)
//   questions          PendingAnswersView, fed by usePendingQuestions (the
//                      same state as the top bar's badge)
//   applications       ApplicationsView (GET /api/applications)
//   runs               RunsView (GET /api/runs?profile_id=…)
//   settings           SettingsView (preferences + wizard launch, profiles,
//                      discover, add company)
//   assess             AssessView; its AssessResponse is handed to
//                      FindJobsView and its job page opens (#/jobs/<id>)
//
// The first-run interview (P9b's SetupWizard) still shows before anything
// else when no prefs exist (CHANGE #2); editing prefs later renders the
// same wizard in place of the app, from Settings.
export default function App() {
  const { loading: configLoading, config: configResponse, error: configError, reload: reloadConfig } = useConfig();
  const setupState = useSetup();
  const profilesState = useProfiles();
  const route = useHashRoute();
  const questions = usePendingQuestions();
  const runsState = useRuns(profilesState.selectedProfileId);
  const applicationsState = useApplications();

  const [editingSetup, setEditingSetup] = useState(false);
  // The last "+ Assess a job" response, handed to FindJobsView's job model.
  const [assessedItem, setAssessedItem] = useState(null);

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

  // Each route names the tab; a job/run page keeps its section's name.
  useEffect(() => {
    const entry = routeFor(route.view);
    document.title = entry && route.view !== "jobs" ? `Scout · ${entry.label}` : "Scout";
  }, [route.view]);

  // Every view starts at the top (a job page also does this on its own
  // when its id changes).
  useEffect(() => {
    if (route.view !== "job") {
      window.scrollTo(0, 0);
    }
  }, [route.view]);

  function handleSelectProfile(profileId) {
    profilesState.switchTo(profileId).catch(() => {
      /* surfaced via profilesState.error on the next reload; the switcher
         itself stays on the previous selection rather than guessing. */
    });
  }

  const handleAssessed = useCallback(
    (response) => {
      setAssessedItem(response);
      questions.reload();
      navigate(jobHash(response.job.job_identity));
    },
    [questions.reload],
  );

  const wizardDone = () => {
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
        onDone={() => {
          wizardDone();
          navigate(JOBS_HASH);
        }}
      />
    );
  }

  if (editingSetup && setupState.prefs) {
    return (
      <SetupWizard
        onDone={() => {
          wizardDone();
          navigate(SETTINGS_HASH);
        }}
        onCancel={() => setEditingSetup(false)}
      />
    );
  }

  return (
    <div className="app">
      <TopBar
        currentView={route.view}
        questionsCount={questions.count}
        profiles={profilesState.profiles}
        selectedProfileId={profilesState.selectedProfileId}
        onSelectProfile={handleSelectProfile}
        profilesLoading={profilesState.loading}
        profilesError={profilesState.error}
      />

      <main className="app-main" data-view={route.view}>
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
            for jobs / job / run. */}
        {!configLoading && (
          <FindJobsView
            route={route}
            profile={selectedProfile}
            config={configResponse}
            reloadConfig={reloadConfig}
            runsState={runsState}
            applicationsState={applicationsState}
            questions={questions}
            externalQuickItem={assessedItem}
          />
        )}

        {route.view === "questions" && <PendingAnswersView pending={questions} />}

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
            profiles={profilesState.profiles}
            selectedProfileId={profilesState.selectedProfileId}
            onSelectProfile={handleSelectProfile}
            reloadProfiles={profilesState.reload}
          />
        )}

        {route.view === "assess" && (
          <AssessView profiles={profilesState.profiles} selectedProfileId={profilesState.selectedProfileId} onAssessed={handleAssessed} />
        )}
      </main>
    </div>
  );
}

import { useCallback, useEffect, useState } from "react";
import { ApiError, getConfig, getSetup } from "./api.js";
import { useProfiles } from "./hooks.js";
import SetupWizard from "./wizard/index.js";
import ProfileSwitcher from "./components/ProfileSwitcher.jsx";
import DashboardView from "./views/DashboardView.jsx";
import ProfilesView from "./views/ProfilesView.jsx";
import FindJobsView from "./views/FindJobsView.jsx";
import QuickAssessPanel from "./views/QuickAssessPanel.jsx";
import PendingAnswersView from "./views/PendingAnswersView.jsx";

const TABS = [
  { value: "dashboard", label: "Dashboard" },
  { value: "profiles", label: "Profiles" },
  { value: "findjobs", label: "Find jobs" },
  { value: "quickassess", label: "Quick assess" },
  { value: "answers", label: "Pending answers" },
];

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

// P9 (v0.1.9): App.jsx is now a router across F3's app views (dashboard /
// profiles / find jobs / quick assess / pending answers / setup) instead of
// the single-screen run flow it used to be. The run flow itself moved to
// views/FindJobsView.jsx (same polling logic, per-profile). The setup route
// renders P9b's <SetupWizard/> (ui/src/wizard/) in place of the old
// single-page SetupInterviewForm.
export default function App() {
  const { loading: configLoading, config: configResponse, error: configError, reload: reloadConfig } = useConfig();
  const setupState = useSetup();
  const profilesState = useProfiles();

  const [tab, setTab] = useState("dashboard");
  const [editingSetup, setEditingSetup] = useState(false);

  // S2-B: `gigai scout run` opens this UI; if no discovery prefs exist yet,
  // the interview shows first, ahead of every other view (CHANGE #2).
  const showFirstRunInterview = !setupState.loading && setupState.prefsMissing && !setupState.error;

  const selectedProfile = profilesState.profiles.find((profile) => profile.profile_id === profilesState.selectedProfileId) || null;

  function handleSelectProfile(profileId) {
    profilesState.switchTo(profileId).catch(() => {
      /* surfaced via profilesState.error on the next reload; the switcher
         itself stays on the previous selection rather than guessing. */
    });
  }

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
        <SetupWizard
          onDone={() => {
            setupState.reload();
            // P0-2: PUT /api/setup also rewrites find-jobs.json, so the
            // config -- and its config_digest that a run POST sends --
            // goes stale the moment the wizard's Finish save succeeds.
            reloadConfig();
            setTab("dashboard");
          }}
        />
      )}

      {editingSetup && setupState.prefs && (
        <SetupWizard
          onDone={() => {
            setEditingSetup(false);
            setupState.reload();
            reloadConfig();
            setTab("dashboard");
          }}
          onCancel={() => setEditingSetup(false)}
        />
      )}

      {/* The rest of the app is gated behind the first-run interview --
          CHANGE #2's "asked ONCE... shows the interview first". Once prefs
          exist it's never blocking again; editing happens via the
          Preferences link inside the Profiles view. */}
      {!showFirstRunInterview && !editingSetup && (
        <>
          <nav className="top-nav" aria-label="Main views">
            {TABS.map((item) => (
              <button
                key={item.value}
                type="button"
                className={tab === item.value ? "active" : ""}
                onClick={() => setTab(item.value)}
              >
                {item.label}
              </button>
            ))}
          </nav>

          <div className="panel" style={{ padding: "10px 16px" }}>
            {profilesState.loading && <p className="muted">Loading profiles…</p>}
            {profilesState.error && <div className="callout danger">Could not load profiles: {profilesState.error}</div>}
            {!profilesState.loading && !profilesState.error && (
              <ProfileSwitcher
                profiles={profilesState.profiles}
                selectedProfileId={profilesState.selectedProfileId}
                onSelect={handleSelectProfile}
              />
            )}
          </div>

          {configError && (
            <div className="callout danger">
              Could not load configuration: {configError}{" "}
              <button className="button small secondary" onClick={reloadConfig}>
                Retry
              </button>
            </div>
          )}

          {tab === "dashboard" && (
            <DashboardView
              profiles={profilesState.profiles}
              selectedProfileId={profilesState.selectedProfileId}
              onSelectProfile={handleSelectProfile}
              cadenceDays={setupState.prefs?.cadence_days}
            />
          )}

          {tab === "profiles" && (
            <ProfilesView
              profiles={profilesState.profiles}
              selectedProfileId={profilesState.selectedProfileId}
              onSelectProfile={handleSelectProfile}
              config={configResponse?.config}
              reloadProfiles={profilesState.reload}
            />
          )}

          {tab === "findjobs" && !configLoading && (
            <FindJobsView profile={selectedProfile} config={configResponse} reloadConfig={reloadConfig} />
          )}

          {tab === "quickassess" && (
            <QuickAssessPanel profiles={profilesState.profiles} selectedProfileId={profilesState.selectedProfileId} />
          )}

          {tab === "answers" && <PendingAnswersView />}

          {setupState.prefs && (
            <div className="panel">
              <button className="button small secondary" onClick={() => setEditingSetup(true)}>
                Preferences
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

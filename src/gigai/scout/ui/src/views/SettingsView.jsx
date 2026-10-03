import ConfigPanel from "../components/ConfigPanel.jsx";
import AddCompanyForm from "../components/AddCompanyForm.jsx";
import SourcesUpdatePanel from "../components/SourcesUpdatePanel.jsx";
import BackgroundUpdatesPanel from "../components/BackgroundUpdatesPanel.jsx";
import ModelMetricsPanel from "../components/ModelMetricsPanel.jsx";
import ExaSourceToggle from "../components/ExaSourceToggle.jsx";
import ProfilesView from "./ProfilesView.jsx";
import { ANSWERS_HASH } from "../routing.js";

export default function SettingsView({
  config,
  configLoading,
  configError,
  reloadConfig,
  prefs,
  onEditPreferences,
  profiles,
  selectedProfileId,
  onSelectProfile,
  reloadProfiles,
  defaultSearchSettings,
}) {
  return (
    <div>
      {/* uat-batch2 (N11-C): "Update sources" is the one action on this page
          the operator comes back for (the Jobs page links here for it), so
          it is the first panel. */}
      <SourcesUpdatePanel />

      {/* 0110-024/025/026: what runs by itself (automatic checks, model
          tagging, the starter snapshot), right under the action it follows. */}
      <BackgroundUpdatesPanel />

      {/* 0.1.10.7 E: what the model calls have taken, per model (hidden until one is recorded). */}
      <ModelMetricsPanel />

      <section className="panel" id="settings-preferences">
        <h2>Preferences</h2>
        <p className="muted">
          Job titles, location, visa, publication window and the resume every run and assessment use. Editing them
          re-opens the setup wizard. Location, work mode, countries and the publication window here are the default
          profile's; every other profile has its own, under Profiles below.
        </p>
        {configLoading && <p className="muted">Loading configuration…</p>}
        {configError && (
          <div className="callout danger">
            Could not load configuration: {configError}{" "}
            <button className="button small secondary" onClick={reloadConfig}>
              Retry
            </button>
          </div>
        )}
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
          <button className="button secondary" onClick={onEditPreferences} disabled={!prefs}>
            Edit preferences
          </button>
          {!prefs && <span className="muted">Preferences are saved by the setup wizard.</span>}
        </div>
      </section>

      {config && <ExaSourceToggle config={config.config} reloadConfig={reloadConfig} />}

      <div id="settings-profiles">
        <ProfilesView
          profiles={profiles}
          selectedProfileId={selectedProfileId}
          onSelectProfile={onSelectProfile}
          config={config ? config.config : null}
          reloadProfiles={reloadProfiles}
          defaultSearchSettings={defaultSearchSettings}
        />
      </div>

      {/* 0.1.10.7 C: answers and stories are the user's, not a profile's: one
          read-only page for all of them. */}
      <section className="panel" id="settings-answers-stories">
        <h2>Answers and stories</h2>
        <p className="muted">
          What you told your agent, kept once and used for every profile: the answers postings asked for and the stories behind them.
        </p>
        <a className="button secondary" data-action="open-answers-stories" href={ANSWERS_HASH}>
          Open answers and stories
        </a>
      </section>

      <div id="settings-add-company">
        <AddCompanyForm />
      </div>
    </div>
  );
}

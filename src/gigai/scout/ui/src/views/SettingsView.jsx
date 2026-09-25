import ConfigPanel from "../components/ConfigPanel.jsx";
import AddCompanyForm from "../components/AddCompanyForm.jsx";
import ProfilesView from "./ProfilesView.jsx";

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
}) {
  return (
    <div>
      <section className="panel" id="settings-preferences">
        <h2>Preferences</h2>
        <p className="muted">
          Job titles, location, visa, publication window and the resume every run and assessment use. Editing them
          re-opens the setup wizard.
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

      <div id="settings-profiles">
        <ProfilesView
          profiles={profiles}
          selectedProfileId={selectedProfileId}
          onSelectProfile={onSelectProfile}
          config={config ? config.config : null}
          reloadProfiles={reloadProfiles}
        />
      </div>

      <div id="settings-add-company">
        <AddCompanyForm />
      </div>
    </div>
  );
}

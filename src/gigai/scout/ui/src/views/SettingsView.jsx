import ConfigPanel from "../components/ConfigPanel.jsx";
import CompanyListsPanel from "../components/CompanyListsPanel.jsx";
import AddCompanyForm from "../components/AddCompanyForm.jsx";
import SourcesUpdatePanel from "../components/SourcesUpdatePanel.jsx";
import BackgroundUpdatesPanel from "../components/BackgroundUpdatesPanel.jsx";
import ModelMetricsPanel from "../components/ModelMetricsPanel.jsx";
import PipelinePanel from "../components/PipelinePanel.jsx";
import ExaSourceToggle from "../components/ExaSourceToggle.jsx";
import JobsFolderPanel from "../components/JobsFolderPanel.jsx";
import ResumesFolderPanel from "../components/ResumesFolderPanel.jsx";
import ProfilesView from "./ProfilesView.jsx";
import { ANSWERS_HASH, MASTER_HASH } from "../routing.js";
import MasterSelections from "../components/MasterSelections.jsx";

export default function SettingsView({
  config,
  configLoading,
  configError,
  reloadConfig,
  prefs,
  onEditPreferences,
  onPrefsSaved,
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

      {/* 0.1.10.7 M4b: the background pipeline: lanes, today's calls against their caps, approvals, settings, last errors. */}
      <PipelinePanel />

      {/* 0.1.10.7 E: what the model calls have taken, per model (hidden until one is recorded). */}
      <ModelMetricsPanel />

      <section className="panel" id="settings-preferences">
        <h2>Preferences</h2>
        <p className="muted">
          Job titles, location, visa, publication window and the resume every search and assessment use. Editing them
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

      {/* 0.1.11.4 J3: the visible folder new picks go to, one folder per job. */}
      <JobsFolderPanel />

      {/* 0110-10-05 A: the older, flat folder (master.md and PDFs made without a header); legacy for job resumes. */}
      <ResumesFolderPanel />

      <div id="settings-profiles">
        <ProfilesView
          profiles={profiles}
          selectedProfileId={selectedProfileId}
          onSelectProfile={onSelectProfile}
          config={config ? config.config : null}
          reloadProfiles={reloadProfiles}
          defaultSearchSettings={defaultSearchSettings}
        />
        {/* 0.1.10.9 master P5: each profile's selection of the master ("3 new master lines: refresh?"); nothing without a master. */}
        <MasterSelections version={profiles.length} onChanged={reloadProfiles} />
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

      {/* 0.1.10.9 master P5: the master resume is the user's, not a profile's. */}
      <section className="panel" id="settings-master-resume">
        <h2>Master resume</h2>
        <p className="muted">
          One resume with every role, bullet, project and skill you have. Each profile shows a selection of it, and the resume for a job
          is picked from all of it.
        </p>
        <a className="button secondary" data-action="open-master-resume" href={MASTER_HASH}>
          Open master resume
        </a>
      </section>

      {/* 0.1.11.2 (UAT-008): the company lists left onboarding; they are edited here, add-by-URL right under them. */}
      <CompanyListsPanel prefs={prefs} onSaved={onPrefsSaved} />

      <div id="settings-add-company">
        <AddCompanyForm />
      </div>
    </div>
  );
}

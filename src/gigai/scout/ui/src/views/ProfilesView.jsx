import ResumeWarning from "../components/ResumeWarning.jsx";
import ResumeDisplayPanel from "../components/ResumeDisplayPanel.jsx";
import NewProfileResume from "../components/NewProfileResume.jsx";
import ProfileSearchSettings from "../components/ProfileSearchSettings.jsx";
import { settingsSummary } from "../profileSettingsModel.js";
import { storeResume } from "../wizard/wizardApi.js";
import {
  canCreateProfile,
  createProfileWithResume,
  existingChoices,
  initialNewResume,
  resumeDescription,
  sharedResumeNotes,
} from "../profileResumeModel.js";
import { useEffect, useState } from "react";
import { draftSettingsLine } from "../freeSearchModel.js";
import { dropProfileDraft, pendingProfileDraft } from "../freeSearchStore.js";
import TagListInput from "../components/TagListInput.jsx";
import { archiveProfile, createProfile, deleteProfile, updateProfile } from "../api.js";
import { deleteBlockedReason, deleteConfirmText } from "../profileDeleteModel.js";
import { useRuns } from "../hooks.js";
import { relativeTimeLabel } from "../display.js";
import { modelTargetLabel } from "../modelTargets.js";
import { isAssessAll } from "../assessAllModel.js";

// P9/P9c (F3): Profiles.
//
// DROPPED from the mockup (no backing API, still, as of P9c):
//  - Extracted stack / seniority chips: F2's wizard resume-extraction step
//    (S27) is not part of this packet's scope and ProfileRecord carries no
//    stack/seniority fields (profiles.py's `_profile_to_json`: profile_id,
//    revision, label, state, origin, resume_ref, titles, titles_to_avoid,
//    queries, content_digest, created_at, updated_at -- nothing else).
//    Operator decision (0.1.9): dropped for this version.
//  - Rename: PUT /api/profiles/{id} can change label, but the task's own
//    Profiles view spec lists rename as a distinct action with no defined
//    request shape agreed anywhere in this packet's APIs beyond "edit
//    label via PUT" -- implemented here as exactly that (label-only PUT),
//    not dropped, since the API genuinely supports it.
//
// P9c: Run history table now wired to GET /api/runs?profile_id=<this
// profile>, newest first -- "cost"/"duration" still have no source field
// (mockups/README.md's open question #6) and stay out; date/found/
// assessed/matched are real.
//
// KEPT, on real data: profile list + resume_ref/titles/queries (GET
// /api/profiles), create (POST), edit titles/label (PUT), archive (POST
// .../archive) -- all exactly per find_jobs/api/profiles.py's shapes.
// "Shared across all profiles" reuses GET /api/config, since visa/
// cadence/budget/model all live in find-jobs.json, not per profile (S25).
// 0110-022: location, work mode, countries and the posted window are per
// profile (ProfileSearchSettings); the default profile's are the setup's.
// 0.1.11.7 FS2: "Save this search as a profile" (Jobs, "Search all jobs") opens the new-profile form with the typed
// titles and a name filled in, and says what the profile starts with and that the profile rule can list more than
// the search did. The resume is still chosen here; nothing is created before "Create profile".
function RunHistoryTable({ profileId }) {
  const { loading, runs, error } = useRuns(profileId);
  if (loading) {
    return <p className="muted">Loading run history…</p>;
  }
  if (error) {
    return <div className="callout danger">Could not load run history: {error}</div>;
  }
  if (runs.length === 0) {
    return <p className="muted">No runs yet for this profile.</p>;
  }
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Date</th>
          <th>Found</th>
          <th>New</th>
          <th>Assessed</th>
          <th>Matched</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((run) => (
          <tr key={run.run_id}>
            <td>{relativeTimeLabel(run.created_at)}</td>
            <td>{run.counts.found}</td>
            <td>{run.counts.new}</td>
            <td>{run.counts.assessed}</td>
            <td>{run.counts.matched}</td>
            <td>{run.status}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function ProfilesView({ profiles, selectedProfileId, onSelectProfile, config, reloadProfiles, defaultSearchSettings }) {
  const selected = profiles.find((profile) => profile.profile_id === selectedProfileId) || null;
  const sharedNotes = sharedResumeNotes(profiles);

  const [searchDraft, setSearchDraft] = useState(() => pendingProfileDraft());
  const [creating, setCreating] = useState(() => Boolean(searchDraft));
  const [newLabel, setNewLabel] = useState(() => (searchDraft ? searchDraft.label : ""));
  const [newTitles, setNewTitles] = useState(() => (searchDraft ? searchDraft.titles : []));
  // The form a search opened is brought into view (after the app's own scroll to the top of a new page).
  useEffect(() => {
    if (!searchDraft) {
      return undefined;
    }
    dropProfileDraft();
    const timer = setTimeout(() => {
      const field = document.getElementById("new-profile-label");
      if (field) {
        field.scrollIntoView({ block: "center" });
        field.focus({ preventScroll: true });
      }
    }, 0);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const [newResume, setNewResume] = useState(initialNewResume());
  const [resumeBlocked, setResumeBlocked] = useState(false);
  const [createError, setCreateError] = useState(null);
  const [createSaving, setCreateSaving] = useState(false);

  const [editingTitles, setEditingTitles] = useState(null);
  const [titlesSaving, setTitlesSaving] = useState(false);
  const [titlesError, setTitlesError] = useState(null);

  const [archiving, setArchiving] = useState(false);
  const [archiveError, setArchiveError] = useState(null);
  const [archiveNote, setArchiveNote] = useState(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  // 0110-046: "We removed your contact lines; ..." after a resume was stored.
  const [createNote, setCreateNote] = useState(null);

  async function handleCreate(event) {
    event.preventDefault();
    if (!canCreateProfile({ label: newLabel, titles: newTitles, resume: newResume }) || resumeBlocked) {
      return;
    }
    setCreateSaving(true);
    setCreateError(null);
    try {
      const created = await createProfileWithResume({ label: newLabel, titles: newTitles, resume: newResume }, { storeResume, createProfile });
      setCreateNote(created.contactRemoved);
      setNewLabel("");
      setNewTitles([]);
      setNewResume(initialNewResume());
      setCreating(false);
      setSearchDraft(null);
      reloadProfiles();
    } catch (error) {
      setCreateError(error.message || String(error));
    } finally {
      setCreateSaving(false);
    }
  }

  async function handleSaveTitles() {
    if (!selected || !editingTitles) {
      return;
    }
    setTitlesSaving(true);
    setTitlesError(null);
    try {
      await updateProfile(selected.profile_id, { titles: editingTitles });
      setEditingTitles(null);
      reloadProfiles();
    } catch (error) {
      setTitlesError(error.message || String(error));
    } finally {
      setTitlesSaving(false);
    }
  }

  async function handleDelete() {
    if (!selected) {
      return;
    }
    const blocked = deleteBlockedReason(profiles, selected);
    if (blocked) {
      setDeleteError(blocked);
      setConfirmingDelete(false);
      return;
    }
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteProfile(selected.profile_id);
      setConfirmingDelete(false);
      // The server already selected the default; the reload shows it.
      reloadProfiles();
    } catch (error) {
      setDeleteError(error.message || String(error));
    } finally {
      setDeleting(false);
    }
  }

  async function handleArchive() {
    if (!selected) {
      return;
    }
    const replacement = profiles.find((profile) => profile.profile_id !== selected.profile_id);
    if (!replacement) {
      setArchiveNote("This is the only profile; add another one first (archive requires a replacement).");
      return;
    }
    setArchiving(true);
    setArchiveError(null);
    try {
      await archiveProfile(selected.profile_id, replacement.profile_id);
      reloadProfiles();
      onSelectProfile(replacement.profile_id);
    } catch (error) {
      setArchiveError(error.message || String(error));
    } finally {
      setArchiving(false);
    }
  }

  return (
    <div>
      <section className="panel">
        <h2>Profiles</h2>
        <p className="muted">
          An interested profile pairs one resume with job titles/queries. Every profile but the default one also has its
          own location, work mode, countries and posted window.
        </p>

        <div className="profile-list">
          {profiles.map((profile) => (
            <div key={profile.profile_id} className="action-item">
              <div>
                <div style={{ fontWeight: 600 }}>{profile.label}</div>
                <div className="muted" style={{ fontSize: "0.8rem" }}>
                  {profile.titles.join(", ") || "no titles set"}
                </div>
                <div className="muted" style={{ fontSize: "0.8rem" }}>{resumeDescription(profile, config)}</div>
                <div className="muted" style={{ fontSize: "0.8rem" }} data-role="profile-search-summary">
                  {settingsSummary(profile, defaultSearchSettings)}
                </div>
                {sharedNotes[profile.profile_id] && (
                  <div className="callout warn" role="note" data-role="shared-resume-warning" style={{ fontSize: "0.8rem" }}>
                    {sharedNotes[profile.profile_id]}
                  </div>
                )}
              </div>
              <button className="button small secondary" onClick={() => onSelectProfile(profile.profile_id)}>
                {profile.profile_id === selectedProfileId ? "Selected" : "Select"}
              </button>
            </div>
          ))}
        </div>

        {creating ? (
          <form onSubmit={handleCreate} style={{ marginTop: 12 }} data-testid="new-profile-form" data-from-search={searchDraft ? "true" : undefined}>
            {searchDraft && (
              <div className="callout info" data-testid="profile-from-search">
                From your search: the titles below are the ones you typed. {searchDraft.note} <span data-role="profile-from-search-settings">{draftSettingsLine(searchDraft)}</span>{" "}
                Choose its resume below; nothing is created until you press Create profile.
              </div>
            )}
            <div className="form-group">
              <label className="form-label" htmlFor="new-profile-label">
                Label
              </label>
              <input
                id="new-profile-label"
                type="text"
                className="text-input"
                value={newLabel}
                onChange={(event) => setNewLabel(event.target.value)}
                placeholder="e.g. Staff AI engineer"
              />
            </div>
            <TagListInput
              id="new-profile-titles"
              label="Job titles"
              values={newTitles}
              onChange={setNewTitles}
              placeholder="staff ai engineer, staff backend engineer…"
            />
            <NewProfileResume
              value={newResume}
              onChange={setNewResume}
              choices={existingChoices(profiles, config)}
              modelTarget={config ? config.default_model_target : undefined}
              onBlockedChange={setResumeBlocked}
            />
            <p className="muted" style={{ fontSize: "0.8rem" }}>
              This profile gets the resume you give here; it does not use the selected profile's resume unless you choose
              it under "Choose existing". It starts with the default profile's location, work mode, countries and posted
              window; change them in the profile's detail after creating it.
            </p>
            {createError && <div className="callout danger">{createError}</div>}
            <div className="actions">
              <button
                type="button"
                className="button secondary"
                onClick={() => {
                  setCreating(false);
                  setSearchDraft(null);
                }}
                disabled={createSaving}
              >
                Cancel
              </button>
              <button type="submit" className="button" disabled={createSaving || resumeBlocked || !canCreateProfile({ label: newLabel, titles: newTitles, resume: newResume })}>
                {createSaving ? "Creating…" : "Create profile"}
              </button>
            </div>
          </form>
        ) : (
          <div className="card-actions" style={{ marginTop: 10 }}>
            {createNote && (
              <div className="callout info" data-role="contact-removed">
                {createNote}
              </div>
            )}
            <button className="button small secondary" data-action="add-profile" onClick={() => { setCreateNote(null); setCreating(true); }}>
              + Add profile
            </button>
          </div>
        )}
      </section>

      {selected && (
        <section className="panel">
          <h2>
            Profile detail <span className="muted">{selected.label}</span>
          </h2>

          <h3>Resume</h3>
          <div className="action-item">
            <div>
              <div style={{ fontWeight: 500 }}>{selected.resume_ref.record_id}</div>
              <small className="muted">revision {selected.resume_ref.revision_id}</small>
            </div>
          </div>
          <ResumeWarning modelTarget={config ? config.default_model_target : undefined} />
          <p className="muted" style={{ fontSize: "0.8rem" }}>
            Replace via <code>gigai scout resume add &lt;file&gt; --profile {selected.profile_id}</code>.
          </p>

          <h3>Job titles / queries</h3>
          {editingTitles ? (
            <>
              <TagListInput id="edit-profile-titles" label="Titles" values={editingTitles} onChange={setEditingTitles} />
              {titlesError && <div className="callout danger">{titlesError}</div>}
              <div className="actions">
                <button className="button secondary small" onClick={() => setEditingTitles(null)} disabled={titlesSaving}>
                  Cancel
                </button>
                <button className="button small" onClick={handleSaveTitles} disabled={titlesSaving}>
                  {titlesSaving ? "Saving…" : "Save titles"}
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="tag-list">
                {selected.titles.map((title) => (
                  <span className="tag" key={title}>
                    {title}
                  </span>
                ))}
              </div>
              <div className="card-actions" style={{ marginTop: 8 }}>
                <button className="button small secondary" onClick={() => setEditingTitles([...selected.titles])}>
                  Edit titles
                </button>
              </div>
            </>
          )}

          <ProfileSearchSettings
            key={selected.profile_id}
            profile={selected}
            defaults={defaultSearchSettings}
            onSaved={reloadProfiles}
          />
        </section>
      )}

      {selected && <ResumeDisplayPanel profileId={selected.profile_id} />}

      {selected && (
        <section className="panel">
          <h3>Run history</h3>
          <RunHistoryTable profileId={selected.profile_id} />

          <div className="card-actions" style={{ marginTop: 14 }}>
            <button className="button small danger-outline" onClick={handleArchive} disabled={archiving}>
              {archiving ? "Archiving…" : "Archive"}
            </button>
          </div>
          <div className="card-actions" style={{ marginTop: 8 }}>
            {confirmingDelete ? (
              <>
                <div className="callout danger">{deleteConfirmText(profiles, selected)}</div>
                <button className="button small danger-outline" data-action="confirm-delete-profile" onClick={handleDelete} disabled={deleting}>
                  {deleting ? "Deleting…" : "Delete profile"}
                </button>
                <button className="button small" data-action="cancel-delete-profile" onClick={() => setConfirmingDelete(false)} disabled={deleting}>
                  Cancel
                </button>
              </>
            ) : (
              <button
                className="button small danger-outline"
                data-action="delete-profile"
                onClick={() => {
                  setDeleteError(deleteBlockedReason(profiles, selected));
                  setConfirmingDelete(deleteBlockedReason(profiles, selected) === null);
                }}
              >
                Delete
              </button>
            )}
          </div>
          {deleteError && <div className="callout info">{deleteError}</div>}
          {archiveError && <div className="callout danger">{archiveError}</div>}
          {archiveNote && <div className="callout info">{archiveNote}</div>}
        </section>
      )}

      {config && (
        <section className="panel">
          <h2>Shared across all profiles</h2>
          <p className="muted">
            Visa, company excludes/watch, discovery cadence/budget, and model are one set of preferences for every
            profile.
          </p>
          <div className="field-row">
            <div className="field">
              <div className="label">Visa sponsorship required</div>
              <div className="value">{config.visa_sponsorship_required ? "yes" : "no"}</div>
            </div>
          </div>
          <div className="field-row">
            <div className="field">
              <div className="label">Default full assessments</div>
              <div className="value">{isAssessAll(config.default_assess_cap) ? "All new postings" : config.default_assess_cap}</div>
            </div>
            <div className="field">
              <div className="label">Model target</div>
              <div className="value" title={config.default_model_target}>{modelTargetLabel(config.default_model_target)}</div>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}

import { useState } from "react";
import CountryPicker from "./CountryPicker.jsx";
import { updateProfile } from "../api.js";
import { MAX_AGE_DAYS_MAXIMUM, WORK_MODES } from "../wizard/wizardState.js";
import {
  clearSettingsBody,
  hasOwnSettings,
  initialSettingsForm,
  settingsBody,
  settingsFormError,
  settingsSource,
  settingsSummary,
} from "../profileSettingsModel.js";

// 0110-022: the profile form's search settings. Every profile but the default
// one has its own location, work mode, countries and posted window; the
// default profile's are the setup settings (Preferences). The location is the
// search area a run and its ranking use, not the Resume display contact line.
export default function ProfileSearchSettings({ profile, defaults, onSaved }) {
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const formError = form ? settingsFormError(form) : null;

  async function save(body) {
    setSaving(true);
    setError(null);
    try {
      await updateProfile(profile.profile_id, body);
      setForm(null);
      onSaved();
    } catch (failure) {
      setError(failure.message || String(failure));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div data-role="profile-search-settings">
      <h3>Location, work mode, countries and posted window</h3>
      <p className="muted" style={{ fontSize: "0.8rem" }}>
        {settingsSource(profile)}
      </p>
      {form ? (
        <>
          <div className="form-group">
            <label className="form-label" htmlFor="profile-location">
              City / area
            </label>
            <input
              id="profile-location"
              type="text"
              className="text-input"
              value={form.location}
              placeholder="Denver, CO"
              onChange={(event) => setForm({ ...form, location: event.target.value })}
            />
            <small className="muted">Used to search and rank jobs for this profile. It is not printed on the resume.</small>
          </div>
          <div className="form-group">
            <span className="form-label">Work mode</span>
            <div className="toggle-row" role="radiogroup" aria-label="Work mode">
              {WORK_MODES.map((mode) => (
                <button
                  key={mode.value}
                  type="button"
                  className={`toggle-button${form.workMode === mode.value ? " active" : ""}`}
                  aria-pressed={form.workMode === mode.value}
                  onClick={() => setForm({ ...form, workMode: mode.value })}
                >
                  {mode.label}
                </button>
              ))}
            </div>
          </div>
          <CountryPicker
            id="profile-countries"
            label="Countries"
            values={form.countries}
            onChange={(values) => setForm({ ...form, countries: values })}
          />
          <div className="form-group">
            <label className="form-label" htmlFor="profile-max-age-days">
              Only postings from the last … days
            </label>
            <input
              id="profile-max-age-days"
              type="number"
              className="text-input"
              min={1}
              max={MAX_AGE_DAYS_MAXIMUM}
              value={form.maxAgeDays}
              placeholder="same as the default profile"
              onChange={(event) => setForm({ ...form, maxAgeDays: event.target.value })}
            />
          </div>
          {(formError || error) && <div className="callout danger">{formError || error}</div>}
          <div className="actions">
            <button className="button secondary small" onClick={() => setForm(null)} disabled={saving}>
              Cancel
            </button>
            <button className="button small" onClick={() => save(settingsBody(form))} disabled={saving || Boolean(formError)}>
              {saving ? "Saving…" : "Save settings"}
            </button>
          </div>
        </>
      ) : (
        <>
          <div className="value">{settingsSummary(profile, defaults)}</div>
          {error && <div className="callout danger">{error}</div>}
          {!profile.is_default && (
            <div className="card-actions" style={{ marginTop: 8 }}>
              <button className="button small secondary" onClick={() => setForm(initialSettingsForm(profile, defaults))}>
                Edit settings
              </button>
              {hasOwnSettings(profile) && (
                <button className="button small secondary" onClick={() => save(clearSettingsBody())} disabled={saving}>
                  Use the default profile's
                </button>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

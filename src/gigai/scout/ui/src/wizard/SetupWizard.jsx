import { useEffect, useState } from "react";
import {
  ApiError,
  createProfile,
  extractResume,
  getConfig,
  getProfiles,
  getSetup,
  putSetup,
  updateProfile,
} from "./wizardApi.js";
import { existingResumes, initialFields, profileBody, screenIsComplete, setupBody } from "./wizardState.js";
import StepIndicator from "./StepIndicator.jsx";
import ResumeScreen from "./ResumeScreen.jsx";
import TargetScreen from "./TargetScreen.jsx";
import CompaniesScreen from "./CompaniesScreen.jsx";
import FinishScreen from "./FinishScreen.jsx";
import "./wizard.css";

const TOTAL_STEPS = 4;

// P9b (F2): the 4-screen setup wizard that replaces the one-page interview.
//
//   1. Resume + profile  -> POST /api/resume/extract (stack / seniority / titles)
//   2. Target            -> titles (seeded from 1), countries, work mode, visa
//   3. Companies         -> exclude / always watch (catalog: S26, not in 0.1.9)
//   4. Discovery + finish-> cadence, budget, review, then Finish saves:
//        POST /api/profiles  (or PUT /api/profiles/{id} for the selected one)
//        PUT  /api/setup
//      and shows the exact `gigai` commands with the real profile id.
//
// `onDone(result)` fires when the operator presses Done after a successful
// save; `result` is `{profile}` (the saved profile's public shape).
//
// P9c: `onCancel` (optional) fires when the operator backs out before
// saving -- nothing is submitted, and the caller decides where "back to
// where the user came from" means (App.jsx's edit-preferences path closes
// the wizard and returns to whichever tab was showing). Only rendered once
// a save hasn't already succeeded (`!saved`), matching Back/Next's own
// `Boolean(saved)` gating -- once Finish has saved, Cancel would be
// confusing ("cancel" a save that already happened), so FinishScreen's own
// Done button is the only way forward from there.
export default function SetupWizard({ onDone, onCancel }) {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [existingPrefs, setExistingPrefs] = useState(null);
  const [profiles, setProfiles] = useState([]);
  const [selectedProfile, setSelectedProfile] = useState(null);
  const [config, setConfig] = useState(null);
  const [fields, setFields] = useState(null);

  const [step, setStep] = useState(1);
  const [extracting, setExtracting] = useState(false);
  const [extractError, setExtractError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [fieldErrors, setFieldErrors] = useState(null);
  const [saved, setSaved] = useState(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      // Each read is tolerant on its own: a fresh install has no prefs
      // (404 prefs_missing + prefill), may have no find-jobs.json yet
      // (404 config_missing) and no profile at all -- none of that should
      // stop the wizard from opening.
      let prefs = null;
      let savedPrefs = null;
      try {
        const response = await getSetup();
        prefs = response.prefs;
        savedPrefs = response.prefs;
      } catch (error) {
        if (error instanceof ApiError && error.status === 404 && error.code === "prefs_missing") {
          prefs = error.prefill || {};
        } else {
          if (!cancelled) {
            setLoadError(error.message || String(error));
            setLoading(false);
          }
          return;
        }
      }
      let configResponse = null;
      try {
        configResponse = await getConfig();
      } catch {
        configResponse = null;
      }
      let profileList = [];
      let selected = null;
      try {
        const response = await getProfiles();
        profileList = response.profiles || [];
        selected = profileList.find((item) => item.profile_id === response.selected_profile_id) || null;
      } catch {
        profileList = [];
        selected = null;
      }
      if (cancelled) {
        return;
      }
      setExistingPrefs(savedPrefs);
      setConfig(configResponse);
      setProfiles(profileList);
      setSelectedProfile(selected);
      setFields(initialFields({ prefs, config: configResponse, selectedProfile: selected }));
      setLoading(false);
    }

    load();
    return () => {
      cancelled = true;
    };
  }, []);

  function setField(name, value) {
    setFields((prev) => ({ ...prev, [name]: value }));
  }

  async function handleExtract() {
    setExtracting(true);
    setExtractError(null);
    try {
      const body = { model_target: fields.modelTarget };
      if (fields.resumeMode === "existing") {
        // The chosen resume belongs to a profile; the route reads that
        // profile's pinned resume by profile id (never the raw record).
        const owner = profiles.find(
          (item) =>
            item.resume_ref &&
            item.resume_ref.record_id === fields.existingRef.record_id &&
            item.resume_ref.revision_id === fields.existingRef.revision_id,
        );
        if (!owner) {
          throw new ApiError(0, "That resume is no longer attached to a profile. Reload and choose again.");
        }
        body.profile_id = owner.profile_id;
      } else {
        body.resume_text = fields.resumeText;
      }
      const result = await extractResume(body);
      setFields((prev) => ({
        ...prev,
        extraction: result,
        stack: result.stack || [],
        seniority: result.seniority ? [result.seniority] : [],
        suggestedTitles: result.titles || [],
        titlesSeeded: false,
      }));
    } catch (error) {
      setExtractError(error.message || String(error));
    } finally {
      setExtracting(false);
    }
  }

  function goNext() {
    if (step === 1 && !fields.titlesSeeded && fields.suggestedTitles.length > 0) {
      // Mockup default: screen 1's suggested titles copy into screen 2 on
      // the first visit only (suggested first, then any title the profile
      // already had that the model did not suggest); later edits on either
      // screen stay separate.
      setFields((prev) => {
        const seen = new Set(prev.suggestedTitles.map((title) => title.toLowerCase()));
        const kept = prev.titles.filter((title) => !seen.has(title.toLowerCase()));
        return { ...prev, titles: [...prev.suggestedTitles, ...kept], titlesSeeded: true };
      });
    }
    setStep((current) => Math.min(TOTAL_STEPS, current + 1));
    window.scrollTo({ top: 0 });
  }

  function goBack() {
    setStep((current) => Math.max(1, current - 1));
    window.scrollTo({ top: 0 });
  }

  async function handleFinish() {
    setSaving(true);
    setSaveError(null);
    setFieldErrors(null);
    try {
      const body = profileBody(fields);
      const profileResponse =
        fields.profileMode === "update" && selectedProfile
          ? await updateProfile(selectedProfile.profile_id, body)
          : await createProfile(body);
      const prefsResponse = await putSetup(setupBody(fields, existingPrefs));
      setExistingPrefs(prefsResponse.prefs);
      setSaved({ profile: profileResponse.profile });
    } catch (error) {
      if (error instanceof ApiError && error.status === 400 && error.field_errors) {
        const errors = error.field_errors;
        setFieldErrors(errors);
        if (errors.resume_record_id && /no default resume/i.test(errors.resume_record_id)) {
          setSaveError(
            "No resume is stored yet, so the profile cannot be created from pasted text alone. " +
              "Save your resume as a file, run `gigai scout resume add <file>`, then run this wizard again.",
          );
        } else {
          setSaveError(`Some fields were rejected: ${Object.values(errors).join(" ")}`);
        }
      } else {
        setSaveError(error.message || String(error));
      }
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="setup-wizard">
        <p>Loading setup…</p>
      </div>
    );
  }
  if (loadError) {
    return (
      <div className="setup-wizard">
        <div className="callout danger">Could not load setup: {loadError}</div>
      </div>
    );
  }

  const resumes = existingResumes({ profiles, config });
  const complete = screenIsComplete(step, fields);
  const isLast = step === TOTAL_STEPS;

  return (
    <div className="setup-wizard">
      <StepIndicator step={step} />

      {step === 1 && (
        <ResumeScreen
          fields={fields}
          setField={setField}
          selectedProfile={selectedProfile}
          resumes={resumes}
          extracting={extracting}
          extractError={extractError}
          onExtract={handleExtract}
        />
      )}
      {step === 2 && <TargetScreen fields={fields} setField={setField} fieldErrors={fieldErrors} />}
      {step === 3 && <CompaniesScreen fields={fields} setField={setField} fieldErrors={fieldErrors} />}
      {step === 4 && (
        <FinishScreen
          fields={fields}
          setField={setField}
          resumes={resumes}
          fieldErrors={fieldErrors}
          saveError={saveError}
          saved={saved}
          onDone={() => onDone && onDone(saved)}
        />
      )}

      <div className="wz-actions">
        <div>
          <button type="button" className="button secondary" onClick={goBack} disabled={step === 1 || saving || Boolean(saved)}>
            Back
          </button>
          {onCancel && !saved && (
            <button type="button" className="button secondary" onClick={onCancel} disabled={saving} style={{ marginLeft: 8 }}>
              Cancel
            </button>
          )}
        </div>
        <div className="wz-right">
          {!isLast && (
            <button type="button" className="button" onClick={goNext} disabled={!complete || extracting}>
              Next
            </button>
          )}
          {isLast && !saved && (
            <button type="button" className="button" onClick={handleFinish} disabled={!complete || saving}>
              {saving ? "Saving…" : "Finish"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

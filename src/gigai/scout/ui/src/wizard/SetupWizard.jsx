import { useEffect, useState } from "react";
import {
  ApiError,
  createProfile,
  extractResume,
  getConfig,
  getProfiles,
  getResumeDisplay,
  getSecretsStatus,
  getSetup,
  putResumeDisplay,
  putSetup,
  selectProfile,
  storeResume,
  updateProfile,
} from "./wizardApi.js";
import { draftFromResponse } from "../resumeDisplayModel.js";
import { finishSetup } from "./wizardFinish.js";
import { existingResumes, extractBody, initialFields, screenIsComplete, setupHints } from "./wizardState.js";
import StepIndicator from "./StepIndicator.jsx";
import ResumeScreen from "./ResumeScreen.jsx";
import ResumeDisplayScreen from "./ResumeDisplayScreen.jsx";
import TargetScreen from "./TargetScreen.jsx";
import FinishScreen from "./FinishScreen.jsx";
import "./wizard.css";

const TOTAL_STEPS = 4;

// P9b (F2): the 4-screen setup wizard that replaces the one-page interview.
//
//   1. Resume + profile  -> POST /api/resume/extract (stack / seniority / titles)
//   2. Resume display    -> the PDF title/layout (0110-013; GET /api/resume-display,
//      skippable; saved by Finish with PUT /api/resume-display)
//   3. Target            -> titles (seeded from 1), countries, work mode, visa
//   4. Review            -> the review table, what is still missing (an
//      unset key, Ollama: wizardState.setupHints), then Finish
//      (wizardFinish.js) stores the resume, saves the profile with it and
//      the preferences (A2: no discovery cadence or budget is asked;
//      Discover is hidden in 0.1.9). 0.1.11.2: there is no Companies step; the
//      exclude / always-watch lists and add-by-URL live in Settings (CompanyListsPanel,
//      AddCompanyForm), and a save here passes the saved lists through unchanged:
//        POST /api/resumes   (pasted text or the uploaded file; uat-bug-020)
//        POST /api/profiles  (or PUT /api/profiles/{id})
//        POST /api/profiles/selection (a first profile only)
//        PUT  /api/setup
//        PUT  /api/resume-display (unless the Resume display step was skipped)
//
// `onDone(result)` fires as soon as Finish has saved; `result` is
// `{profile}` (the saved profile's public shape). The caller decides where
// that leads: the first run opens Jobs, which says to run Update sources
// while no company postings are stored; an edit returns to Settings.
//
// P9c: `onCancel` (optional) fires when the operator backs out before
// saving -- nothing is submitted, and the caller decides where "back to
// where the user came from" means (App.jsx's edit-preferences path closes
// the wizard and returns to whichever tab was showing). Only rendered once
// a save hasn't already succeeded (`!saved`), matching Back/Next's own
// `Boolean(saved)` gating.
export default function SetupWizard({ onDone, onCancel }) {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [existingPrefs, setExistingPrefs] = useState(null);
  const [profiles, setProfiles] = useState([]);
  const [selectedProfile, setSelectedProfile] = useState(null);
  const [config, setConfig] = useState(null);
  // GET /api/secrets/status' `keys`, or null when it could not be read.
  const [keys, setKeys] = useState(null);
  const [fields, setFields] = useState(null);

  const [step, setStep] = useState(1);
  const [extracting, setExtracting] = useState(false);
  const [extractError, setExtractError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [fieldErrors, setFieldErrors] = useState(null);
  const [saved, setSaved] = useState(null);
  const [displayError, setDisplayError] = useState(null);

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
      let keyState = null;
      try {
        const response = await getSecretsStatus();
        keyState = response.keys || null;
      } catch {
        keyState = null;
      }
      if (cancelled) {
        return;
      }
      setExistingPrefs(savedPrefs);
      setConfig(configResponse);
      setKeys(keyState);
      setProfiles(profileList);
      setSelectedProfile(selected);
      setFields(
        initialFields({
          prefs,
          config: configResponse,
          selectedProfile: selected,
          resumes: existingResumes({ profiles: profileList, config: configResponse }),
        }),
      );
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
      // A stored resume no profile uses yet (A1) is sent as `resume_ref`.
      const result = await extractResume(extractBody(fields, profiles));
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

  // 0110-013: the Resume display draft loads once, when the step is first
  // opened, as on the profile page. A failed read leaves the draft empty so
  // the step still works.
  useEffect(() => {
    if (step !== 2 || !fields || fields.display) {
      return undefined;
    }
    let live = true;
    setDisplayError(null);
    getResumeDisplay(selectedProfile ? selectedProfile.profile_id : undefined)
      .then((response) => live && setField("display", draftFromResponse(response)))
      .catch((error) => {
        if (live) {
          setDisplayError(error.message || String(error));
          setField("display", draftFromResponse(null));
        }
      });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, Boolean(fields)]);

  function changeDisplay(patch) {
    setFields((prev) => ({ ...prev, display: { ...prev.display, ...patch }, displaySkipped: false }));
  }

  function skipDisplay() {
    setField("displaySkipped", true);
    goNext();
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
      const result = await finishSetup(
        { fields, selectedProfile, existingPrefs },
        { storeResume, getProfiles, createProfile, updateProfile, selectProfile, putSetup, putResumeDisplay },
      );
      setExistingPrefs(result.prefs);
      const done = { profile: result.profile, contactRemoved: result.contactRemoved };
      setSaved(done);
      if (onDone) {
        onDone(done);
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 400 && error.field_errors) {
        const errors = error.field_errors;
        setFieldErrors(errors);
        setSaveError(`Some fields were rejected: ${Object.values(errors).join(" ")}`);
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
      {step === 2 && <ResumeDisplayScreen fields={fields} loadError={displayError} onChange={changeDisplay} onSkip={skipDisplay} />}
      {step === 3 && <TargetScreen fields={fields} setField={setField} fieldErrors={fieldErrors} />}
      {step === 4 && (
        <FinishScreen
          fields={fields}
          resumes={resumes}
          hints={setupHints({
            modelTarget: fields.modelTarget,
            keys,
            extraction: fields.extraction,
            exaEnabled: Boolean(config && config.config && config.config.sources && config.config.sources.exa),
          })}
          fieldErrors={fieldErrors}
          saveError={saveError}
          saved={saved}
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

// uat-bug-020: what Finish does, as one function with no React in it (the
// api is handed in, so the same code runs against the real server in the
// api-e2e journey and against a recording fake in the model test).
//
//   1. POST /api/resumes            the pasted text or the uploaded file
//                                   becomes a stored resume (skipped when an
//                                   existing resume was chosen)
//   2. GET  /api/profiles           what exists NOW: storing the first
//                                   resume can make the default profile
//   3. PUT  /api/profiles/{id}      or POST /api/profiles, with that resume
//                                   (wizardState.profileToUpdate decides)
//   4. POST /api/profiles/selection only when no profile is selected yet: a
//                                   first profile is the selected one; with
//                                   one already selected, a new profile is
//                                   not (screen 1 says so)
//   5. PUT  /api/setup              the preferences, with `profile_id`: the
//                                   profile step 3 saved. The titles go to
//                                   that profile only (uat-bug-024: without
//                                   it they went to the SELECTED profile,
//                                   so "Create a new profile" overwrote the
//                                   selected profile's titles)
//   6. PUT  /api/resume-display     the Resume display step (0110-013), only
//                                   when it was opened and not skipped
//
// Pressing Finish again after a failure at any step repeats the steps
// without making a second resume (the server stores a resume once per
// content) or a second profile (step 3 finds the one already saved).
import { displayBody, profileBody, profileToUpdate, resumeBody, setupBody } from "./wizardState.js";

export async function finishSetup({ fields, selectedProfile, existingPrefs }, api) {
  let resumeRef = null;
  const resume = resumeBody(fields);
  if (resume) {
    const stored = await api.storeResume(resume);
    resumeRef = stored.resume_ref;
  } else if (fields.existingRef) {
    resumeRef = { record_id: fields.existingRef.record_id, revision_id: fields.existingRef.revision_id };
  }

  const current = await api.getProfiles();
  const profileId = profileToUpdate({
    fields,
    selectedAtLoad: selectedProfile,
    profiles: current.profiles,
    selectedProfileId: current.selected_profile_id,
    resumeRef,
  });
  const body = profileBody(fields, resumeRef);
  const saved = profileId ? await api.updateProfile(profileId, body) : await api.createProfile(body);
  const profile = saved.profile;

  let selectedProfileId = current.selected_profile_id || null;
  if (!selectedProfileId) {
    const selection = await api.selectProfile(profile.profile_id);
    selectedProfileId = selection.selected_profile_id;
  }

  const prefsResponse = await api.putSetup({ ...setupBody(fields, existingPrefs), profile_id: profile.profile_id });
  // 0110-013: the Resume display step, saved for the profile just written
  // (its title is this profile's). Only PUT /api/resume-display gets it.
  const display = displayBody(fields, profile.profile_id);
  if (display) {
    await api.putResumeDisplay(display);
  }
  return { profile, selectedProfileId, resumeRef, prefs: prefsResponse.prefs };
}

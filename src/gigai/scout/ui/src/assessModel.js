// uat-batch2 (uat-bug-013): the Assess page assesses against the ACTIVE
// profile (the top bar's) unless the operator chooses otherwise, and says
// so. Pure functions (no React), pinned by the node-backed static test.
//
//   mode "active"   the top bar's profile; its resume label comes from
//                   GET /api/config (resume_label / resume_created_at, the
//                   selected profile's own resume)
//   mode "profile"  another profile, picked under "Use another profile or
//                   paste a resume"; GET /api/profiles carries its resume's
//                   ids only, so the line names the profile
//   mode "text"     a pasted resume, used for this call only
import { resumeDisplayLabel } from "./display.js";

export const ASSESS_MODES = ["active", "profile", "text"];

function profileById(profiles, profileId) {
  return (profiles || []).find((profile) => profile && profile.profile_id === profileId) || null;
}

// The profiles the secondary choice offers: every one but the active one.
export function otherProfiles(profiles, activeProfileId) {
  return (profiles || []).filter((profile) => profile && profile.profile_id !== activeProfileId);
}

// {who, resume, line, tooltip}: "Assessing against: default · resume.md ·
// added Sep 23". `config` is the GET /api/config response for the active
// profile (null while it loads, `configLoading`, or when it failed: then the
// line names the profile and no resume, never a guess).
export function assessingAgainst({ mode, profiles, activeProfileId, otherProfileId, config, configLoading = false }) {
  let who = "";
  let resume = "";
  let tooltip;
  if (mode === "text") {
    who = "a pasted resume";
    resume = "used for this assessment only, never stored";
  } else if (mode === "profile") {
    const profile = profileById(profiles, otherProfileId);
    who = profile ? profile.label : "choose a profile";
    resume = profile ? "its own resume" : "";
    tooltip = profile && profile.resume_ref ? `${profile.resume_ref.record_id} (${profile.resume_ref.revision_id})` : undefined;
  } else {
    const profile = profileById(profiles, activeProfileId);
    who = profile ? profile.label : "the selected profile";
    if (config) {
      resume = resumeDisplayLabel(config.resume_preview, config.resume_label, config.resume_created_at) || "no resume added yet";
      tooltip = config.resume_preview ? `${config.resume_preview.record_id} (${config.resume_preview.revision_id})` : undefined;
    } else if (configLoading) {
      resume = "loading its resume…";
    }
  }
  return { who, resume, tooltip, line: `Assessing against: ${resume ? `${who} · ${resume}` : who}` };
}

// The `resume` half of POST /api/assess. The active profile is sent by id
// (what the page says is what is assessed); null only when no profile is
// known, which the server reads as "the selected profile".
export function assessResume({ mode, activeProfileId, otherProfileId, resumeText }) {
  if (mode === "text") {
    return { resume_text: resumeText };
  }
  if (mode === "profile") {
    return { profile_id: otherProfileId || null };
  }
  return { profile_id: activeProfileId || null };
}

export function canAssess({ jobMode, jobUrl, jobText, mode, otherProfileId, resumeText }) {
  const hasJob = Boolean(jobMode === "url" ? (jobUrl || "").trim() : (jobText || "").trim());
  if (!hasJob) {
    return false;
  }
  if (mode === "text") {
    return Boolean((resumeText || "").trim());
  }
  if (mode === "profile") {
    return Boolean(otherProfileId);
  }
  return true;
}

// What leaves the machine for one assessment: the resume goes to the model
// target that assesses (and ranks) and nowhere else; a pasted one is used
// for this call only and never stored.
export function assessPrivacyNote(mode) {
  return mode === "text"
    ? "A pasted resume is sent to the assessment model for this call only and is never stored."
    : "Your resume is sent to your own model target only, the one that assesses and ranks postings.";
}

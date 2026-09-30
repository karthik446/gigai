// 0110-016: a new profile in Settings gets its OWN resume. Pure (no React, no
// fetch: the API is passed in), so the words and rules are testable under node.
//
// Reuses the wizard's pieces: resumeBody (paste / upload -> POST /api/resumes'
// body), existingResumes (the "Choose existing" list) and the same create
// route (POST /api/profiles with resume_record_id / resume_revision_id). A
// new profile never silently takes the selected profile's resume: with no
// resume chosen, nothing is created. Nothing here goes to a model; the text
// is only stored.
import { existingResumes, resumeBody } from "./wizard/wizardState.js";

export const RESUME_MODES = [
  ["paste", "Paste text"],
  ["upload", "Upload file"],
  ["existing", "Choose existing"],
];

// Default to Paste (an own resume), never "existing".
export function initialNewResume() {
  return { mode: "paste", text: "", uploadName: null, uploadBase64: null, existingRef: null };
}

// The wizard's `fields` shape for resumeBody / hasResume.
function asFields(resume) {
  return {
    resumeMode: resume.mode,
    resumeText: resume.text,
    uploadName: resume.uploadName,
    uploadBase64: resume.uploadBase64,
    existingRef: resume.existingRef,
  };
}

export function hasNewResume(resume) {
  if (resume.mode === "existing") {
    return Boolean(resume.existingRef);
  }
  return resume.text.trim().length > 0;
}

export function canCreateProfile({ label, titles, resume }) {
  return label.trim().length > 0 && titles.length > 0 && hasNewResume(resume);
}

// The resume the user picked, stored first (paste / upload), then the profile
// with that resume pinned. Returns the created profile.
export async function createProfileWithResume({ label, titles, resume }, api) {
  if (!canCreateProfile({ label, titles, resume })) {
    throw new Error("A profile needs a name, job titles and a resume.");
  }
  let ref;
  const body = resumeBody(asFields(resume));
  if (body) {
    const stored = await api.storeResume(body);
    ref = stored.resume_ref;
  } else {
    ref = resume.existingRef;
  }
  const saved = await api.createProfile({
    label: label.trim(),
    titles,
    resume_record_id: ref.record_id,
    resume_revision_id: ref.revision_id,
  });
  return saved.profile;
}

function sameResume(a, b) {
  return Boolean(a && b && a.record_id === b.record_id && a.revision_id === b.revision_id);
}

// What the list says about a profile's resume. Only the newest stored resume
// has a file name in the API (GET /api/config resume_label); any other is
// described without an id.
export function resumeDescription(profile, config) {
  const preview = config && config.resume_preview;
  if (config && config.resume_label && sameResume(preview, profile.resume_ref)) {
    return `Resume: ${config.resume_label}`;
  }
  return "Resume: its own stored resume";
}

// profile_id -> label of the EARLIER profile that pins the same resume, for
// every active profile that shares one. Archived profiles are ignored.
export function sharedResumeNotes(profiles) {
  const notes = {};
  const firstByKey = new Map();
  for (const profile of profiles || []) {
    const ref = profile.resume_ref;
    if (profile.state === "archived" || !ref || !ref.record_id) {
      continue;
    }
    const key = `${ref.record_id}/${ref.revision_id}`;
    if (firstByKey.has(key)) {
      notes[profile.profile_id] = `Uses the same resume as ${firstByKey.get(key)}: intended?`;
    } else {
      firstByKey.set(key, profile.label);
    }
  }
  return notes;
}

// The "Choose existing" list for the form.
export function existingChoices(profiles, config) {
  return existingResumes({ profiles, config });
}

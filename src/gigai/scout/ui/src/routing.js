// Q4a / Q4a-nav (v0.1.9): hash routing for the whole Scout app.
//
// ROUTES below is the ONE route table: App.jsx renders a view per `view`,
// the top bar links to `path`, and tests/api_e2e/test_ui_routes_static.py
// checks that every entry has a view in App.jsx and that the served bundle
// carries every path. Add a route here first; nothing else invents a hash.
//
//   #/jobs               Jobs by posting (home; also the empty hash): the
//                        stored postings every active profile matches, no
//                        run (0.1.10.7 M4b)
//   #/jobs/<encoded id>  one posting's job page, keyed by its normalized_url
//                        (or a quick assessment's job_identity)
//   #/assessments        every on-demand assessment, newest first (uat-bug-016)
//   #/assessments/<id>   the same job page, opened from Assessments (keyed by
//                        the assessment's job_identity)
//   #/applications       every job that is applied or beyond (uat-bug-018)
//   #/runs               past find-jobs runs of the selected profile: history,
//                        read-only (no run is started from the UI any more)
//   #/runs/<run id>      one past run: status, node receipts, its postings
//   #/settings           preferences, resume, setup wizard, discover, add company
//   #/assess             "+ Assess a job", reached from Assessments (its
//                        result opens #/assessments/<id>)
//   #/answers            0.1.10.7 C: Answers and stories, read-only (browse, the
//                        jobs that used each, delete); reached from Settings
//   #/pdf                0110-046: Generate PDF, where an agent's or the
//   #/pdf/<profile>/<job>  CLI's headerless PDF is finished (the link they
//                        print); markdown picked here, or the stored
//                        tailored resume for that profile and job
//
// Hash routes so the browser back/forward buttons work (every link is a
// plain <a href="#/…">), the server never sees a client route (the packaged
// UI is static files under api/static.py, no catch-all), and a deep link or
// a reload lands on the same view. An unknown hash falls back to the grid.
//
// uat-bug-018: there is no Questions page. "Needs your answers" is a job
// state: a filter chip on Jobs and on Assessments, and the count beside
// those two links. An old #/questions link is an unknown hash: it lands on
// Jobs.
import { useEffect, useState } from "react";
import { postingHome } from "./jobModel.js";

export const ROUTES = [
  { view: "jobs", path: "#/jobs", label: "Jobs", pattern: /^#\/jobs\/?$/ },
  { view: "job", path: "#/jobs/", label: "Job", pattern: /^#\/jobs\/(.+)$/, param: "jobId" },
  { view: "assessments", path: "#/assessments", label: "Assessments", pattern: /^#\/assessments\/?$/ },
  { view: "assessment", path: "#/assessments/", label: "Assessment", pattern: /^#\/assessments\/(.+)$/, param: "jobId" },
  { view: "applications", path: "#/applications", label: "Applications", pattern: /^#\/applications\/?$/ },
  { view: "runs", path: "#/runs", label: "Past runs", pattern: /^#\/runs\/?$/ },
  { view: "run", path: "#/runs/", label: "Past run", pattern: /^#\/runs\/(.+)$/, param: "runId" },
  { view: "settings", path: "#/settings", label: "Settings", pattern: /^#\/settings\/?$/ },
  { view: "assess", path: "#/assess", label: "Assess a job", pattern: /^#\/assess\/?$/ },
  { view: "pdf", path: "#/pdf", label: "Generate PDF", pattern: /^#\/pdf(?:\/(.*))?$/, param: "pdfTarget" },
  { view: "answers", path: "#/answers", label: "Answers and stories", pattern: /^#\/answers\/?$/ },
];

// The top bar's primary links, in order (the profile switcher and the
// Settings gear are laid out separately by TopBar.jsx).
export const NAV_VIEWS = ["jobs", "assessments", "applications", "runs"];

export const JOBS_HASH = "#/jobs";
export const ASSESSMENTS_HASH = "#/assessments";
export const APPLICATIONS_HASH = "#/applications";
export const RUNS_HASH = "#/runs";
export const SETTINGS_HASH = "#/settings";
export const ASSESS_HASH = "#/assess";
export const ANSWERS_HASH = "#/answers";
// Kept for the phase-1 callers (JobPage/JobCard): the grid's hash.
export const GRID_HASH = JOBS_HASH;

export function routeFor(view) {
  return ROUTES.find((route) => route.view === view) || null;
}

function decodeParam(raw) {
  try {
    return decodeURIComponent(raw);
  } catch {
    /* a hand-edited hash that isn't valid percent-encoding: match it raw */
    return raw;
  }
}

export function parseHash(hash) {
  const value = hash || "";
  if (value === "" || value === "#" || value === "#/") {
    return { view: "jobs", params: {}, known: true };
  }
  for (const route of ROUTES) {
    const match = route.pattern.exec(value);
    if (match) {
      const params = route.param && match[1] !== undefined ? { [route.param]: decodeParam(match[1]) } : {};
      return { view: route.view, params, known: true };
    }
  }
  return { view: "jobs", params: {}, known: false };
}

// 0110-046: "<profile_id>/<encoded job identity>" (the server's finish link) ->
// {profileId, jobIdentity}; "" or anything else -> nulls (the markdown form).
// The router has already decoded the hash once, so the job identity (a URL)
// arrives decoded; a profile id never holds a "/".
export function parsePdfTarget(target) {
  const value = typeof target === "string" ? target : "";
  const slash = value.indexOf("/");
  if (slash <= 0 || slash === value.length - 1) {
    return { profileId: null, jobIdentity: null };
  }
  return { profileId: value.slice(0, slash), jobIdentity: value.slice(slash + 1) };
}

export function pdfHash(profileId, jobIdentity) {
  return profileId && jobIdentity ? `#/pdf/${encodeURIComponent(profileId)}/${encodeURIComponent(jobIdentity)}` : "#/pdf";
}

export function jobHash(jobId) {
  return `#/jobs/${encodeURIComponent(jobId)}`;
}

// uat-bug-016: the job page of an on-demand assessment, opened from
// Assessments (the same JobPage; the hash only decides which top-bar tab is
// current and where "←" goes back to).
export function assessmentHash(jobId) {
  return `#/assessments/${encodeURIComponent(jobId)}`;
}

// uat-batch2-r1: a link to a stored assessment opens where its posting
// lives: a job page's under Jobs, an on-demand one's under Assessments
// (jobModel.postingHome: the item's `origin`, else `runPostingIds`, App.jsx's).
export function postingHash(item, runPostingIds) {
  const identity = item.job.job_identity;
  return postingHome(item, runPostingIds) === "jobs" ? jobHash(identity) : assessmentHash(identity);
}

export function runHash(runId) {
  return `#/runs/${encodeURIComponent(runId)}`;
}

// Pushes a history entry for `hash` (a no-op when already there).
export function navigate(hash) {
  if (window.location.hash !== hash) {
    window.location.hash = hash;
  }
}

// Which top-bar link is "current" for a route: a job page belongs to Jobs,
// a run page to Runs; an assessment's job page and the assess form belong
// to Assessments (uat-bug-016: "+ Assess a job" lives there).
export function navViewFor(view) {
  if (view === "job") {
    return "jobs";
  }
  if (view === "assessment" || view === "assess") {
    return "assessments";
  }
  if (view === "run") {
    return "runs";
  }
  return view;
}

export function useHashRoute() {
  const [route, setRoute] = useState(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

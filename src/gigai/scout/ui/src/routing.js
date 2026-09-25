// Q4a / Q4a-nav (v0.1.9): hash routing for the whole Scout app.
//
// ROUTES below is the ONE route table: App.jsx renders a view per `view`,
// the top bar links to `path`, and tests/api_e2e/test_ui_routes_static.py
// checks that every entry has a view in App.jsx and that the served bundle
// carries every path. Add a route here first; nothing else invents a hash.
//
//   #/jobs               the card grid (home; also the empty hash)
//   #/jobs/<encoded id>  one posting's job page, keyed by its normalized_url
//                        (or a quick assessment's job_identity)
//   #/questions          every open question across postings (pending answers)
//   #/applications       the application pipeline + every recorded event
//   #/runs               every find-jobs run for the selected profile
//   #/runs/<run id>      one run: status, node receipts, its postings
//   #/settings           preferences, resume, setup wizard, discover, add company
//   #/assess             "+ Assess a job" (its result opens #/jobs/<id>)
//
// Hash routes so the browser back/forward buttons work (every link is a
// plain <a href="#/…">), the server never sees a client route (the packaged
// UI is static files under api/static.py, no catch-all), and a deep link or
// a reload lands on the same view. An unknown hash falls back to the grid.
import { useEffect, useState } from "react";

export const ROUTES = [
  { view: "jobs", path: "#/jobs", label: "Jobs", pattern: /^#\/jobs\/?$/ },
  { view: "job", path: "#/jobs/", label: "Job", pattern: /^#\/jobs\/(.+)$/, param: "jobId" },
  { view: "questions", path: "#/questions", label: "Questions", pattern: /^#\/questions\/?$/ },
  { view: "applications", path: "#/applications", label: "Applications", pattern: /^#\/applications\/?$/ },
  { view: "runs", path: "#/runs", label: "Runs", pattern: /^#\/runs\/?$/ },
  { view: "run", path: "#/runs/", label: "Run", pattern: /^#\/runs\/(.+)$/, param: "runId" },
  { view: "settings", path: "#/settings", label: "Settings", pattern: /^#\/settings\/?$/ },
  { view: "assess", path: "#/assess", label: "Assess a job", pattern: /^#\/assess\/?$/ },
];

// The top bar's primary links, in order (the profile switcher and the
// Settings gear are laid out separately by TopBar.jsx).
export const NAV_VIEWS = ["jobs", "questions", "applications", "runs"];

export const JOBS_HASH = "#/jobs";
export const QUESTIONS_HASH = "#/questions";
export const APPLICATIONS_HASH = "#/applications";
export const RUNS_HASH = "#/runs";
export const SETTINGS_HASH = "#/settings";
export const ASSESS_HASH = "#/assess";
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
      const params = route.param ? { [route.param]: decodeParam(match[1]) } : {};
      return { view: route.view, params, known: true };
    }
  }
  return { view: "jobs", params: {}, known: false };
}

export function jobHash(jobId) {
  return `#/jobs/${encodeURIComponent(jobId)}`;
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
// a run page to Runs, the assess form to Jobs (it is Jobs' own action).
export function navViewFor(view) {
  if (view === "job" || view === "assess") {
    return "jobs";
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

// Q4a (v0.1.9): hash routing for the Find-jobs card grid and its job pages.
//
//   #/jobs                 the card grid (also the empty hash)
//   #/jobs/<encoded url>   one posting's job page, keyed by its normalized_url
//
// Hash routes so the browser back button works (a card is a plain <a href>
// that pushes a history entry; "← All postings" is another), the server
// never sees a client route (the packaged UI is static files under
// api/static.py, no catch-all), and a job page survives a reload as long as
// the run that holds the posting can be loaded again (FindJobsView reloads
// the newest run for the profile when nothing is loaded yet).
import { useEffect, useState } from "react";

const JOB_ROUTE = /^#\/jobs\/(.+)$/;

export function parseHash(hash) {
  const match = JOB_ROUTE.exec(hash || "");
  if (!match) {
    return { view: "grid", jobId: null };
  }
  let jobId = match[1];
  try {
    jobId = decodeURIComponent(jobId);
  } catch {
    /* a hand-edited hash that isn't valid percent-encoding: match it raw */
  }
  return { view: "job", jobId };
}

export function jobHash(normalizedUrl) {
  return `#/jobs/${encodeURIComponent(normalizedUrl)}`;
}

export const GRID_HASH = "#/jobs";

// Leaves a job page for the grid, pushing a history entry (so back returns
// to the job page). A no-op on the grid itself.
export function leaveJobPage() {
  if (parseHash(window.location.hash).view === "job") {
    window.location.hash = GRID_HASH;
  }
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

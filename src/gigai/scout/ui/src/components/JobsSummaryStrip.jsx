import { useMemo } from "react";
import { relativeTimeLabel } from "../display.js";
import { pipelineCounts } from "../applicationsModel.js";
import { APPLICATIONS_HASH, QUESTIONS_HASH, RUNS_HASH, runHash } from "../routing.js";

// Q4a-nav: the Dashboard's summary strip, now at the top of Jobs.
//
//   lastRun        GET /api/runs?profile_id=<selected>, newest entry (found /
//                  new / assessed / matched are the run's own counts;
//                  "matched" = Verdict.MATCHED_ABOVE_THRESHOLD)
//   questionsCount usePendingQuestions (the same count the top bar's badge shows)
//   applications   GET /api/applications rows, folded into the pipeline
//
// Every tile is a real number from one of those responses; a missing read
// shows "–", never a placeholder.
function Tile({ label, value, href, title }) {
  const body = (
    <>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
    </>
  );
  return href ? (
    <a className="stat-tile stat-link" href={href} title={title}>
      {body}
    </a>
  ) : (
    <div className="stat-tile" title={title}>
      {body}
    </div>
  );
}

export default function JobsSummaryStrip({ lastRun, runsLoading, questionsCount, applications, applicationsLoading }) {
  const counts = useMemo(() => pipelineCounts(applications || []), [applications]);
  const runValue = runsLoading ? "…" : lastRun ? relativeTimeLabel(lastRun.created_at) : "none yet";
  const count = (key) => (runsLoading ? "…" : lastRun ? lastRun.counts[key] : "–");
  const applied = applicationsLoading ? "…" : counts.applied + counts.interviewing + counts.offer;
  return (
    <div className="summary-strip" aria-label="Summary">
      <Tile label="Last run" value={runValue} href={lastRun ? runHash(lastRun.run_id) : RUNS_HASH} title={lastRun ? `Status: ${lastRun.status}` : undefined} />
      <Tile label="Found" value={count("found")} />
      <Tile label="New" value={count("new")} />
      <Tile label="Assessed" value={count("assessed")} />
      <Tile label="Matched" value={count("matched")} />
      <Tile label="Open questions" value={questionsCount === null || questionsCount === undefined ? "…" : questionsCount} href={QUESTIONS_HASH} />
      <Tile label="In progress" value={applied} href={APPLICATIONS_HASH} title="Applied, interviewing or offer" />
    </div>
  );
}

import { useMemo } from "react";
import { relativeTimeLabel } from "../display.js";
import { inProgressCount, needAnswersLabel } from "../jobStateModel.js";
import { APPLICATIONS_HASH, RUNS_HASH, runHash } from "../routing.js";

// Q4a-nav: the Dashboard's summary strip, now at the top of Jobs.
//
//   lastRun        GET /api/runs?profile_id=<selected>, newest entry (found /
//                  new / assessed / matched are the run's own counts;
//                  "matched" = Verdict.MATCHED_ABOVE_THRESHOLD)
//   needAnswersCount  how many of the shown run's jobs are in the state
//                  "Needs your answers" (uat-bug-018; the same count the top
//                  bar shows beside Jobs). Null while no run is shown.
//   applications   GET /api/applications rows; "In progress" counts the
//                  jobs whose state is applied, interview scheduled or
//                  offer received
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

export default function JobsSummaryStrip({ lastRun, runsLoading, needAnswersCount, applications, applicationsLoading }) {
  const inProgress = useMemo(() => inProgressCount(applications || []), [applications]);
  const runValue = runsLoading ? "…" : lastRun ? relativeTimeLabel(lastRun.created_at) : "none yet";
  const count = (key) => (runsLoading ? "…" : lastRun ? lastRun.counts[key] : "–");
  const applied = applicationsLoading ? "…" : inProgress;
  const waiting = needAnswersCount === null || needAnswersCount === undefined;
  return (
    <div className="summary-strip" aria-label="Summary">
      <Tile label="Last run" value={runValue} href={lastRun ? runHash(lastRun.run_id) : RUNS_HASH} title={lastRun ? `Status: ${lastRun.status}` : undefined} />
      <Tile label="Found" value={count("found")} />
      <Tile label="New" value={count("new")} />
      <Tile label="Assessed" value={count("assessed")} />
      <Tile label="Matched" value={count("matched")} />
      <Tile label="Need your answers" value={waiting ? "–" : needAnswersCount} title={waiting ? undefined : needAnswersLabel(needAnswersCount)} />
      <Tile label="In progress" value={applied} href={APPLICATIONS_HASH} title="Applied, interview scheduled or offer received" />
    </div>
  );
}

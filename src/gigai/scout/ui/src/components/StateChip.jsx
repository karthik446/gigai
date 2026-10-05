import { dateLabel } from "../jobModel.js";
import { isApplicationState, stateLabel } from "../jobStateModel.js";

// uat-bug-018: a job's state past its verdict, in words: "Resume ready",
// "Applied", "Interview scheduled", "Offer received", "Rejected",
// "Withdrawn". The verdict states (not assessed, needs your answers,
// matched, not a match) are the verdict chip's, so this draws nothing for
// them unless `always` is set. `showSince` adds the date the job entered
// the state, when the server gave one.
export default function StateChip({ state, always = false, showSince = false }) {
  const id = state && state.state;
  if (!id) {
    return null;
  }
  if (!always && id !== "tailored" && !isApplicationState(id)) {
    return null;
  }
  const since = showSince && state.since ? dateLabel(state.since) : null;
  return (
    <span className={`state-chip state-${id}`} data-state={id} title={state.since || undefined}>
      {stateLabel(id)}
      {since ? ` · ${since}` : ""}
    </span>
  );
}

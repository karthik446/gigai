// `status` is a matrix-row status (met|unmet|unclear|partial|gap, C9: used
// as-is for the CSS class) by default. P9: pass `kind="verdict"` to render
// the card-level Verdict enum (matched_above_threshold|pending_user_answers|
// not_a_match) with its own label instead -- same badge shape, different
// vocabulary and text.
import { isThinMatch, verdictLabel } from "../display.js";

const VERDICT_CLASS = {
  matched_above_threshold: "met",
  pending_user_answers: "unclear",
  not_a_match: "unmet",
};

export default function MatrixBadge({ status, kind, assessment }) {
  if (kind === "verdict") {
    // 0.1.11.2: a thin match (fewer than 4 requirement rows) is not drawn as a met verdict.
    const thin = isThinMatch(status, assessment);
    return <span className={`status-badge ${thin ? "unclear" : VERDICT_CLASS[status] || ""}`}>{verdictLabel(status, assessment) || status}</span>;
  }
  return <span className={`status-badge ${status}`}>{status}</span>;
}

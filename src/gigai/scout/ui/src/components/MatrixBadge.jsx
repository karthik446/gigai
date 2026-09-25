// `status` is a matrix-row status (met|unmet|unclear|partial|gap, C9: used
// as-is for the CSS class) by default. P9: pass `kind="verdict"` to render
// the card-level Verdict enum (matched_above_threshold|pending_user_answers|
// not_a_match) with its own label instead -- same badge shape, different
// vocabulary and text.
import { verdictLabel } from "../display.js";

const VERDICT_CLASS = {
  matched_above_threshold: "met",
  pending_user_answers: "unclear",
  not_a_match: "unmet",
};

export default function MatrixBadge({ status, kind }) {
  if (kind === "verdict") {
    return <span className={`status-badge ${VERDICT_CLASS[status] || ""}`}>{verdictLabel(status) || status}</span>;
  }
  return <span className={`status-badge ${status}`}>{status}</span>;
}

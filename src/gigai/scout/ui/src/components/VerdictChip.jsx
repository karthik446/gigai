import { isThinMatch, THIN_LABEL, THIN_POSTING } from "../display.js";
import { VERDICT_LABELS, openQuestions } from "../jobModel.js";

// Q4a: the card-level verdict chip (Verdict enum, contracts.py) with the
// open-question count for a pending one; "Not assessed" for a row with no
// assessment at all (its reason is shown separately by the caller).
export default function VerdictChip({ verdict, assessment }) {
  let label = VERDICT_LABELS[verdict] || verdict;
  if (verdict === "pending_user_answers") {
    const count = openQuestions(assessment).length;
    if (count) {
      label = `${label} (${count})`;
    }
  }
  if (isThinMatch(verdict, assessment)) {
    // 0.1.11.2: a match read from fewer than 4 requirement rows never reads "Matched".
    return <span className={`verdict-chip ${verdict} fit-${THIN_POSTING}`} data-fit={THIN_POSTING}>{THIN_LABEL}</span>;
  }
  return <span className={`verdict-chip ${verdict}`}>{label}</span>;
}

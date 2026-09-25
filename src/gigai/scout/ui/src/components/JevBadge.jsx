import { jevReasonsLine } from "../jobModel.js";

// Q4a: the Jev pre-rank tile (RankScore, jev_contracts.py): score + fit,
// colored by fit; a dashed "–" tile when the row was never scored (past the
// cost cap, or no key -- fail open, P6). Reasons/flags go in the tooltip.
export default function JevBadge({ rank }) {
  if (!rank || rank.fit === null || rank.fit === undefined) {
    return (
      <span className="jev-badge unscored" title="Not scored by Jev (past the cost cap or no key)">
        <span className="jev-score">–</span>Jev
      </span>
    );
  }
  const reasons = jevReasonsLine(rank);
  return (
    <span className={`jev-badge ${rank.fit}`} title={`Jev: ${rank.fit}${reasons ? ` — ${reasons}` : ""}`}>
      <span className="jev-score">{typeof rank.score === "number" ? rank.score : "–"}</span>Jev · {rank.fit}
    </span>
  );
}

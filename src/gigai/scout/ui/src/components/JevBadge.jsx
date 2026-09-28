import { isScored, jevReasonsLine, jevSkipText } from "../jobModel.js";

// Q4a: the Jev pre-rank tile (RankScore, jev_contracts.py): score + fit,
// colored by fit; a dashed "–" tile when the row was never scored (past the
// cost cap, or no key -- fail open, P6). Reasons/flags go in the tooltip.
//
// uat-batch2 (uat-bug-015): a quick assessment says WHY it has no score
// (`skipReason`, AssessResponse.rank_skip_reason) in words: in the tile's
// tooltip always, and under the tile when `showReason` is set (the job
// page; a card has no room for the sentence).
export default function JevBadge({ rank, skipReason, showReason = false }) {
  if (!isScored(rank)) {
    const why = jevSkipText(skipReason);
    return (
      <>
        <span
          className="jev-badge unscored"
          title={why ? `Not scored by Jev: ${why}` : "Not scored by Jev (past the cost cap or no key)"}
          data-skip-reason={skipReason || undefined}
        >
          <span className="jev-score">–</span>Jev
        </span>
        {showReason && why && (
          <div className="jev-reasons" data-role="jev-skip-reason">
            {why}
          </div>
        )}
      </>
    );
  }
  const reasons = jevReasonsLine(rank);
  return (
    <span className={`jev-badge ${rank.fit}`} title={`Jev: ${rank.fit}${reasons ? ` — ${reasons}` : ""}`}>
      <span className="jev-score">{typeof rank.score === "number" ? rank.score : "–"}</span>Jev · {rank.fit}
    </span>
  );
}

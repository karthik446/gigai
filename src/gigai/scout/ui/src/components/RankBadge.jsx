import { RANK_HONEST_NOTE, isRanked, rankBlockersLine, rankReasonsLine, rankTileText, rankTooltip } from "../rankModel.js";

// SCOPE-ADD-3 D: the model's rank for one posting, as a tile: the score and
// its band ("likely fit", "possible fit", "likely no-match"), or "blocker"
// for a posting the model named a blocker for (it is demoted to the end of
// the list, never hidden). The tooltip carries the model's reasons and
// blockers (rankModel.rankTooltip). A rank is the model's guess, never a
// verdict: it says nothing about whether the posting matches.
//
// `detail` (the job page) shows the reasons, the blockers and the honest
// note under the tile. `hideWhenNone`: a posting with no rank at all (an
// on-demand assessment) draws nothing.
export default function RankBadge({ rank, detail = false, hideWhenNone = false }) {
  if (!rank && hideWhenNone) {
    return null;
  }
  const tile = rankTileText(rank);
  const band = !isRanked(rank) ? "unranked" : rank.demoted ? "blocked" : rank.fit;
  const reasons = rankReasonsLine(rank);
  const blockers = rankBlockersLine(rank);
  return (
    <>
      <span className={`rank-badge ${band}`} title={rankTooltip(rank)} data-role="rank-badge" data-rank-band={band}>
        <span className="rank-score">{tile.score}</span>
        {tile.label}
      </span>
      {detail && (
        <div className="rank-detail" data-role="rank-detail">
          {reasons && <div className="rank-reasons">Why: {reasons}</div>}
          {blockers && <div className="rank-blockers">{blockers}</div>}
          {isRanked(rank) && <div className="rank-note">{RANK_HONEST_NOTE}</div>}
        </div>
      )}
    </>
  );
}

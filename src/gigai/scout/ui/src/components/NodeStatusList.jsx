import { relativeTimeLabel } from "../display.js";
import { SETTINGS_HASH } from "../routing.js";
import { notImportedLine, rotationLine, searchLines } from "../runText.js";
import { rankCountsLine, rankShortfallLine, rankedByLine } from "../rankModel.js";
import { keywordsLine } from "../keywordsModel.js";

const NODE_ORDER = ["acquire", "assess", "present"];

function nodeReceiptFor(nodeReceipts, slug) {
  return nodeReceipts.find((receipt) => receipt.node_slug === slug) || null;
}

// U18 (0.1.8.1 UAT): a receipt only exists once a step *finishes*, so before
// this packet every step showed "waiting" for its entire duration, even
// while acquire had live HTTPS connections open. `progressSteps` (from
// GET /progress's non-authoritative steps.json, keyed by step name to
// {status, started_at, finished_at}) fills that gap: "running" the instant
// the step starts, "done"/"failed" once it finishes -- the receipt (when
// present) still wins for the terminal label, since it's the sealed
// authority and progress is best-effort.
function stepLabel(receipt, progressStep) {
  if (receipt) {
    return receipt.status;
  }
  if (progressStep?.status === "running") {
    return "running";
  }
  if (progressStep?.status === "failed") {
    return "failed";
  }
  return "waiting";
}

// `rotation` / `boards` (GET /progress): one line under the step pills
// while/after acquire pages through the watchlist (runText.rotationLine).
// `notImported` (GET /progress's not_imported_count, uat-bug-011): "N more
// matched, not imported this run", shown only when it is above 0.
//
// N11-C part 2: a search that read the company index (boards.source ===
// "index") has no rotation; it says what it read, what it fetched for the
// companies Exa found, how many wait, and when the stored postings need an
// update, each with a link to Settings where that helps (runText.searchLines).
//
// SCOPE-ADD-3 D: the ranking and assessing counts, "Ranked 350 of 1,458 ·
// Assessing 3 of 10", from GET /progress's `rank` and `assess_counts`
// (rankModel.rankCountsLine; a run sealed before either existed has them
// null and gets no line). "Ranked by your model: <name>" only when the
// server names the model (`rank_status`/`rank`), and one line when the pass
// ended with postings it did not rank.
export default function NodeStatusList({ status, nodeReceipts, progressSteps, rotation, boards, notImported, rank, assessCounts, rankStatus }) {
  const search = searchLines(boards, relativeTimeLabel);
  const line = search ? null : rotationLine(rotation, boards);
  const leftOut = notImportedLine(notImported);
  // 0110-026 F2: what the search did with its keywords (boards.keywords), quietly.
  const keywords = keywordsLine(boards);
  const counts = rankCountsLine(rank, assessCounts);
  const rankedBy = counts ? rankedByLine(rankStatus, rank) : null;
  const shortfall = rankShortfallLine(rank, rankStatus);
  return (
    <section className="panel">
      <h2>Run status: {status}</h2>
      <div className="node-status-list">
        {NODE_ORDER.map((slug) => {
          const receipt = nodeReceiptFor(nodeReceipts, slug);
          const progressStep = progressSteps && progressSteps[slug];
          return (
            <div className="node-status-pill" key={slug}>
              <div className="node-name">{slug}</div>
              <div className="node-state">{stepLabel(receipt, progressStep)}</div>
              {receipt && receipt.failure && (
                <div className="node-failure-message">{receipt.failure.message}</div>
              )}
            </div>
          );
        })}
      </div>
      {line && (
        <p className="muted" data-role="rotation-line" style={{ margin: "0.5rem 0 0" }}>
          {line}
        </p>
      )}
      {search &&
        search.map((item) => (
          <p className="muted search-line" data-role={`search-${item.role}`} key={item.role}>
            {item.text}
            {item.settings && (
              <>
                {" "}
                <a href={SETTINGS_HASH}>Open Settings</a>
              </>
            )}
          </p>
        ))}
      {keywords && (
        <p className="muted keywords-line" data-role="run-keywords" data-ignored={keywords.ignored ? "true" : undefined}>
          {keywords.text}
        </p>
      )}
      {leftOut && (
        <p className="muted not-imported-line" data-role="not-imported" title="They matched every filter. A run imports a bounded number of postings: the best ranked first, the newest first where the model gave no score.">
          {leftOut}
        </p>
      )}
      {counts && (
        <p className="rank-counts" data-role="rank-counts" data-rank-status={(rank && rank.status) || undefined}>
          {counts}
          {rankedBy && <span className="muted"> · {rankedBy}</span>}
        </p>
      )}
      {shortfall && (
        <p className="muted" data-role="rank-shortfall" style={{ margin: "0.35rem 0 0", fontSize: "0.88rem" }}>
          {shortfall}
        </p>
      )}
    </section>
  );
}

import { relativeTimeLabel } from "../display.js";
import { SETTINGS_HASH } from "../routing.js";
import { notImportedLine, rotationLine, searchLines } from "../runText.js";

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
export default function NodeStatusList({ status, nodeReceipts, progressSteps, rotation, boards, notImported }) {
  const search = searchLines(boards, relativeTimeLabel);
  const line = search ? null : rotationLine(rotation, boards);
  const leftOut = notImportedLine(notImported);
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
      {leftOut && (
        <p className="muted not-imported-line" data-role="not-imported" title="They matched every filter. A run imports a bounded number of postings: the best Jev fit first, the newest first when Jev did not score them.">
          {leftOut}
        </p>
      )}
    </section>
  );
}

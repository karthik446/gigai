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

// acquire-rotation: one line under the step pills while/after acquire pages
// through the watchlist -- "boards N-M of T this run; full rotation every
// ~K runs" (from GET /progress's `rotation` block; `last`/K are the previous
// run's estimate until this run's page is measured, `?` on the first run).
export function rotationLine(rotation, boards) {
  if (!rotation || rotation.total == null) {
    return null;
  }
  const span = `${rotation.first ?? 1}–${rotation.last ?? "?"}`;
  const runs = rotation.runs_per_rotation;
  const cadence = runs === 1 ? "every run" : runs > 1 ? `every ~${runs} runs` : "cadence unknown";
  const done = boards && boards.done != null && boards.status === "running" ? ` (${boards.done} done so far)` : "";
  return `Boards ${span} of ${rotation.total} this run${done}; full rotation ${cadence}.`;
}

export default function NodeStatusList({ status, nodeReceipts, progressSteps, rotation, boards }) {
  const line = rotationLine(rotation, boards);
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
    </section>
  );
}

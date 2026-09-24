const NODE_ORDER = ["acquire", "assess", "present"];

function nodeReceiptFor(nodeReceipts, slug) {
  return nodeReceipts.find((receipt) => receipt.node_slug === slug) || null;
}

export default function NodeStatusList({ status, nodeReceipts }) {
  return (
    <section className="panel">
      <h2>Run status: {status}</h2>
      <div className="node-status-list">
        {NODE_ORDER.map((slug) => {
          const receipt = nodeReceiptFor(nodeReceipts, slug);
          return (
            <div className="node-status-pill" key={slug}>
              <div className="node-name">{slug}</div>
              <div className="node-state">{receipt ? receipt.status : "waiting"}</div>
              {receipt && receipt.failure && (
                <div className="node-failure-message">{receipt.failure.message}</div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

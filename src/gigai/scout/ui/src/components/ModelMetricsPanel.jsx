import { useEffect, useState } from "react";
import { getMetrics } from "../api.js";
import { comparisonRows } from "../metricsModel.js";

// 0.1.10.7 E: Settings' per-model comparison, over GET /api/metrics. One row
// per kind of model call and model target; nothing is shown until a call has
// been recorded.
export default function ModelMetricsPanel() {
  const [rows, setRows] = useState([]);
  useEffect(() => {
    let live = true;
    getMetrics()
      .then((report) => live && setRows(comparisonRows(report)))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);
  if (rows.length === 0) {
    return null;
  }
  return (
    <section className="panel" id="settings-model-metrics" data-role="model-metrics">
      <h2>Model usage</h2>
      <p className="muted">Averages of the model calls made here, per kind of call and model. Only counts and timings are recorded.</p>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Call</th>
              <th>Model</th>
              <th>Calls</th>
              <th>Avg tokens</th>
              <th>Avg time</th>
              <th>Avg cost</th>
              <th>Failed</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key}>
                <td>{row.kind}</td>
                <td title={row.models || undefined}>{row.model}</td>
                <td>{row.calls}</td>
                <td>{row.tokens}</td>
                <td>{row.seconds}</td>
                <td>{row.cost}</td>
                <td>{row.failed}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

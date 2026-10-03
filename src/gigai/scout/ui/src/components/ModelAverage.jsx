import { useEffect, useState } from "react";
import { getConfig, getMetrics } from "../api.js";
import { averageLabel } from "../metricsModel.js";

// 0.1.10.7 E: "avg 19.5k tokens, 11 s per job on codex" beside an action that
// calls the model. `target` is the model target the action will use; without
// one it is the configured default. Shows nothing until there is history.
export default function ModelAverage({ kind = "assess", target }) {
  const [label, setLabel] = useState(null);
  useEffect(() => {
    let live = true;
    Promise.all([getMetrics({ kind }), target ? null : getConfig().catch(() => null)])
      .then(([report, loaded]) => {
        const config = loaded && (loaded.config || loaded);
        const used = target || (config && config.default_model_target);
        if (live) {
          setLabel(averageLabel(report, kind, used));
        }
      })
      .catch(() => live && setLabel(null));
    return () => {
      live = false;
    };
  }, [kind, target]);
  return label ? (
    <span className="muted small" data-role="model-average">
      {label}
    </span>
  ) : null;
}

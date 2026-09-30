import { useCallback, useState } from "react";
import { startSourcesUpdate } from "../api.js";
import { startErrorText } from "../sourcesModel.js";

// uat-bug-048: the strip above "Run find jobs". `strip` is
// sourcesStripModel.sourcesStrip(status); `read` re-reads the status.
export default function SourcesStrip({ strip, read }) {
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState(null);
  const start = useCallback(() => {
    setStarting(true);
    setError(null);
    startSourcesUpdate({ force: false })
      .then(() => read())
      .catch((err) => {
        setError(startErrorText(err));
        return read();
      })
      .finally(() => setStarting(false));
  }, [read]);

  if (strip.kind === "unknown") {
    return null;
  }
  const button = (
    <button type="button" className="button secondary" onClick={start} disabled={strip.running || starting} data-action="update-sources-strip">
      {strip.running ? "Updating sources…" : starting ? "Starting…" : "Update sources"}
    </button>
  );
  return (
    <div className={`sources-strip${strip.amber ? " amber" : ""}`} data-role="sources-strip" data-kind={strip.kind}>
      {strip.steps ? (
        <ol className="first-run-steps" data-role="first-run-steps">
          {strip.steps.map((step) => (
            <li key={step.n} className={`first-run-step ${step.state}`} data-step={step.n} data-state={step.state} data-highlight={step.state === "current" ? "true" : undefined}>
              <span className="first-run-num" aria-hidden="true">{step.state === "done" ? "✓" : step.n}</span>
              <span className="first-run-text">
                <span className="first-run-title">{step.title}</span>
                <span className="muted first-run-desc">{step.description}</span>
                {step.n === 2 && step.state !== "done" && strip.runBlocked && (
                  <span className="muted first-run-desc" data-role="run-blocked">{strip.runBlocked}</span>
                )}
              </span>
              {step.action === "update-sources" && button}
            </li>
          ))}
        </ol>
      ) : (
        <div className="sources-strip-row">
          <span data-role="sources-strip-line">{strip.line}</span> {button}
        </div>
      )}
      {strip.running && strip.progress && (
        <div data-role="sources-strip-progress">
          <div
            className={`progress-bar-track${strip.progress.determinate ? "" : " indeterminate"}`}
            role="progressbar"
            aria-label="Update sources progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={strip.progress.determinate ? strip.progress.percent : undefined}
          >
            <div className="progress-bar-fill" style={strip.progress.determinate ? { width: `${strip.progress.percent}%` } : undefined} />
          </div>
          <p className="muted sources-line">{strip.progress.line}</p>
        </div>
      )}
      {error && <div className="field-error" data-role="sources-strip-error">{error}</div>}
    </div>
  );
}

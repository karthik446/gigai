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
        <ol className="sources-steps" data-role="first-run-steps">
          <li className={strip.steps.highlightStep === 1 ? "highlight" : undefined} data-step="1" data-highlight={strip.steps.highlightStep === 1 ? "true" : undefined}>
            {strip.steps.update} {button}
          </li>
          <li data-step="2">{strip.steps.run}</li>
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

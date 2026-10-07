import { batchProgress } from "../assessBatchModel.js";

// 0.1.11.5 (ASSESS-01): the running assess batch on the Jobs page: "12 of 50 assessed", the estimate ("about 29 min
// for 50"), the profile of the call started last, and Cancel. Cancel starts no further model call; the calls in
// flight finish and what finished is kept (the line says so while it waits for them).
export default function AssessBatchProgress({ batch, profiles }) {
  const progress = batchProgress(batch.status, profiles);
  if (!progress) {
    return null;
  }
  const total = batch.status.batch.total || 0;
  const done = (batch.status.batch.assessed || 0) + (batch.status.batch.failed || 0);
  return (
    <div className="callout info assess-batch" role="status" data-testid="assess-batch" data-status={progress.cancelling ? "cancelling" : "running"}>
      <div className="assess-batch-row">
        <strong>{progress.cancelling ? "Cancelling" : "Assessing"}:</strong> <span data-role="assess-batch-count">{progress.line}</span>
        {progress.estimate && !progress.cancelling && (
          <span className="muted" data-role="assess-batch-estimate">
            {" · "}
            {progress.estimate}
          </span>
        )}
        {progress.profile && (
          <span className="muted" data-role="assess-batch-profile">
            {" · "}
            {progress.profile}
          </span>
        )}{" "}
        <button
          type="button"
          className="button small secondary"
          data-testid="assess-batch-cancel"
          disabled={!progress.canCancel || batch.cancelSent}
          title="Stops the batch: no further model call starts. The calls in flight finish and what finished is kept."
          onClick={batch.cancel}
        >
          {progress.cancelling || batch.cancelSent ? "Cancelling…" : "Cancel"}
        </button>
      </div>
      <progress className="assess-batch-bar" max={Math.max(1, total)} value={Math.min(done, total)} aria-label="Assessed so far" />
      {progress.waiting && (
        <div className="muted small" data-role="assess-batch-waiting">
          {progress.waiting}
        </div>
      )}
      {batch.cancelError && <div className="muted small">Could not cancel: {batch.cancelError}</div>}
    </div>
  );
}

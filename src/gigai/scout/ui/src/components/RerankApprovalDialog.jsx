// 0.1.11.2 RANKUI: the approval "Re-rank latest 100" asks for. `dialog` is rankNowModel.rerankDialog(the ASK's answer):
// the postings and the calls (the cost) are the server's, shown BEFORE anything runs. No model call has been made when
// this shows; Approve sends the body for the yes. When it may not run (today's rank calls do not cover it, ranking is
// off) the reason is said and Approve is off.
export default function RerankApprovalDialog({ dialog, submitting, error, onApprove, onCancel }) {
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="rerank-approval-title" data-testid="rerank-dialog">
      <div className="modal">
        <h2 id="rerank-approval-title">{dialog.title}</h2>
        <ul className="approval-facts">
          <li data-role="rerank-postings">
            <strong>Postings:</strong> {dialog.postings}, the newest of the last 7 days, ranked again even if they have a rank
          </li>
          <li data-role="rerank-cost">
            <strong>Cost:</strong> {dialog.costLine}
          </li>
          <li data-role="rerank-today">
            <strong>Today:</strong> {dialog.todayLine}
          </li>
        </ul>
        {dialog.refusal ? (
          <div className="callout danger" data-role="rerank-refusal">
            {dialog.refusal}
          </div>
        ) : (
          <p className="muted" data-role="rerank-nothing-yet">
            Nothing has been ranked again yet. Ranking starts only when you approve.
          </p>
        )}
        {error && <div className="callout danger">{error}</div>}
        <div className="actions">
          <button type="button" className="button secondary" onClick={onCancel} disabled={submitting} data-action="rerank-cancel">
            Cancel
          </button>
          <button type="button" className="button" onClick={onApprove} disabled={submitting || !dialog.approveBody} data-action="rerank-approve">
            {submitting ? "Starting…" : `Approve and re-rank (${dialog.calls} ${dialog.calls === 1 ? "call" : "calls"})`}
          </button>
        </div>
      </div>
    </div>
  );
}

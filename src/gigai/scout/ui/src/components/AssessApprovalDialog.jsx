import { approvalBatchLine, approvalBody, assessingLine, approvalTitle, estimateLine, lowRankLine } from "../postingsModel.js";
import { modelTargetLabel } from "../modelTargets.js";

// 0.1.10.7 M4b: the approval "Assess these" asks for. `dialog` is
// postingsModel.approvalDialog(the ASK's answer): the count and the estimate
// are the server's. Nothing has been assessed when this shows; Approve sends
// the body the server named for the yes.
// 0110-10-02: postings ranked below the assess threshold are left out of the
// count; they are a second question in the same dialog (a box, off by
// default), and Approve assesses them only when it is ticked.
// 0110-10-11: one approval assesses the NEWEST 50 and never more. With more
// than that selected the title says "the newest 50 of N", the estimate is the
// 50's, and a line says how many are left and how to take the next 50.
export default function AssessApprovalDialog({ dialog, submitting, error, includeLowRank = false, onIncludeLowRank, onApprove, onCancel }) {
  const low = dialog.lowRank;
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="assess-approval-title" data-testid="approval-dialog">
      <div className="modal">
        <h2 id="assess-approval-title">{approvalTitle(dialog)}</h2>
        <ul className="approval-facts">
          {dialog.byProfile.map((item) => (
            <li key={item.label}>
              <strong>{item.label}:</strong> {item.count}
            </li>
          ))}
          {approvalBatchLine(dialog) && (
            <li data-role="approval-batch">
              <strong>50 at a time:</strong> {approvalBatchLine(dialog)}
            </li>
          )}
          <li data-role="approval-estimate">
            <strong>Estimate:</strong> {estimateLine(dialog)}
            {dialog.basisCalls === 0 ? " (no recorded calls yet to estimate tokens or time from)" : ""}
          </li>
          {dialog.modelTarget && (
            <li>
              <strong>Model:</strong> {modelTargetLabel(dialog.modelTarget)}
            </li>
          )}
          {dialog.alreadyCurrent > 0 && (
            <li>
              <strong>Left out:</strong> {dialog.alreadyCurrent} already assessed with the current settings
            </li>
          )}
        </ul>
        {low && (
          <label className="approval-low-rank" data-testid="approval-low-rank">
            <input
              type="checkbox"
              checked={includeLowRank}
              disabled={submitting || !low.approveBody}
              onChange={(event) => onIncludeLowRank && onIncludeLowRank(event.target.checked)}
            />{" "}
            {lowRankLine(low)}
          </label>
        )}
        {submitting ? (
          <p className="muted" data-role="approval-assessing">
            {assessingLine(dialog, includeLowRank)}
          </p>
        ) : (
          <p className="muted" data-role="approval-nothing-yet">
            Nothing has been assessed yet. Assessing starts only when you approve.
          </p>
        )}
        {error && <div className="callout danger">{error}</div>}
        <div className="actions">
          <button type="button" className="button secondary" onClick={onCancel} disabled={submitting} data-action="approval-cancel">
            Cancel
          </button>
          <button type="button" className="button" onClick={onApprove} disabled={submitting || !approvalBody(dialog, includeLowRank)} data-action="approval-approve">
            {submitting ? "Assessing…" : "Approve and assess"}
          </button>
        </div>
      </div>
    </div>
  );
}

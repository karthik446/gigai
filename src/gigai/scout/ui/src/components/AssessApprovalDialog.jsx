import { approvalBatchLine, approvalBody, assessingLine, approvalTitle, estimateLine, lowRankLine, lowRankNote, shownEstimate } from "../postingsModel.js";
import { modelTargetLabel } from "../modelTargets.js";

// 0.1.10.7 M4b: the approval "Assess these" asks for. `dialog` is
// postingsModel.approvalDialog(the ASK's answer): the count and the estimate
// are the server's. Nothing has been assessed when this shows; Approve sends
// the body the server named for the yes.
// 0110-10-02: postings ranked below the assess threshold are left out of the
// count; they are a second question in the same dialog (a box, off by
// default), and Approve assesses them only when it is ticked.
// 0110-10-11: one approval assesses 50 and never more. 0.1.11.2: the TOP 50
// BY RANK. With more than that selected the title says "the top 50 by rank of
// N", the estimate is the 50's, and a line says how many are left and how to
// take the next 50. While the background rank still runs, a line says so
// (`dialog.ranking`): the 50 are then the top of what is ranked so far.
// 0.1.11.5 (ASSESS-01): Approve STARTS the batch and the dialog closes (the page shows the progress and its Cancel);
// `submitting` is only the moment until the server says the batch is live. Cancel works then too (the page cancels
// the batch that was just started). The low-rank box says what it does ("Include the 105 low-ranked ones in the pool
// (still 50 per run)") and is not shown when it cannot change the run (a line says so instead).
export default function AssessApprovalDialog({ dialog, submitting, error, includeLowRank = false, onIncludeLowRank, onApprove, onCancel }) {
  const low = dialog.lowRank;
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="assess-approval-title" data-testid="approval-dialog">
      <div className="modal">
        <h2 id="assess-approval-title">{approvalTitle(dialog)}</h2>
        <ul className="approval-facts">
          {/* 0.1.11.9: no count per role. The batch is of JOBS; each is assessed once, whichever roles found it. */}
          {approvalBatchLine(dialog) && (
            <li data-role="approval-batch">
              <strong>50 at a time:</strong> {approvalBatchLine(dialog)}
            </li>
          )}
          {dialog.ranking && (
            <li data-role="approval-ranking">
              <strong>Ranking:</strong> {dialog.ranking}
            </li>
          )}
          <li data-role="approval-estimate">
            <strong>Estimate:</strong> {estimateLine(shownEstimate(dialog, includeLowRank))}
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
        {lowRankNote(low, dialog.count) && (
          <p className="muted" data-testid="approval-low-rank-note">
            {lowRankNote(low, dialog.count)}
          </p>
        )}
        {lowRankLine(low, dialog.count) && (
          <label className="approval-low-rank" data-testid="approval-low-rank">
            <input
              type="checkbox"
              checked={includeLowRank}
              disabled={submitting || !low.approveBody}
              onChange={(event) => onIncludeLowRank && onIncludeLowRank(event.target.checked)}
            />{" "}
            {lowRankLine(low, dialog.count)}
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
          <button type="button" className="button secondary" onClick={onCancel} data-action="approval-cancel">
            Cancel
          </button>
          <button type="button" className="button" onClick={onApprove} disabled={submitting || !approvalBody(dialog, includeLowRank)} data-action="approval-approve">
            {submitting ? "Starting…" : "Approve and assess"}
          </button>
        </div>
      </div>
    </div>
  );
}

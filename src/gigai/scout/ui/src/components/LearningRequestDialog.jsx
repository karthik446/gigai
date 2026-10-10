// 0.1.11.10 Part B (G6): the estimate dialog shown after "Generate course" is pressed, before anything is sent
// (the shape of AssessApprovalDialog.jsx). `dialog` is learningModel.generateDialog(the ASK's answer, modelLabel):
// nothing is generated before "Generate"; "Not now" starts nothing (no second POST).
export default function LearningRequestDialog({ dialog, submitting, error, onApprove, onCancel }) {
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="learning-request-title" data-testid="learning-request-dialog">
      <div className="modal">
        <h2 id="learning-request-title">{dialog.title}</h2>
        <p>This is a higher-usage action than an assessment.</p>
        <p data-role="learning-request-estimate">{dialog.estimateText}</p>
        <p data-role="learning-request-sends">{dialog.sendsText}</p>
        <p data-role="learning-request-stops">{dialog.stopsText}</p>
        {error && <div className="callout danger">{error}</div>}
        <div className="actions">
          <button type="button" className="button secondary" onClick={onCancel} disabled={submitting} data-action="learning-request-cancel">
            Not now
          </button>
          <button type="button" className="button" onClick={onApprove} disabled={submitting} data-action="learning-request-approve">
            {submitting ? "Starting…" : dialog.approveLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

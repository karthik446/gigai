// 0.1.11.10 shipped lessons (slices 4b/5b): the consent dialog shown after "Build my pathway" is pressed, before
// anything is sent (the pattern of LearningRequestDialog.jsx). `dialog` is learningModel.pathwayBuildDialog(the
// ASK's answer, modelLabel). The resume is always the reference, so this is the usual consent to a model call, not
// a choice about the resume: "Build" starts the build, "Not now" starts nothing (no second POST).
export default function LearningPathwayDialog({ dialog, submitting, error, onApprove, onCancel }) {
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="learning-pathway-title" data-testid="learning-pathway-dialog">
      <div className="modal">
        <h2 id="learning-pathway-title">{dialog.title}</h2>
        <p data-role="learning-pathway-page">{dialog.pageText}</p>
        <p data-role="learning-pathway-sends">{dialog.sendsText}</p>
        {dialog.estimateText && <p data-role="learning-pathway-estimate">{dialog.estimateText}</p>}
        {error && <div className="callout danger">{error}</div>}
        <div className="actions">
          <button type="button" className="button secondary" onClick={onCancel} disabled={submitting} data-action="learning-pathway-cancel">
            Not now
          </button>
          <button type="button" className="button" onClick={onApprove} disabled={submitting} data-action="learning-pathway-approve">
            {submitting ? "Starting…" : dialog.approveLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

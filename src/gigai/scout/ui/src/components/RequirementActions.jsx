// uat-batch1 (N6/N7): the action at the top of Requirements.
//
//   Re-assess       saves every filled answer box, then re-assesses once.
//                   0110-10-12: an OLD assessment is re-assessed as it is
//                   when no box is filled (`reassess.plain`)
//
// 0.1.11 N6 (SPEC section 6, item 2): ONE action. There is no tailor call:
// the resume for a job is picked when the job is assessed, so the second
// action ("Tailor resume"), its status line and its gate are gone. On the job
// page the button says what it costs ("Re-assess · 1 model call").
//
// 0.1.10.7 E: `reassess.average` shows what an assessment has taken on
// average with the configured model, beside the button (ModelAverage).
//
// The action is {enabled, reason, label, busy, onClick}; the gate is
// answersModel.js's reassessGate. A disabled action says why twice: in the
// tooltip (on a wrapper, since a disabled button shows none in every browser)
// and in the helper line under the button.
import ModelAverage from "./ModelAverage.jsx";

function Action({ action, name, primary, busy }) {
  const disabled = !action.enabled || busy;
  return (
    <span className="req-action" title={action.reason}>
      <button
        type="button"
        className={`button small${primary ? "" : " secondary"}`}
        disabled={disabled}
        aria-describedby={`${name}-help`}
        data-action={name}
        onClick={action.onClick}
      >
        {action.label}
      </button>
    </span>
  );
}

function Help({ action, name }) {
  return (
    <li id={`${name}-help`} className={action.enabled ? "on" : "off"} data-help={name}>
      <strong>{action.helpName || action.label}</strong>
      {action.enabled ? ": " : " is off: "}
      {action.reason}
    </li>
  );
}

export default function RequirementActions({ reassess, busy, error }) {
  return (
    <div className="req-actions">
      <div className="req-actions-buttons">
        <Action action={reassess} name="reassess" primary busy={busy} />
        {reassess.average && <ModelAverage kind="assess" />}
        {reassess.busy && (
          <span className="reassess-progress" role="status">
            <span className="spinner" aria-hidden="true" /> {reassess.plain ? "Re-assessing…" : "Re-assessing with your answers…"}
          </span>
        )}
      </div>
      <ul className="action-help">
        <Help action={reassess} name="reassess" />
      </ul>
      {error && <div className="field-error">{error}</div>}
    </div>
  );
}

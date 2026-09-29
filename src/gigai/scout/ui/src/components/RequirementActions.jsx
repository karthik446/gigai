// uat-batch1 (N6/N7): the two actions at the top of Requirements.
//
//   Re-assess       saves every filled answer box, then re-assesses once
//   Tailor resume   (job page only) opens the tailored-resume panel below
//
// Each action is {enabled, reason, label, busy, onClick}; the gates are
// answersModel.js's reassessGate / tailorGate. A disabled action says why
// twice: in the tooltip (on a wrapper, since a disabled button shows none
// in every browser) and in the helper line under the buttons.
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

// uat-bug-043: the tailoring status sits next to the button, where the
// click happened (the panel it fills is below the requirement table, off
// screen). `status` is {phase: "running"|"done"|"error", text, onJump}; a
// finished run offers a jump to the panel instead of moving the page.
function TailorStatus({ status }) {
  return (
    <span className={`tailor-status ${status.phase}`} role="status" data-tailor-status={status.phase}>
      {status.phase === "running" && <span className="spinner" aria-hidden="true" />}
      {status.text}
      {status.phase !== "running" && status.onJump && (
        <button type="button" className="link-button" data-action="tailor-jump" onClick={status.onJump}>
          {status.phase === "done" ? "Jump to it" : "See the error"}
        </button>
      )}
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

export default function RequirementActions({ reassess, tailor, busy, error }) {
  return (
    <div className="req-actions">
      <div className="req-actions-buttons">
        <Action action={reassess} name="reassess" primary busy={busy} />
        {tailor && <Action action={tailor} name="tailor" busy={busy} />}
        {tailor && tailor.status && <TailorStatus status={tailor.status} />}
        {reassess.busy && (
          <span className="reassess-progress" role="status">
            <span className="spinner" aria-hidden="true" /> Re-assessing with your answers…
          </span>
        )}
      </div>
      <ul className="action-help">
        <Help action={reassess} name="reassess" />
        {tailor && <Help action={tailor} name="tailor" />}
      </ul>
      {error && <div className="field-error">{error}</div>}
    </div>
  );
}

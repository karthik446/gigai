import { STEPS } from "./wizardState.js";

// The 4-dot progress bar above every wizard screen (mockup: Resume → Target
// → Companies → Discovery + finish; labels hide under 480px, see wizard.css).
export default function StepIndicator({ step }) {
  return (
    <nav className="wz-steps" aria-label="Setup steps">
      {STEPS.map((label, index) => {
        const number = index + 1;
        const state = number === step ? " active" : number < step ? " done" : "";
        return (
          <div key={label} className="wz-step-wrap" style={{ display: "contents" }}>
            <div className={`wz-step${state}`} aria-current={number === step ? "step" : undefined}>
              <div className="wz-step-dot">{number}</div>
              <div className="wz-step-label">{label}</div>
            </div>
            {number < STEPS.length && <div className={`wz-step-line${number < step ? " done" : ""}`} />}
          </div>
        );
      })}
    </nav>
  );
}

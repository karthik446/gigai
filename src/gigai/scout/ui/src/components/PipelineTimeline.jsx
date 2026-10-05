import { useCallback, useEffect, useRef, useState } from "react";
import { getPipelineJob, postPipelineProcess } from "../api.js";
import HelpLink from "./HelpLink.jsx";
import { atsChip, isLegacyPipeline, labelChip, pickDoneStamp, pickFinished, pipelineLive, processAction, processResultLine, stepTimeline } from "../pipelineModel.js";

const POLL_MS = 3000;

// A chip whose click opens its breakdown: a <details>, so it needs no
// script to open, close or reach by keyboard.
function ChipPopover({ label, tone, testId, older, legacy, children }) {
  return (
    <details className="chip-popover">
      <summary className={`state-pill tone-${tone}`} data-testid={testId} data-older={older ? "true" : undefined} data-legacy={legacy ? "true" : undefined}>
        {label}
      </summary>
      <div className="chip-popover-body">{children}</div>
    </details>
  );
}

// 0.1.10.7 M4b: the job page's pipeline step timeline, over GET
// /api/pipeline/job. 0.1.11 (SPEC 4.2, section 6): FOUR rows, Assessed ->
// Resume picked -> Scout ATS -> Scout label. Each step says its state and,
// once it ran, the model, tokens and time of its last attempt (only
// "Assessed" can call a model, and only for an old assessment). The "before
// -> after tailoring" line is gone with the tailoring. The Scout ATS chip
// opens its breakdown and the Scout label chip its reasons; the sentence
// under each is the server's own wording. A job the 0.1.10 pipeline
// processed keeps its label, marked "made on 0.1.10's tailored resume", and
// offers "Check again".
// "Process now" queues the job (POST /api/pipeline/process, 202: it never
// waits for a model) and the timeline is read again while it moves.
// `onPickDone` is called when a read says the pick step finished since the
// read before it (pipelineModel.pickFinished): the pipeline stored a resume,
// and the job page reads it again.
// 0110-10-12: `assessedAt` is when the assessment the page shows was made. A
// Scout label made before it (a re-assessment does not make the label again)
// says so on its chip and in its popover, instead of reading as the new
// assessment's (pipelineModel.labelChip).
export default function PipelineTimeline({ jobIdentity, profileId, assessed, assessedAt, refreshKey, onPickDone }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const [posting, setPosting] = useState(false);
  const [result, setResult] = useState(null);
  const timer = useRef(null);
  const current = useRef(null);
  const pickSeen = useRef({ key: null, stamp: "" }); // the pick step at the last read; it outlives a refreshKey reset
  const pickDone = useRef(onPickDone);
  pickDone.current = onPickDone;

  const read = useCallback(() => {
    if (!jobIdentity || !profileId) {
      return;
    }
    const key = `${profileId}\n${jobIdentity}`;
    current.current = key;
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    getPipelineJob({ jobIdentity, profileId })
      .then((loaded) => {
        if (current.current !== key) {
          return;
        }
        setDetail(loaded);
        setError(null);
        const stamp = pickDoneStamp(loaded);
        const before = pickSeen.current.key === key ? pickSeen.current.stamp : undefined;
        pickSeen.current = { key, stamp };
        if (pickDone.current && pickFinished(before, stamp)) {
          pickDone.current();
        }
        if (pipelineLive(loaded)) {
          timer.current = setTimeout(read, POLL_MS);
        }
      })
      .catch((err) => current.current === key && setError(err.detail || err.message || String(err)));
  }, [jobIdentity, profileId]);

  useEffect(() => {
    setDetail(null);
    setResult(null);
    setError(null);
    read();
    return () => {
      current.current = null;
      if (timer.current) {
        clearTimeout(timer.current);
      }
    };
  }, [read, refreshKey]);

  if (!jobIdentity || !profileId) {
    return null;
  }

  const action = processAction(detail, { assessed, jobIdentity, profileId });
  const process = () => {
    setPosting(true);
    setResult(null);
    setError(null);
    postPipelineProcess(action.body)
      .then((response) => {
        setResult(processResultLine(response));
        read();
      })
      .catch((err) => setError(err.detail || err.message || String(err)))
      .finally(() => setPosting(false));
  };

  const stages = stepTimeline(detail);
  const legacy = isLegacyPipeline(detail);
  const ats = atsChip(detail);
  const label = labelChip(detail, { assessedAt });

  return (
    <section className="panel" data-testid="step-timeline" data-state={detail && detail.state ? detail.state : "not_started"} data-legacy={legacy ? "true" : undefined}>
      <div className="timeline-head">
        <h3>Background pipeline</h3>
        <div className="timeline-actions">
          {result && (
            <span className="muted" data-role="process-result">
              {result}
            </span>
          )}
          {action.reason && <span className="muted">{action.reason}</span>}
          <button type="button" className="button small secondary" onClick={process} disabled={!action.enabled || posting} data-action="process-now">
            {posting ? "Queueing…" : action.label}
          </button>
        </div>
      </div>
      {error && <div className="field-error">{error}</div>}
      <ol className="step-timeline">
        {stages.map((steps, index) => (
          <li key={steps.map((step) => step.name).join("+")} className="step-stage" data-stage={index + 1}>
            {steps.map((step) => (
              <div key={step.name} className={`step-card tone-${step.tone}`} data-step={step.name} data-state={step.state}>
                <div className="step-title">{step.title}</div>
                <div className="step-state">{step.stateLabel}</div>
                {(step.model || step.tokens || step.seconds) && (
                  <div className="step-numbers muted">{[step.model, step.tokens, step.seconds].filter(Boolean).join(" · ")}</div>
                )}
                {step.waiting && <div className="step-numbers muted">Waiting: {step.waiting}</div>}
                {step.error && (
                  <div className="step-error" data-role="step-error">
                    {step.error}
                  </div>
                )}
              </div>
            ))}
          </li>
        ))}
      </ol>
      {(ats || label) && (
        <div className="timeline-results">
          {ats && (
            <ChipPopover label={ats.label} tone="plain" testId="ats-chip">
              {ats.line && <div className="chip-popover-line">{ats.line}</div>}
              <ul>
                {ats.rows.map((row) => (
                  <li key={row}>{row}</li>
                ))}
              </ul>
              {ats.wording && <p className="muted">{ats.wording}</p>}
            </ChipPopover>
          )}
          {ats && <HelpLink topic="ats" />}
          {label && (
            <ChipPopover label={label.label} tone={label.tone} testId="scout-label-chip" older={label.older} legacy={label.legacy}>
              {label.note && (
                <div className="chip-popover-line" data-role={label.legacy ? "label-legacy" : "label-older"}>
                  {label.note}
                </div>
              )}
              {label.reasons.length > 0 ? (
                <ul>
                  {label.reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              ) : (
                <div className="chip-popover-line">No reason against it.</div>
              )}
              {label.wording && <p className="muted">{label.wording}</p>}
            </ChipPopover>
          )}
          {label && <HelpLink topic="scout-label" />}
        </div>
      )}
    </section>
  );
}

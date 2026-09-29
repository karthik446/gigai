import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getTailoredResumes, postTailoredResume } from "../api.js";
import { dateTimeLabel } from "../jobModel.js";
import {
  changeSummary,
  downloadName,
  inlineSegments,
  latestStored,
  previewLines,
  previewStats,
  sourcesHover,
  sourceLabel,
  statsLine,
} from "../tailoredResumeModel.js";

// Q4b-ui (v0.1.9): the tailored-resume panel on the job page
// (mockups/cards-and-job-page.html, "Tailored resume"), over Q3's routes:
//
//   load     GET /api/tailored-resumes?profile_id=<selected>&job_identity=<job>
//            -> the latest stored one, shown with "Tailor again"
//   tailor   POST /api/tailored-resumes {job:{job_url}, resume:{profile_id}}
//            (one model call, one retry on a rejected draft; Codex ~25 s, up to ~45 s)
//   errors   422 (server message), 502 model_output_invalid (the draft failed
//            a guard; the message names the line), 504 tailor_timeout
//   preview  tailoredResumeModel.previewLines(result): every content line is
//            the response's own text, with its refs (the cited resume line /
//            answer text) on hover and on click. Nothing is fabricated here.
//   download a client-side Blob of the response's `markdown` verbatim
//
// uat-batch1 (N6): the right-hand column is gone, and with it this panel's
// own button and explainer. "Tailor resume" is one of the two actions at
// the top of Requirements (RequirementActions.jsx); the state both share is
// useTailoredResume() below, and the panel renders under Requirements only
// once there is something to show (a stored resume, a run in progress, an
// error).
//
// A posting without a URL (a pasted-text quick assessment: the store never
// serializes the text) cannot be tailored from here (answersModel.tailorGate
// says so on the action); a stored one for it still shows.
function saveMarkdown(response) {
  const blob = new Blob([response.markdown], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = downloadName(response);
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function errorView(error) {
  const detail = error.detail || error.message || String(error);
  if (error.code === "model_output_invalid") {
    return {
      heading: "The model's draft was rejected",
      body: detail,
      hint: "Every line must be supported by your resume or your answers. Try again; the model gets a fresh attempt.",
    };
  }
  if (error.code === "tailor_timeout" || error.status === 504) {
    return {
      heading: "The model timed out",
      body: detail,
      hint: "Try again, or pick a faster model target in Settings.",
    };
  }
  return { heading: "Could not tailor the resume", body: detail, hint: null };
}

// uat-bug-044: the changed words of a rewritten line, highlighted. Every
// piece of model text below goes into the tree as a React text child (React
// escapes it); the panel never injects raw HTML.
function DiffText({ segments }) {
  return segments.map((segment, index) => (
    <span key={index}>
      {index > 0 && " "}
      {segment.added ? <mark className="diff-added">{segment.text}</mark> : segment.text}
    </span>
  ));
}

function Inline({ text }) {
  return inlineSegments(text).map((segment, index) => (segment.bold ? <strong key={index}>{segment.text}</strong> : <span key={index}>{segment.text}</span>));
}

// The clean copy: headings, bold and bullets as formatted text, no markers.
function CleanCopy({ lines }) {
  return (
    <div className="clean-resume" data-view="clean">
      {lines.map((line, index) => {
        if (line.kind === "blank") {
          return null;
        }
        if (line.kind === "heading") {
          return (
            <h4 className="clean-section" key={index}>
              {line.text}
            </h4>
          );
        }
        if (line.role === "title") {
          return (
            <h3 className="clean-title" key={index}>
              <Inline text={line.plain} />
            </h3>
          );
        }
        if (line.role === "entry") {
          return (
            <h5 className="clean-entry" key={index}>
              <Inline text={line.plain} />
            </h5>
          );
        }
        if (line.role === "bullet") {
          return (
            <p className="clean-bullet" key={index}>
              <Inline text={line.plain} />
            </p>
          );
        }
        return (
          <p className="clean-text" key={index}>
            <Inline text={line.plain} />
          </p>
        );
      })}
    </div>
  );
}

function PreviewLine({ line, index, open, onToggle, promptFor, showChanges }) {
  if (line.kind === "blank") {
    return (
      <div className="md-line blank">
        <span className="prov blank">·</span>
        <span>&nbsp;</span>
      </div>
    );
  }
  if (line.kind === "heading") {
    return (
      <div className="md-line heading">
        <span className="prov blank">·</span>
        <span>{line.display}</span>
      </div>
    );
  }
  const copied = line.kind === "copy";
  const originals = showChanges && !copied ? line.original : [];
  return (
    <>
      {originals.map((original, originalIndex) => (
        <div className="md-line original" key={`original-${originalIndex}`} title={original.label} data-original-of={index}>
          <span className="prov blank">·</span>
          <span className="original-text">{original.text}</span>
        </div>
      ))}
    <div
      className={`md-line ${line.kind}${open ? " open" : ""}`}
      title={sourcesHover(line, promptFor)}
      role="button"
      tabIndex={0}
      aria-expanded={open}
      data-line-index={index}
      onClick={() => onToggle(index)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onToggle(index);
        }
      }}
    >
      {copied ? (
        <span className="prov resume" title="Copied verbatim from the resume">
          R
        </span>
      ) : (
        <span className="prov rewritten" title="Rewritten; click for sources">
          ✎
        </span>
      )}
      <span>
        {showChanges && !copied && line.diff ? (
          <>
            {line.display.slice(0, line.display.length - line.plain.length)}
            <DiffText segments={line.diff} />
          </>
        ) : (
          line.display
        )}
        {open && (
          <span className="src-list">
            {line.refs.length === 0 && <span className="src-item muted">No source cited.</span>}
            {line.refs.map((ref, refIndex) => (
              <span className="src-item" key={`${ref.kind}-${ref.line || ref.question_id || refIndex}`}>
                <span className={`src-kind ${ref.kind}`}>{sourceLabel(ref, promptFor)}</span>
                {ref.kind === "answer" && promptFor && promptFor(ref.question_id) && <code className="question-id">{ref.question_id}</code>}
                {copied && <span className="src-note">copied verbatim</span>}
                <span className="src-text">{ref.text}</span>
              </span>
            ))}
          </span>
        )}
      </span>
    </div>
    </>
  );
}

export function Preview({ response, profileLabel, promptFor, initialView = "changes" }) {
  const [open, setOpen] = useState(() => new Set());
  const [view, setView] = useState(initialView); // "changes" (default) | "clean"
  const lines = previewLines(response.result);
  const stats = previewStats(lines);
  const toggle = useCallback((index) => {
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  }, []);
  const resumeName = response.resume && response.resume.profile_id ? profileLabel || response.resume.profile_id : "a pasted resume";
  return (
    <>
      <div className="tailor-meta" title={response.updated_at || undefined}>
        Tailored {dateTimeLabel(response.updated_at) || "just now"} · from resume <strong>{resumeName}</strong>
      </div>
      <div className="resume-change-bar">
        <div className="resume-summary" data-testid="change-summary">
          {changeSummary(stats)}
        </div>
        <div className="view-toggle" role="group" aria-label="Resume view">
          <button type="button" className={`button small ${view === "changes" ? "" : "secondary"}`} aria-pressed={view === "changes"} onClick={() => setView("changes")}>
            Show changes
          </button>
          <button type="button" className={`button small ${view === "clean" ? "" : "secondary"}`} aria-pressed={view === "clean"} onClick={() => setView("clean")}>
            Clean copy
          </button>
        </div>
      </div>
      {view === "changes" && (
      <div className="resume-legend">
        <span>
          <span className="prov resume">R</span> copied verbatim from the resume
        </span>
        <span>
          <span className="prov rewritten">✎</span> rewritten from the resume lines / answers it cites
        </span>
        <span className="muted">Hover or click a line to see its sources.</span>
      </div>
      )}
      {view === "changes" ? (
        <div className="md-preview" data-tailored-lines={stats.total} data-view="changes">
          {lines.map((line, index) => (
            <PreviewLine key={index} line={line} index={index} open={open.has(index)} onToggle={toggle} promptFor={promptFor} showChanges />
          ))}
        </div>
      ) : (
        <div className="clean-wrap" data-tailored-lines={stats.total}>
          <CleanCopy lines={lines} />
        </div>
      )}
      <div className="resume-stats">{statsLine(stats)}</div>
    </>
  );
}

// The panel's state, shared with the "Tailor resume" action.
export function useTailoredResume({ jobIdentity, jobUrl, profileId }) {
  const [stored, setStored] = useState(null);
  const [loadingStored, setLoadingStored] = useState(true);
  const [tailoring, setTailoring] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState(null);
  const [outcome, setOutcome] = useState(null); // uat-bug-043: "done" | "error" after a run, for the status by the button
  const requestKey = useRef(0);

  useEffect(() => {
    const key = ++requestKey.current;
    setStored(null);
    setError(null);
    setOutcome(null);
    setTailoring(false);
    setLoadingStored(true);
    if (!jobIdentity || !profileId) {
      setLoadingStored(false);
      return undefined;
    }
    getTailoredResumes({ profileId, jobIdentity })
      .then((response) => {
        if (requestKey.current === key) {
          setStored(latestStored(response.items));
          setLoadingStored(false);
        }
      })
      .catch(() => {
        if (requestKey.current === key) {
          setLoadingStored(false); // an unreachable list just means "nothing stored to show"
        }
      });
    return () => {
      requestKey.current += 1;
    };
  }, [jobIdentity, profileId]);

  useEffect(() => {
    if (!tailoring) {
      return undefined;
    }
    const startedAt = Date.now();
    setElapsed(0);
    const timer = setInterval(() => setElapsed(Math.round((Date.now() - startedAt) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [tailoring]);

  const tailor = useCallback(() => {
    if (!jobUrl || !profileId) {
      return;
    }
    const key = requestKey.current;
    setTailoring(true);
    setError(null);
    setOutcome(null);
    postTailoredResume({ job: { job_url: jobUrl }, resume: { profile_id: profileId } })
      .then((response) => {
        if (requestKey.current !== key) {
          return;
        }
        setStored(response);
        setOutcome("done");
        setTailoring(false);
      })
      .catch((err) => {
        if (requestKey.current !== key) {
          return;
        }
        setTailoring(false);
        setOutcome("error");
        setError(err instanceof ApiError ? err : { message: err.message || String(err) });
      });
  }, [jobUrl, profileId]);

  return { stored, loadingStored, tailoring, elapsed, error, outcome, tailor, visible: Boolean(stored || tailoring || error) };
}

export default function TailoredResumePanel({ state, profileLabel, questionPrompts }) {
  const promptFor = (id) => (questionPrompts && questionPrompts.get(id)) || null;
  const { stored, tailoring, elapsed, error } = state;
  const panel = useRef(null);

  // uat-bug-043: the action sits above the requirement table and its status
  // sits by the button; when a run finishes (result or error) bring the panel
  // into view.
  const wasTailoring = useRef(false);
  useEffect(() => {
    if (wasTailoring.current && !tailoring && panel.current && typeof panel.current.scrollIntoView === "function") {
      panel.current.scrollIntoView({ block: "start", behavior: "smooth" });
    }
    wasTailoring.current = tailoring;
  }, [tailoring]);

  if (!state.visible) {
    return null;
  }

  return (
    <section className="panel tailored-resume" id="tailored-resume" ref={panel} data-state={tailoring ? "tailoring" : stored ? "stored" : "idle"}>
      <div className="resume-toolbar">
        <h3>Tailored resume</h3>
        {stored && !tailoring && (
          <button type="button" className="button small secondary" onClick={() => saveMarkdown(stored)}>
            Download .md
          </button>
        )}
      </div>
      {error && (
        <div className="callout danger tailor-error" role="alert">
          <strong>{errorView(error).heading}.</strong> {errorView(error).body}
          {errorView(error).hint && <div className="muted" style={{ marginTop: 4, fontSize: "0.82rem" }}>{errorView(error).hint}</div>}
        </div>
      )}
      {stored && <Preview key={stored.updated_at || stored.stored_path} response={stored} profileLabel={profileLabel} promptFor={promptFor} />}
    </section>
  );
}

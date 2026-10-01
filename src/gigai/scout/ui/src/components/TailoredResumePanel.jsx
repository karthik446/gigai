import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getResumeDisplay, getTailoredResumes, postTailoredResume, postTailoredResumePdf, putTailoredResumeLine } from "../api.js";
import { dateTimeLabel } from "../jobModel.js";
import { hasContactLine, savedHeaderLine } from "../resumeDisplayModel.js";
import { SETTINGS_HASH } from "../routing.js";
import {
  changeSummary,
  inlineSegments,
  latestStored,
  lineActions,
  lostLabels,
  previewLines,
  previewStats,
  reasonLabel,
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
//   download POST /api/tailored-resumes/pdf -> the PDF, saved under the
//            server's Content-Disposition name (0.1.10-003; the .md link is
//            gone from the UI, the API's `markdown` field stays)
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
function saveBlob(blob, fileName) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
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

// 0110-006: what the operator can decide about one line, under it: why the
// model rewrote it (its reason), what a rejected rewrite dropped, and the one
// button (Keep original / Use rewrite anyway / Undo). Model text goes in as
// React text children, like everywhere else in the panel.
// 0110-032: an edited line (its text was typed by the operator or their agent,
// PUT use: "custom") says so and offers the way back: Use original / Use rewrite.
function LineControls({ line, onChoose, busy }) {
  const actions = onChoose ? lineActions(line) : [];
  const reason = line.kind === "rewritten" ? reasonLabel(line.reason) : "";
  const fallback = line.origin === "fallback" && line.kind === "copy" && line.alternative && line.alternative.kind === "rewritten";
  const lost = fallback ? lostLabels(line.alternative) : [];
  if (actions.length === 0 && !reason && !fallback && !line.edited) {
    return null;
  }
  return (
    <div className="md-line line-controls" data-line-id={line.id || undefined}>
      <span className="prov blank">·</span>
      <span className="src-list">
        {reason && <span className="src-item muted" data-role="reason">{reason}</span>}
        {fallback && (
          <span className="src-item muted" data-role="kept-original">
            Kept your line: the rewrite dropped {lost.length > 0 ? lost.join("; ") : "a fact"}.
          </span>
        )}
        {line.edited && (
          <span className="src-item muted" data-role="edited">
            Edited: your own text, no source cited.
          </span>
        )}
        {actions.map((action) => (
          <button key={action.use} type="button" className="button small secondary" data-action={action.use} disabled={busy} onClick={() => onChoose(line.id, action.use)}>
            {action.label}
          </button>
        ))}
      </span>
    </div>
  );
}

function PreviewLine({ line, index, open, onToggle, promptFor, showChanges, onChoose, busy }) {
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
      ) : line.edited ? (
        <span className="prov rewritten edited" data-role="edited-mark" title="Edited: your own text, no source cited">
          E
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
            {line.refs.length === 0 && <span className="src-item muted">{line.edited ? "Edited: your own text, no source cited." : "No source cited."}</span>}
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
    {showChanges && <LineControls line={line} onChoose={onChoose} busy={busy} />}
    </>
  );
}

export function Preview({ response, profileLabel, promptFor, initialView = "changes", onChooseLine = null, choiceBusy = false, choiceError = null }) {
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
        {(stats.edited || 0) > 0 && (
          <span>
            <span className="prov rewritten edited">E</span> edited: your own text, no source cited
          </span>
        )}
        <span className="muted">Hover or click a line to see its sources.</span>
      </div>
      )}
      {choiceError && (
        <div className="callout danger" role="alert" data-role="choice-error">
          {choiceError}
        </div>
      )}
      {view === "changes" ? (
        <div className="md-preview" data-tailored-lines={stats.total} data-view="changes">
          {lines.map((line, index) => (
            <PreviewLine key={index} line={line} index={index} open={open.has(index)} onToggle={toggle} promptFor={promptFor} showChanges onChoose={onChooseLine} busy={choiceBusy} />
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

  return { stored, setStored, loadingStored, tailoring, elapsed, error, outcome, tailor, jobIdentity, profileId, visible: Boolean(stored || tailoring || error) };
}

export default function TailoredResumePanel({ state, profileLabel, questionPrompts }) {
  const promptFor = (id) => (questionPrompts && questionPrompts.get(id)) || null;
  const { stored, tailoring, elapsed, error } = state;
  const panel = useRef(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState(null);
  const [hasContact, setHasContact] = useState(true);
  // 0110-013: the saved header on one line ("" while nothing is saved).
  const [headerText, setHeaderText] = useState("");
  const [choosing, setChoosing] = useState(false);
  const [choiceError, setChoiceError] = useState(null);

  // Empty settings still download (name only); the panel then points at the
  // settings section. A failed read just hides the hint.
  useEffect(() => {
    let live = true;
    if (!stored) {
      return undefined;
    }
    getResumeDisplay(state.profileId)
      .then((response) => {
        if (live) {
          setHasContact(hasContactLine(response));
          setHeaderText(savedHeaderLine(response));
        }
      })
      .catch(() => {
        if (live) {
          setHasContact(true);
          setHeaderText("");
        }
      });
    return () => {
      live = false;
    };
  }, [stored, state.profileId]);

  const downloadPdf = useCallback(() => {
    setDownloading(true);
    setDownloadError(null);
    postTailoredResumePdf({ profileId: state.profileId, jobIdentity: state.jobIdentity })
      .then(({ blob, fileName }) => saveBlob(blob, fileName))
      .catch((err) => setDownloadError(err.detail || err.message || String(err)))
      .finally(() => setDownloading(false));
  }, [state.profileId, state.jobIdentity]);

  // 0110-006: PUT one line's choice; the response replaces `stored`, so the
  // clean copy and the PDF follow. A 409 means a newer tailoring replaced this
  // one: reload the stored resume and say so.
  const chooseLine = useCallback(
    (lineId, use) => {
      setChoosing(true);
      setChoiceError(null);
      putTailoredResumeLine({ profileId: state.profileId, jobIdentity: state.jobIdentity, updatedAt: stored.updated_at, lineId, use })
        .then((response) => state.setStored(response))
        .catch((err) => {
          setChoiceError(err.detail || err.message || String(err));
          if (err.code === "tailored_resume_changed") {
            getTailoredResumes({ profileId: state.profileId, jobIdentity: state.jobIdentity })
              .then((response) => state.setStored(latestStored(response.items)))
              .catch(() => {});
          }
        })
        .finally(() => setChoosing(false));
    },
    [stored, state],
  );

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
          <button type="button" className="button small secondary" onClick={downloadPdf} disabled={downloading}>
            {downloading ? "Preparing…" : "Download PDF"}
          </button>
        )}
      </div>
      {stored && !tailoring && headerText && (
        <div className="muted" data-role="pdf-header" style={{ fontSize: "0.82rem" }}>
          PDF header: <span data-role="pdf-header-line">{headerText}</span> ·{" "}
          <a href={`${SETTINGS_HASH}`} data-role="pdf-header-edit" onClick={() => setTimeout(() => document.getElementById("resume-display")?.scrollIntoView(), 0)}>
            Edit
          </a>
        </div>
      )}
      {stored && !tailoring && !hasContact && (
        <div className="muted" data-role="contact-hint" style={{ fontSize: "0.82rem" }}>
          <a href={`${SETTINGS_HASH}`} onClick={() => setTimeout(() => document.getElementById("resume-display")?.scrollIntoView(), 0)}>
            Add your contact line
          </a>{" "}
          to your PDF.
        </div>
      )}
      {downloadError && (
        <div className="callout danger" role="alert">
          Could not make the PDF. {downloadError}
        </div>
      )}
      {error && (
        <div className="callout danger tailor-error" role="alert">
          <strong>{errorView(error).heading}.</strong> {errorView(error).body}
          {errorView(error).hint && <div className="muted" style={{ marginTop: 4, fontSize: "0.82rem" }}>{errorView(error).hint}</div>}
        </div>
      )}
      {stored && <Preview key={stored.updated_at || stored.stored_path} response={stored} profileLabel={profileLabel} promptFor={promptFor} onChooseLine={chooseLine} choiceBusy={choosing} choiceError={choiceError} />}
    </section>
  );
}

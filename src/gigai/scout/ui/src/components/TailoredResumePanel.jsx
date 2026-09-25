import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getTailoredResumes, postTailoredResume } from "../api.js";
import { dateTimeLabel } from "../jobModel.js";
import { downloadName, latestStored, previewLines, previewStats, sourcesHover, sourceLabel, statsLine } from "../tailoredResumeModel.js";

// Q4b-ui (v0.1.9): the tailored-resume panel on the job page's right column
// (mockups/cards-and-job-page.html, "Tailored resume"), over Q3's routes:
//
//   load     GET /api/tailored-resumes?profile_id=<selected>&job_identity=<job>
//            -> the latest stored one, shown with "Tailor again"
//   tailor   POST /api/tailored-resumes {job:{job_url}, resume:{profile_id}}
//            (~20-30 s: one model call, one retry on a rejected draft)
//   errors   422 (server message), 502 model_output_invalid (the draft failed
//            a guard; the message names the line), 504 tailor_timeout
//   preview  tailoredResumeModel.previewLines(result): every content line is
//            the response's own text, with its refs (the cited resume line /
//            answer text) on hover and on click. Nothing is fabricated here.
//   download a client-side Blob of the response's `markdown` verbatim
//
// A posting without a URL (a pasted-text quick assessment: the store never
// serializes the text) cannot be tailored from here; a stored one for it
// still shows.
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

function PreviewLine({ line, index, open, onToggle, promptFor }) {
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
  return (
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
        {line.display}
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
  );
}

function Preview({ response, profileLabel, promptFor }) {
  const [open, setOpen] = useState(() => new Set());
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
      <div className="resume-legend">
        <span>
          <span className="prov resume">R</span> copied verbatim from the resume
        </span>
        <span>
          <span className="prov rewritten">✎</span> rewritten from the resume lines / answers it cites
        </span>
        <span className="muted">Hover or click a line to see its sources.</span>
      </div>
      <div className="md-preview" data-tailored-lines={stats.total}>
        {lines.map((line, index) => (
          <PreviewLine key={index} line={line} index={index} open={open.has(index)} onToggle={toggle} promptFor={promptFor} />
        ))}
      </div>
      <div className="resume-stats">{statsLine(stats)}</div>
    </>
  );
}

export default function TailoredResumePanel({ jobIdentity, jobUrl, profileId, profileLabel, company, questionPrompts }) {
  const promptFor = (id) => (questionPrompts && questionPrompts.get(id)) || null;
  const [stored, setStored] = useState(null);
  const [loadingStored, setLoadingStored] = useState(true);
  const [tailoring, setTailoring] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState(null);
  const requestKey = useRef(0);

  useEffect(() => {
    const key = ++requestKey.current;
    setStored(null);
    setError(null);
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

  const tailor = () => {
    if (!jobUrl || !profileId) {
      return;
    }
    const key = requestKey.current;
    setTailoring(true);
    setError(null);
    postTailoredResume({ job: { job_url: jobUrl }, resume: { profile_id: profileId } })
      .then((response) => {
        if (requestKey.current !== key) {
          return;
        }
        setStored(response);
        setTailoring(false);
      })
      .catch((err) => {
        if (requestKey.current !== key) {
          return;
        }
        setTailoring(false);
        setError(err instanceof ApiError ? err : { message: err.message || String(err) });
      });
  };

  const canTailor = Boolean(jobUrl && profileId);
  const buttonLabel = tailoring ? "Tailoring…" : stored ? "Tailor again" : `Tailor resume${company ? ` for ${company}` : ""}`;

  return (
    <section className="panel tailored-resume" data-state={tailoring ? "tailoring" : stored ? "stored" : "idle"}>
      <h3>Tailored resume</h3>
      {!stored && !tailoring && (
        <p className="muted" style={{ fontSize: "0.85rem", margin: "0 0 10px" }}>
          Builds a markdown resume for this posting from your pinned resume and your recorded answers. Header, section and role headings
          are copied verbatim; only the summary and bullets are rewritten, and every line shows its sources.
        </p>
      )}
      <div className="resume-toolbar">
        {canTailor ? (
          <button type="button" className="button small" disabled={tailoring || loadingStored} onClick={tailor}>
            {buttonLabel}
          </button>
        ) : (
          <span className="muted" style={{ fontSize: "0.82rem" }}>
            This posting has no stored URL or text, so it cannot be tailored from here.
          </span>
        )}
        {stored && !tailoring && (
          <button type="button" className="button small secondary" onClick={() => saveMarkdown(stored)}>
            Download .md
          </button>
        )}
      </div>
      {tailoring && (
        <div className="tailor-progress" role="status">
          <span className="spinner" aria-hidden="true" />
          Tailoring your resume for this posting… this usually takes 20–30 seconds ({elapsed}s).
        </div>
      )}
      {error && (
        <div className="callout danger tailor-error" role="alert">
          <strong>{errorView(error).heading}.</strong> {errorView(error).body}
          {errorView(error).hint && <div className="muted" style={{ marginTop: 4, fontSize: "0.82rem" }}>{errorView(error).hint}</div>}
        </div>
      )}
      {loadingStored && !stored && (
        <p className="muted" style={{ fontSize: "0.82rem", margin: 0 }}>
          Checking for a stored tailored resume…
        </p>
      )}
      {stored && <Preview key={stored.updated_at || stored.stored_path} response={stored} profileLabel={profileLabel} promptFor={promptFor} />}
    </section>
  );
}

import { useEffect, useState } from "react";
import { getTailoredResumes, postResumePdf, postTailoredResumePdf } from "../api.js";
import GeneratePdfForm from "../components/GeneratePdfForm.jsx";
import { latestStored } from "../tailoredResumeModel.js";
import { parsePdfTarget } from "../routing.js";
import { TAILORED_WORDING } from "../wording.js";

// 0110-046: the Generate PDF page, where an agent's or the CLI's PDF is
// finished. `gigai scout resume pdf` and the two PDF routes render without a
// header (GigAI stores no name or contact details) and point here:
//
//   #/pdf/<profile_id>/<job identity>  the stored tailored resume for that job
//   #/pdf                              resume markdown the user picks or pastes
//
// The user types their details in the form; they go in the one render
// request and are dropped. The markdown, too, stays in this page's state.
export default function PdfView({ target }) {
  const { profileId, jobIdentity } = parsePdfTarget(target);
  const [stored, setStored] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [markdown, setMarkdown] = useState("");

  useEffect(() => {
    let live = true;
    setStored(null);
    setLoadError(null);
    if (!jobIdentity) {
      return undefined;
    }
    getTailoredResumes({ profileId, jobIdentity })
      .then((response) => {
        if (!live) {
          return;
        }
        const latest = latestStored(response.items);
        if (latest) {
          setStored(latest);
        } else {
          setLoadError("No tailored resume is stored for this job yet.");
        }
      })
      .catch((err) => live && setLoadError(err.message || String(err)));
    return () => {
      live = false;
    };
  }, [profileId, jobIdentity]);

  function pickFile(event) {
    const file = event.target.files && event.target.files[0];
    if (file) {
      file.text().then(setMarkdown);
    }
  }

  const job = stored && stored.job;
  return (
    <section className="panel" id="generate-pdf" data-role="pdf-view">
      <h2>Generate PDF</h2>
      <p className="muted">
        Your resume PDF gets its header (name and contact details) here, in your browser. An agent or the command line makes the PDF
        without it: GigAI has none of your details to give them.
      </p>
      {jobIdentity ? (
        <>
          {loadError && <div className="callout danger">{loadError}</div>}
          {!stored && !loadError && <p className="muted">Loading…</p>}
          {stored && (
            <>
              <p data-role="pdf-source">
                Tailored resume for <strong>{(job && job.title) || "this job"}</strong>
                {job && job.company ? ` at ${job.company}` : ""}.
              </p>
              <p className="muted small" data-role="tailored-wording">
                {TAILORED_WORDING}
              </p>
              <GeneratePdfForm render={(header) => postTailoredResumePdf({ profileId: profileId || stored.resume.profile_id, jobIdentity, header })} />
            </>
          )}
        </>
      ) : (
        <>
          <div className="form-group">
            <label className="form-label" htmlFor="pdf-markdown-file">
              Resume markdown (the file the agent or <code>gigai scout resume pdf --in</code> used)
            </label>
            <input id="pdf-markdown-file" type="file" accept=".md,.markdown,.txt,text/markdown,text/plain" onChange={pickFile} />
            <textarea
              className="text-input"
              aria-label="Resume markdown"
              rows={8}
              value={markdown}
              onChange={(event) => setMarkdown(event.target.value)}
              placeholder="## Summary"
            />
          </div>
          <GeneratePdfForm disabled={!markdown.trim()} render={(header) => postResumePdf({ markdown, header })} />
        </>
      )}
    </section>
  );
}

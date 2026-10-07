import { editedLine } from "../resumesFolderModel.js";
import { dateTimeLabel } from "../jobModel.js";
import {
  changeSummary,
  inlineSegments,
  previewLines,
  previewStats,
  statsLine,
} from "../tailoredResumeModel.js";
import { lengthNote } from "../tailorLengthModel.js";
import { recordedBasis } from "../masterModel.js";

// The PREVIEW of one stored job resume: the resume as it will print, and the
// same lines with where each came from. The panel around it is
// JobResumePanel.jsx (0.1.11 N6: the suggested resume; it replaced the
// "Tailored resume" panel, its button and the tailor call's status and
// errors, which went with the call).
//
//   preview  tailoredResumeModel.previewLines(result): every content line is
//            the stored resume's own text, with its refs (the master line /
//            the answer text) on hover and on click. Nothing is made up here.
//   view     ONE view (0.1.11.5): the resume as it will print (the PDF's own render, ResumePreview.jsx, with the
//            points list beside it, ResumePoints.jsx). There is no second "Show changes" / "Clean copy" view: a
//            changed point is marked "Your words" in the points list, and the Changed tab (PickedLeftOut.jsx) lists
//            the changes with their sources and Restore
//   length   what was cut for length, with its ONE Restore (0110-10-05 C)
//
// The store, its files and its routes keep the names they had
// (/api/tailored-resumes): the file here keeps its name for the same reason.
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
        if (line.kind === "heading" && line.level === 3) {
          return (
            <h5 className="clean-entry" key={index} data-role={line.earlier ? "earlier-heading" : undefined}>
              {line.text}
            </h5>
          );
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
          <p className="clean-text" key={index} data-role={line.earlier ? "earlier-role" : undefined}>
            <Inline text={line.plain} />
          </p>
        );
      })}
    </div>
  );
}

// `provenance` is JobResumePanel's one line ("Picked by the assessment from your master (revision 5) · 27 lines ·
// 2 pages"). `rendered(text)` is the job page's rendered preview (ResumePreview.jsx: the resume as the PDF prints
// it, with the spacing slider and the points list); it gets the clean copy as the text behind the pictures.
export function Preview({ response, provenance = null, choiceBusy = false, choiceError = null, onLength = null, rendered }) {
  const lines = previewLines(response.result, response.markdown);
  const stats = previewStats(lines);
  // 0110-10-05 C: what was left out for length, and the way back.
  const length = lengthNote(response);
  // 0110-10-10 item 3: what the stored resume records it was made from (the master, or the profile's own resume).
  const basis = recordedBasis(response);
  return (
    <>
      <div className="tailor-meta" title={response.updated_at || undefined} data-basis={basis}>
        <span data-role="provenance" data-origin={provenance ? provenance.origin || undefined : undefined} data-picked-by={provenance ? provenance.pickedBy || undefined : undefined}>
          {provenance ? provenance.text : "Stored resume"}
        </span>{" "}
        · stored {dateTimeLabel(response.updated_at) || "just now"}
        {editedLine(response) && <span data-role="edited-by"> · {editedLine(response)}</span>}
      </div>
      <div className="resume-change-bar">
        <div className="resume-summary" data-testid="change-summary">
          {changeSummary(stats)}
        </div>
      </div>
      {length && (
        <div className="callout info length-note" data-testid="length-note" data-status={length.status}>
          <span>{length.text}</span>{" "}
          {length.action && onLength && (
            <button type="button" className="button small secondary" data-action={`length-${length.action.use}`} disabled={choiceBusy} onClick={() => onLength(length.action.use)}>
              {length.action.label}
            </button>
          )}
        </div>
      )}
      {choiceError && (
        <div className="callout danger" role="alert" data-role="choice-error">
          {choiceError}
        </div>
      )}
      {rendered(<CleanCopy lines={lines} />)}
      <div className="resume-stats">{statsLine(stats)}</div>
    </>
  );
}

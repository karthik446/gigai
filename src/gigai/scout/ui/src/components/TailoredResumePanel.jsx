import { useCallback, useState } from "react";
import { editedLine } from "../resumesFolderModel.js";
import { dateTimeLabel } from "../jobModel.js";
import {
  changeSummary,
  inlineSegments,
  lineActions,
  lostLabels,
  previewLines,
  previewStats,
  reasonLabel,
  sourcesHover,
  sourceLabel,
  statsLine,
} from "../tailoredResumeModel.js";
import { lengthNote } from "../tailorLengthModel.js";
import { recordedBasis, saveWordingTarget } from "../masterModel.js";

// The PREVIEW of one stored job resume: the resume as it will print, and the
// same lines with where each came from. The panel around it is
// JobResumePanel.jsx (0.1.11 N6: the suggested resume; it replaced the
// "Tailored resume" panel, its button and the tailor call's status and
// errors, which went with the call).
//
//   preview  tailoredResumeModel.previewLines(result): every content line is
//            the stored resume's own text, with its refs (the master line /
//            the answer text) on hover and on click. Nothing is made up here.
//   views    "Preview" is the resume as it will print (0.1.11.5: the PDF's
//            own render, ResumePreview.jsx; "Clean copy", the same lines as
//            text, where there is no stored job to render); "Show changes"
//            marks each line: R copied word for word, a pencil for a line
//            whose wording was changed (in chat by the user's agent, which
//            cites its sources; or by the tailoring of 0.1.10, labelled
//            "reworded by the old tailor"), E for typed text
//   length   what was cut for length, with its ONE Restore (0110-10-05 C)
//
// The store, its files and its routes keep the names they had
// (/api/tailored-resumes): the file here keeps its name for the same reason.
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

// 0110-006: what the operator can decide about one line, under it: why the
// model rewrote it (its reason), what a rejected rewrite dropped, and the one
// button (Keep original / Use rewrite anyway / Undo). Model text goes in as
// React text children, like everywhere else in the panel.
// 0110-032: an edited line (its text was typed by the operator or their agent,
// PUT use: "custom") says so and offers the way back: Use original / Use rewrite.
// 0.1.10.9 master P5: an edited line that replaced a line of the master offers
// "Save this wording to your master" (`onSaveWording`; `saved` names the line
// it was saved for and what the page says about it).
function LineControls({ line, onChoose, busy, onSaveWording = null, saved = null }) {
  const actions = onChoose ? lineActions(line) : [];
  const wording = onSaveWording ? saveWordingTarget(line) : null;
  const reason = line.kind === "rewritten" ? reasonLabel(line.reason) : "";
  const fallback = line.origin === "fallback" && line.kind === "copy" && line.alternative && line.alternative.kind === "rewritten";
  const lost = fallback ? lostLabels(line.alternative) : [];
  // 0.1.11 (SPEC 4.3): a line a model reworded can only be the tailoring of 0.1.10's (0.1.11 rewords nothing).
  const oldTailor = line.kind === "rewritten" && line.origin === "model";
  if (actions.length === 0 && !reason && !fallback && !line.edited && !oldTailor) {
    return null;
  }
  return (
    <div className="md-line line-controls" data-line-id={line.id || undefined}>
      <span className="prov blank">·</span>
      <span className="src-list">
        {oldTailor && <span className="src-item muted" data-role="old-tailor">reworded by the old tailor</span>}
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
        {wording && (
          <button type="button" className="button small secondary" data-action="save-wording" disabled={busy} onClick={() => onSaveWording(line.id, wording)}>
            Save this wording to your master
          </button>
        )}
        {saved && saved.lineId === line.id && (
          <span className="src-item muted" data-role="wording-saved">
            {saved.text}
          </span>
        )}
      </span>
    </div>
  );
}

function PreviewLine({ line, index, open, onToggle, promptFor, showChanges, onChoose, busy, onSaveWording, saved }) {
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
      <div className="md-line heading" data-role={line.earlier ? "earlier-heading" : undefined}>
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
      data-role={line.earlier ? "earlier-role" : undefined}
      onClick={() => onToggle(index)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onToggle(index);
        }
      }}
    >
      {copied ? (
        <span className="prov resume" title="Copied word for word">
          R
        </span>
      ) : line.edited ? (
        <span className="prov rewritten edited" data-role="edited-mark" title="Edited: your own text, no source cited">
          E
        </span>
      ) : (
        <span className="prov rewritten" title="Reworded; click for sources">
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
    {showChanges && <LineControls line={line} onChoose={onChoose} busy={busy} onSaveWording={onSaveWording} saved={saved} />}
    </>
  );
}

// `provenance` is JobResumePanel's one line ("Picked by the assessment from your master (revision 5) · 27 lines ·
// 2 pages"); `initialView` is "clean" (the resume as it will print) unless the caller has changed lines to show.
// 0.1.11.5 (a): `rendered` is the job page's rendered preview (ResumePreview.jsx: the resume as the PDF prints it,
// with the spacing slider). It takes the clean copy's place: `rendered(text)` gets the clean copy as the text behind
// the pictures. Without it (a caller with no stored job) the clean copy shows as text, as before.
export function Preview({ response, provenance = null, promptFor, initialView = "changes", onChooseLine = null, choiceBusy = false, choiceError = null, onLength = null, onSaveWording = null, wordingSaved = null, rendered = null }) {
  const [open, setOpen] = useState(() => new Set());
  const [view, setView] = useState(initialView); // "changes" | "clean"
  const lines = previewLines(response.result, response.markdown);
  const stats = previewStats(lines);
  // 0110-10-05 C: what was left out for length, and the way back.
  const length = lengthNote(response);
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
  // 0110-10-10 item 3: what the stored resume records it was made from (the master, or the profile's own resume).
  const basis = recordedBasis(response);
  const source = basis === "master" ? "your master" : "the resume";
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
        <div className="view-toggle" role="group" aria-label="Resume view">
          <button type="button" data-action="view-changes" className={`button small ${view === "changes" ? "" : "secondary"}`} aria-pressed={view === "changes"} onClick={() => setView("changes")}>
            Show changes
          </button>
          <button type="button" data-action="view-clean" className={`button small ${view === "clean" ? "" : "secondary"}`} aria-pressed={view === "clean"} onClick={() => setView("clean")}>
            {rendered ? "Preview" : "Clean copy"}
          </button>
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
      {view === "changes" && (
      <div className="resume-legend">
        <span>
          <span className="prov resume">R</span> copied word for word from {source}
        </span>
        {stats.rewritten > 0 && (
          <span>
            <span className="prov rewritten">✎</span> reworded, from the lines and answers it cites
          </span>
        )}
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
            <PreviewLine key={index} line={line} index={index} open={open.has(index)} onToggle={toggle} promptFor={promptFor} showChanges onChoose={onChooseLine} busy={choiceBusy} onSaveWording={onSaveWording} saved={wordingSaved} />
          ))}
        </div>
      ) : rendered ? (
        rendered(<CleanCopy lines={lines} />)
      ) : (
        <div className="clean-wrap" data-tailored-lines={stats.total}>
          <CleanCopy lines={lines} />
        </div>
      )}
      <div className="resume-stats">{statsLine(stats)}</div>
    </>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import {
  getJobSuggestions,
  getMaster,
  getJobsFolder,
  openJobsFolder,
  getTailoredResumes,
  postJobResumePick,
  putMasterLine,
  putTailoredResumeLength,
  putTailoredResumeLine,
  putTailoredResumeSelection,
} from "../api.js";
import { jobFilePath, openFolderNote } from "../resumesFolderModel.js";
import { TAILORED_WORDING } from "../wording.js";
import { latestStored, newerStored } from "../tailoredResumeModel.js";
import { NO_EDUCATION_TEXT, conflictOf } from "../masterModel.js";
import {
  DRAFT_LABEL,
  NO_MASTER_TEXT,
  NO_RESUME_OLD_ASSESSMENT_TEXT,
  OWN_RESUME_TEXT,
  REASSESS_LABEL,
  assessmentIsOld,
  attentionItems,
  changedLines,
  gateHolds,
  holdSentence,
  isUsersResume,
  needsEducationNotice,
  pickErrorText,
  printedWords,
  proposedChange,
  proposedReplaces,
  proposedSummary,
  PROPOSED_KEEPS_TEXT,
  PROPOSED_READY_TEXT,
  provenanceLine,
  resumeOrigin,
  selectionErrorText,
  servesView,
  staleActions,
  staleLineFixes,
  STALE_PICKED_LINE,
  EDITED_KEEPS_TEXT,
  suggestionsAnswer,
  unpickable,
} from "../jobResumeModel.js";
import { MASTER_HASH } from "../routing.js";
import { headerBody } from "../generatePdfModel.js";
import { previewHeader } from "../resumePreviewModel.js";
import ResumePreview from "./ResumePreview.jsx";
import ResumePoints from "./ResumePoints.jsx";
import ApplyPanel from "./ApplyPanel.jsx";
import PickedLeftOut from "./PickedLeftOut.jsx";
import { Preview } from "./TailoredResumePanel.jsx";

// 0.1.11 N6 (SPEC section 6, item 4): the SUGGESTED RESUME of one job. It
// replaces the "Tailored resume" panel: no model writes a resume any more.
// The resume is picked from the master when the job is assessed, by the
// assessment (or by Scout's own rules when its pick cannot be used), and only
// for a job the gate lets through.
//
//   reads    GET /api/tailored-resumes?profile_id=&job_identity=  the stored
//            job resume (the store kept its path), and GET
//            /api/jobs/suggestions the job's suggestion record with its
//            `stale` list and a waiting `proposed` resume. Opening a job
//            recomputes nothing and writes nothing (SPEC A5)
//   shows    gate `suggest`: the resume as it will print and ONE provenance
//            line; a conflict or `ready: false`: a banner above the resume
//            naming each requirement and line; stale: the label and the
//            refresh buttons, each with its cost in the button (a line the
//            master retired or reworded is NAMED, with its no-model fix
//            first); `proposed`: "A new resume is ready for this job:
//            Compare · Use it · Dismiss" at the top of the card; the gate holds: no resume, one sentence, a link to the
//            questions and "Make a draft anyway"; no master, or a profile
//            on a resume put in by hand: the profile's own resume is used as
//            it is, and the page says how to get one picked (no dead button);
//            a suggested resume that is not stored: "Pick it now"; a pick
//            that is refused: one plain sentence by its code. The resume
//            ALWAYS opens on "Preview" (0.1.11.5: the rendered pages with the
//            points beside them), changed lines or not; "Show changes" is one
//            click away
//   writes   only on a click, and never through a model: POST
//            /api/job-resumes/pick (re-pick, draft, use / dismiss proposed),
//            the per-line Restore, the length Restore, Add and Remove
//
// The state both this panel and the page's other sections share is
// useJobResume() below (Requirements shows each row's coverage, Suggestions
// and Apply read the record and the stale list).
// `expectRecord`: the stored assessment carries a `resume_gate` (made by 0.1.11), so a suggestion record exists; a
// legacy job asks the suggestion route nothing (it would answer 404).
export function useJobResume({ jobIdentity, jobUrl, profileId, expectRecord = false }) {
  const [stored, setStored] = useState(null);
  const [loadingStored, setLoadingStored] = useState(true);
  const [suggested, setSuggested] = useState(null); // GET /api/jobs/suggestions' answer
  const [view, setView] = useState(null); // the answer of the last POST /api/job-resumes/pick: stale, conflicts, proposed
  const answer = suggestionsAnswer(suggested, view); // {record, stale, origin}
  const [picking, setPicking] = useState(null); // the `use` of the pick request in flight
  const [error, setError] = useState(null);
  const [changes, setChanges] = useState(0); // resumes stored or edited on THIS page: the pipeline takes each up, so the job page reads its timeline again
  const [focus, setFocus] = useState(null); // {line, at}: a master line to show under Left out
  const requestKey = useRef(0);

  const readRecord = useCallback(
    (key) =>
      getJobSuggestions({ jobIdentity, profileId, expect: expectRecord })
        .then((response) => {
          if (requestKey.current !== key) {
            return;
          }
          setSuggested(response);
          if (servesView(response)) {
            setView(null); // a read made after the last step is the newest answer: that step's view no longer hides it
          }
        })
        .catch(() => {}), // the record is extra: the page shows the stored resume and the assessment without it
    [jobIdentity, profileId, expectRecord],
  );

  const replaceStored = useCallback(
    (response) => {
      setStored(response);
      setChanges((count) => count + 1);
      readRecord(requestKey.current); // what the resume prints changed: the final-selection check is the server's
    },
    [readRecord],
  );

  useEffect(() => {
    const key = ++requestKey.current;
    setStored(null);
    setSuggested(null);
    setView(null);
    setError(null);
    setPicking(null);
    setFocus(null);
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
    readRecord(key);
    return () => {
      requestKey.current += 1;
    };
  }, [jobIdentity, profileId, readRecord]);

  // The background pipeline stored a resume for this job (the timeline says
  // its pick step finished), or the job was assessed again on this page: read
  // both again, quietly. What the page shows stays until the answers arrive,
  // and `changes` does not move, so the timeline that reported it is not read
  // again for it.
  const reload = useCallback(() => {
    if (!jobIdentity || !profileId) {
      return Promise.resolve();
    }
    const key = requestKey.current;
    readRecord(key);
    return getTailoredResumes({ profileId, jobIdentity })
      .then((response) => requestKey.current === key && setStored((held) => newerStored(held, latestStored(response.items))))
      .catch(() => {}); // what is shown stays; the next finished pick step, or opening the job again, reads it
  }, [jobIdentity, profileId, readRecord]);

  // The code-only pick: "refresh" | "draft" | "use_proposed" | "dismiss_proposed". No model call. Whatever the
  // route answers, the stored resume and the record are read again: the page shows what is stored.
  const pick = useCallback(
    (use) => {
      if (!profileId || (!jobUrl && !jobIdentity)) {
        return Promise.resolve();
      }
      const key = requestKey.current;
      setPicking(use);
      setError(null);
      return postJobResumePick({ jobUrl: jobUrl || jobIdentity, profileId, action: use })
        .then((viewAfter) => {
          if (requestKey.current === key) {
            setView(viewAfter);
          }
          return getTailoredResumes({ profileId, jobIdentity });
        })
        .then((response) => {
          if (requestKey.current !== key) {
            return;
          }
          setStored(latestStored(response.items));
          setChanges((count) => count + 1);
          return readRecord(key);
        })
        // Said in the page's own words, by the refusal's code: never the server's text (it names commands).
        .catch((err) => requestKey.current === key && setError(pickErrorText(err)))
        .finally(() => requestKey.current === key && setPicking(null));
    },
    [jobUrl, jobIdentity, profileId, readRecord],
  );

  const showLine = useCallback((line) => setFocus({ line, at: Date.now() }), []);

  return {
    stored,
    setStored: replaceStored,
    record: answer.record,
    stale: answer.stale,
    origin: resumeOrigin(stored, { served: answer.origin, record: answer.record }),
    reloadRecord: () => readRecord(requestKey.current),
    changes,
    reload,
    loadingStored,
    picking,
    pick,
    error,
    focus,
    showLine,
    jobIdentity,
    jobUrl,
    profileId,
  };
}

function Attention({ items, onShowLine }) {
  if (items.length === 0) {
    return null;
  }
  return (
    <div className="callout danger" role="alert" data-role="needs-attention">
      <strong>Needs attention.</strong>
      <ul>
        {items.map((item, index) => (
          <li key={`${item.code}-${item.requirement || index}`} data-code={item.code} data-requirement={item.requirement || undefined}>
            {item.text}
            {item.lines.map((line) => (
              <button key={line} type="button" className="link-button" data-action="show-line" data-line={line} onClick={() => onShowLine(line)}>
                show {line} under Left out
              </button>
            ))}
          </li>
        ))}
      </ul>
    </div>
  );
}

// `fixes` (jobResumeModel.staleLineFixes): the printed lines behind "a line this resume prints was changed or retired",
// each named by its words with the fix that needs no model; the refresh buttons stay as the other choice.
function Stale({ items, busy, picking, onRepick, onReassess, reassess, yours, fixes = [], onFix }) {
  if (items.length === 0) {
    return null;
  }
  const actions = staleActions(items);
  const warn = items.some((item) => !item.note);
  // The fix that needs no model is offered FIRST: the named lines, then the other reasons and the refresh buttons.
  const named = (item) => item.code === STALE_PICKED_LINE && fixes.length > 0;
  const ordered = [...items.filter(named), ...items.filter((item) => !named(item))];
  return (
    <div className={`callout ${warn ? "" : "info"}`} data-role="resume-stale">
      <ul>
        {ordered.map((item) =>
          named(item) ? (
            fixes.map((fix) => (
              <li key={`${item.code}-${fix.id}`} data-stale={item.code} data-change={fix.change}>
                Stale: {fix.label}.{" "}
                <button type="button" className="button small secondary" data-action={fix.action.use === "remove" ? "remove-stale-line" : "reword-stale-line"} disabled={busy} onClick={() => onFix(fix)}>
                  {fix.action.label}
                </button>
              </li>
            ))
          ) : (
            <li key={item.code} data-stale={item.code} data-note={item.note ? "true" : undefined}>
              {item.note ? "Note" : "Stale"}: {item.label}.
            </li>
          ),
        )}
      </ul>
      {actions.map((action) =>
        action.use === "repick" ? (
          <button key="repick" type="button" className="button small secondary" data-action="repick" disabled={busy} onClick={onRepick}>
            {picking === "refresh" ? "Picking…" : action.label}
          </button>
        ) : (
          <span key="reassess" title={reassess && !reassess.enabled ? reassess.reason : undefined}>
            <button type="button" className="button small secondary" data-action="reassess-stale" disabled={busy || !reassess || !reassess.enabled} onClick={onReassess}>
              {action.label}
            </button>
          </span>
        ),
      )}
      <span className="muted small"> Nothing refreshes by itself.</span>
      {yours && actions.some((action) => action.use === "repick") && (
        <p className="muted small" data-role="edited-keeps">
          {EDITED_KEEPS_TEXT}
        </p>
      )}
    </div>
  );
}

// "A new resume is ready for this job": Compare · Use it · Dismiss, at the TOP of the card, with what it changes
// (lines added and left out) and which edited points Use it would drop. Compare lists what it would add and drop, by
// master line (the texts are the master's, read when Compare is first opened).
// `replaces` (jobResumeModel.proposedReplaces): what Use it would cost a resume the user edited; null: nothing extra.
// `printed`: the words this resume prints per master line, for a line the master no longer holds (never its id).
function Proposed({ change, replaces, printed = {}, busy, picking, onUse, onDismiss }) {
  const [open, setOpen] = useState(false);
  const [master, setMaster] = useState(null);
  useEffect(() => {
    if (!open || master) {
      return undefined;
    }
    let current = true;
    getMaster()
      .then((response) => current && setMaster(response.master || { items: [] }))
      .catch(() => current && setMaster({ items: [] }));
    return () => {
      current = false;
    };
  }, [open, master]);
  if (!change) {
    return null;
  }
  const texts = new Map(((master && master.items) || []).map((item) => [item.id, item.text]));
  const lines = (ids, role) => (
    <ul data-role={role}>
      {ids.length === 0 && <li className="muted">none</li>}
      {ids.map((id) => (
        <li key={id} data-line={id}>
          {texts.get(id) || printed[id] || id}
        </li>
      ))}
    </ul>
  );
  return (
    <div className="callout info proposed-ready" data-role="proposed">
      <strong data-role="proposed-ready">{PROPOSED_READY_TEXT}</strong> <span>{PROPOSED_KEEPS_TEXT}</span>{" "}
      <button type="button" className="button small secondary" data-action="compare-proposed" aria-expanded={open} onClick={() => setOpen((shown) => !shown)}>
        Compare
      </button>{" "}
      <button type="button" className="button small" data-action="use-proposed" disabled={busy} onClick={onUse}>
        {picking === "use_proposed" ? "Replacing…" : "Use it"}
      </button>{" "}
      <button type="button" className="button small secondary" data-action="dismiss-proposed" disabled={busy} onClick={onDismiss}>
        Dismiss
      </button>
      {proposedSummary(change) && <p data-role="proposed-changes">{proposedSummary(change)}</p>}
      {replaces && (
        <div data-role="proposed-replaces">
          <p>{replaces.text}</p>
          <ul>
            {replaces.points.map((words, index) => (
              <li key={`${index}-${words}`}>{words}</li>
            ))}
          </ul>
        </div>
      )}
      {open && (
        <div data-role="proposed-compare">
          {change.adds === null ? (
            <p data-role="proposed-summary">
              Picked by {change.pickedBy === "code" ? "Scout's own rules" : "the assessment"}
              {change.draft ? " (a draft)" : ""}
              {change.pages ? ` · ${change.pages} pages` : ""}
              {change.conflicts ? ` · ${change.conflicts} conflict${change.conflicts === 1 ? "" : "s"}` : ""}. The server does not list its lines; Use it replaces yours with it.
            </p>
          ) : (
            <>
              <div className="label">The new one would add ({change.adds.length})</div>
              {lines(change.adds, "proposed-adds")}
              <div className="label">and leave out ({change.drops.length})</div>
              {lines(change.drops, "proposed-drops")}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function scrollToQuestions() {
  const section = document.getElementById("job-questions");
  if (section && typeof section.scrollIntoView === "function") {
    section.scrollIntoView({ block: "start", behavior: "smooth" });
  }
}

// `gate` is jobResumeModel.gateOf(), `items` staleItems(); `reassess` is the page's ONE Re-assess
// ({enabled, reason, onClick}: the stale label's "Re-assess · 1 model call" is the same action).
export default function JobResumePanel({ state, assessment, gate, items, reassess, questionPrompts, hasQuestions = false, visaRequired = false }) {
  const promptFor = (id) => (questionPrompts && questionPrompts.get(id)) || null;
  const { stored, record, origin, picking } = state;
  const [choosing, setChoosing] = useState(false);
  const [choiceError, setChoiceError] = useState(null);
  // 0.1.11.4 J3: where this job's resume is in the jobs folder (<company>/<role>/resume.md); asked
  // again when the stored resume changes (a line choice rewrites the file).
  const [folderFile, setFolderFile] = useState("");
  const [folderNote, setFolderNote] = useState("");
  const storedStamp = stored ? `${stored.updated_at}|${stored.markdown ? stored.markdown.length : 0}` : "";
  // 0.1.11.5 (a): the rendered preview. Its header is what Generate PDF would send: the form's values while the
  // form is open (`formHeader`; the page holds them no longer than the form does). With the form closed the preview
  // shows the header the server has for it (0.1.11.5 PH: the person's header file, else a placeholder header of the
  // same size); the page holds none of it. `previewSpacing` is the spacing on screen.
  const [formHeader, setFormHeader] = useState(undefined);
  const [previewSpacing, setPreviewSpacing] = useState(null);
  useEffect(() => {
    setPreviewSpacing(null);
    setFormHeader(undefined);
  }, [state.profileId, state.jobIdentity]);
  const onFormValues = useCallback((values) => setFormHeader(values === undefined ? undefined : previewHeader(headerBody(values))), []);
  useEffect(() => {
    let current = true;
    setFolderFile("");
    setFolderNote("");
    if (!storedStamp || !state.profileId || !state.jobIdentity) {
      return undefined;
    }
    getJobsFolder({ profileId: state.profileId, jobIdentity: state.jobIdentity })
      .then((response) => current && setFolderFile(jobFilePath(response)))
      .catch(() => {}); // the folder line is extra: the panel works without it
    return () => {
      current = false;
    };
  }, [storedStamp, state.profileId, state.jobIdentity]);

  // "Open folder": the server finds the job's folder and asks the computer to show it; when it cannot, the note says
  // so with the path to copy.
  const openFolder = useCallback(() => {
    setFolderNote("");
    openJobsFolder({ profileId: state.profileId, jobIdentity: state.jobIdentity })
      .then((response) => setFolderNote(openFolderNote(response, null)))
      .catch((caught) => setFolderNote(openFolderNote(null, caught)));
  }, [state.profileId, state.jobIdentity]);

  // One write of the stored resume; the response replaces `stored`, so the
  // clean copy and the PDF follow. A 409 means a newer resume replaced this
  // one: reload the stored resume and say so.
  const change = useCallback(
    (send) => {
      setChoosing(true);
      setChoiceError(null);
      send()
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
    [state],
  );
  // 0110-006: one line's choice (the master line, the changed wording): the per-line Restore is `use: "original"`.
  const chooseLine = useCallback(
    (lineId, use) => change(() => putTailoredResumeLine({ profileId: state.profileId, jobIdentity: state.jobIdentity, updatedAt: stored.updated_at, lineId, use })),
    [change, stored, state],
  );
  // 0110-10-05 C: Restore / Cut for length again.
  const changeLength = useCallback(
    (use) => change(() => putTailoredResumeLength({ profileId: state.profileId, jobIdentity: state.jobIdentity, updatedAt: stored.updated_at, use })),
    [change, stored, state],
  );

  // The no-model fix of a line the master retired or reworded (the stale notice): take the point off this resume, or
  // put the master's words as they are now on it. Both are the points list's own writes; the stale list is read again.
  const fixStaleLine = useCallback(
    (fix) =>
      change(() => {
        const key = { profileId: state.profileId, jobIdentity: state.jobIdentity, updatedAt: stored.updated_at };
        if (fix.action.use === "remove") {
          return putTailoredResumeSelection({ ...key, use: "remove", itemId: fix.id });
        }
        return getMaster().then((body) => {
          const line = ((body.master && body.master.items) || []).find((item) => item.id === fix.id);
          if (!line) {
            throw new Error("That line is no longer in your master. Remove it from this resume instead.");
          }
          return putTailoredResumeLine({ ...key, lineId: fix.lineId, use: "custom", text: line.text });
        });
      }),
    [change, stored, state],
  );

  // 0.1.10.9 master P5: "Save this wording to your master". The master is
  // read for its revision, then the line is written on top of it; when the
  // agent wrote the master in between, the write is refused and says so.
  const [wordingSaved, setWordingSaved] = useState(null);
  const saveWording = useCallback((lineId, wording) => {
    setChoosing(true);
    setChoiceError(null);
    setWordingSaved(null);
    getMaster()
      .then((body) => {
        if (!body.master) {
          throw new Error("There is no master resume to save it to.");
        }
        return putMasterLine({ revision: body.master.revision, id: wording.id, use: "edit", text: wording.text });
      })
      .then((response) =>
        setWordingSaved({
          lineId,
          text: response.status === "unchanged" ? "Your master already says this." : `Saved to your master (revision ${response.master.revision}). Other jobs and profiles use it from now on.`,
        }),
      )
      .catch((err) => {
        const conflict = conflictOf(err);
        setChoiceError(conflict ? "Not saved: your master changed a moment ago. Try again." : err.detail || err.message || String(err));
      })
      .finally(() => setChoosing(false));
  }, []);

  if (!assessment || state.loadingStored || !state.profileId) {
    return null; // nothing is suggested for a job that is not assessed; the page says that above
  }

  const holds = gateHolds(gate);
  const sentence = holdSentence(gate, assessment);
  const busy = choosing || picking !== null;
  const provenance = provenanceLine({ stored, record, origin });
  const attention = stored ? attentionItems({ record, assessment, stored }) : [];
  const proposed = stored ? proposedChange(record, stored) : null;
  const changed = stored ? changedLines(stored) : [];
  const users = isUsersResume(origin);
  const heading = stored && users ? "Your resume for this job" : "Suggested resume";
  const cannotPick = unpickable(record); // "no_master" | "own_resume" | null
  const dataState = stored ? "stored" : holds ? "held" : cannotPick === "no_master" ? "no-master" : cannotPick === "own_resume" ? "own-resume" : "none";
  const notPickedWhy = selectionErrorText(record && record.selection_error);
  // A pick from an old assessment is refused (a new selection never sits beside scores made on other evidence):
  // the page offers the step that works, Re-assess, instead of a button that answers a refusal.
  const oldAssessment = assessmentIsOld(items);

  return (
    <section
      className="panel tailored-resume job-resume"
      id="job-resume"
      data-testid="job-resume"
      data-state={dataState}
      data-gate={gate ? gate.decision : undefined}
      data-ready={gate && gate.ready !== null ? String(gate.ready) : undefined}
      data-origin={origin || undefined}
      data-draft={provenance && provenance.draft ? "true" : undefined}
    >
      <div className="resume-toolbar">
        <h3>{heading}</h3>
      </div>
      {/* 0.1.11.5 SP: a resume that waits is the card's FIRST line and its primary action, above the stale list. */}
      {stored && (
        <Proposed change={proposed} replaces={proposedReplaces(stored)} printed={printedWords(stored)} busy={busy} picking={picking} onUse={() => state.pick("use_proposed")} onDismiss={() => state.pick("dismiss_proposed")} />
      )}
      <ApplyPanel state={state} items={items} reassess={reassess} visaRequired={visaRequired} spacing={previewSpacing} onHeader={onFormValues} />
      {state.error && (
        <div className="callout danger" role="alert" data-role="pick-error">
          {state.error}
        </div>
      )}

      {holds && (
        <div className="callout" data-role="resume-held" data-decision={gate.decision}>
          <span data-role="hold-sentence">
            {stored ? "This job is held: " : "No resume is suggested for this job: "}
            {sentence}.
          </span>{" "}
          {hasQuestions && (
            <button type="button" className="link-button" data-action="go-to-questions" onClick={scrollToQuestions}>
              Go to the questions
            </button>
          )}{" "}
          {!stored && (
            <button type="button" className="button small secondary" data-action="make-draft" disabled={busy} title="Picked by Scout's own rules from your master. No model call. It is marked as a draft." onClick={() => state.pick("draft")}>
              {picking === "draft" ? "Making the draft…" : DRAFT_LABEL}
            </button>
          )}
        </div>
      )}

      {!stored && !holds && cannotPick === "no_master" && (
        <p data-role="no-master">
          {NO_MASTER_TEXT}: <a href={MASTER_HASH}>open the Master page</a>.
        </p>
      )}
      {!stored && !holds && cannotPick === "own_resume" && (
        <p data-role="own-resume">
          {OWN_RESUME_TEXT}: <a href={MASTER_HASH}>open the Master page</a>.
        </p>
      )}
      {!stored && !holds && !cannotPick && oldAssessment && (
        <p data-role="no-resume" data-old-assessment="true">
          {NO_RESUME_OLD_ASSESSMENT_TEXT}{" "}
          <span title={reassess && !reassess.enabled ? reassess.reason : undefined}>
            <button type="button" className="button small secondary" data-action="reassess-stale" disabled={busy || !reassess || !reassess.enabled} onClick={reassess ? reassess.onClick : undefined}>
              {REASSESS_LABEL}
            </button>
          </span>
        </p>
      )}
      {!stored && !holds && !cannotPick && !oldAssessment && (
        <p data-role="no-resume">
          No resume is stored for this job yet.{notPickedWhy ? ` ${notPickedWhy}` : ""}{" "}
          <button type="button" className="button small secondary" data-action="repick" disabled={busy} onClick={() => state.pick("refresh")}>
            {picking === "refresh" ? "Picking…" : "Pick it now · no model call"}
          </button>
        </p>
      )}

      {stored && (
        <>
          <Attention items={attention} onShowLine={state.showLine} />
          <Stale items={items} busy={busy} picking={picking} onRepick={() => state.pick("refresh")} onReassess={reassess ? reassess.onClick : undefined} reassess={reassess} yours={users} fixes={staleLineFixes(record, stored)} onFix={fixStaleLine} />
          {provenance && provenance.draft && (
            <p className="muted small" data-role="draft-note">
              This is a draft: you asked for it on a job the assessment holds. It is not a suggested resume.
            </p>
          )}
          <p className="muted small" data-role="tailored-wording">
            {TAILORED_WORDING}
          </p>
          {needsEducationNotice(record, stored) && (
            <p data-role="no-education">
              {NO_EDUCATION_TEXT} This resume prints none: <a href={MASTER_HASH}>add it on the Master page</a>.
            </p>
          )}
          {folderFile && (
            <p className="muted small" data-testid="jobs-folder-file">
              In your jobs folder: <code>{folderFile}</code>{" "}
              <button type="button" className="link-button" data-testid="jobs-folder-open" onClick={openFolder}>
                Open folder
              </button>
              {folderNote && (
                <span data-testid="jobs-folder-note">
                  {" "}
                  {folderNote}
                </span>
              )}
            </p>
          )}
          <PickedLeftOut stored={stored} state={state} record={record} assessment={assessment} origin={origin} changed={changed} busy={busy} onRestoreLine={(lineId, use) => chooseLine(lineId, use)} />
          <Preview
            key={stored.updated_at || stored.stored_path}
            response={stored}
            provenance={provenance}
            promptFor={promptFor}
            initialView="clean"
            onChooseLine={chooseLine}
            choiceBusy={busy}
            choiceError={choiceError}
            onLength={changeLength}
            onSaveWording={stored.selection ? saveWording : null}
            wordingSaved={wordingSaved}
            rendered={(text) => (
              <ResumePreview
                profileId={state.profileId}
                jobIdentity={state.jobIdentity}
                header={formHeader || null}
                onSpacing={setPreviewSpacing}
                content={stored.markdown || ""}
                side={<ResumePoints stored={stored} state={state} />}
              >
                {text}
              </ResumePreview>
            )}
          />
        </>
      )}
    </section>
  );
}


import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getMaster,
  getMasterHistory,
  getMasterMigration,
  postMasterEntry,
  postMasterLine,
  postMasterMigration,
  putMasterEntry,
  putMasterLine,
} from "../api.js";
import MasterSelections from "../components/MasterSelections.jsx";
import {
  ANSWER_LABELS,
  ENTRY_SECTIONS,
  LINE_WORD,
  afterWriteLine,
  answersComplete,
  conflictOf,
  entryWhen,
  errorText,
  historyRows,
  masterSections,
  migrationState,
  migrationSummary,
  nearDuplicateLine,
  questionWhere,
  retiredRows,
  revisionLine,
  shownByLabel,
  strengthMark,
} from "../masterModel.js";
import { SETTINGS_HASH } from "../routing.js";

// 0.1.10.9 master P5: the Master resume page (#/master).
//
// The master is the user's one resume of everything, an id on every line,
// no contact data. This page lists it BY ROLE and changes it one line at a
// time: add a line, edit one, retire one (History puts it back), add a role.
// Each line shows its evidence strength; the page shows the revision it
// read and sends it with every write, so a write that crosses the agent's
// is refused (409) and the page shows the master as it is now.
//
// With no master yet the page is the MIGRATION: the master is built from
// the resumes the profiles hold, and two versions of a line that state
// different numbers are asked about before anything is written.
//
// The rules are masterModel.js's (pure). Every text of the master goes into
// the tree as a React text child.
function LineForm({ label, initial = "", submit, busy, onDone, onCancel, placeholder }) {
  const [value, setValue] = useState(initial);
  const ready = value.trim() !== "" && value.trim() !== initial.trim();
  return (
    <form
      className="master-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (ready) {
          submit(value.trim()).then((ok) => ok && onDone && onDone());
        }
      }}
    >
      <textarea aria-label={label} rows={2} value={value} placeholder={placeholder} onChange={(event) => setValue(event.target.value)} disabled={busy} />
      <div className="card-actions">
        <button type="submit" className="button small" data-action="save-line" disabled={busy || !ready}>
          {busy ? "Saving…" : "Save"}
        </button>
        {onCancel && (
          <button type="button" className="button small secondary" data-action="cancel" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}

function Line({ item, shownBy, write, busy }) {
  const [editing, setEditing] = useState(false);
  const strength = strengthMark(item);
  return (
    <li className="master-line" data-line-id={item.id} data-strength={item.strength}>
      <span className={`master-strength ${item.strength}`} data-role="strength" title={strength.title} aria-label={strength.label}>
        {strength.mark}
      </span>
      <div className="master-line-body">
        {editing ? (
          <LineForm
            label="The line"
            initial={item.text}
            busy={busy}
            submit={(text) => write((revision) => putMasterLine({ revision, id: item.id, use: "edit", text }))}
            onDone={() => setEditing(false)}
            onCancel={() => setEditing(false)}
          />
        ) : (
          <span data-role="line-text">{item.text}</span>
        )}
        <div className="muted small">
          {(item.tags || []).map((tag) => (
            <span key={tag} className="tag">
              {tag}
            </span>
          ))}
          {shownBy && <span data-role="shown-by">{shownBy}</span>}
        </div>
      </div>
      {!editing && (
        <span className="master-line-actions">
          <button className="link-button" data-action="edit-line" onClick={() => setEditing(true)} disabled={busy}>
            Edit
          </button>
          <button
            className="link-button"
            data-action="retire-line"
            disabled={busy}
            onClick={() => window.confirm("Retire this line? It is never selected again. History can put it back.") && write((revision) => putMasterLine({ revision, id: item.id, use: "retire" }))}
          >
            Retire
          </button>
        </span>
      )}
    </li>
  );
}

function AddLine({ label, target, write, busy }) {
  const [open, setOpen] = useState(false);
  if (!open) {
    return (
      <button className="button small secondary" data-action="add-line" onClick={() => setOpen(true)} disabled={busy}>
        {label}
      </button>
    );
  }
  return (
    <LineForm
      label={label}
      busy={busy}
      placeholder="One line, in your own words. Every number is yours."
      submit={(text) => write((revision) => postMasterLine({ revision, text, ...target }))}
      onDone={() => setOpen(false)}
      onCancel={() => setOpen(false)}
    />
  );
}

function EntryForm({ initial, submit, busy, onDone, onCancel }) {
  const [heading, setHeading] = useState(initial ? initial.heading : "");
  const [when, setWhen] = useState(initial ? (initial.sublines || []).join("\n") : "");
  const ready = heading.trim() !== "";
  return (
    <form
      className="master-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (ready) {
          const sublines = when.split("\n").map((line) => line.trim()).filter(Boolean);
          submit(heading.trim(), sublines).then((ok) => ok && onDone && onDone());
        }
      }}
    >
      <input aria-label="Employer, project or school" value={heading} placeholder="Employer, project or school" onChange={(event) => setHeading(event.target.value)} disabled={busy} />
      <textarea aria-label="Title and dates" rows={2} value={when} placeholder="Staff Engineer | Jun 2022 - Present" onChange={(event) => setWhen(event.target.value)} disabled={busy} />
      <div className="card-actions">
        <button type="submit" className="button small" data-action="save-entry" disabled={busy || !ready}>
          {busy ? "Saving…" : "Save"}
        </button>
        <button type="button" className="button small secondary" data-action="cancel" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function Entry({ group, body, write, busy }) {
  const [editing, setEditing] = useState(false);
  const { entry, lines } = group;
  return (
    <div className="master-entry" data-entry-id={entry.id}>
      {editing ? (
        <EntryForm
          initial={entry}
          busy={busy}
          submit={(heading, sublines) => write((revision) => putMasterEntry({ revision, id: entry.id, use: "edit", heading, sublines }))}
          onDone={() => setEditing(false)}
          onCancel={() => setEditing(false)}
        />
      ) : (
        <div className="story-entry-head">
          <span>
            <strong data-role="entry-heading">{entry.heading}</strong>
            {entryWhen(entry) && <span className="muted small"> · {entryWhen(entry)}</span>}
          </span>
          <span className="master-line-actions">
            <button className="link-button" data-action="edit-entry" onClick={() => setEditing(true)} disabled={busy}>
              Edit
            </button>
            <button
              className="link-button"
              data-action="retire-entry"
              disabled={busy}
              onClick={() => window.confirm("Retire this entry and its lines? History can put them back.") && write((revision) => putMasterEntry({ revision, id: entry.id, use: "retire" }))}
            >
              Retire
            </button>
          </span>
        </div>
      )}
      <ul className="master-lines">
        {lines.map((item) => (
          <Line key={item.id} item={item} shownBy={shownByLabel(item.id, body.shown_by, body.profiles)} write={write} busy={busy} />
        ))}
      </ul>
      <AddLine label="Add a line" target={{ entryId: entry.id }} write={write} busy={busy} />
    </div>
  );
}

function Migration({ onMade }) {
  const [plan, setPlan] = useState(null);
  const [answers, setAnswers] = useState({});
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let current = true;
    getMasterMigration()
      .then((response) => current && setPlan(response))
      .catch((err) => current && setError(errorText(err)));
    return () => {
      current = false;
    };
  }, []);

  const make = () => {
    setBusy(true);
    setError(null);
    postMasterMigration(answers)
      .then((response) => {
        if (response.written) {
          onMade();
        } else {
          setPlan(response); // a question is still open: nothing was written
        }
      })
      .catch((err) => setError(errorText(err)))
      .finally(() => setBusy(false));
  };

  const state = migrationState(plan);
  const questions = plan ? plan.questions || [] : [];
  return (
    <section className="panel" data-role="master-migration" data-migration-state={state}>
      <h2>Make your master resume</h2>
      <p className="muted">
        GigAI merges the resumes your profiles hold into one master: every line once, the newer wording kept where two say the same thing. Each
        profile keeps showing the resume it shows now, so nothing already assessed changes. Nothing is written until you say so.
      </p>
      {error && <div className="callout danger">{error}</div>}
      {state === "loading" && !error && <p className="muted">Reading your resumes…</p>}
      {state === "blocked" && <div className="callout" data-role="migration-blocked">{plan.blocked.message}</div>}
      {state === "nothing" && <p className="muted">Every profile already has its selection of the master.</p>}
      {(state === "questions" || state === "ready") && (
        <>
          <p data-role="migration-summary">{migrationSummary(plan)}</p>
          {questions.length > 0 && (
            <p>
              <strong>Your resumes disagree on {questions.length === 1 ? "one line" : `${questions.length} lines`}.</strong> Pick the one that is right; GigAI does not guess.
            </p>
          )}
          <ul className="story-list">
            {questions.map((question) => (
              <li key={question.question_id} className="story-entry" data-question-id={question.question_id}>
                <p className="muted small">{questionWhere(question)}: the same line with different numbers.</p>
                {(question.options || []).map((option) => (
                  <p key={option.key} className="story-answer">
                    <strong>{option.key.toUpperCase()}:</strong> {option.text}
                    {(option.profiles || []).length > 0 && <span className="muted small"> (from {option.profiles.join(", ")})</span>}
                  </p>
                ))}
                <div className="card-actions" role="group" aria-label="Which is right">
                  {(question.choices || []).map((choice) => (
                    <button
                      key={choice}
                      type="button"
                      className={`chip${answers[question.question_id] === choice ? " active" : ""}`}
                      aria-pressed={answers[question.question_id] === choice}
                      data-choice={choice}
                      onClick={() => setAnswers((current) => ({ ...current, [question.question_id]: choice }))}
                    >
                      {ANSWER_LABELS[choice] || choice}
                    </button>
                  ))}
                </div>
              </li>
            ))}
          </ul>
          <button className="button" data-action="make-master" disabled={busy || !answersComplete(questions, answers)} onClick={make}>
            {busy ? "Making it…" : "Make my master resume"}
          </button>
        </>
      )}
    </section>
  );
}

function History({ history, write, busy }) {
  const revisions = historyRows(history);
  const retired = retiredRows(history);
  return (
    <section className="panel" data-role="master-history">
      <h2>History</h2>
      <p className="muted small">Every change is a new revision; nothing is ever deleted.</p>
      <ul className="story-postings" data-role="revisions">
        {revisions.map((row) => (
          <li key={row.key} data-revision={row.revision}>
            {row.text}
          </li>
        ))}
      </ul>
      <h3>Retired</h3>
      {retired.length === 0 && <p className="muted">Nothing is retired.</p>}
      <ul className="master-lines" data-role="retired">
        {retired.map((gone) => (
          <li key={gone.id} className="master-line" data-retired-id={gone.id}>
            <div className="master-line-body">
              <span>{gone.text}</span>
              <div className="muted small">
                {[gone.what === "entry" ? "an entry with its lines" : gone.where, gone.note].filter(Boolean).join(" · ")}
              </div>
            </div>
            <span className="master-line-actions">
              <button
                className="link-button"
                data-action="restore"
                disabled={busy}
                onClick={() => write((revision) => (gone.what === "entry" ? putMasterEntry : putMasterLine)({ revision, id: gone.id, use: "restore" }))}
              >
                Restore
              </button>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function MasterView({ reloadProfiles = null }) {
  const [body, setBody] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [version, setVersion] = useState(0);

  const load = useCallback(() => {
    return getMaster()
      .then((response) => {
        setBody(response);
        setError(null);
        return response;
      })
      .catch((err) => setError(errorText(err)));
  }, []);

  const loadHistory = useCallback(() => {
    getMasterHistory()
      .then(setHistory)
      .catch((err) => setError(errorText(err)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (historyOpen) {
      loadHistory();
    }
  }, [historyOpen, loadHistory, version]);

  const master = body ? body.master : null;
  const revision = master ? master.revision : null;

  // One write: `call(revision)` sends it with the revision this page read; resolves true when saved.
  const write = useCallback(
    (call) => {
      setBusy(true);
      setError(null);
      setNotice(null);
      return call(revision)
        .then((response) => {
          setNotice(nearDuplicateLine(response.near_duplicate, response.master) || afterWriteLine(response.profiles) || (response.status === "unchanged" ? "Nothing changed: the master already says this." : null));
          setVersion((count) => count + 1);
          if (reloadProfiles) {
            reloadProfiles(); // a profile that showed the line has a new resume
          }
          return load().then(() => true);
        })
        .catch((err) => {
          const conflict = conflictOf(err);
          if (conflict) {
            setNotice(conflict.message);
            return load().then(() => false);
          }
          setError(errorText(err));
          return false;
        })
        .finally(() => setBusy(false));
    },
    [load, reloadProfiles, revision],
  );

  const sections = useMemo(() => masterSections(master), [master]);

  if (body === null) {
    return (
      <section className="panel" data-role="master-page">
        <h2>Master resume</h2>
        {error ? <div className="callout danger">Could not load your master resume: {error}</div> : <p className="muted">Loading…</p>}
      </section>
    );
  }
  if (!master) {
    return (
      <div data-role="master-page" data-master-revision="">
        <Migration onMade={load} />
      </div>
    );
  }

  return (
    <div data-role="master-page" data-master-revision={master.revision}>
      <section className="panel">
        <h2>Master resume</h2>
        <p className="muted">
          Everything you have done, once: every role, bullet, project and skill. A profile shows a selection of it; a resume tailored for a job picks
          from all of it. Only your own facts go here, and never a name or contact details. Kept on this machine.
        </p>
        <p data-role="master-revision">{revisionLine(master)}</p>
        <p className="muted small">
          <span className="master-strength backed">B</span> backed by a story or an answer · <span className="master-strength quantified">#</span> states a number ·{" "}
          <span className="master-strength stated">·</span> stated
        </p>
        {notice && (
          <div className="callout info" data-role="master-notice">
            {notice}
          </div>
        )}
        {error && (
          <div className="callout danger" role="alert" data-role="master-error">
            {error}
          </div>
        )}
        <div className="story-bank-tools">
          <button className="button small secondary" data-action="refresh" onClick={() => load()} disabled={busy}>
            Refresh
          </button>
          <button className="button small secondary" data-action="toggle-history" aria-expanded={historyOpen} onClick={() => setHistoryOpen((open) => !open)}>
            {historyOpen ? "Hide history" : "History"}
          </button>
          <a href={SETTINGS_HASH}>Back to Settings</a>
        </div>
      </section>

      {historyOpen && history && <History history={history} write={write} busy={busy} />}

      <MasterSelections version={version} onChanged={reloadProfiles} />

      {sections.map((group) => (
        <section className="panel" key={group.section} data-master-section={group.section}>
          <h2>{group.label}</h2>
          {ENTRY_SECTIONS.has(group.section) ? (
            <>
              {group.entries.map((entryGroup) => (
                <Entry key={entryGroup.entry.id} group={entryGroup} body={body} write={write} busy={busy} />
              ))}
              <AddEntry section={group.section} write={write} busy={busy} />
            </>
          ) : (
            <>
              <ul className="master-lines">
                {group.lines.map((item) => (
                  <Line key={item.id} item={item} shownBy={shownByLabel(item.id, body.shown_by, body.profiles)} write={write} busy={busy} />
                ))}
              </ul>
              <AddLine label={`Add a ${LINE_WORD[group.section] || "line"}`} target={{ section: group.section }} write={write} busy={busy} />
            </>
          )}
        </section>
      ))}
    </div>
  );
}

function AddEntry({ section, write, busy }) {
  const [open, setOpen] = useState(false);
  const word = section === "experience" ? "role" : section === "projects" ? "project" : "school";
  if (!open) {
    return (
      <button className="button small secondary" data-action="add-entry" onClick={() => setOpen(true)} disabled={busy}>
        Add a {word}
      </button>
    );
  }
  return (
    <EntryForm
      busy={busy}
      submit={(heading, sublines) => write((revision) => postMasterEntry({ revision, section, heading, sublines }))}
      onDone={() => setOpen(false)}
      onCancel={() => setOpen(false)}
    />
  );
}

import { useCallback, useEffect, useMemo, useState } from "react";
import { addStory, deleteStory, getStoryBank, putStory, putStoryBankSharing } from "../api.js";
import {
  addBody,
  addError,
  conflictOf,
  editBody,
  editError,
  entryMeta,
  entryTitle,
  filterEntries,
  initialEditForm,
  postingLines,
  removeEntry,
  replaceEntry,
  saveErrorText,
  sharingOptions,
  sharingSummary,
  usedByLabel,
} from "../storyBankModel.js";

// 0110-034: one profile's story bank (Settings > Profiles, under the
// profile's detail). Every question the profile answered on a posting and
// every story it (or an agent, over the API) added: search, edit, delete,
// the jobs that used each answer, and which other profile's bank it reads.
//
// The rules are storyBankModel.js's (pure). Two writers share the bank, so:
//   - an edit / delete sends the entry's `updated_at`; a 409
//     story_bank_changed shows the entry as it is now, never overwrites it;
//   - the list is re-read when the window gets focus again (an agent may
//     have written in the meantime) and with "Refresh".
// A shared entry (read from another profile) is read only here.
function Entry({ entry, labels, profileId, onChanged, onRemoved }) {
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [open, setOpen] = useState(false);
  const postings = postingLines(entry);

  const failed = (err) => {
    const conflict = conflictOf(err);
    if (conflict) {
      onChanged(conflict.entry);
      setForm(null);
      setNotice(conflict.message);
      setError(null);
      return;
    }
    setError(saveErrorText(err));
  };

  const save = async () => {
    const body = editBody(entry, form, profileId);
    if (!body) {
      setForm(null);
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const response = await putStory(entry.question_id, body);
      onChanged(response.entry);
      setForm(null);
    } catch (err) {
      failed(err);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!window.confirm("Delete this answer from the story bank? It will not be reused again.")) {
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await deleteStory(entry.question_id, { profileId, updatedAt: entry.updated_at });
      onRemoved(entry.question_id);
    } catch (err) {
      failed(err);
    } finally {
      setBusy(false);
    }
  };

  const formError = form ? editError(form) : null;
  return (
    <li className="story-entry" data-story-id={entry.question_id} data-shared={entry.shared ? "true" : "false"}>
      <div className="story-entry-head">
        <strong title={entry.question_id}>{entryTitle(entry)}</strong>
        <span className="tag">{entry.tag}</span>
      </div>
      {form ? (
        <>
          <div className="form-group">
            <label className="form-label" htmlFor={`story-question-${entry.question_id}`}>
              Question
            </label>
            <input
              id={`story-question-${entry.question_id}`}
              type="text"
              className="text-input"
              value={form.question}
              onChange={(event) => setForm({ ...form, question: event.target.value })}
              disabled={busy}
            />
          </div>
          <div className="form-group">
            <label className="form-label" htmlFor={`story-answer-${entry.question_id}`}>
              Answer
            </label>
            <textarea
              id={`story-answer-${entry.question_id}`}
              className="text-input"
              rows={5}
              value={form.answer}
              onChange={(event) => setForm({ ...form, answer: event.target.value })}
              disabled={busy}
            />
          </div>
          <div className="form-group">
            <label className="form-label" htmlFor={`story-tag-${entry.question_id}`}>
              Tag
            </label>
            <input
              id={`story-tag-${entry.question_id}`}
              type="text"
              className="text-input"
              value={form.tag}
              onChange={(event) => setForm({ ...form, tag: event.target.value })}
              disabled={busy}
            />
          </div>
          {(formError || error) && <div className="callout danger">{formError || error}</div>}
          <div className="card-actions">
            <button className="button small secondary" onClick={() => setForm(null)} disabled={busy}>
              Cancel
            </button>
            <button className="button small" data-action="story-save" onClick={save} disabled={busy || Boolean(formError)}>
              {busy ? "Saving…" : "Save"}
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="story-answer">{entry.answer}</p>
          <p className="muted small">{entryMeta(entry, labels)}</p>
          {notice && <div className="callout">{notice}</div>}
          {error && <div className="callout danger">{error}</div>}
          <div className="card-actions">
            <button className="link-button" onClick={() => setOpen(!open)} aria-expanded={open}>
              {usedByLabel(entry)}
            </button>
            {!entry.shared && (
              <>
                <button className="button small secondary" data-action="story-edit" onClick={() => setForm(initialEditForm(entry))} disabled={busy}>
                  Edit
                </button>
                <button className="button small secondary" data-action="story-delete" onClick={remove} disabled={busy}>
                  Delete
                </button>
              </>
            )}
          </div>
          {open && postings.length > 0 && (
            <ul className="story-postings">
              {postings.map((posting) => (
                <li key={posting.key}>
                  {posting.url ? (
                    <a href={posting.url} target="_blank" rel="noreferrer">
                      {posting.label}
                    </a>
                  ) : (
                    posting.label
                  )}{" "}
                  <span className="muted small">{posting.at}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </li>
  );
}

const EMPTY_STORY = { question: "", answer: "", tag: "" };

export default function StoryBankPanel({ profile }) {
  const profileId = profile.profile_id;
  const [bank, setBank] = useState(null);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState("");
  const [tag, setTag] = useState("");
  const [adding, setAdding] = useState(null);
  const [addFailure, setAddFailure] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    getStoryBank(profileId)
      .then((response) => {
        setBank(response);
        setError(null);
      })
      .catch((err) => setError(err.message || String(err)));
  }, [profileId]);

  useEffect(() => {
    setBank(null);
    setQuery("");
    setTag("");
    setAdding(null);
    load();
    // An agent may have written the bank while this window was in the background.
    window.addEventListener("focus", load);
    return () => window.removeEventListener("focus", load);
  }, [load]);

  const labels = useMemo(() => {
    const map = { [profileId]: profile.label };
    ((bank && bank.sharing && bank.sharing.profiles) || []).forEach((other) => {
      map[other.profile_id] = other.label;
    });
    return map;
  }, [bank, profileId, profile.label]);

  const shown = useMemo(() => filterEntries(bank ? bank.entries : [], { query, tag }), [bank, query, tag]);

  const share = async (value) => {
    setBusy(true);
    try {
      await putStoryBankSharing(profileId, value || null);
      load();
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
    }
  };

  const add = async () => {
    setBusy(true);
    setAddFailure(null);
    try {
      await addStory(addBody(adding, profileId));
      setAdding(null);
      load();
    } catch (err) {
      setAddFailure(saveErrorText(err));
    } finally {
      setBusy(false);
    }
  };

  const addProblem = adding ? addError(adding) : null;
  return (
    <div className="story-bank" data-role="story-bank">
      <div className="label">Story bank</div>
      <p className="muted small">
        Every question this profile answered on a posting, and the stories you add. The next assessment reuses an answer instead of asking
        again, and they are your material for interviews. Kept on this machine; never a name or contact details.
      </p>
      {error && <div className="callout danger">Could not load the story bank: {error}</div>}
      {!bank && !error && <p className="muted">Loading the story bank…</p>}
      {bank && (
        <>
          <div className="story-bank-tools">
            <input
              type="search"
              className="text-input"
              placeholder="Search questions and answers…"
              aria-label="Search the story bank"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <select className="text-input" aria-label="Tag" value={tag} onChange={(event) => setTag(event.target.value)}>
              <option value="">All tags</option>
              {bank.tags.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
            <button className="button small secondary" onClick={load}>
              Refresh
            </button>
            <button className="button small secondary" data-action="story-add" onClick={() => setAdding(adding ? null : EMPTY_STORY)}>
              {adding ? "Cancel" : "Add a story"}
            </button>
          </div>
          {adding && (
            <div className="story-entry">
              <div className="form-group">
                <label className="form-label" htmlFor="story-new-question">
                  What it answers
                </label>
                <input
                  id="story-new-question"
                  type="text"
                  className="text-input"
                  placeholder="Tell me about a migration you led"
                  value={adding.question}
                  onChange={(event) => setAdding({ ...adding, question: event.target.value })}
                />
              </div>
              <div className="form-group">
                <label className="form-label" htmlFor="story-new-answer">
                  The story
                </label>
                <textarea
                  id="story-new-answer"
                  className="text-input"
                  rows={5}
                  value={adding.answer}
                  onChange={(event) => setAdding({ ...adding, answer: event.target.value })}
                />
              </div>
              {addFailure && <div className="callout danger">{addFailure}</div>}
              <div className="card-actions">
                <button className="button small" onClick={add} disabled={busy || Boolean(addProblem)} title={addProblem || undefined}>
                  {busy ? "Saving…" : "Save story"}
                </button>
                {addProblem && <span className="muted small">{addProblem}</span>}
              </div>
            </div>
          )}
          <p className="muted small">
            {shown.length} of {bank.total} {bank.total === 1 ? "entry" : "entries"}
          </p>
          {bank.total === 0 && <p className="muted">Nothing yet. Answer a question on a job page, or add a story.</p>}
          <ul className="story-list">
            {shown.map((entry) => (
              <Entry
                key={`${entry.shared ? "shared" : "own"}:${entry.question_id}:${entry.updated_at}`}
                entry={entry}
                labels={labels}
                profileId={profileId}
                onChanged={(changed) => setBank((current) => ({ ...current, entries: replaceEntry(current.entries, changed) }))}
                onRemoved={(id) => setBank((current) => ({ ...current, entries: removeEntry(current.entries, id), total: current.total - 1 }))}
              />
            ))}
          </ul>
          <div className="story-sharing">
            <label className="form-label" htmlFor="story-share-with">
              Sharing
            </label>
            <select
              id="story-share-with"
              className="text-input"
              value={bank.sharing.share_with || ""}
              onChange={(event) => share(event.target.value)}
              disabled={busy || bank.sharing.profiles.length === 0}
            >
              {sharingOptions(bank.sharing).map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            {sharingSummary(bank.sharing).map((line) => (
              <p key={line} className="muted small">
                {line}
              </p>
            ))}
            <p className="muted small">
              Profiles can be different people. A profile never reads another profile's answers unless you choose it here. To let another
              profile read this one's, select that profile and choose this one in its Sharing.
            </p>
          </div>
        </>
      )}
    </div>
  );
}

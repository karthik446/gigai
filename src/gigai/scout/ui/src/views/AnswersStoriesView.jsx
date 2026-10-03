import { useCallback, useEffect, useMemo, useState } from "react";
import { deleteAnswer, deleteStory, getAnswers, getStories } from "../api.js";
import {
  allTags,
  answerTitle,
  conflictOf,
  countLine,
  deleteErrorText,
  deleteQuestion,
  earlierAnswers,
  filterByTag,
  itemId,
  jobLines,
  narrativeParts,
  removeItem,
  replaceItem,
  storyWhere,
  usedByLabel,
  writtenLine,
} from "../answersStoriesModel.js";
import { SETTINGS_HASH } from "../routing.js";

// 0.1.10.7 C: the one Answers and stories page (#/answers). READ-ONLY.
//
// Answers (short facts) and stories (experiences worth telling) are the
// user's, shared by every profile, and are written through the API and the
// CLI, mainly by the user's agent. This page browses them, shows which jobs
// used each, and deletes a bad one. It has NO text-entry form: nothing here
// adds or edits.
//
// The rules are answersStoriesModel.js's (pure). The agent may write while
// this page is open, so:
//   - a delete sends the `revision` the page read; a 409 revision_conflict
//     shows the answer or story as it is now and deletes nothing;
//   - the lists are re-read when the window gets focus again and with
//     "Refresh".
function Jobs({ item }) {
  const [open, setOpen] = useState(false);
  const lines = jobLines(item);
  return (
    <>
      <button className="link-button" data-action="show-jobs" onClick={() => setOpen(!open)} aria-expanded={open} disabled={lines.length === 0}>
        {usedByLabel(item)}
      </button>
      {open && lines.length > 0 && (
        <ul className="story-postings">
          {lines.map((line) => (
            <li key={line.key}>
              {line.url ? (
                <a href={line.url} target="_blank" rel="noreferrer">
                  {line.label}
                </a>
              ) : (
                line.label
              )}{" "}
              <span className="muted small">{line.at}</span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function Row({ item, remove, onChanged, onRemoved, children }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);

  const onDelete = async () => {
    if (!window.confirm(deleteQuestion(item))) {
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await remove(itemId(item), item.revision);
      onRemoved(itemId(item));
    } catch (err) {
      const conflict = conflictOf(err);
      if (conflict) {
        onChanged(conflict.item);
        setNotice(conflict.message);
      } else {
        setError(deleteErrorText(err));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="story-entry" data-item-id={itemId(item)}>
      {children}
      <p className="muted small">{writtenLine(item)}</p>
      {notice && <div className="callout">{notice}</div>}
      {error && <div className="callout danger">{error}</div>}
      <div className="card-actions">
        <Jobs item={item} />
        <button className="button small secondary" data-action="delete-item" onClick={onDelete} disabled={busy}>
          {busy ? "Deleting…" : "Delete"}
        </button>
      </div>
    </li>
  );
}

function AnswerBody({ answer }) {
  const earlier = earlierAnswers(answer);
  return (
    <>
      <div className="story-entry-head">
        <strong title={answer.question_id}>{answerTitle(answer)}</strong>
        <span className="tag">{answer.tag}</span>
      </div>
      <p className="story-answer">{answer.answer}</p>
      {earlier.map((entry) => (
        <p key={entry.key} className="muted small" data-role="earlier-answer">
          Earlier ({entry.at}): {entry.answer}
        </p>
      ))}
    </>
  );
}

function StoryBody({ story }) {
  const where = storyWhere(story);
  const parts = narrativeParts(story);
  return (
    <>
      <div className="story-entry-head">
        <strong title={story.story_id}>{story.title}</strong>
        <span>
          {story.tags.map((tag) => (
            <span key={tag} className="tag">
              {tag}
            </span>
          ))}
        </span>
      </div>
      {where && <p className="muted small">{where}</p>}
      {parts.map((part) => (
        <p key={part.key} className="story-answer">
          <strong>{part.label}:</strong> {part.text}
        </p>
      ))}
      {story.raw && (
        <p className="story-answer" data-role="story-raw">
          <span className="muted small">In your words:</span> {story.raw}
        </p>
      )}
      {story.answers_questions.length > 0 && (
        <ul className="story-postings" data-role="story-answers-questions">
          {story.answers_questions.map((question) => (
            <li key={question}>Answers: {question}</li>
          ))}
        </ul>
      )}
    </>
  );
}

export default function AnswersStoriesView() {
  const [answers, setAnswers] = useState(null);
  const [stories, setStories] = useState(null);
  const [error, setError] = useState(null);
  const [tag, setTag] = useState("");

  const load = useCallback(() => {
    Promise.all([getAnswers(), getStories()])
      .then(([answersBody, storiesBody]) => {
        setAnswers(answersBody.answers || []);
        setStories(storiesBody.stories || []);
        setError(null);
      })
      .catch((err) => setError(err.message || String(err)));
  }, []);

  useEffect(() => {
    load();
    // The agent may have written while this window was in the background.
    window.addEventListener("focus", load);
    return () => window.removeEventListener("focus", load);
  }, [load]);

  const tags = useMemo(() => allTags(answers, stories), [answers, stories]);
  const shownAnswers = useMemo(() => filterByTag(answers, tag), [answers, tag]);
  const shownStories = useMemo(() => filterByTag(stories, tag), [stories, tag]);
  const loaded = answers !== null && stories !== null;

  return (
    <div data-role="answers-stories">
      <section className="panel">
        <h2>Answers and stories</h2>
        <p className="muted">
          What you told your agent, kept once and used for every profile. An answer is a short fact a posting asked for; the next assessment
          reuses it instead of asking again. A story is an experience worth telling; an assessment gets the few that fit the job, and they are
          your material for interviews. Your agent writes them; here you can read them, see which jobs used them, and delete one that is wrong.
          Kept on this machine; never a name or contact details.
        </p>
        {error && <div className="callout danger">Could not load your answers and stories: {error}</div>}
        {!loaded && !error && <p className="muted">Loading…</p>}
        {loaded && (
          <div className="story-bank-tools">
            <button className={`chip${tag === "" ? " active" : ""}`} data-tag="" aria-pressed={tag === ""} onClick={() => setTag("")}>
              All
            </button>
            {tags.map((name) => (
              <button key={name} className={`chip${tag === name ? " active" : ""}`} data-tag={name} aria-pressed={tag === name} onClick={() => setTag(name)}>
                {name}
              </button>
            ))}
            <button className="button small secondary" data-action="refresh" onClick={load}>
              Refresh
            </button>
            <a href={SETTINGS_HASH}>Back to Settings</a>
          </div>
        )}
      </section>

      {loaded && (
        <>
          <section className="panel" data-role="answers">
            <h2>Answers</h2>
            <p className="muted small">{countLine(shownAnswers.length, answers.length, "answer", "answers")}</p>
            {answers.length === 0 && <p className="muted">Nothing yet. Answer a question on a job page, or let your agent ask you.</p>}
            <ul className="story-list">
              {shownAnswers.map((answer) => (
                <Row
                  key={`${answer.question_id}:${answer.revision}`}
                  item={answer}
                  remove={deleteAnswer}
                  onChanged={(changed) => setAnswers((current) => replaceItem(current, changed))}
                  onRemoved={(id) => setAnswers((current) => removeItem(current, id))}
                >
                  <AnswerBody answer={answer} />
                </Row>
              ))}
            </ul>
          </section>

          <section className="panel" data-role="stories">
            <h2>Stories</h2>
            <p className="muted small">{countLine(shownStories.length, stories.length, "story", "stories")}</p>
            {stories.length === 0 && <p className="muted">Nothing yet. When an answer has a project, a problem and an outcome, your agent offers to make it a story.</p>}
            <ul className="story-list">
              {shownStories.map((story) => (
                <Row
                  key={`${story.story_id}:${story.revision}`}
                  item={story}
                  remove={deleteStory}
                  onChanged={(changed) => setStories((current) => replaceItem(current, changed))}
                  onRemoved={(id) => setStories((current) => removeItem(current, id))}
                >
                  <StoryBody story={story} />
                </Row>
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  );
}

// 0.1.10.7 C: the Answers and stories page, as pure functions (no React, no
// fetch) so the rules run under node.
//
// Answers and stories are the user's (not one profile's) and are written
// through the API and the CLI, mainly by the user's agent. This page is
// READ-ONLY: it lists them (GET /api/answers, GET /api/stories), shows which
// jobs used each, and can delete a bad one. It has no text-entry form.
//
// A delete sends the `revision` the page read; when the agent wrote the
// answer or story since, the server answers 409 revision_conflict with it as
// it is now, which the page shows instead of deleting it.

const text = (value) => (typeof value === "string" ? value : "");
const day = (value) => text(value).slice(0, 10);
const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

// --- answers ---------------------------------------------------------------

// The question as the row shows it: its own words, or (when only the id is
// known) its id.
export function answerTitle(answer) {
  return answer.question && answer.question !== answer.question_id ? answer.question : answer.question_id;
}

// One line under an answer or a story: who wrote it and when.
export function writtenLine(item) {
  const parts = [`Written by ${item.written_by === "agent" ? "your agent" : "you"}`];
  if (item.updated_at) {
    parts.push(`updated ${day(item.updated_at)}`);
  }
  return parts.join(" · ");
}

// An earlier text kept in an answer's history (two profiles' answers to one
// question were merged in 0.1.10.7): shown so nothing is lost silently.
export function earlierAnswers(answer) {
  return (answer.history || [])
    .filter((entry) => entry && typeof entry.answer === "string" && entry.answer)
    .map((entry) => ({ key: `${entry.at}:${entry.answer}`, at: day(entry.at), note: text(entry.action), answer: entry.answer }));
}

// --- stories ---------------------------------------------------------------

// "Staff Engineer, Acme, 2023": where and when, whatever is known.
export function storyWhere(story) {
  return [story.role, story.company, story.period].filter((part) => text(part)).join(", ");
}

const PART_LABELS = [
  ["situation", "Situation"],
  ["task", "Task"],
  ["action", "Action"],
  ["result", "Result"],
];

// The narrative's parts that are there, in STAR order.
export function narrativeParts(story) {
  const narrative = (story && story.narrative) || {};
  return PART_LABELS.filter(([key]) => text(narrative[key])).map(([key, label]) => ({ key, label, text: narrative[key] }));
}

// --- both ------------------------------------------------------------------

export function itemId(item) {
  return item.story_id || item.question_id;
}

const tagsOf = (item) => (Array.isArray(item.tags) ? item.tags : item.tag ? [item.tag] : []);

// The list as the tag chips narrow it ("" = everything).
export function filterByTag(items, tag) {
  return (items || []).filter((item) => !tag || tagsOf(item).includes(tag));
}

// Every tag of the answers and the stories, once, sorted: the chips.
export function allTags(answers, stories) {
  return Array.from(new Set((answers || []).concat(stories || []).flatMap(tagsOf))).sort();
}

const KIND_LABELS = { answered: "asked by", confirmed: "confirmed for", reused: "reused by", used: "used by" };

// The jobs that used an answer or a story, one line each, newest first.
export function jobLines(item) {
  return (item.jobs || [])
    .slice()
    .sort((a, b) => text(b.at).localeCompare(text(a.at)))
    .map((job) => {
      const name = [job.title, job.company].filter(Boolean).join(" at ") || job.url || job.job_identity;
      return { key: `${job.kind}:${job.job_identity}`, label: `${KIND_LABELS[job.kind] || job.kind} ${name}`, url: job.url || null, at: day(job.at) };
    });
}

export function usedByLabel(item) {
  const jobs = new Set((item.jobs || []).map((job) => job.job_identity)).size;
  return jobs === 0 ? "No job has used this yet" : `Used by ${plural(jobs, "job")}`;
}

// "2 of 5 answers" / "1 of 1 story".
export function countLine(shown, total, word, words) {
  return `${shown} of ${total} ${total === 1 ? word : words}`;
}

// What the delete button asks before it deletes.
export function deleteQuestion(item) {
  return item.story_id
    ? "Delete this story? It will not be offered to an assessment again."
    : "Delete this answer? It will not be reused again, and the question may be asked again.";
}

// A 409 from a delete: the agent (or another window) wrote the answer or
// story after this page read it. The page shows it as it is now and says so;
// nothing is deleted.
export function conflictOf(error) {
  const item = error && (error.answer || error.story);
  if (!error || error.code !== "revision_conflict" || !item) {
    return null;
  }
  const who = item.written_by === "agent" ? "Your agent" : "Another window";
  return { item, message: `${who} changed this after you opened the page. It is shown as it is now and was not deleted; delete it again if you still want to.` };
}

export function deleteErrorText(error) {
  return (error && (error.detail || error.message)) || "It could not be deleted.";
}

export function replaceItem(items, item) {
  return (items || []).map((existing) => (itemId(existing) === itemId(item) ? item : existing));
}

export function removeItem(items, id) {
  return (items || []).filter((existing) => itemId(existing) !== id);
}

// --- the near match on the job page ------------------------------------------

// "We already know: <answer>": a near match for an open question
// (GET /api/answers/match). Shown only while the box is empty and no answer
// is on record; "Use it" fills the box, the user confirms or edits.
export function suggestionNotice(suggestion) {
  if (!suggestion || !suggestion.answer) {
    return null;
  }
  const from = suggestion.bank_question && suggestion.bank_question !== suggestion.bank_question_id ? suggestion.bank_question : suggestion.bank_question_id;
  return {
    text: `We already know: ${suggestion.answer}`,
    source: `From your answer to "${from}".`,
    action: "Use it",
  };
}

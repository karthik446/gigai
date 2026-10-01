// 0110-034: the story bank, as pure functions (no React, no fetch) so the
// rules run under node.
//
// A profile's story bank is every question it answered plus the stories it
// (or an agent working alongside) added: GET /api/story-bank gives
// `entries` (own first, then `shared: true` ones read from the profile
// named by `sharing.share_with`), `tags` and `sharing`. Two writers share
// it, the user here and an agent over the API, so an edit or a delete sends
// the `updated_at` the page read; a stale one answers 409
// story_bank_changed with the current `entry`, which the page then shows
// instead of overwriting it.

const text = (value) => (typeof value === "string" ? value : "");

// The list as the search box and the tag chips narrow it.
export function filterEntries(entries, { query = "", tag = "" } = {}) {
  const needle = text(query).trim().toLowerCase();
  return (entries || []).filter((entry) => {
    if (tag && entry.tag !== tag) {
      return false;
    }
    if (!needle) {
      return true;
    }
    return [entry.question_id, entry.question, entry.answer, entry.tag].some((value) => text(value).toLowerCase().includes(needle));
  });
}

// The question as the row shows it: its own words, or (an answer saved
// before the bank kept them) its id.
export function entryTitle(entry) {
  return entry.question && entry.question !== entry.question_id ? entry.question : entry.question_id;
}

const day = (value) => text(value).slice(0, 10);
const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

// One line under the answer: who wrote it, when, where it came from.
export function entryMeta(entry, labels) {
  const parts = [`Written by ${entry.written_by === "agent" ? "an agent" : "you"}`];
  if (entry.updated_at) {
    parts.push(`updated ${day(entry.updated_at)}`);
  }
  if (entry.shared) {
    const owner = labels && labels[entry.owner_profile_id];
    parts.push(`shared from ${owner || "another profile"} (edit it there)`);
  }
  if (entry.legacy) {
    parts.push("saved before the story bank");
  }
  if (entry.confirmed_from) {
    parts.push(`confirmed from ${entry.confirmed_from}`);
  }
  return parts.join(" · ");
}

const KIND_LABELS = { answered: "asked by", confirmed: "confirmed for", reused: "reused by" };

// The jobs that used an answer, one line each, newest first.
export function postingLines(entry) {
  return (entry.postings || [])
    .slice()
    .sort((a, b) => text(b.at).localeCompare(text(a.at)))
    .map((posting) => {
      const name = [posting.title, posting.company].filter(Boolean).join(" at ") || posting.url || posting.job_identity;
      return { key: `${posting.kind}:${posting.job_identity}`, label: `${KIND_LABELS[posting.kind] || posting.kind} ${name}`, url: posting.url || null, at: day(posting.at) };
    });
}

export function usedByLabel(entry) {
  const jobs = new Set((entry.postings || []).map((posting) => posting.job_identity)).size;
  return jobs === 0 ? "No job has used this yet" : `Used by ${plural(jobs, "job")}`;
}

// The form an own entry opens with.
export function initialEditForm(entry) {
  return { answer: text(entry.answer), question: entryTitle(entry), tag: text(entry.tag) };
}

// The PUT /api/story-bank/{id} body for an edit: only what changed, with
// the updated_at the page read. null when nothing changed.
export function editBody(entry, form, profileId) {
  const body = {};
  if (form.answer.trim() !== text(entry.answer).trim()) {
    body.answer = form.answer.trim();
  }
  if (form.question.trim() !== entryTitle(entry)) {
    body.question = form.question.trim();
  }
  if (form.tag.trim().toLowerCase() !== text(entry.tag)) {
    body.tag = form.tag.trim().toLowerCase();
  }
  if (Object.keys(body).length === 0) {
    return null;
  }
  return { ...body, updated_at: entry.updated_at, profile_id: profileId, actor: "operator" };
}

export function editError(form) {
  if (!form.answer.trim()) {
    return "The answer cannot be empty. Delete the entry instead.";
  }
  if (!form.question.trim()) {
    return "The question cannot be empty.";
  }
  return null;
}

// The POST /api/story-bank body for a new story.
export function addBody(form, profileId) {
  const body = { question: form.question.trim(), answer: form.answer.trim(), profile_id: profileId, actor: "operator" };
  if (form.tag && form.tag.trim()) {
    body.tag = form.tag.trim().toLowerCase();
  }
  return body;
}

export function addError(form) {
  if (!form.question.trim()) {
    return "Say what the story answers, for example: Tell me about a migration you led.";
  }
  if (!form.answer.trim()) {
    return "Write the story.";
  }
  return null;
}

// A 409 from an edit or a delete: someone else (an agent) wrote the entry
// after this page read it. The page shows that entry and says so.
export function conflictOf(error) {
  if (!error || error.code !== "story_bank_changed" || !error.entry) {
    return null;
  }
  const who = error.entry.written_by === "agent" ? "An agent" : "Another window";
  return { entry: error.entry, message: `${who} changed this entry after you opened it. It is shown as it is now; make your change again if you still want it.` };
}

// What a refused save says. The server names what looked personal, never the text.
export function saveErrorText(error) {
  if (error && error.code === "personal_info_refused") {
    return error.detail || "This looks like it holds a name or contact details. The story bank holds experience only; remove them and save again.";
  }
  if (error && error.code === "story_exists") {
    return "This profile already has an entry with that id. Edit that entry instead.";
  }
  return (error && (error.detail || error.message)) || "The story bank could not be saved.";
}

export function replaceEntry(entries, entry) {
  return (entries || []).map((item) => (item.question_id === entry.question_id && !item.shared ? entry : item));
}

export function removeEntry(entries, questionId) {
  return (entries || []).filter((item) => item.shared || item.question_id !== questionId);
}

// Sharing: this profile may also read ONE other profile's bank.
export function sharingOptions(sharing) {
  return [{ value: "", label: "Only this profile's own answers" }].concat(
    ((sharing && sharing.profiles) || []).map((profile) => ({ value: profile.profile_id, label: `Also use the story bank of ${profile.label}` })),
  );
}

export function sharingSummary(sharing) {
  const label = (id) => {
    const found = ((sharing && sharing.profiles) || []).find((profile) => profile.profile_id === id);
    return found ? found.label : id;
  };
  const lines = [];
  if (sharing && sharing.share_with) {
    lines.push(`This profile also reads the story bank of ${label(sharing.share_with)}.`);
  } else {
    lines.push("This profile reads only its own story bank.");
  }
  if (sharing && sharing.read_by && sharing.read_by.length > 0) {
    lines.push(`Its own answers are read by: ${sharing.read_by.map(label).join(", ")}.`);
  } else {
    lines.push("No other profile reads its answers.");
  }
  return lines;
}

// "We already know: <answer>": a near match for an open question
// (GET /api/story-bank/match). Shown only while the box is empty and no
// answer is on record; "Use it" fills the box, the user confirms or edits.
export function suggestionNotice(suggestion) {
  if (!suggestion || !suggestion.answer) {
    return null;
  }
  const from = suggestion.bank_question && suggestion.bank_question !== suggestion.bank_question_id ? suggestion.bank_question : suggestion.bank_question_id;
  return {
    text: `We already know: ${suggestion.answer}`,
    source: `From your answer to "${from}"${suggestion.shared ? " (a shared story bank)" : ""}.`,
    action: "Use it",
  };
}

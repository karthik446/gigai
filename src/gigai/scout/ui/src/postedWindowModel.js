// 0110-019: the Jobs page's "Posted" chips and "Find postings from the last
// N days". Pure functions (no React), run under node by
// tests/api_e2e/test_ui_posted_window_model.py.
//
// The chips (7d / 10d / 30d / 60d / Any) filter the postings SHOWN by their
// own posting date (posting.published_at): no request, no run. A run holds
// only the postings of the window it searched, so a chip wider than that
// shows nothing more by itself; the grid then offers one button, which posts
// POST /api/runs/{run_id}/posted-window {days} (find_jobs/api/posted_window.py):
// a search of the boards stored on this machine that adds the postings the
// run does not hold yet, then ranks and assesses only those.
//
// `state` is that route's answer: {run_days, searched_days, choices,
// added_total, search, rank, assess, skip_reason}.
export const POSTED_ANY = "any";
// find_jobs/posted_window.py WINDOW_CHOICES.
export const POSTED_DAYS = [7, 10, 30, 60];
const DAY_MS = 86400000;

// [value, label] pairs for the chip row: the day windows, then "Any".
export function postedOptions(choices = POSTED_DAYS) {
  const days = (Array.isArray(choices) ? choices : POSTED_DAYS).filter((value) => Number.isInteger(value) && value > 0);
  return days.map((value) => [value, `${value}d`]).concat([[POSTED_ANY, "Any"]]);
}

export function isPostedWindow(posted) {
  return Number.isInteger(posted) && posted > 0;
}

// Was the posting published in the last `posted` days? A posting with no
// date (or one that does not parse) is kept, as the run's own window keeps
// it (filters.published_too_old): a window can only judge a date it has.
export function postedWithin(publishedAt, posted, now = Date.now()) {
  if (!isPostedWindow(posted) || !publishedAt) {
    return true;
  }
  const published = new Date(publishedAt).getTime();
  if (Number.isNaN(published)) {
    return true;
  }
  return published >= now - posted * DAY_MS;
}

// The button shows when the chosen window is wider than what was searched
// for the shown run (its own window, or a wider search made since).
export function canFindOlder(state, posted) {
  if (!state || !isPostedWindow(posted) || !Number.isInteger(state.searched_days)) {
    return false;
  }
  return posted > state.searched_days;
}

export function findOlderLabel(days) {
  return `Find postings from the last ${days} days`;
}

// Beside the button: why it is there.
export function findOlderHint(state) {
  if (!state || !Number.isInteger(state.searched_days)) {
    return "";
  }
  return `This run searched the last ${state.searched_days} days. The search reads the boards stored on this machine: no download, no new run.`;
}

const SKIP_TEXT = {
  run_not_finished: "This run is still going; older postings can be added once it ends.",
  no_run_input: "This run sealed no search to repeat.",
  sources_update_required: "No company postings are stored on this machine yet. Run Update sources, then try again.",
};

function postings(count) {
  return `${count} posting${count === 1 ? "" : "s"}`;
}

// What the last click did, in one line ("" before any click).
export function searchLine(state) {
  if (!state) {
    return "";
  }
  if (state.skip_reason) {
    return SKIP_TEXT[state.skip_reason] || `Not searched: ${state.skip_reason}.`;
  }
  const search = state.search;
  if (!search || !Number.isInteger(search.days)) {
    return "";
  }
  const added = search.added || 0;
  if (added === 0) {
    return `No more postings from the last ${search.days} days in the stored boards.`;
  }
  let line = `Added ${postings(added)} from the last ${search.days} days.`;
  const limit = state.assess ? state.assess.limit : null;
  if (state.assess) {
    line += Number.isInteger(limit) && limit < added ? ` Ranking those, then assessing the top ${limit}.` : " Ranking and assessing only those.";
  }
  if (search.not_added) {
    line += ` ${postings(search.not_added)} more matched and are over the run's limit.`;
  }
  return line;
}

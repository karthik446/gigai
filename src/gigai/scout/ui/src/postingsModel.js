// 0.1.10.7 M4b: the Jobs page by posting, as pure functions (no React, no
// fetch) so the rules run under node.
//
// The page reads three routes and writes two:
//   GET  /api/postings         the live search over the stored postings, across
//                              the active profiles: `postings.rows` (one per
//                              posting, shown for its best profile, or for the
//                              one profile the filter names), `profiles` (the
//                              tags), `counts` {matched, shown, new, by_state}
//   GET  /api/new?peek=1       the PEEK: `counts.new` is the "New since last
//                              check (N)" chip's number. A GET never moves the
//                              "new since" anchor
//   POST /api/new/seen         "Mark all seen": moves the anchor to now
//   POST /api/postings/assess  "Assess these". Without `approve: true` it
//                              answers `status: "ask"` with the count and the
//                              estimate and assesses nothing; the approval
//                              dialog shows that, and its Approve sends the
//                              body the server itself names (`question.yes`)
//
// A profile is a FILTER here (chips), never a mode: every row carries the
// tag of each active profile it matches, best first. Everything a row shows
// is the posting's own text or a code or number about it; nothing the user
// wrote is on this page, and every string is drawn as text.
import { displayCompanyName } from "./display.js";
import { rankEntry } from "./rankModel.js";
import { tokensText, secondsText } from "./metricsModel.js";

export const PAGE_ROWS = 50;
// 0110-10-01: real pages. The sizes the page offers; the server's own cap is MAX_LOOKUP_ROWS.
export const PAGE_SIZES = [25, 50, 100];
export const JOBS_HASH_BASE = "#/jobs";
// The most rows one read may ask for (posting_search.MAX_LIMIT): a job page opened by its link looks its posting up in them.
export const MAX_LOOKUP_ROWS = 200;
export const PROFILE_FILTER_KEY = "scout.jobs.profileFilter";

// The Scout label's name and its two codes, as the backend carries them
// (pipeline/steps.py LABEL_NAME, LABELS).
export const SCOUT_LABEL_NAME = "Scout label";
export const LABEL_WORDS = { recommended: "recommended", needs_attention: "needs attention" };
export const SCOUT_ATS_NAME = "Scout ATS";

// --- the time chips (mutually exclusive) ------------------------------------------------------

export const WINDOWS = ["new", "7d", "30d"];
const WINDOW_LABELS = { "7d": "7 days", "30d": "30 days" };

// The number the New chip shows, from the PEEK (GET /api/new): null until it is read.
export function newCountFromPeek(peek) {
  const count = peek && peek.counts && peek.counts.new;
  return typeof count === "number" && Number.isFinite(count) && count >= 0 ? count : null;
}

// [{window, label, testId, active}]: "New since last check (N)", "7 days", "30 days".
export function timeChips(newCount, selected) {
  return WINDOWS.map((window) => ({
    window,
    label: window === "new" ? `New since last check${typeof newCount === "number" ? ` (${newCount})` : ""}` : WINDOW_LABELS[window],
    testId: `time-chip-${window}`,
    active: selected === window,
  }));
}

// A click on a time chip: the chosen one, or none when it was the active one.
export function toggleWindow(selected, window) {
  return selected === window ? null : window;
}

// --- the profile filter (multi) ---------------------------------------------------------------

// [{profileId, label, matched, active}] from the response's `profiles`.
export function profileChips(profiles, selectedIds) {
  const selected = new Set(selectedIds || []);
  return (profiles || []).map((profile) => ({
    profileId: profile.profile_id,
    label: profile.label || profile.profile_id,
    matched: typeof profile.matched === "number" ? profile.matched : null,
    active: selected.has(profile.profile_id),
  }));
}

export function toggleProfile(selectedIds, profileId) {
  const selected = selectedIds || [];
  return selected.includes(profileId) ? selected.filter((id) => id !== profileId) : selected.concat(profileId);
}

// The stored filter, kept only for profiles that are still active.
export function keepActiveProfiles(selectedIds, profiles) {
  const active = new Set((profiles || []).map((profile) => profile.profile_id));
  return (selectedIds || []).filter((id) => active.has(id));
}

// --- the state chips (multi) ------------------------------------------------------------------

// `removed` is not a state of the search: it lists the postings the board no longer shows.
// 0110-10-02: a weak fit (it waits on answers, few requirements are met and its rank is low) is listed ONLY while its
// chip is on: the server leaves it out of every other list, and `counts.weak_fit` is the chip's number.
export const WEAK_FIT = "weak_fit";
export const STATE_FILTERS = [
  { value: "needs_answers", label: "Needs your answers" },
  { value: "assessed", label: "Assessed" },
  { value: "recommended", label: `${SCOUT_LABEL_NAME}: ${LABEL_WORDS.recommended}` },
  { value: "applied", label: "Applied", title: "Jobs you marked applied, and the ones that moved on from there (interview, offer, rejected, withdrawn)." },
  { value: WEAK_FIT, label: "Weak fit", title: "Jobs that need answers from you, but match few of the requirements and rank low, so they are probably not worth your time. They stay out of the list unless this is on." },
];
export const REMOVED_FILTER = { value: "removed", label: "Removed" };
// 0.1.11.4 R1: what a removed posting's chip and its page say.
export const CLOSED_LABEL = "Closed";
export const CLOSED_TEXT = "This posting is closed";

// The job page's closed banner: {text, since} when the posting is closed, else null. `liveness` is the job read's
// (GET /api/jobs: {state, checked_at, closed_at, note}); a row or a posting that carries `removed_at` says it too.
export function closedBanner(liveness, ...carriers) {
  const removed = carriers.map((item) => (item && typeof item.removed_at === "string" ? item.removed_at : "")).find(Boolean) || "";
  const closed = Boolean(liveness && liveness.state === "closed");
  if (!closed && !removed) {
    return null;
  }
  return { text: CLOSED_TEXT, since: (closed && typeof liveness.closed_at === "string" && liveness.closed_at) || removed || null };
}

// 0.1.11.4 7b: a posting stored under the company's own URL also has an address on its board (the job read's
// `liveness.board_url`). {url, label, down, note} while there is one, else null: the page shows it as a second link
// beside "Open posting", and first, with the server's sentence, when the company page is down and the job is open.
export const BOARD_LINK_LABEL = "Open on the job board";
const BOARD_PREFIXES = ["https://job-boards.greenhouse.io/", "https://jobs.lever.co/", "https://jobs.ashbyhq.com/"];
export function boardLink(liveness) {
  const url = liveness && typeof liveness.board_url === "string" ? liveness.board_url : "";
  if (!BOARD_PREFIXES.some((prefix) => url.startsWith(prefix))) {
    return null;
  }
  const note = liveness.state === "open" && liveness.company_page === "down" && typeof liveness.company_page_note === "string" ? liveness.company_page_note.trim() : "";
  return { url, label: BOARD_LINK_LABEL, down: Boolean(note), note: note || null };
}

// How many weak fits the other filters select (listed or not); null when the server does not say.
export function weakFitCount(counts) {
  const value = counts && counts.weak_fit;
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

// The state chips: [{value, label, title, active, count}]. Only the weak-fit chip carries a count (what it would list).
export function stateChips(states, counts) {
  const on = states || [];
  return STATE_FILTERS.map((option) => ({
    value: option.value,
    label: option.label,
    title: option.title,
    active: on.includes(option.value),
    count: option.value === WEAK_FIT ? weakFitCount(counts) : null,
  }));
}

export function toggleState(states, value) {
  const current = states || [];
  return current.includes(value) ? current.filter((item) => item !== value) : current.concat(value);
}

// 0.1.11.2: RANKED LOW. A posting nothing assessed yet whose known rank is below the weak-fit rank (50) says
// `ranked_low: true`. It is ORDERED lower, never hidden or collapsed: the server lists it with the rest, in rank order
// (after the other ranked postings not assessed yet, before the ones not ranked yet), and `counts.ranked_low` says how
// many the list holds. The page draws a plain "Ranked low (N)" divider above them; each stays a row like any other
// (open it, tick it, assess it). The master may be missing real experience: once it has it, a re-rank lifts the posting.
export function rankedLowCount(counts) {
  const value = counts && counts.ranked_low;
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;
}

// The list as the page draws it: [{kind: "row", row} | {kind: "divider", key, text, testId}], the rows untouched and in
// the server's order. In the rank order (`sort` "fit") a divider "Ranked low (N)" stands above the first ranked-low row
// of a run, and "Not ranked yet" where the postings with no rank score start right below them. In any other order
// ("newest posted") the ranked-low rows are spread through the list, so there is no divider: their chip says it.
export function listItems(rows, counts, sort = null) {
  const items = [];
  const byRank = !sort || sort === "fit";
  let before = null;
  for (const row of rows || []) {
    const low = row.ranked_low === true;
    const wasLow = before !== null && before.ranked_low === true;
    if (byRank && low && !wasLow) {
      items.push({ kind: "divider", key: `ranked-low:${row.job_identity}`, text: `Ranked low (${rankedLowCount(counts)})`, testId: "ranked-low-divider" });
    } else if (byRank && wasLow && !low && (row.state || "not_assessed") === "not_assessed" && typeof row.rank_score !== "number") {
      items.push({ kind: "divider", key: `not-ranked:${row.job_identity}`, text: "Not ranked yet", testId: "not-ranked-divider" });
    }
    items.push({ kind: "row", row });
    before = row;
  }
  return items;
}

// 0.1.11.2: "N not assessed", of the postings the list holds now (`counts.by_state.not_assessed`); null when none.
export function notAssessedCount(counts) {
  const value = counts && counts.by_state && counts.by_state.not_assessed;
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : 0;
}

export function notAssessedLine(counts) {
  const count = notAssessedCount(counts);
  return count ? `${count} not assessed` : null;
}

// 0.1.11.2: while the background rank has postings left (`ranking` of GET /api/postings and of the ASK), the order and
// a batch are of what is ranked SO FAR. Said, so a click during ranking is not silently the top of a half-ranked list.
export function rankingLine(ranking) {
  if (!ranking || !ranking.in_progress || !Array.isArray(ranking.by_profile)) {
    return null;
  }
  const sum = (key) => ranking.by_profile.reduce((total, item) => total + (Number.isInteger(item && item[key]) ? item[key] : 0), 0);
  return `Ranking is still running: ${sum("ranked")} of ${sum("total")} ranked. The order, and the top 50 by rank, are of what is ranked so far.`;
}

// 0110-10-14: the list's ORDER, beside the filters. Off (null) is the server's own order (the fit, the rank, the newest
// seen); on, `sort=newest_posted`: the day the posting went up, the newest first. An order is not a filter: it selects
// nothing (`hasFilter` does not count it), but it rides with them in the request and in the address.
export const NEWEST_POSTED = "newest_posted";
export const ORDER_CHIP = {
  value: NEWEST_POSTED,
  label: "Newest posted",
  title: "Order by the day the posting went up, the newest first. Off: the best fit first.",
};

export function toggleSort(sort) {
  return sort === NEWEST_POSTED ? null : NEWEST_POSTED;
}

export const EMPTY_FILTER = { profileIds: [], window: null, states: [], removed: false, query: "", sort: null };

export function hasFilter(filter) {
  return Boolean(filter.profileIds.length || filter.window || filter.states.length || filter.removed || filter.query.trim());
}

// The query string of GET /api/postings for a filter and a page.
export function postingsQuery(filter, { limit = PAGE_ROWS, offset = 0 } = {}) {
  const query = new URLSearchParams();
  (filter.profileIds || []).forEach((id) => query.append("profile_id", id));
  if (filter.query && filter.query.trim()) {
    query.set("q", filter.query.trim());
  }
  (filter.states || []).forEach((state) => query.append("state", state));
  if (filter.window) {
    query.set("window", filter.window);
  }
  if (filter.removed) {
    query.set("removed", "1");
  }
  if (filter.sort === NEWEST_POSTED) {
    query.set("sort", NEWEST_POSTED);
  }
  query.set("limit", String(limit));
  if (offset) {
    query.set("offset", String(offset));
  }
  return query.toString();
}

// --- one row ----------------------------------------------------------------------------------

// The profile tags of a row: every active profile it matches, best first
// (the server's `match_rank`). `shown` marks the profile the row's state is of.
export function profileTags(row, profiles) {
  const labels = new Map((profiles || []).map((profile) => [profile.profile_id, profile.label]));
  return (row.profiles || [])
    .slice()
    .sort((a, b) => (a.match_rank || 0) - (b.match_rank || 0))
    .map((item, index) => ({
      profileId: item.profile_id,
      label: labels.get(item.profile_id) || item.profile_id,
      best: index === 0,
      shown: item.profile_id === row.profile_id,
      state: item.state || null,
      rankScore: typeof item.rank_score === "number" ? item.rank_score : null,
    }));
}

// The score column, in the server's own words (0110-8-04, 0110-10-02, `score_text`):
// "Matched · fit 100% · 9 of 9 requirements · rank 76", "Matched (old assessment: older
// prompt) · fit 100% · 3 of 3 requirements · rank 83", "rank 97 · not assessed". The
// verdict first, the row's ONE fit number (`fit`: the share of requirements met with the
// must-haves counted twice) and "N of M", never a bare percent: a 1-of-1 reads "1 of 1".
// The rows are drawn in the order the server sends them (current, then
// stale, then not assessed; inside a group by fit, then rank, then the newest): this
// page never sorts them itself.
// A response without `score_text` (an older server) falls back to the number.
export function scoreText(row) {
  if (typeof row.score_text === "string" && row.score_text.trim()) {
    // 0110-10-03: "Matched · 11 of 12 requirements · rank 80 · 1 minor gap: Helm".
    // Only a match has "minor gaps"; a weak fit or a needs-answers row says what it is missing in its own words.
    const matched = (typeof row.state !== "string" || row.state === "matched") && row.thin_posting !== true; // 0.1.11.2: a thin posting is no match
    const gap = matched && typeof row.minor_gap_text === "string" && row.minor_gap_text.trim() ? ` · ${row.minor_gap_text}` : "";
    return `${row.score_text}${gap}`;
  }
  if (typeof row.score !== "number") {
    return "not ranked yet";
  }
  return row.score_kind === "assessment" ? `${row.score}% of requirements met` : `rank ${row.score}`;
}

// 0110-10-12: why an assessment is old, in the SERVER's words (scout_new._STALE_WORDS): the row, the job page and the
// terminal say the same reason. The row used to say two ("old assessment: older prompt" and "Stale: older settings").
export const STALE_WORDS = {
  posting_changed: "posting changed",
  older_prompt: "older prompt",
  settings_changed: "settings changed",
  story_bank_changed: "answers changed",
  resume_changed: "resume changed",
};

export function staleWords(reason) {
  return STALE_WORDS[reason] || humanCode(reason);
}

// "old assessment: older prompt": the server's `stale_label` when the row has one, else the same words from the code.
export function staleLabel(row) {
  if (!row || !row.stale_reason) {
    return null;
  }
  return typeof row.stale_label === "string" && row.stale_label.trim() ? row.stale_label.trim() : `old assessment: ${staleWords(row.stale_reason)}`;
}

// True when the score column already says the row's assessment is old (the server writes it into `score_text`).
function scoreSaysStale(row) {
  const label = staleLabel(row);
  return Boolean(label) && typeof row.score_text === "string" && row.score_text.includes(label);
}

const ROW_STATE_WORDS = {
  not_assessed: "Not assessed",
  needs_answers: "Needs your answers",
  matched: "Matched",
  has_gap: "Has a gap", // 0.1.11 (OD1): matched by verdict, held by the gate: a must-have is confirmed unmet
  not_a_match: "Not a match",
  tailored: "Resume ready",
  weak_fit: "Weak fit",
  thin_posting: "Thin posting", // 0.1.11.2: the row's `thin_posting`, or the state of a match on no requirement at all
};

// 0.1.11.3 (item 12): the application badge, in plain words: "Applied · Oct 6", "Interview · Oct 8", "Offer · ...",
// "Rejected · ...", "Withdrawn · ...". `application` is the row's {status, since} (or a job state with the same two
// keys); null for a job with no application. A label only: it never stands for the job's assessment state.
const APPLICATION_WORDS = {
  applied: "Applied",
  interview_scheduled: "Interview",
  offer_received: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};

export function applicationBadge(application) {
  const status = application && (application.status || application.state);
  if (!status || !APPLICATION_WORDS[status]) {
    return null;
  }
  const parsed = application.since ? new Date(application.since) : null;
  const day = parsed && !Number.isNaN(parsed.getTime()) ? parsed.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" }) : null;
  return { status, label: day ? `${APPLICATION_WORDS[status]} · ${day}` : APPLICATION_WORDS[status], title: application.since || undefined };
}

function humanCode(code) {
  return String(code || "").replace(/_/g, " ");
}

// The state chips of a row, in order: [{kind, label, tone, testId?, title?}].
//   state     the row's own state (needs answers with the number of open questions; a weak fit asks none)
//   assessed  beside any assessed state, so "assessed" always reads the same
//   label     the Scout label (recommended | needs_attention)
//   ats       the Scout ATS score
//   stale     the assessment was made on older inputs. 0110-10-12: ONE label per row. The score column says it in the
//             server's words ("Matched (old assessment: older prompt) · ..."), so this chip is drawn only for a row
//             whose score column does not (an older server), with the same words
//   removed   the board no longer lists the posting
export function rowChips(row) {
  const chips = [];
  const state = row.state || "not_assessed";
  const assessed = state !== "not_assessed";
  const applied = applicationBadge(row.application);
  if (applied) {
    chips.push({ kind: "application", label: applied.label, tone: applied.status === "rejected" ? "danger" : applied.status === "withdrawn" ? "plain" : "ok", testId: "application-badge", title: applied.title });
  }
  if (state === "needs_answers") {
    const open = Array.isArray(row.open_questions) ? row.open_questions.length : 0;
    chips.push({ kind: "state", label: open ? `${ROW_STATE_WORDS.needs_answers} (${open})` : ROW_STATE_WORDS.needs_answers, tone: "warn" });
  } else if (state === WEAK_FIT) {
    chips.push({ kind: "state", label: ROW_STATE_WORDS.weak_fit, tone: "plain", testId: "weak-fit-chip", title: "Few requirements met and a low rank: no questions are asked for it." });
  } else if ((row.thin_posting === true && state === "matched") || state === "thin_posting") {
    // 0.1.11.2: a match read from fewer than 4 requirement rows is never drawn as a green "Matched".
    chips.push({ kind: "state", label: ROW_STATE_WORDS.thin_posting, tone: "warn", testId: "thin-posting-chip", title: "Thin posting, not enough requirements to score. Open the posting to check." });
  } else if (assessed) {
    chips.push({ kind: "state", label: ROW_STATE_WORDS[state] || humanCode(state), tone: state === "not_a_match" ? "danger" : state === "has_gap" ? "warn" : "ok" });
  }
  chips.push(assessed ? { kind: "assessed", label: "Assessed", tone: "plain" } : { kind: "state", label: ROW_STATE_WORDS.not_assessed, tone: "plain" });
  if (!assessed && row.ranked_low === true) {
    // 0.1.11.2: listed with the rest, lower by its rank; never a verdict.
    chips.push({ kind: "rank", label: "Ranked low", tone: "plain", testId: "ranked-low-chip", title: "Not assessed, and its rank is below the weak-fit rank: listed after the other ranked postings. A re-rank can lift it." });
  }
  if (row.label === "recommended" || row.label === "needs_attention") {
    chips.push({
      kind: "label",
      label: `${SCOUT_LABEL_NAME}: ${LABEL_WORDS[row.label]}`,
      tone: row.label === "recommended" ? "ok" : "warn",
      testId: "scout-label-chip",
    });
  }
  if (typeof row.ats_score === "number") {
    chips.push({ kind: "ats", label: `${SCOUT_ATS_NAME} ${row.ats_score}`, tone: "plain", testId: "ats-chip" });
  }
  if (assessed && row.stale_reason && !scoreSaysStale(row)) {
    const label = staleLabel(row);
    chips.push({ kind: "stale", label: `${label.charAt(0).toUpperCase()}${label.slice(1)}`, tone: "warn", testId: "stale-chip", title: row.stale_reason });
  }
  if (row.removed_at) {
    // 0.1.11.4 R1: the board no longer lists the posting. The chip says what that means; the filter that lists them is still "Removed".
    chips.push({ kind: "removed", label: CLOSED_LABEL, tone: "danger", title: `Closed: the board no longer lists this posting (since ${row.removed_at})` });
  }
  return chips;
}

const MODE_WORDS = { remote: "Remote", hybrid: "Hybrid", onsite: "On-site", on_site: "On-site" };

// "Acme · Denver, CO · Hybrid · $180k-$220k": only what the posting states.
export function detailLine(row) {
  return [row.company ? displayCompanyName(row.company) : null, row.location, MODE_WORDS[row.work_mode] || null, row.salary].filter((part) => typeof part === "string" && part.trim()).join(" · ");
}

// --- the posting's date (0110-10-14) -----------------------------------------------------------

// A posting has three dates and they are different facts (scout_new.posting_dates):
//   published_at   the BOARD's own date, the one the 7 / 30 days chips judge. `published_kind` says what it is:
//                  "posted" (Greenhouse, Lever, Ashby: the day the posting went up) or "updated" (a board kind that
//                  gives only the posting's last change: none today)
//   updated_at     the board's LAST CHANGE to the posting (null when it gives none): never the posting day
//   first_seen_at  when Scout first stored the posting: what "New since last check" judges (`first_seen`, its older name)
// The row shows ONE of them with its own word, never one as the other: "posted 10 days ago", "updated 3 days ago",
// or, when the board gives no date, "first seen 3 days ago". Null when the row has no date at all. The job page adds
// the last change beside it (`updated`, below) when it is a later day than the posting day.
const DATE_WORDS = { posted: "posted", updated: "updated", first_seen: "first seen" };

export function postingDate(row) {
  if (!row) {
    return null;
  }
  const valid = (value) => typeof value === "string" && value && !Number.isNaN(new Date(value).getTime());
  const seen = valid(row.first_seen_at) ? row.first_seen_at : valid(row.first_seen) ? row.first_seen : null;
  if (valid(row.published_at)) {
    return { kind: row.published_kind === "updated" ? "updated" : "posted", at: row.published_at, firstSeenAt: seen };
  }
  return seen ? { kind: "first_seen", at: seen, firstSeenAt: seen } : null;
}

// Whole calendar days between two instants, in the reader's own time zone (a posting of yesterday evening is "yesterday").
function calendarDays(iso, now) {
  const day = (value) => {
    const date = new Date(value);
    return Date.UTC(date.getFullYear(), date.getMonth(), date.getDate());
  };
  return Math.round((day(now) - day(iso)) / 86400000);
}

// "today", "yesterday", "10 days ago", "3 months ago", "2 years ago".
export function agoText(iso, now = Date.now()) {
  const days = calendarDays(iso, now);
  if (days <= 0) {
    return "today";
  }
  if (days === 1) {
    return "yesterday";
  }
  if (days < 60) {
    return `${days} days ago`;
  }
  if (days < 365) {
    return `${Math.floor(days / 30)} months ago`;
  }
  const years = Math.floor(days / 365);
  return `${years} year${years === 1 ? "" : "s"} ago`;
}

function exactDate(iso) {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

// {kind, at, text, date, title, updated}: `text` is the line ("posted 10 days ago"), `date` the exact day ("Sep 24,
// 2026"), `title` the hover, which says what the date is and, when there is a board date, when Scout first saw the
// posting. `updated` ({at, text, date}, else null) is the board's last change when it is a later day than the posting
// day ("updated 3 days ago"), or the only board date there is: it is said BESIDE the line, never in its place.
export function postedLine(row, now = Date.now()) {
  const found = postingDate(row);
  if (!found) {
    return null;
  }
  const date = exactDate(found.at);
  const seen = found.firstSeenAt ? `First seen by Scout ${exactDate(found.firstSeenAt)}.` : "";
  const changedAt = typeof row.updated_at === "string" && row.updated_at && !Number.isNaN(new Date(row.updated_at).getTime()) ? row.updated_at : null;
  const later = changedAt && (found.kind === "first_seen" || (found.kind === "posted" && calendarDays(found.at, changedAt) >= 1));
  const updated = later ? { at: changedAt, text: `updated ${agoText(changedAt, now)}`, date: exactDate(changedAt) } : null;
  const titles = {
    posted: `Posted ${date} (the board's date).${updated ? ` The board last changed it ${updated.date}.` : ""} ${seen}`,
    updated: `The board last changed this posting ${date}; it gives no posting day. ${seen}`,
    first_seen: updated
      ? `First seen by Scout ${date}. The board gives no posting day; it last changed this posting ${updated.date}.`
      : `First seen by Scout ${date}. The board gives no date for this posting.`,
  };
  return { kind: found.kind, at: found.at, text: `${DATE_WORDS[found.kind]} ${agoText(found.at, now)}`, date, title: titles[found.kind].trim(), updated };
}

// The matching profiles a row is NOT shown for: the "Assess as <profile>" actions.
export function secondProfiles(row, profiles) {
  return profileTags(row, profiles).filter((tag) => !tag.shown);
}

// True when the row's posting was first seen after the anchor the response names.
export function isNew(row, anchor) {
  const since = anchor && anchor.since;
  return Boolean(since) && typeof row.first_seen === "string" && row.first_seen > since && !row.removed_at;
}

// --- assess these: the question, then the approval --------------------------------------------

// The body of the ASK (never `approve`). A selection names its postings;
// else the filter selects them. The route takes one profile, so with several
// profile chips on, the rows on the page are named instead.
export function assessAskBody({ selectedIds = [], filter = EMPTY_FILTER, rows = [], profileId = null } = {}) {
  if (selectedIds.length > 0) {
    return profileId ? { jobs: selectedIds.slice(), profile_id: profileId } : { jobs: selectedIds.slice() };
  }
  if (profileId) {
    return { profile_id: profileId };
  }
  const profiles = filter.profileIds || [];
  if (profiles.length > 1) {
    return { jobs: rows.map((row) => row.job_identity) };
  }
  const body = {};
  if (profiles.length === 1) {
    body.profile_id = profiles[0];
  }
  if (filter.query && filter.query.trim()) {
    body.query = filter.query.trim();
  }
  if (filter.states && filter.states.length) {
    body.states = filter.states.slice();
  }
  if (filter.window) {
    body.window = filter.window;
  }
  return body;
}

// 0.1.11.2: the ASK of "Assess all" (beside "N not assessed"): every not-assessed posting the filter selects, whatever
// is ticked. The server answers the top 50 by rank, the estimate and how many are left; the low-ranked ones are its
// second question. Several profile chips: the page's rows, as above.
export function assessAllBody({ filter = EMPTY_FILTER, rows = [] } = {}) {
  const profiles = filter.profileIds || [];
  if (profiles.length > 1) {
    return { jobs: rows.filter((row) => (row.state || "not_assessed") === "not_assessed").map((row) => row.job_identity) };
  }
  const body = { states: ["not_assessed"] };
  if (profiles.length === 1) {
    body.profile_id = profiles[0];
  }
  if (filter.query && filter.query.trim()) {
    body.query = filter.query.trim();
  }
  if (filter.window) {
    body.window = filter.window;
  }
  return body;
}

// What the approval dialog shows, from the ASK's answer. Null unless the
// server asked (`status: "ask"`): nothing is assessed before the Approve.
//   count           postings Approve assesses: 0110-10-11, 50 at a time and never more; 0.1.11.2: the top 50 by rank
//                   (`question.batch`; an older server has no cap and gives none: then all of them)
//   total           all the postings that are not assessed (`question.to_assess`)
//   moreAfter       how many are left after this batch; "Assess these" again takes the next 50
//   alreadyCurrent  selected postings whose assessment is current (left out)
//   byProfile       [{label, count}]
//   calls / tokens / seconds   the estimate from the recorded model calls
//                   (tokens and seconds null when the history cannot say)
//   approveBody     the body the server names for the yes, sent as it is
//   lowRank         0110-10-02: the postings ranked below the assess threshold, left out of `count`:
//                   {count, batch, moreAfter, minRank, calls, tokens, seconds, approveBody} (the body that assesses
//                   them too; `batch` of them at a time), or null
//   ranking         0.1.11.2: the "ranking is still running" line (rankingLine), or null
export function approvalDialog(response, profiles) {
  const question = response && response.status === "ask" ? response.question : null;
  if (!question) {
    return null;
  }
  const labels = new Map((profiles || (response && response.profiles) || []).map((profile) => [profile.profile_id, profile.label]));
  const estimate = question.estimate || {};
  const yes = question.yes && question.yes.api && question.yes.api.body;
  const low = response.low_rank && typeof response.low_rank === "object" ? response.low_rank : null;
  const lowYes = low && low.yes && low.yes.api && low.yes.api.body;
  const lowEstimate = (low && low.estimate) || {};
  const whole = (value, fallback) => (Number.isInteger(value) && value >= 0 ? value : fallback);
  const batch = whole(question.batch, question.to_assess);
  return {
    lowRank:
      low && low.skipped > 0
        ? {
            count: low.skipped,
            batch: whole(low.batch, low.skipped),
            moreAfter: whole(low.more_after, 0),
            minRank: typeof low.min_rank === "number" ? low.min_rank : null,
            calls: typeof lowEstimate.calls === "number" ? lowEstimate.calls : low.skipped,
            tokens: tokensText(lowEstimate.tokens),
            seconds: secondsText(lowEstimate.seconds),
            approveBody: lowYes && typeof lowYes === "object" ? { ...lowYes, approve: true, include_low_rank: true } : null,
          }
        : null,
    count: batch,
    total: question.to_assess,
    moreAfter: whole(question.more_after, 0),
    alreadyCurrent: question.already_current || 0,
    byProfile: (question.by_profile || []).map((item) => ({ label: labels.get(item.profile_id) || item.profile_id, count: item.count })),
    modelTarget: question.model_target || null,
    calls: typeof estimate.calls === "number" ? estimate.calls : batch,
    tokens: tokensText(estimate.tokens),
    seconds: secondsText(estimate.seconds),
    basisCalls: typeof estimate.basis_calls === "number" ? estimate.basis_calls : 0,
    approveBody: yes && typeof yes === "object" ? { ...yes, approve: true } : null,
    ranking: rankingLine(response.ranking),
  };
}

// The body Approve sends: the server's yes, or (the low-rank box ticked) the one that assesses the low-ranked too.
// Null when there is nothing to send: nothing above the threshold and the box not ticked.
export function approvalBody(dialog, includeLowRank) {
  if (!dialog) {
    return null;
  }
  if (includeLowRank && dialog.lowRank) {
    return dialog.lowRank.approveBody;
  }
  return dialog.count > 0 ? dialog.approveBody : null;
}

// "112 low-ranked ones are skipped (rank below 50). Assess those too? ~112 model calls, ~2.6M tokens"
export function lowRankLine(lowRank) {
  if (!lowRank) {
    return null;
  }
  const one = lowRank.count === 1;
  const below = lowRank.minRank === null ? "" : ` (rank below ${lowRank.minRank})`;
  // 0110-10-11, 0.1.11.2: its batch is the top 50 by rank too.
  const more = lowRank.moreAfter > 0;
  const which = more ? `the top ${lowRank.batch} by rank of those` : one ? "that" : "those";
  const after = more ? ` (${lowRank.moreAfter} more after these ${lowRank.batch})` : "";
  return `${lowRank.count} low-ranked ${one ? "one is" : "ones are"} skipped${below}. Assess ${which} too? ${estimateLine(lowRank)}${after}`;
}

// 0.1.11.2 (UAT-010): the dialog's line while the approved batch runs: the count is the request's own (`count`, the
// top-50-by-rank batch the Approve sent); the server streams no progress, so there is no done/total. With the low-ranked box
// ticked the count is not the one shown at the ask, so the line names no number.
export function assessingLine(dialog, includeLowRank = false) {
  if (includeLowRank && dialog.lowRank) {
    return "Assessing postings, the low-ranked ones included… this can take a minute.";
  }
  return `Assessing ${dialog.count} posting${dialog.count === 1 ? "" : "s"}… this can take a minute.`;
}

// 0110-10-11, 0.1.11.2: the dialog's title. "Assess the top 50 by rank of 120 postings?" when a batch is less than all of them.
export function approvalTitle(dialog) {
  if (dialog.count === 0 && dialog.lowRank) {
    return "Only low-ranked postings are selected";
  }
  if (dialog.moreAfter > 0) {
    return `Assess the top ${dialog.count} by rank of ${dialog.total} postings?`;
  }
  return `Assess ${dialog.count} posting${dialog.count === 1 ? "" : "s"}?`;
}

// 0110-10-11: "50 at a time: 70 more after these 50. Assess these again takes the next 50." Null when the batch is all of them.
export function approvalBatchLine(dialog) {
  if (!dialog || !(dialog.moreAfter > 0)) {
    return null;
  }
  const next = Math.min(dialog.count, dialog.moreAfter);
  return `the top ${dialog.count} by rank now, never more in one go. ${dialog.moreAfter} more after these ${dialog.count}: "Assess these" again takes the next ${next}.`;
}

// "~12 model calls, ~230k tokens, ~4.5 min" (the parts the history can say).
export function estimateLine(dialog) {
  const parts = [`~${dialog.calls} model call${dialog.calls === 1 ? "" : "s"}`];
  if (dialog.tokens) {
    parts.push(`~${dialog.tokens} tokens`);
  }
  if (dialog.seconds && dialog.seconds !== "0 s") {
    parts.push(`~${dialog.seconds}`);
  }
  return parts.join(", ");
}

// What an answered call says in one line; null for the ASK (the dialog says it).
export function assessOutcomeLine(response) {
  if (!response || response.status === "ask") {
    return null;
  }
  const low = response.low_rank && response.low_rank.skipped > 0 ? response.low_rank : null;
  const skipped = low ? ` ${low.skipped} low-ranked ${low.skipped === 1 ? "one was" : "ones were"} skipped (rank below ${low.min_rank}).` : "";
  if (response.status === "nothing_to_assess") {
    return low ? `Nothing was assessed.${skipped}` : "Nothing to assess: every selected posting has a current assessment.";
  }
  const assessed = response.assessed || {};
  const failed = Array.isArray(assessed.failed) ? assessed.failed : [];
  const codes = [...new Set(failed.map((item) => item.error_code).filter(Boolean))];
  // 0110-10-11, 0.1.11.2: a batch is the top 50 by rank; what is left is said with how to go on.
  const left = response.counts && Number.isInteger(response.counts.more_after) ? response.counts.more_after : 0;
  const more = left > 0 ? ` ${left} more not assessed yet: 50 at a time, "Assess these" again takes the next.` : "";
  return `Assessed ${assessed.assessed ?? 0} of ${assessed.requested ?? 0}.${codes.length ? ` Not assessed: ${codes.join(", ")}.` : ""}${skipped}${more}`;
}

// --- the summary and the count line -----------------------------------------------------------

// --- real pages (0110-10-01) ------------------------------------------------------------------

// A page number from the hash or a click: a whole number of at least 1, else 1.
export function cleanPage(value) {
  const number = Number.parseInt(value, 10);
  return Number.isFinite(number) && number >= 1 ? number : 1;
}

export function cleanSize(value) {
  const number = Number.parseInt(value, 10);
  return PAGE_SIZES.includes(number) ? number : PAGE_ROWS;
}

// How many pages `total` rows make at `size` per page (at least 1, so an empty list is "page 1 of 1").
export function pageCount(total, size = PAGE_ROWS) {
  const rows = Number.isFinite(total) && total > 0 ? total : 0;
  return Math.max(1, Math.ceil(rows / cleanSize(size)));
}

// The query offset of a page.
export function pageOffset(page, size = PAGE_ROWS) {
  return (cleanPage(page) - 1) * cleanSize(size);
}

// "Showing 51-100 of 591 postings"; "Showing 0 of 0 postings" when there is nothing.
export function countLine(counts, loaded, page = 1, size = PAGE_ROWS) {
  const total = counts && typeof counts.matched === "number" ? counts.matched : 0;
  const noun = `posting${total === 1 ? "" : "s"}`;
  if (!total || !loaded) {
    return `Showing 0 of ${total} ${noun}`;
  }
  const first = pageOffset(page, size) + 1;
  const last = Math.min(total, first + loaded - 1);
  return first === last ? `Showing ${first} of ${total} ${noun}` : `Showing ${first}-${last} of ${total} ${noun}`;
}

// The page buttons: every page up to 7, else 1 2 3 … 12 around the current page. "…" is a gap, not a link.
export function pageNumbers(page, pages) {
  const current = Math.min(Math.max(1, page), pages);
  if (pages <= 7) {
    return Array.from({ length: pages }, (_, index) => index + 1);
  }
  const wanted = new Set([1, 2, pages - 1, pages, current - 1, current, current + 1]);
  const numbers = [...wanted].filter((number) => number >= 1 && number <= pages).sort((a, b) => a - b);
  const out = [];
  numbers.forEach((number, index) => {
    if (index > 0 && number - numbers[index - 1] > 1) {
      out.push("…");
    }
    out.push(number);
  });
  return out;
}

// --- the page and the filters in the address (#/jobs?page=3&state=needs_answers) ---------------

// The hash of a view: only what differs from the default is written, so a plain list is `#/jobs`.
export function jobsHash(filter, page = 1, size = PAGE_ROWS) {
  const query = new URLSearchParams();
  if (cleanPage(page) > 1) {
    query.set("page", String(cleanPage(page)));
  }
  if (cleanSize(size) !== PAGE_ROWS) {
    query.set("size", String(cleanSize(size)));
  }
  (filter.profileIds || []).forEach((id) => query.append("profile", id));
  (filter.states || []).forEach((state) => query.append("state", state));
  if (filter.window) {
    query.set("window", filter.window);
  }
  if (filter.removed) {
    query.set("removed", "1");
  }
  if (filter.query && filter.query.trim()) {
    query.set("q", filter.query.trim());
  }
  if (filter.sort === NEWEST_POSTED) {
    query.set("sort", NEWEST_POSTED);
  }
  const text = query.toString();
  return text ? `${JOBS_HASH_BASE}?${text}` : JOBS_HASH_BASE;
}

// The view a hash names: {filter, page, size, bare} (`bare`: no parameter at all). Anything unknown is dropped.
export function parseJobsHash(hash) {
  const text = typeof hash === "string" ? hash : "";
  const mark = text.indexOf("?");
  const query = new URLSearchParams(mark < 0 ? "" : text.slice(mark + 1));
  const states = query.getAll("state").filter((value) => STATE_FILTERS.some((option) => option.value === value));
  const window = query.get("window");
  return {
    filter: {
      profileIds: query.getAll("profile").filter(Boolean),
      window: WINDOWS.includes(window) ? window : null,
      states: [...new Set(states)],
      removed: query.get("removed") === "1",
      query: query.get("q") || "",
      sort: query.get("sort") === NEWEST_POSTED ? NEWEST_POSTED : null,
    },
    page: cleanPage(query.get("page")),
    size: cleanSize(query.get("size")),
    bare: mark < 0 || text.slice(mark + 1) === "",
  };
}

export function needsAnswers(counts) {
  const value = counts && counts.by_state && counts.by_state.needs_answers;
  return typeof value === "number" ? value : 0;
}

// --- a posting as the job page's job ----------------------------------------------------------

// A stored posting no loaded run and no stored assessment carries, in
// jobModel's job shape, so its job page is the same JobPage. `text` is the
// row's excerpt of the posting (plain text; the page draws it as text).
export function postingJob(row) {
  const posting = {
    title: row.title || "",
    company: row.company || "",
    location: row.location || "",
    url: row.job_url || row.job_identity,
    normalized_url: row.job_identity,
    text: row.description || null,
    // The list row's description is a preview: the server ends a cut one with "…".
    text_cut: typeof row.description === "string" && row.description.endsWith("…"),
    published_at: null,
    provider: null,
    source_kind: "stored postings",
    work_mode: row.work_mode && row.work_mode !== "unknown" ? row.work_mode : null,
  };
  return {
    id: row.job_identity,
    posting,
    row: null,
    fromPostings: true,
    status: "posting",
    notAssessedReason: null,
    fromRunDate: null,
    runCreatedAt: null,
    rank: typeof row.rank_score === "number" ? rankEntry({ score: row.rank_score }) : null,
    quick: null,
    assessment: null,
    assessmentSource: null,
    verdict: "not_assessed",
    // 0.1.11.3: the row's labels (the posting's stated sponsorship, the company's catalog H-1B figure; null = none)
    sponsorship: row.sponsorship || "unknown",
    h1b: row.h1b || null,
  };
}

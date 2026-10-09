// 0.1.11.7 FS2: "Search all jobs" on the Jobs page, as pure functions (no React, no fetch) so the rules run under node.
//
// GET /api/search (find_jobs/api/search.py, `scout-free-search:1`) searches EVERY stored posting, whichever profile
// holds it or none: typed titles (a comma separates titles; every typed word must be in the title), company and
// location words, the default profile's filters unless `all=1`, newest posted first, never ranked. It takes no
// profile, so the Jobs page's profile chips and the top bar's selector do not change it. A search stores nothing.
//
//   page first      the request without `count`: the rows as soon as the page is read (`counts.total` null unless the
//                   server knew it already)
//   count after     the same search with `count=1` (and `limit=1`: only its counts are used): `counts.total`,
//                   `total_all` (the same search with no default filter) and `hidden`
//   Load more       the next 50: `offset` = the rows read so far
//
// A row carries labels, never a rank: the roles whose list holds it, its assessment state, its application.
// What a row can do: open its job page, "Assess · 1 model call" (POST /api/assess with the default profile's id: on
// today's storage every assessment is a profile's; the job is assessed once, no role named), "Mark applied" (the
// job's own event). Everything a row shows is the posting's own text, a code or a number, drawn as text.
import { applicationBadge, rowPlace, unclearLabel, usOnlyChecked } from "./postingsModel.js";
import { ORIGIN_JOB_PAGE, ORIGIN_QUICK_ASSESS } from "./jobModel.js";

export const SEARCH_PAGE = 50;
// `usOnly` (0.1.11.8 N1): null until the box is touched (the server applies the setup's default), then true / false.
export const EMPTY_FORM = { title: "", company: "", location: "", showAll: false, usOnly: null };
export const NOT_RANKED = "Not ranked. Save as a role to rank.";
export const ANY_SCOPE = "any place, any date";
// 0.1.11.8: how the two switches relate (the box's help line), and what a row of several copies is.
export const US_ONLY_WITH_SHOW_ALL =
  "Show all drops the default work mode, countries and posted window; US only is a switch of its own and still applies with Show all until you turn it off. With US only off and Show all off, your default countries apply.";
export const SEARCH_COPIES_RULE = "The counts are rows.";
export const PROFILE_RULE_NOTE = "The role matches by the role rule, which can list more than this search.";
export const NO_DEFAULTS_TEXT =
  "There are no default filters to apply: Scout's setup has no readable search settings. Turn on Show all to search every stored posting.";

// --- the request ------------------------------------------------------------------------------

function squeeze(text) {
  return typeof text === "string" ? text.replace(/\s+/g, " ").trim() : "";
}

// "Senior Engineer,  Staff Engineer ," -> ["Senior Engineer", "Staff Engineer"].
export function typedTitles(text) {
  return String(text || "")
    .split(",")
    .map(squeeze)
    .filter(Boolean);
}

export function cleanForm(form) {
  const given = form || EMPTY_FORM;
  const usOnly = given.usOnly === true || given.usOnly === false ? given.usOnly : null;
  return { title: typedTitles(given.title).join(", "), company: squeeze(given.company), location: squeeze(given.location), showAll: Boolean(given.showAll), usOnly };
}

// Something to search for: a title, or a company or location word (the server lists a company on its own).
export function canSearch(form) {
  const clean = cleanForm(form);
  return Boolean(clean.title || clean.company || clean.location);
}

export function sameSearch(a, b) {
  const [left, right] = [cleanForm(a), cleanForm(b)];
  return left.title === right.title && left.company === right.company && left.location === right.location && left.showAll === right.showAll && left.usOnly === right.usOnly;
}

// The query of one page. It never names a profile: the search is not a profile's list.
export function searchQuery(form, { offset = 0, limit = SEARCH_PAGE, count = false } = {}) {
  const clean = cleanForm(form);
  const query = new URLSearchParams();
  if (clean.title) {
    query.set("title", clean.title);
  }
  if (clean.company) {
    query.set("company", clean.company);
  }
  if (clean.location) {
    query.set("location", clean.location);
  }
  if (clean.showAll) {
    query.set("all", "1");
  }
  if (clean.usOnly !== null) {
    query.set("us_only", clean.usOnly ? "1" : "0"); // left out: the server applies the setup's default
  }
  query.set("limit", String(limit));
  if (offset > 0) {
    query.set("offset", String(offset));
  }
  if (count) {
    query.set("count", "1");
  }
  return query.toString();
}

// The count alone, asked after the page is shown: one row is the least the route reads.
export function countQuery(form) {
  return searchQuery(form, { limit: 1, count: true });
}

// --- what was read ----------------------------------------------------------------------------

// {form, rows, read, more, total, totalAll, hidden, filters, query, profiles, labelsRead, source}:
//   read     how many rows the server has given (the next page's offset), whatever was shown of them
//   filters  the default filters the search applied ({text: "remote, US, last 30 days", ...}); null for Show all
export function emptyResults(form) {
  return {
    form: cleanForm(form), rows: [], read: 0, more: false, total: null, totalAll: null, hidden: null, filters: null, query: null, profiles: [], labelsRead: true, source: null,
    usOnly: null, scope: null, allScope: null,
  };
}

function whole(value) {
  return Number.isInteger(value) && value >= 0 ? value : null;
}

function rowKey(row) {
  return `${row.company_key || ""}|${row.job_url || ""}`;
}

function pageRows(response) {
  const listing = response && response.postings;
  return listing && Array.isArray(listing.rows) ? listing.rows : [];
}

function mergeProfiles(known, served) {
  const byId = new Map(known.map((profile) => [profile.profile_id, profile]));
  (Array.isArray(served) ? served : []).forEach((profile) => byId.set(profile.profile_id, profile));
  return [...byId.values()];
}

// A page that was read: the first one replaces the rows, "Load more" adds its rows under them (a row already
// shown is not shown twice). The totals are kept unless this answer has them.
export function withPage(results, response, { append = false } = {}) {
  const served = pageRows(response);
  const counts = (response && response.counts) || {};
  const shown = append ? results.rows : [];
  const seen = new Set(shown.map(rowKey));
  const added = served.filter((row) => !seen.has(rowKey(row)) && seen.add(rowKey(row)));
  return {
    ...results,
    rows: shown.concat(added),
    read: (append ? results.read : 0) + served.length,
    more: Boolean(counts.more),
    total: whole(counts.total) ?? (append ? results.total : null),
    totalAll: whole(counts.total_all) ?? (append ? results.totalAll : null),
    hidden: whole(counts.hidden) ?? (append ? results.hidden : null),
    filters: response && response.filters ? response.filters : null,
    query: (response && response.query) || results.query,
    profiles: mergeProfiles(append ? results.profiles : [], response && response.profiles),
    labelsRead: response ? response.labels_read !== false : results.labelsRead,
    source: (response && response.source) || results.source,
    // 0.1.11.8: {on, default, rule} of the US-only switch as the server applied it, and the scope in its words.
    usOnly: (response && response.us_only) || results.usOnly,
    scope: (response && response.scope_text) || null,
    allScope: (response && response.all_scope_text) || null,
  };
}

// The count that came after the page: only its numbers are taken (its one row is a row the page already has).
export function withCount(results, response) {
  const counts = (response && response.counts) || {};
  return { ...results, total: whole(counts.total), totalAll: whole(counts.total_all), hidden: whole(counts.hidden) };
}

export function needsCount(results) {
  return Boolean(results) && results.total === null;
}

export function nextPageQuery(results) {
  return searchQuery(results.form, { offset: results.read });
}

// --- the lines --------------------------------------------------------------------------------

// `"senior engineer", "staff engineer", company acme, location denver`: what was typed, as the terminal says it.
export function typedText(results) {
  const query = results.query || {};
  const titles = Array.isArray(query.titles) ? query.titles : typedTitles(results.form.title);
  const company = Array.isArray(query.company) ? query.company.join(" ") : results.form.company;
  const location = Array.isArray(query.location) ? query.location.join(" ") : results.form.location;
  const parts = titles.length ? [titles.map((title) => `"${title}"`).join(", ")] : [];
  if (company) {
    parts.push(`company ${company}`);
  }
  if (location) {
    parts.push(`location ${location}`);
  }
  return parts.join(", ");
}

export function scopeText(results) {
  return results.scope || (results.filters && results.filters.text ? results.filters.text : ANY_SCOPE);
}

// Whether the "US only" box is ticked: what was set here, else what the last answer applied, else the known default.
export function searchUsOnlyChecked(form, results, knownDefault) {
  return usOnlyChecked(form.usOnly, (results && results.usOnly) || (typeof knownDefault === "boolean" ? { default: knownDefault } : null));
}

// Where a row is: of several copies, "Remote: Estonia, Lithuania, Latvia +4"; else its own location.
export function rowLocation(row) {
  return rowPlace(row);
}

function what(results) {
  return `${typedText(results)} (${scopeText(results)})`;
}

function number(value) {
  return value.toLocaleString("en-US");
}

// "Showing 1-50, newest first: "senior engineer" (remote, US, last 30 days)." Null with no row.
export function shownLine(results) {
  return results.rows.length ? `Showing 1-${number(results.rows.length)}, newest first: ${what(results)}.` : null;
}

// "708 postings match "senior engineer" (remote, US, last 30 days)." Null until the count is in.
export function totalLine(results) {
  if (results.total === null) {
    return null;
  }
  if (results.total === 0) {
    return `No stored posting matches ${what(results)}.`;
  }
  return `${number(results.total)} posting${results.total === 1 ? "" : "s"} match ${what(results)}.`;
}

// The sentence of a search with no row, said at once (the count may still be on its way).
export function noMatchLine(results) {
  return results.rows.length ? null : `No stored posting matches ${what(results)}.`;
}

// "Show all 17,132 (any place, any date)": what the default filters hid, once it was counted. Null for Show all.
export function hiddenLabel(results) {
  if (!results.filters || !results.hidden || results.totalAll === null) {
    return null;
  }
  return `Show all ${number(results.totalAll)} (${results.allScope || ANY_SCOPE})`;
}

// The switch says what it drops. `defaultsText` is the last answer's `filters.text` (null before the first search).
export function showAllLabel(defaultsText) {
  return defaultsText ? `Show all (drops the default filters: ${defaultsText})` : `Show all (${ANY_SCOPE}; drops the default profile's filters)`;
}

// What a refused or failed search says. `config_unavailable` (409): there are no default filters, so Show all.
export function errorText(error) {
  if (error && error.code === "config_unavailable") {
    return NO_DEFAULTS_TEXT;
  }
  return (error && (error.detail || error.message)) || String(error);
}

// --- one row ----------------------------------------------------------------------------------

function humanCode(code) {
  return String(code || "").replace(/_/g, " ");
}

function labelOf(profileId, names) {
  return names.get(profileId) || "a role that is no longer active";
}

// profile id -> label: the answer's own profiles first, then the app's (a profile the labels did not name).
export function profileNames(results, profiles) {
  const names = new Map();
  (profiles || []).forEach((profile) => names.set(profile.profile_id, profile.label));
  ((results && results.profiles) || []).forEach((profile) => names.set(profile.profile_id, profile.label));
  return names;
}

// The labels of a row, in order: [{kind, label, tone, title?, status?}].
//   profile      each role (tag) whose list holds the posting
//   assessment   its assessment state, with the role it was assessed as
//   application  "Applied · Oct 6" (postingsModel.applicationBadge)
//   removed      the board no longer lists it (only a search that asked for removed postings has such rows)
//   place        0.1.11.8: "unclear location", first, on a row US only kept without knowing where it is (`usOnly`)
export function rowLabels(row, names, { usOnly = false } = {}) {
  const place = unclearLabel(row, usOnly);
  const labels = (place ? [place] : []).concat((row.profiles || []).map((item) => ({
    kind: "profile",
    label: `found by: ${labelOf(item.profile_id, names)}`,
    tone: "plain",
    title: "This role's list holds this posting",
  })));
  if (row.assessment && row.assessment.state) {
    const as = row.assessment.profile_id ? ` as ${labelOf(row.assessment.profile_id, names)}` : "";
    labels.push({ kind: "assessment", label: `assessed: ${humanCode(row.assessment.state)}`, tone: "ok", title: `Assessed${as}${row.assessment.assessed_at ? ` · ${row.assessment.assessed_at}` : ""}` });
  }
  const badge = applicationBadge(row.application);
  if (badge) {
    labels.push({ kind: "application", label: badge.label, status: badge.status, tone: badge.status === "rejected" ? "danger" : badge.status === "withdrawn" ? "plain" : "ok", title: badge.title });
  }
  if (row.removed) {
    labels.push({ kind: "removed", label: "Closed", tone: "danger", title: "The board no longer lists this posting" });
  }
  return labels;
}

// The address of a row's job page: its job identity (the normalized URL), else its URL as stored.
export function rowJobId(row) {
  return row.job_identity || row.job_url || null;
}

// --- the row's actions ------------------------------------------------------------------------

// The profile an assessment from a search row is made as: the default one (GET /api/profiles `is_default`).
export function defaultProfileOf(profiles) {
  return (profiles || []).find((profile) => profile.is_default) || null;
}

export function assessLabel() {
  return "Assess · 1 model call";
}

// Offered for a live posting the default profile has not assessed yet (its re-assessment is on its job page).
export function canAssess(row, profile) {
  return Boolean(profile && row.job_url && !row.removed && !(row.assessment && row.assessment.profile_id === profile.profile_id));
}

// POST /api/assess: this posting, as the default profile. A posting a profile's list holds stays a Jobs posting
// ("job_page"); one no list holds is listed under Assessments ("quick_assess"), where it can be found again.
export function assessRequest(row, profile) {
  return {
    job: { job_url: row.job_url },
    resume: { profile_id: profile.profile_id },
    origin: (row.profiles || []).length ? ORIGIN_JOB_PAGE : ORIGIN_QUICK_ASSESS,
  };
}

// The row once POST /api/assess answered: its assessment label is the new one's.
export function assessedRow(row, response, profile) {
  const state = response && response.job_state && typeof response.job_state.state === "string" ? response.job_state.state : "assessed";
  return { ...row, assessment: { state, profile_id: profile.profile_id, assessed_at: (response && (response.updated_at || response.created_at)) || null } };
}

// "Mark applied": the job's own event (POST /api/applications takes the job alone, no profile).
export function canApply(row) {
  return Boolean(row.job_identity && !row.application && !row.removed);
}

export function applyRequest(row) {
  return { job_identity: row.job_identity, event_kind: "applied" };
}

export function appliedRow(row, at) {
  return { ...row, application: { status: "applied", since: at || null } };
}

export function replaceRow(results, changed) {
  return { ...results, rows: results.rows.map((row) => (rowKey(row) === rowKey(changed) ? changed : row)) };
}

// --- a row as its job page's row ----------------------------------------------------------------

// A posting NO profile's list holds has no row in GET /api/postings: its job page is built from the search row, in
// the Jobs row's shape (postingsModel.postingJob, postedLine), and stays that page when the by-address job read
// (GET /api/jobs) has nothing for it. Null for a posting a list holds: that page reads its own row by address.
export function unheldPostingRow(row) {
  const id = rowJobId(row);
  if (!id || (row.profiles || []).length) {
    return null;
  }
  return {
    job_identity: id,
    job_url: row.job_url || id,
    title: row.title || "",
    company: row.company || "",
    location: row.location || "",
    published_at: row.published_at || null,
    first_seen: row.first_seen || null,
    removed_at: null,
    profiles: [],
    application: row.application || null,
    description: null,
    from_search: true,
  };
}

// The same row for a page opened by its ADDRESS (a reload, a pasted link), where there is no search row: GET
// /api/jobs?url= answers such a posting from the company index and says so (`posting.fetch_kind`). Null for every
// other answer (a run's, an assessment's, a list's posting: those pages have their own source).
export const COMPANY_INDEX_KIND = "company_index";

export function servedPostingRow(served) {
  const posting = served && served.posting;
  if (!posting || posting.fetch_kind !== COMPANY_INDEX_KIND) {
    return null;
  }
  const id = served.job_identity || posting.job_identity || posting.normalized_url || null;
  if (!id) {
    return null;
  }
  return {
    job_identity: id,
    job_url: posting.source_url || id,
    title: posting.title || "",
    company: posting.company || "",
    location: posting.location || "",
    published_at: posting.published_at || null,
    published_kind: posting.published_kind || null,
    first_seen: posting.first_seen_at || posting.first_seen || null,
    removed_at: posting.removed_at || null,
    profiles: [],
    application: null,
    description: null,
    from_search: true,
  };
}

// --- "Save this search as a profile" ------------------------------------------------------------

export function canSaveAsProfile(results) {
  return Boolean(results) && typedTitles(results.form.title).length > 0;
}

// What the new-profile form is opened with: the typed titles, a name to start from, and what the search applied.
//   settingsText  the search's default filters ("remote, US, last 30 days"): a new profile starts with exactly
//                 these (the server gives it the default profile's settings), so nothing more is sent
//   showAll       the search dropped them: the profile still starts with the default profile's settings
//   wordsLeftOut  the company and location words, which a profile does not have
export function profileDraft(results) {
  const titles = typedTitles(results.form.title);
  return {
    label: titles[0] || "",
    titles,
    settingsText: results.filters && results.filters.text ? results.filters.text : null,
    showAll: !results.filters,
    wordsLeftOut: [results.form.company, results.form.location].filter(Boolean).join(", ") || null,
    note: PROFILE_RULE_NOTE,
  };
}

export function draftSettingsLine(draft) {
  const start = draft.showAll
    ? "This search showed every posting (Show all). The profile starts with the default profile's location, work mode, countries and posted window."
    : `The profile starts with this search's settings: ${draft.settingsText}.`;
  return draft.wordsLeftOut ? `${start} The company and location words of the search (${draft.wordsLeftOut}) are not part of a profile.` : start;
}

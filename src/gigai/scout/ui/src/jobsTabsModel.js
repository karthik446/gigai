// 0.1.11.8 N3: the two tabs of the Jobs page and the help behind each "?", as pure functions and constants (no React)
// so the rules run under node.
//
//   Your jobs   #/jobs (with its page and filters: #/jobs?page=3&us=0)   the profiles' list, its filters and its box
//   Search      #/jobs/search                                            Search all jobs: every stored posting
//
// One search box is on the page at a time. The tab is in the address, so a reload and Back / Forward keep it. A job
// page is #/jobs/<its address>; an address is never the bare word "search" (routing.js reads the tab first).
//
// The help: what was a paragraph under each box is ONE short line there, and the whole text behind a "?" beside it
// (components/HelpTip.jsx). The whole text is the same sentences as before (postingsModel / freeSearchModel).
import { NOT_RANKED, SEARCH_COPIES_RULE, US_ONLY_WITH_SHOW_ALL } from "./freeSearchModel.js";
import { CANONICAL_RULE, COPIES_RULE, JOBS_HASH_BASE, US_ONLY_RULE } from "./postingsModel.js";

export const TAB_YOURS = "yours";
export const TAB_SEARCH = "search";
export const SEARCH_TAB_HASH = "#/jobs/search";

export const JOBS_TABS = [
  { tab: TAB_YOURS, label: "Your jobs", id: "jobs-tab-yours", panelId: "jobs-panel-yours" },
  { tab: TAB_SEARCH, label: "Search", id: "jobs-tab-search", panelId: "jobs-panel-search" },
];

// The tab a route's `tab` parameter names (routing.js: "search" or nothing).
export function tabOf(param) {
  return param === TAB_SEARCH ? TAB_SEARCH : TAB_YOURS;
}

// The tab a hash names: #/jobs/search (a "/" or a query after it too) is Search; any other Jobs address is Your jobs.
export function tabOfHash(hash) {
  return /^#\/jobs\/search\/?(?:\?.*)?$/.test(typeof hash === "string" ? hash : "") ? TAB_SEARCH : TAB_YOURS;
}

// Where a tab's link goes. `listHash`: the list as it was left (its page and filters), so coming back from Search
// shows the same list.
export function tabHash(tab, listHash) {
  if (tab === TAB_SEARCH) {
    return SEARCH_TAB_HASH;
  }
  return typeof listHash === "string" && tabOfHash(listHash) === TAB_YOURS && listHash.startsWith(JOBS_HASH_BASE) ? listHash : JOBS_HASH_BASE;
}

// The tab an arrow key, Home or End moves to from `tab`; null for any other key. The ends wrap.
export function tabForKey(tab, key) {
  const order = JOBS_TABS.map((entry) => entry.tab);
  const at = Math.max(0, order.indexOf(tab));
  if (key === "ArrowRight" || key === "ArrowDown") {
    return order[(at + 1) % order.length];
  }
  if (key === "ArrowLeft" || key === "ArrowUp") {
    return order[(at + order.length - 1) % order.length];
  }
  if (key === "Home") {
    return order[0];
  }
  if (key === "End") {
    return order[order.length - 1];
  }
  return null;
}

// The Jobs tab last shown in this browser tab (in memory: a reload forgets it). A job page's "←" goes back to it, so
// a search result's page returns to Search, where the results still are.
let lastTab = TAB_YOURS;

export function rememberTab(tab) {
  lastTab = tabOf(tab);
}

// {hash, tab} of a job page's back link.
export function backToJobs() {
  return lastTab === TAB_SEARCH ? { hash: SEARCH_TAB_HASH, tab: TAB_SEARCH } : { hash: JOBS_HASH_BASE, tab: TAB_YOURS };
}

// --- the short lines and the whole text behind each "?" -----------------------------------------

export const US_ONLY_SHORT = "Hides postings clearly outside the US; unclear places stay listed.";
export const COPIES_SHORT = "One row per job: the same job posted in several places is one row.";
export const SEARCH_SHORT = "Searches every stored posting, newest first. A comma separates titles.";

export const SEARCH_RULES = [
  "Searches every stored posting, in a role's list or in none.",
  "A comma separates titles; every word of a typed title must be in the posting's title.",
  "Company and location are whole words of the company's name or the posting's location.",
  "Your default profile's work mode, countries and posted window apply until you turn on Show all.",
  `Newest posted first. ${NOT_RANKED}`,
  "The role chips of Your jobs and the selected role do not change it, and a search stores nothing.",
];

// The paragraphs behind a "?": {label (what the button is for), paragraphs}.
export const HELP = {
  listUsOnly: { label: "About US only", paragraphs: [US_ONLY_RULE] },
  listCopies: { label: "About one row per job", paragraphs: [COPIES_RULE, CANONICAL_RULE] },
  searchUsOnly: { label: "About US only", paragraphs: [US_ONLY_RULE, US_ONLY_WITH_SHOW_ALL] },
  searchCopies: { label: "About one row per job", paragraphs: [COPIES_RULE, CANONICAL_RULE, SEARCH_COPIES_RULE] },
  searchRules: { label: "About this search", paragraphs: SEARCH_RULES },
};

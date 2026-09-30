// N33: the Jobs page shows ~50 cards at a time. Pure state helpers (no
// React), run under node by tests/api_e2e/test_ui_paging_model.py.
//
// `shown` is how many cards the grid may draw. It starts at one page, grows
// by one page on "Show more" and goes back to one page when a filter changes.
// The rows come from the server a page at a time in the grid's own order
// (createResultsPager, api.js), so the first `shown` rows of the sorted list
// are the top of the order and later pages only add cards at the end.
export const JOBS_PAGE_SIZE = 50;

export function initialShown(pageSize = JOBS_PAGE_SIZE) {
  return pageSize;
}

export function showMoreShown(shown, pageSize = JOBS_PAGE_SIZE) {
  return shown + pageSize;
}

// The same filters (by value) keep the paging; any change goes back to page 1.
export function filtersKey(filters) {
  return JSON.stringify(Object.keys(filters || {}).sort().map((key) => [key, filters[key]]));
}

// The cards drawn: the top `shown` of the (filtered, sorted) list.
export function pageOf(list, shown) {
  return list.slice(0, Math.max(0, shown));
}

// How many rows must be loaded for the grid to draw `shown` cards: with a
// filter on, every row of the run (a filter reads all of them); otherwise
// the cards on show. Never more than the run has.
export function rowsWanted({ shown, total, filtersActive }) {
  if (filtersActive) {
    return total;
  }
  return Math.min(shown, total);
}

// Is there more of the run than the grid is drawing?
export function hasMore({ drawn, matching }) {
  return drawn < matching;
}

// "Showing 50 of 500 postings". `matching` is the run's total while no
// filter is on (the rows not loaded yet count), else the filtered count.
export function showingLine({ drawn, matching, noun = "postings" }) {
  return `Showing ${drawn} of ${matching} ${noun}`;
}

// The label of the button that draws more.
export function showMoreLabel({ drawn, matching, pageSize = JOBS_PAGE_SIZE }) {
  const next = Math.min(pageSize, Math.max(0, matching - drawn));
  return `Show ${next} more`;
}

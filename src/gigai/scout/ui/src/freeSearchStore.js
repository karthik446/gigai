// 0.1.11.7 FS2: what "Search all jobs" has read, kept in the client (never on the server, never in the browser's
// storage: a search stores nothing and there is no history list; a reload starts empty).
//
//   * Kept at module level by its one user (components/FreeSearchPanel.jsx), so the results survive the route
//     change that unmounts the Jobs page: open a result, come back, the rows are there.
//   * PAGE FIRST, COUNT AFTER: `search()` asks for the page and shows it; then, when that answer had no total, it
//     asks the same search with `count=1` and the total line appears (`counting` meanwhile). A count that fails
//     leaves the rows as they are.
//   * "Load more" asks for the next 50 from the rows read so far and adds them under the ones shown.
//   * Only the newest search counts: an answer of an earlier one (the box changed, Show all was switched) is dropped.
//
// Plain JavaScript with its fetcher passed in, so the model test runs it under node.
import { EMPTY_FORM, canSearch, cleanForm, countQuery, emptyResults, needsCount, nextPageQuery, replaceRow, searchQuery, withCount, withPage } from "./freeSearchModel.js";

const START = { form: EMPTY_FORM, results: null, loading: false, loadingMore: false, counting: false, error: null, moreError: null, defaultsText: null };

export function createFreeSearchStore({ fetchSearch }) {
  let state = START;
  let run = 0; // the search the answers in flight belong to
  const listeners = new Set();

  const set = (patch) => {
    state = { ...state, ...patch };
    listeners.forEach((listener) => listener());
  };

  const count = (mine, form) => {
    set({ counting: true });
    return fetchSearch(countQuery(form))
      .then((response) => mine === run && state.results && set({ results: withCount(state.results, response), counting: false }))
      .catch(() => mine === run && set({ counting: false }));
  };

  return {
    getState: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    // The boxes and the switch as typed; nothing is asked until `search()`.
    setForm(patch) {
      set({ form: { ...state.form, ...patch } });
    },
    // The first page of what the boxes say, then its count. Resolves when both are in.
    search() {
      if (!canSearch(state.form)) {
        return Promise.resolve();
      }
      const form = cleanForm(state.form);
      const mine = ++run;
      set({ loading: true, loadingMore: false, counting: false, error: null, moreError: null, results: null });
      return fetchSearch(searchQuery(form))
        .then((response) => {
          if (mine !== run) {
            return undefined;
          }
          const results = withPage(emptyResults(form), response);
          set({ results, loading: false, defaultsText: results.filters && results.filters.text ? results.filters.text : state.defaultsText });
          return needsCount(results) ? count(mine, form) : undefined;
        })
        .catch((error) => mine === run && set({ loading: false, error }));
    },
    // The switch is part of the search: with results shown, switching it searches again.
    setShowAll(showAll) {
      set({ form: { ...state.form, showAll: Boolean(showAll) } });
      return state.results || state.error ? this.search() : Promise.resolve();
    },
    loadMore() {
      const before = state.results;
      if (!before || !before.more || state.loadingMore || state.loading) {
        return Promise.resolve();
      }
      const mine = run;
      set({ loadingMore: true, moreError: null });
      return fetchSearch(nextPageQuery(before))
        .then((response) => mine === run && state.results && set({ results: withPage(state.results, response, { append: true }), loadingMore: false }))
        .catch((error) => mine === run && set({ loadingMore: false, moreError: error }));
    },
    // A row changed on this page (assessed, marked applied): its labels say so without another search.
    changeRow(row) {
      if (state.results) {
        set({ results: replaceRow(state.results, row) });
      }
    },
    // Back to the empty box: nothing of the search is kept.
    clear() {
      run += 1;
      set({ ...START, defaultsText: state.defaultsText });
    },
  };
}

// "Save this search as a profile": what the new-profile form (views/ProfilesView.jsx) opens with, handed over once.
// Kept in this tab's memory only, until the form has it.
let pendingDraft = null;

export function keepProfileDraft(draft) {
  pendingDraft = draft || null;
}

// The form reads it when it mounts and drops it once it is shown (a read alone changes nothing: a render may run twice).
export function pendingProfileDraft() {
  return pendingDraft;
}

export function dropProfileDraft() {
  pendingDraft = null;
}

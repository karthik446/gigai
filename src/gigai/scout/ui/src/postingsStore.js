// 0110-9-01: the Jobs list, kept in the client. 0110-10-01: one PAGE of it at a time.
//
// On the operator's home the Jobs page asked for the whole list again on
// every mount (open ONE job, go back: a new GET /api/postings, a new peek),
// showed a spinner over nothing until it came, and a reload stacked another
// request on the one still running. This store is what the page reads now:
//
//   * ONE page (per filter, page and size) kept at module level, so it survives the route
//     changes that unmount the page. Coming back shows the rows at once.
//   * STALE-WHILE-REVALIDATE: a kept list is shown first and refreshed IN
//     PLACE (`refreshing`, no spinner over rows that are there); one read less
//     than FRESH_MS old is not read again at all.
//   * ONE request in flight per resource (the list, the peek, the status):
//     asking again while one is out returns without sending another.
//   * A page change is ONE request for that page (GET /api/postings?limit=&offset=);
//     a page read a moment ago is shown from the store without one.
//   * No interval timer: while the server prepares the postings (202,
//     `status: "preparing"`), GET /api/postings/status is polled, and the
//     next poll is scheduled only AFTER the previous answer.
//   * A slow answer is said (`slow` after SLOW_MS), and "preparing" carries
//     the percent: a message, never an endless spinner.
//   * `release()` (the page unmounts) aborts what is in flight and stops the
//     poll; the rows that were read stay.
//
// Plain JavaScript with its fetchers, clock and timers passed in, so the
// model test (tests/api_e2e/test_ui_postings_store.py) runs it under node.
import { PAGE_ROWS, newCountFromPeek, pageOffset, postingsQuery } from "./postingsModel.js";

export const FRESH_MS = 30000;
export const STATUS_POLL_MS = 1500;
export const SLOW_MS = 4000;

export const PREPARING_TEXT = "Preparing your postings (one time after an upgrade)…";
export const SLOW_TEXT = "Still loading your postings…";
export const LOADING_TEXT = "Loading postings…";

// "Preparing your postings (one time after an upgrade)… 42%".
export function preparingLine(preparing) {
  const percent = preparing && Number.isFinite(preparing.percent) ? Math.max(0, Math.min(100, Math.round(preparing.percent))) : 0;
  return `${PREPARING_TEXT} ${percent}%`;
}

// What the count line says while there are no rows to count yet; null when the list's own count line is shown.
export function waitingLine(state) {
  if (state.preparing) {
    return preparingLine(state.preparing);
  }
  if (state.loading) {
    return state.slow ? SLOW_TEXT : LOADING_TEXT;
  }
  return null;
}

function isPreparing(answer) {
  return Boolean(answer) && answer.status === "preparing";
}

function aborted(error) {
  return Boolean(error) && (error.code === "aborted" || error.name === "AbortError");
}

const EMPTY_STATE = Object.freeze({
  key: null,
  response: null,
  rows: [],
  loading: false,
  refreshing: false,
  preparing: null,
  slow: false,
  error: null,
  errorCode: null,
  newCount: null,
});

export function createPostingsStore({
  fetchPostings,
  fetchPeek,
  fetchStatus,
  now = () => Date.now(),
  setTimer = (run, ms) => setTimeout(run, ms),
  clearTimer = (timer) => clearTimeout(timer),
  makeAbort = () => new AbortController(),
}) {
  const lists = new Map(); // query key (filter, limit, offset) -> { response, rows, readAt }
  const listeners = new Set();
  let state = EMPTY_STATE;
  let filterShown = null;
  let viewShown = null; // { filter, page, size }
  let list = null; // the ONE list request in flight: { id, key, abort }
  let peek = null; // the ONE peek in flight
  let peekAt = null;
  let poll = null; // the status poll while the server prepares: { timer, abort }
  let slowTimer = null;
  let serial = 0;
  const sent = { list: 0, peek: 0, status: 0 };

  const set = (patch) => {
    state = { ...state, ...patch };
    listeners.forEach((listener) => listener());
  };

  const stopSlow = () => {
    if (slowTimer !== null) {
      clearTimer(slowTimer);
      slowTimer = null;
    }
  };

  const stopPoll = () => {
    if (poll) {
      if (poll.timer !== null) {
        clearTimer(poll.timer);
      }
      if (poll.abort) {
        poll.abort.abort();
      }
      poll = null;
    }
  };

  const dropList = () => {
    if (list) {
      list.abort.abort();
      list = null;
    }
    stopSlow();
  };

  const schedulePoll = (view, key) => {
    // The next poll is scheduled here, AFTER the previous answer (or its failure): never two status requests at once.
    poll = {
      abort: null,
      timer: setTimer(() => {
        const mine = poll;
        mine.timer = null;
        mine.abort = makeAbort();
        sent.status += 1;
        fetchStatus({ signal: mine.abort.signal })
          .then((status) => {
            if (poll !== mine) {
              return;
            }
            poll = null;
            if (status && status.state === "preparing") {
              set({ preparing: status });
              schedulePoll(view, key);
            } else {
              read(view, key);
            }
          })
          .catch((error) => {
            if (poll !== mine || aborted(error)) {
              return;
            }
            poll = null;
            schedulePoll(view, key);
          });
      }, STATUS_POLL_MS),
    };
  };

  const read = (view, key) => {
    dropList();
    stopPoll();
    const kept = lists.get(key);
    const id = (serial += 1);
    const abort = makeAbort();
    list = { id, key, abort };
    set({ loading: !kept, refreshing: Boolean(kept), slow: false, error: null, errorCode: null });
    slowTimer = setTimer(() => {
      slowTimer = null;
      if (list && list.id === id) {
        set({ slow: true });
      }
    }, SLOW_MS);
    sent.list += 1;
    fetchPostings(postingsQuery(view.filter, { limit: view.size, offset: pageOffset(view.page, view.size) }), { signal: abort.signal })
      .then((loaded) => {
        if (!list || list.id !== id) {
          return;
        }
        list = null;
        stopSlow();
        if (isPreparing(loaded)) {
          // The server is building the postings for the first time: say how far it is and ask its status, in turn.
          set({ preparing: loaded, loading: !kept, refreshing: false, slow: false });
          schedulePoll(view, key);
          return;
        }
        const rows = loaded.postings.rows;
        lists.set(key, { response: loaded, rows, readAt: now() });
        if (state.key === key) {
          set({ response: loaded, rows, loading: false, refreshing: false, preparing: null, slow: false, error: null, errorCode: null });
        }
        if (peekAt === null && !peek) {
          store.peekNew(); // the peek was answered "preparing" too: now that the postings are there, the New number is read
        }
      })
      .catch((error) => {
        if (!list || list.id !== id) {
          return;
        }
        list = null;
        stopSlow();
        if (aborted(error)) {
          set({ loading: false, refreshing: false, slow: false });
          return;
        }
        // A refresh that failed leaves the rows that are shown; only a first read with nothing to show is an error.
        set({
          loading: false,
          refreshing: false,
          slow: false,
          preparing: null,
          error: kept ? null : error.message || String(error),
          errorCode: error.code || null,
        });
      });
  };

  const store = {
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    getState: () => state,
    // What was sent so far, per resource (the model test counts requests with it).
    sent: () => ({ ...sent }),
    inFlight: () => ({ list: Boolean(list), peek: Boolean(peek), status: Boolean(poll && poll.abort) }),
    lastFilter: () => filterShown,
    // The page the list was left on: { filter, page, size }, so coming back (the job page's arrow) is the same page.
    lastView: () => viewShown,

    // Show page `page` (of `size` rows) of the list for `filter`: the kept rows at once, then one read unless one
    // is out or the kept one is fresh.
    show(filter, { page = 1, size = PAGE_ROWS, force = false } = {}) {
      const view = { filter, page, size };
      const key = postingsQuery(filter, { limit: size, offset: pageOffset(page, size) });
      const kept = lists.get(key);
      filterShown = filter;
      viewShown = view;
      if (state.key !== key) {
        // Another page or list: what was in flight for the one before is dropped, not left running beside this one.
        dropList();
        stopPoll();
        set({
          key,
          response: kept ? kept.response : null,
          rows: kept ? kept.rows : [],
          loading: false,
          refreshing: false,
          preparing: null,
          slow: false,
          error: null,
          errorCode: null,
        });
      }
      if (!force && ((list && list.key === key) || (poll && state.key === key && state.preparing))) {
        return; // one in flight for this page already (or its build is being watched): never a second
      }
      if (!force && kept && now() - kept.readAt < FRESH_MS) {
        return; // read a moment ago: the rows are shown as they are
      }
      read(view, key);
    },

    // The "New since last check (N)" number: one peek in flight, and not again while the last one is fresh.
    peekNew({ force = false } = {}) {
      if (peek && !force) {
        return;
      }
      if (!force && peekAt !== null && now() - peekAt < FRESH_MS) {
        return;
      }
      if (peek) {
        peek.abort.abort();
      }
      const mine = { abort: makeAbort() };
      peek = mine;
      sent.peek += 1;
      fetchPeek({ signal: mine.abort.signal })
        .then((answer) => {
          if (peek !== mine) {
            return;
          }
          peek = null;
          if (isPreparing(answer)) {
            set({ newCount: null });
            return;
          }
          peekAt = now();
          set({ newCount: newCountFromPeek(answer) });
        })
        .catch((error) => {
          if (peek !== mine) {
            return;
          }
          peek = null;
          if (!aborted(error)) {
            set({ newCount: null });
          }
        });
    },

    // After something changed the rows (an assessment, "Mark all seen"): read the list and the peek again, in place.
    refresh(filter, options = {}) {
      store.show(filter, { ...options, force: true });
      store.peekNew({ force: true });
    },

    // 0.1.11.3 (item 12): something outside the list changed what its rows say (an application was recorded): every kept
    // page is stale now, so the next time the page is shown it is read again (the kept rows still show first).
    expire() {
      lists.forEach((kept) => {
        kept.readAt = -Infinity;
      });
    },

    // The page unmounts: nothing stays in flight, nothing keeps polling. The rows that were read stay.
    release() {
      dropList();
      stopPoll();
      if (peek) {
        peek.abort.abort();
        peek = null;
      }
      set({ loading: false, refreshing: false, slow: false, preparing: null });
    },
  };
  return store;
}

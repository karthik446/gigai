// 0110-9-01: the Jobs list, kept in the client.
//
// On the operator's home the Jobs page asked for the whole list again on
// every mount (open ONE job, go back: a new GET /api/postings, a new peek),
// showed a spinner over nothing until it came, and a reload stacked another
// request on the one still running. This store is what the page reads now:
//
//   * ONE list (per filter) kept at module level, so it survives the route
//     changes that unmount the page. Coming back shows the rows at once.
//   * STALE-WHILE-REVALIDATE: a kept list is shown first and refreshed IN
//     PLACE (`refreshing`, no spinner over rows that are there); one read less
//     than FRESH_MS old is not read again at all.
//   * ONE request in flight per resource (the list, the peek, the status):
//     asking again while one is out returns without sending another.
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
import { PAGE_ROWS, newCountFromPeek, postingsQuery } from "./postingsModel.js";

export const FRESH_MS = 30000;
export const STATUS_POLL_MS = 1500;
export const SLOW_MS = 4000;
// GET /api/postings takes at most 200 rows: a refresh in place reads the rows shown, up to that.
export const MAX_REFRESH_ROWS = 200;

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
  loadingMore: false,
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
  const lists = new Map(); // query key -> { response, rows, readAt }
  const listeners = new Set();
  let state = EMPTY_STATE;
  let filterShown = null;
  let list = null; // the ONE list request in flight: { id, key, abort }
  let more = null;
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
    if (more) {
      more.abort.abort();
      more = null;
    }
    stopSlow();
  };

  const schedulePoll = (filter, key) => {
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
              schedulePoll(filter, key);
            } else {
              read(filter, key);
            }
          })
          .catch((error) => {
            if (poll !== mine || aborted(error)) {
              return;
            }
            poll = null;
            schedulePoll(filter, key);
          });
      }, STATUS_POLL_MS),
    };
  };

  const read = (filter, key) => {
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
    const limit = kept ? Math.min(MAX_REFRESH_ROWS, Math.max(PAGE_ROWS, kept.rows.length)) : PAGE_ROWS;
    sent.list += 1;
    fetchPostings(postingsQuery(filter, { limit, offset: 0 }), { signal: abort.signal })
      .then((loaded) => {
        if (!list || list.id !== id) {
          return;
        }
        list = null;
        stopSlow();
        if (isPreparing(loaded)) {
          // The server is building the postings for the first time: say how far it is and ask its status, in turn.
          set({ preparing: loaded, loading: !kept, refreshing: false, slow: false });
          schedulePoll(filter, key);
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
    inFlight: () => ({ list: Boolean(list), more: Boolean(more), peek: Boolean(peek), status: Boolean(poll && poll.abort) }),
    lastFilter: () => filterShown,

    // Show the list for `filter`: the kept rows at once, then one read unless one is out or the kept one is fresh.
    show(filter, { force = false } = {}) {
      const key = postingsQuery(filter, { limit: PAGE_ROWS, offset: 0 });
      const kept = lists.get(key);
      filterShown = filter;
      if (state.key !== key) {
        // Another list: what was in flight for the one before is dropped, not left running beside this one.
        dropList();
        stopPoll();
        set({
          key,
          response: kept ? kept.response : null,
          rows: kept ? kept.rows : [],
          loading: false,
          refreshing: false,
          loadingMore: false,
          preparing: null,
          slow: false,
          error: null,
          errorCode: null,
        });
      }
      if (!force && ((list && list.key === key) || (poll && state.key === key && state.preparing))) {
        return; // one in flight for this list already (or its build is being watched): never a second
      }
      if (!force && kept && now() - kept.readAt < FRESH_MS) {
        return; // read a moment ago: the rows are shown as they are
      }
      read(filter, key);
    },

    // "Show N more": the next page, appended to the rows shown.
    more(filter) {
      const key = state.key;
      const kept = lists.get(key);
      if (!kept || more || list) {
        return;
      }
      const abort = makeAbort();
      const mine = { abort };
      more = mine;
      set({ loadingMore: true });
      sent.list += 1;
      fetchPostings(postingsQuery(filter, { limit: PAGE_ROWS, offset: kept.rows.length }), { signal: abort.signal })
        .then((loaded) => {
          if (more !== mine) {
            return;
          }
          more = null;
          if (isPreparing(loaded)) {
            set({ loadingMore: false });
            return;
          }
          const rows = kept.rows.concat(loaded.postings.rows);
          lists.set(key, { ...kept, rows });
          if (state.key === key) {
            set({ rows, loadingMore: false });
          }
        })
        .catch((error) => {
          if (more !== mine) {
            return;
          }
          more = null;
          set({ loadingMore: false, error: aborted(error) ? state.error : error.message || String(error) });
        });
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
    refresh(filter) {
      store.show(filter, { force: true });
      store.peekNew({ force: true });
    },

    // The page unmounts: nothing stays in flight, nothing keeps polling. The rows that were read stay.
    release() {
      dropList();
      stopPoll();
      if (peek) {
        peek.abort.abort();
        peek = null;
      }
      set({ loading: false, refreshing: false, loadingMore: false, slow: false, preparing: null });
    },
  };
  return store;
}

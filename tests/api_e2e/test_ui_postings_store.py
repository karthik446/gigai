"""0110-9-01: the Jobs list kept in the client (``ui/src/postingsStore.js``), run under node.

On the operator's home the page asked for the whole list on every mount
(open ONE job, go back), a reload stacked a request on the one still running,
and "Loading postings…" never ended. The store is plain JavaScript with its
fetchers, clock and timers passed in, so this drives it under the system
``node`` with fetchers the script answers by hand, and asserts on the JSON it
prints. LOUD skip without ``node``. What lives in JSX is pinned by reading the
source.

Pinned:

* one request in flight per resource: asking again while one is out sends
  nothing;
* OPEN A JOB AND GO BACK: the page unmounts (``release``) and mounts again;
  the rows are there at once, with no request when the list was read a moment
  ago and exactly ONE refresh, in place, when it was not (the rows stay shown
  while it runs: never a spinner over rows);
* a 202 "preparing" answer is a message with the percent; the status is
  polled with the next poll scheduled only after the previous answer, and the
  list is read once when the server is ready;
* a slow answer says so; leaving the page aborts what is in flight and keeps
  the rows; after something changed the rows are read again in place.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

SCRIPT = """
import * as s from STORE_URL;
import { EMPTY_FILTER } from MODEL_URL;

// Fetchers the script answers by hand, a clock it moves, timers it fires.
function harness() {
  const calls = { list: [], peek: [], status: [] };
  const pending = { list: [], peek: [], status: [] };
  const fetcher = (kind) => (...args) => {
    const options = args[args.length - 1];
    calls[kind].push(kind === "list" ? args[0] : "");
    return new Promise((resolve, reject) => {
      const entry = { resolve, reject, signal: options.signal };
      options.signal.addEventListener("abort", () => {
        pending[kind] = pending[kind].filter((item) => item !== entry);   // a cancelled request is never answered
        reject(Object.assign(new Error("cancelled"), { code: "aborted" }));
      });
      pending[kind].push(entry);
    });
  };
  let clock = 1000;
  let timers = [];
  let id = 0;
  const store = s.createPostingsStore({
    fetchPostings: fetcher("list"), fetchPeek: fetcher("peek"), fetchStatus: fetcher("status"),
    now: () => clock,
    setTimer: (run, ms) => { id += 1; timers.push({ id, run, at: clock + ms }); return id; },
    clearTimer: (timer) => { timers = timers.filter((item) => item.id !== timer); },
  });
  const advance = (ms) => {
    clock += ms;
    const due = timers.filter((item) => item.at <= clock);
    timers = timers.filter((item) => item.at > clock);
    due.forEach((item) => item.run());
  };
  const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
  const answer = async (kind, value) => { pending[kind].shift().resolve(value); await settle(); };
  const fail = async (kind, error) => { pending[kind].shift().reject(error); await settle(); };
  return { store, calls, pending, advance, answer, fail, settle, timers: () => timers.length };
}

const page = (n, matched = 120) => ({
  schema_version: "scout-postings:1",
  counts: { matched, shown: n, new: 3, by_state: { needs_answers: 2 } },
  postings: { rows: Array.from({ length: n }, (_, i) => ({ job_identity: `https://jobs.example/${i}`, title: `Job ${i}` })) },
  profiles: [],
});
const preparing = (percent) => ({ schema_version: "scout-postings-status:1", status: "preparing", state: "preparing", percent, phase: "matching" });
const view = (store) => {
  const { rows, loading, refreshing, preparing, slow, error, newCount } = store.getState();
  return { rows: rows.length, loading, refreshing, preparing: preparing ? preparing.percent : null, slow, error, newCount };
};
const out = {};

// --- one in flight per resource --------------------------------------------------------------
{
  const h = harness();
  h.store.show(EMPTY_FILTER);
  h.store.show(EMPTY_FILTER);
  h.store.show(EMPTY_FILTER);
  h.store.peekNew();
  h.store.peekNew();
  out.stack = { sent: h.store.sent(), first: view(h.store), query: h.calls.list[0] };
  await h.answer("list", page(50));
  await h.answer("peek", { counts: { new: 7 } });
  out.stack.loaded = view(h.store);
  h.store.show(EMPTY_FILTER);           // read a moment ago: nothing is sent
  h.store.peekNew();
  out.stack.sentAfter = h.store.sent();
}

// --- open a job and go back ------------------------------------------------------------------
{
  const h = harness();
  h.store.show(EMPTY_FILTER);
  h.store.peekNew();
  await h.answer("list", page(50));
  await h.answer("peek", { counts: { new: 7 } });
  const before = h.store.sent();

  h.store.release();                    // the job page: the Jobs page unmounts
  h.advance(5000);
  const kept = h.store.lastFilter();
  h.store.show(kept);                   // back: it mounts again
  h.store.peekNew();
  out.back = { soon: view(h.store), sentSoon: h.store.sent(), before, sameFilter: kept === EMPTY_FILTER };

  h.store.release();                    // a second job, a longer read
  h.advance(s.FRESH_MS + 1000);
  h.store.show(h.store.lastFilter());
  h.store.peekNew();
  h.store.show(h.store.lastFilter());   // the effect running twice changes nothing
  out.back.later = view(h.store);       // the rows at once, refreshed in place
  out.back.sentLater = h.store.sent();
  await h.answer("list", page(50, 121));
  await h.answer("peek", { counts: { new: 8 } });
  out.back.refreshed = { ...view(h.store), matched: h.store.getState().response.counts.matched };
  out.back.sentDone = h.store.sent();

  // A refresh that fails leaves the rows that are shown, and says nothing.
  h.store.release();
  h.advance(s.FRESH_MS + 1000);
  h.store.show(h.store.lastFilter());
  await h.fail("list", Object.assign(new Error("Request failed with status 500."), { code: "internal_error" }));
  out.back.failedRefresh = view(h.store);
}

// --- the server prepares the postings: a message with the percent, one status poll at a time ---
{
  const h = harness();
  h.store.show(EMPTY_FILTER);
  h.store.peekNew();
  await h.answer("list", preparing(12));
  await h.answer("peek", preparing(12));
  out.prep = { first: view(h.store), line: s.waitingLine(h.store.getState()), sent0: h.store.sent() };
  h.store.show(EMPTY_FILTER);           // the page asks again (a re-render): the build is being watched, nothing is sent
  out.prep.sentAsked = h.store.sent();
  h.advance(s.STATUS_POLL_MS);          // the first poll goes out
  out.prep.sent1 = h.store.sent();
  h.advance(s.STATUS_POLL_MS * 10);     // it has not answered: no second poll, however long it takes
  out.prep.sentWaiting = h.store.sent();
  out.prep.inFlight = h.store.inFlight();
  await h.answer("status", { state: "preparing", percent: 57, phase: "matching" });
  out.prep.second = { ...view(h.store), line: s.waitingLine(h.store.getState()) };
  out.prep.sentAfterAnswer = h.store.sent();   // answered: the next poll is only scheduled
  h.advance(s.STATUS_POLL_MS);
  out.prep.sent2 = h.store.sent();
  await h.fail("status", new Error("Could not reach the local API."));   // a poll that fails is tried again, in turn
  h.advance(s.STATUS_POLL_MS);
  out.prep.sent3 = h.store.sent();
  await h.answer("status", { state: "ready", percent: 100, phase: "idle" });
  out.prep.sentReady = h.store.sent();         // ready: the list is read, once
  await h.answer("list", page(50));
  out.prep.sentListed = h.store.sent();        // the peek was answered "preparing" too: it is read now
  await h.answer("peek", { counts: { new: 4 } });
  out.prep.done = { ...view(h.store), line: s.waitingLine(h.store.getState()), timers: h.timers() };
}

// --- a slow answer says so; leaving aborts what is in flight; a change reads again in place -----
{
  const h = harness();
  h.store.show(EMPTY_FILTER);
  out.slow = { before: s.waitingLine(h.store.getState()) };
  h.advance(s.SLOW_MS);
  out.slow.after = s.waitingLine(h.store.getState());
  const signal = h.pending.list[0].signal;
  h.store.release();
  await h.settle();
  out.slow.released = { aborted: signal.aborted, ...view(h.store), inFlight: h.store.inFlight(), timers: h.timers() };

  h.store.show(EMPTY_FILTER);
  await h.answer("list", page(50));
  out.slow.firstSent = h.store.sent();
  h.store.show(EMPTY_FILTER, { page: 2 });
  h.store.show(EMPTY_FILTER, { page: 2 });   // one request per page change, however often the page asks
  out.slow.pageQuery = h.calls.list[h.calls.list.length - 1];
  out.slow.pageSent = h.store.sent();
  await h.answer("list", page(50));
  out.slow.page2 = view(h.store);
  h.store.show(EMPTY_FILTER, { page: 1 });   // page 1 was read a moment ago: shown from the store, nothing sent
  out.slow.page1Again = { ...view(h.store), sent: h.store.sent() };
  h.store.show(EMPTY_FILTER, { page: 2, size: 25 });   // another size is another list
  out.slow.size25 = { query: h.calls.list[h.calls.list.length - 1], ...view(h.store) };
  await h.answer("list", page(25));
  h.store.show(EMPTY_FILTER, { page: 2 });
  out.slow.lastView = h.store.lastView();
  const sent = h.store.sent();
  h.store.refresh(EMPTY_FILTER);        // an assessment was stored: the list and the peek again, the rows staying shown
  out.slow.refresh = { ...view(h.store), sent: h.store.sent(), sentBefore: sent, query: h.calls.list[h.calls.list.length - 1] };
  await h.answer("list", page(50));
  await h.answer("peek", { counts: { new: 0 } });
  out.slow.refreshed = view(h.store);

  // Another filter: its own list; coming back to the first one shows its kept rows at once.
  const filtered = { ...EMPTY_FILTER, states: ["matched"] };
  h.store.show(filtered);
  out.slow.other = view(h.store);
  const dropped = h.pending.list[0].signal;
  h.store.show(EMPTY_FILTER, { page: 2 });
  await h.settle();
  out.slow.backToFirst = { ...view(h.store), otherAborted: dropped.aborted };
}

out.lines = { preparing: s.preparingLine({ percent: 41.6 }), none: s.preparingLine(null), over: s.preparingLine({ percent: 180 }) };
console.log(JSON.stringify(out));
"""


def _run() -> dict[str, object]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the postings store was NOT run")
    script = SCRIPT.replace("STORE_URL", json.dumps((UI_SRC / "postingsStore.js").as_uri())).replace(
        "MODEL_URL", json.dumps((UI_SRC / "postingsModel.js").as_uri())
    )
    completed = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def out() -> dict[str, object]:
    return _run()


def test_one_request_in_flight_per_resource(out: dict[str, object]) -> None:
    stack = out["stack"]
    assert stack["sent"] == {"list": 1, "peek": 1, "status": 0}  # three shows and two peeks: one request each
    assert stack["first"] == {"rows": 0, "loading": True, "refreshing": False, "preparing": None, "slow": False, "error": None, "newCount": None}
    assert stack["query"] == "limit=50"
    assert stack["loaded"] == {"rows": 50, "loading": False, "refreshing": False, "preparing": None, "slow": False, "error": None, "newCount": 7}
    assert stack["sentAfter"] == stack["sent"]  # read a moment ago: asking again sends nothing


def test_open_a_job_and_go_back_reuses_the_list_and_fires_at_most_one_refresh(out: dict[str, object]) -> None:
    back = out["back"]
    shown = {"rows": 50, "loading": False, "refreshing": False, "preparing": None, "slow": False, "error": None, "newCount": 7}
    # Back after a moment: the rows at once, the same filter, and NOT ONE request.
    assert back["sameFilter"] is True
    assert back["soon"] == shown and back["sentSoon"] == back["before"] == {"list": 1, "peek": 1, "status": 0}
    # Back after a longer read: the rows at once (never "loading" over them) and exactly ONE refresh, in place.
    assert back["later"] == {**shown, "refreshing": True}
    assert back["sentLater"] == {"list": 2, "peek": 2, "status": 0}
    assert back["refreshed"] == {**shown, "newCount": 8, "matched": 121}
    assert back["sentDone"] == back["sentLater"]
    # A refresh that fails: the rows stay, no error over them.
    assert back["failedRefresh"] == {**shown, "newCount": 8}


def test_preparing_is_a_message_with_the_percent_and_the_status_is_polled_one_at_a_time(out: dict[str, object]) -> None:
    prep = out["prep"]
    assert prep["first"] == {"rows": 0, "loading": True, "refreshing": False, "preparing": 12, "slow": False, "error": None, "newCount": None}
    assert prep["line"] == "Preparing your postings (one time after an upgrade)… 12%"
    assert prep["sent0"] == prep["sentAsked"] == {"list": 1, "peek": 1, "status": 0}
    assert prep["sent1"] == {"list": 1, "peek": 1, "status": 1}
    # The poll has not answered: no second one goes out, however long it takes (no setInterval, no stacking).
    assert prep["sentWaiting"] == prep["sent1"] and prep["inFlight"] == {"list": False, "peek": False, "status": True}
    assert prep["second"]["preparing"] == 57 and prep["second"]["line"].endswith("57%")
    assert prep["sentAfterAnswer"] == prep["sent1"]  # only scheduled: sent after the pause
    assert prep["sent2"]["status"] == 2 and prep["sent3"]["status"] == 3
    assert prep["sentReady"] == {"list": 2, "peek": 1, "status": 3}  # ready: the list is read, once
    assert prep["sentListed"] == {"list": 2, "peek": 2, "status": 3}
    assert prep["done"] == {
        "rows": 50, "loading": False, "refreshing": False, "preparing": None, "slow": False, "error": None, "newCount": 4,
        "line": None, "timers": 0,
    }
    assert out["lines"] == {
        "preparing": "Preparing your postings (one time after an upgrade)… 42%",
        "none": "Preparing your postings (one time after an upgrade)… 0%",
        "over": "Preparing your postings (one time after an upgrade)… 100%",
    }


def test_a_slow_answer_says_so_leaving_aborts_and_a_change_is_read_in_place(out: dict[str, object]) -> None:
    slow = out["slow"]
    assert slow["before"] == "Loading postings…" and slow["after"] == "Still loading your postings…"
    released = slow["released"]
    assert released["aborted"] is True and released["loading"] is False and released["error"] is None
    assert released["inFlight"] == {"list": False, "peek": False, "status": False} and released["timers"] == 0
    # 0110-10-01: a page change is ONE request for that page; a page read a moment ago needs none.
    assert slow["pageQuery"] == "limit=50&offset=50" and slow["pageSent"]["list"] == slow["firstSent"]["list"] + 1
    assert slow["page2"]["rows"] == 50 and slow["page1Again"]["rows"] == 50 and slow["page1Again"]["sent"] == slow["pageSent"]
    assert slow["size25"]["query"] == "limit=25&offset=25" and slow["size25"]["rows"] == 0
    assert slow["lastView"]["page"] == 2 and slow["lastView"]["size"] == 50
    refresh = slow["refresh"]
    assert refresh["rows"] == 50 and refresh["loading"] is False and refresh["refreshing"] is True  # the rows stay shown
    assert refresh["sent"] == {"list": refresh["sentBefore"]["list"] + 1, "peek": refresh["sentBefore"]["peek"] + 1, "status": 0}
    assert refresh["query"] == "limit=50"  # the page asked for (page 1 here) is read again
    assert slow["refreshed"]["rows"] == 50 and slow["refreshed"]["refreshing"] is False and slow["refreshed"]["newCount"] == 0
    assert slow["other"]["rows"] == 0 and slow["other"]["loading"] is True  # another filter: its own list
    assert slow["backToFirst"]["rows"] == 50 and slow["backToFirst"]["loading"] is False and slow["backToFirst"]["otherAborted"] is True


def test_the_jobs_page_reads_the_store_and_nothing_polls_on_an_interval() -> None:
    view = (UI_SRC / "views" / "JobsView.jsx").read_text(encoding="utf-8")
    store = (UI_SRC / "postingsStore.js").read_text(encoding="utf-8")
    api = (UI_SRC / "api.js").read_text(encoding="utf-8")
    # The list is a module-level store (it survives the page's unmount), read with useSyncExternalStore.
    assert "\nconst postingsStore = createPostingsStore(" in view
    assert view.index("const postingsStore = createPostingsStore(") < view.index("export default function JobsView(")
    assert "useSyncExternalStore(postingsStore.subscribe, postingsStore.getState)" in view
    assert "return () => postingsStore.release();" in view and "postingsStore.lastView()" in view
    # The page itself fetches no list and no peek any more: only the store does.
    assert "getPostings(" not in view.split("createPostingsStore(")[1].split(");")[1]
    assert "setInterval(" not in view and "setInterval(" not in store
    assert "waitingLine(listed) || countLine(counts, rows.length, page, size)" in view
    # Requests can be cancelled, and the status route is the one polled.
    assert "signal," in api and '"aborted"' in api and 'request("GET", "/api/postings/status", undefined, options)' in api

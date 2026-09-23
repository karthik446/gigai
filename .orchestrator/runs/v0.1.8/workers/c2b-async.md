# C-2 follow-up: async POST /api/run — status

## State: DONE

## Files (owned, both pre-existing from C-2, edited only)
- `src/gigai/scout_present_api.py`
- `tests/behaviors/scout_find_jobs/test_present_ui.py`

No other files touched. `scout_find_jobs_contracts.py` was NOT edited (see "Flag for
contracts" below).

## Test output

```
$ uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_present_ui.py -q
..................                                                       [100%]
18 passed in 7.4-7.6s (ran 3x to check for timing flakiness — stable each time)
```

`uv run ruff check src/gigai/scout_present_api.py tests/behaviors/scout_find_jobs/test_present_ui.py`
— clean.

## What changed

`src/gigai/scout_present_api.py`:
- `Backend.start_run` signature is now
  `start_run(self, run_request: RunRequest, config_bytes: bytes, on_run_allocated: Callable[[str], None]) -> None`.
  Docstring on the Protocol method spells out the allocation-boundary contract: an
  exception before `on_run_allocated` is called must reach the POST caller; an exception
  after it must not (the POST has already answered) and has to be recorded by the backend
  so `run_status`/`run_results` reflect it.
- `NotWiredBackend.start_run` matches the new signature; still raises "not wired yet"
  immediately (never calls the callback), which is fine since nothing calls it from
  `__main__` code paths yet.
- `_handle_post_run` now: starts a daemon thread running `backend.start_run(...)`, waits
  on a `threading.Event` set either by `on_run_allocated` or by the thread's own exception
  handler (`allocated.wait(run_start_timeout_seconds)`, default 30s via the new
  `RUN_START_TIMEOUT_SECONDS` module constant). Three outcomes:
  - **Timeout** (event never set): 504 `run_start_timeout`.
  - **Set via a pre-allocation exception**: `FindJobsContractError` → 422 with its `.code`;
    anything else is re-raised (surfaces as an uncaught exception / 500 from the stdlib
    handler, same "don't swallow real bugs" behavior as before this follow-up).
  - **Set via `on_run_allocated`**: 202 with `{schema_version, run_id, status: "pending",
    node_receipts: []}`, same shape as before. A later post-allocation exception is
    intentionally not looked at here — it can only show up via `run_status`.
- `_make_handler` and `serve()` both gained a `run_start_timeout_seconds` keyword
  (default `RUN_START_TIMEOUT_SECONDS = 30.0`), so tests can inject a short timeout
  without touching the frozen contracts module. Production callers (I-3) should leave it
  at the default; nothing in `main()`/`__main__` overrides it.
- Loopback acceptance unchanged: still `{"127.0.0.1", "::1"}`, exactly as before this
  follow-up (not touched).

`tests/behaviors/scout_find_jobs/test_present_ui.py`:
- `FakeBackend.start_run` updated to the new 3-arg signature. Added
  `pre_allocation_error` / `post_allocation_error` / `sleep_after_allocation_seconds`
  constructor knobs so a single fake can model all three race outcomes; `post_allocation_error`
  is not re-raised (nothing would catch it) but recorded onto `self.failed_run_id`, which
  `run_status` then reports as an aggregate `"failed"` status — the fake's stand-in for
  "the real backend records the run's failure so run_status can see it."
- New tests:
  - `test_post_run_does_not_block_on_the_whole_traversal`: backend allocates immediately,
    then sleeps 2s inside `start_run` (simulating the rest of acquire+assess). Asserts the
    POST returns 202 in well under 1s (measured with `time.monotonic()`), i.e. the handler
    is not joining the whole `start_run` call.
  - `test_post_run_raise_before_allocation_is_mapped_to_422`: `pre_allocation_error` set to
    a `FindJobsContractError` → POST gets 422 with that error's own code, and
    `backend.started_requests` stays empty (never got past the raise).
  - `test_post_run_raise_after_allocation_still_returns_202_and_status_reflects_failure`:
    `post_allocation_error` set → POST still returns 202 with a run_id, then polls
    `GET /api/runs/{run_id}` until `run_status` reports `"failed"` (bounded 5s poll loop,
    typically resolves in a couple of iterations since the fake sets the failure
    synchronously right after calling back).
  - `test_post_run_allocation_timeout_is_504`: a `NeverAllocatesBackend` (never calls
    `on_run_allocated`, sleeps 1s) against `serve(..., run_start_timeout_seconds=0.05)` →
    504 `run_start_timeout`. Uses an injectable per-server timeout rather than a global
    constant so this test doesn't need to touch the 30s production default or race against
    it.
- Existing tests (`running_server` fixture and everything using plain `FakeBackend()`)
  needed no other changes: the fixture default's `start_run` completes essentially
  instantly (no sleep, no errors), so the new async wait-with-timeout path behaves exactly
  like the old join-based path for those cases — same 202 status, same run_id, same
  "started_requests" bookkeeping.

## Flag for contracts (did NOT edit `scout_find_jobs_contracts.py`)
Per the task, `ROUTES` for `POST /api/run` still lists `(202, 400, 403, 409, 422)` — it
does not include 504. This follow-up adds a real 504 `run_start_timeout` response for
that route (allocation timeout), which is now a status this route can return that isn't
in the frozen `ROUTES` status_codes tuple. I did not touch the contracts module per the
task's explicit instruction ("do NOT edit contracts") — flagging this here for whoever
owns the next contracts revision (I-0 owner / coordinator) to add 504 to the `POST
/api/run` RouteSpec's `status_codes`, or to confirm 504 should instead be folded into an
existing code.

## Choices made (not fully specified, flagging for I-3 review)
- Chose `threading.Event` + a small `dict` for the allocation handoff rather than a
  `queue.Queue`; functionally equivalent, simpler to reason about here since there's
  exactly one producer and one consumer per request.
- A pre-allocation exception that is NOT a `FindJobsContractError` is re-raised inside
  `_handle_post_run` (unchanged behavior from before this follow-up) — it propagates out
  of `do_POST` and becomes whatever `BaseHTTPRequestHandler`'s default error handling does
  (typically closes the connection / 500-ish from the client's view). This preserves "loud
  failure over silent 500 swallowing" but I-3 may want a specific mapped code for
  loopback/consent-refusal style pre-allocation errors — the task listed "a consent/loopback
  refusal ⇒ 403" as an example mapping, but nothing in the current `Backend` contract
  raises a typed exception for that case (loopback is already handled by the handler itself
  before `start_run` is ever called, and there's no `ConsentError`/similar type in
  `scout_find_jobs_contracts.py` to special-case). If I-3's real backend raises a specific
  exception type for a consent problem, that needs an explicit `except` branch here mapped
  to 403 — didn't invent a type to catch since none is specified by the frozen contracts.
- `NeverAllocatesBackend.start_run` in the timeout test never calls `on_run_allocated` at
  all (as opposed to calling it after the timeout has already fired) — this is the cleaner
  test of "the handler must not wait forever," and matches the task's "never calls back"
  framing directly.

## READ vs EXECUTED
**READ:**
- The original `src/gigai/scout_present_api.py` (my own prior C-2 work, full file) and
  `tests/behaviors/scout_find_jobs/test_present_ui.py` (full file) before editing either.
- This follow-up task's prompt, which fully specifies the new `Backend.start_run` shape,
  the handler's required behavior on each of the four outcomes (fast-return, pre-allocation
  raise, post-allocation raise, timeout), and the acceptance tests required.
- Did not re-read `scout_find_jobs_contracts.py` or the roadmap/amendment docs this time —
  nothing about the DTOs or route table changed; this is purely a handler/backend-boundary
  change confined to my own two files, and the task explicitly said not to touch contracts.

**EXECUTED:**
- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_present_ui.py -q`
  — run repeatedly while iterating, then 3x back-to-back at the end to check for timing
  flakiness in the new sleep/poll-based tests (all green, ~7.4-7.6s each run).
- `uv run ruff check src/gigai/scout_present_api.py tests/behaviors/scout_find_jobs/test_present_ui.py`
  — clean.
- Ad hoc `uv run --locked --extra test python3 -c "..."` snippets: confirmed
  `HTTPStatus.GATEWAY_TIMEOUT.value == 504`, and confirmed `RunStatusResponse.from_json`
  accepts `status: "failed"` with empty `node_receipts` (the DTO's aggregate-status
  cross-check only fires when `node_receipts` is non-empty, so a bare status override in
  the fake's fixture is valid). No fixtures or app files touched by these.
- Did NOT run `make test`, did NOT touch git (no stash/reset/clean/add/commit), no live
  network/provider/model calls — the only network activity in the tests is `httpx.Client`
  talking to our own `ThreadingHTTPServer` on ephemeral 127.0.0.1 ports, and the "traversal"
  is simulated with `time.sleep` in the fake, never a real graph run.

## What's left
- I-3 wires a real backend whose `start_run` calls `run.launch_find_jobs_run(...,
  ui_loopback_verified=True)` with the same `on_run_allocated` callback threaded through
  (per the task's framing, I-2's `launch_find_jobs_run` gets the same callback signature —
  that's I-2/I-3 work, not this packet's).
- Someone with contracts ownership should decide whether 504 belongs in `ROUTES`'
  `POST /api/run` status_codes tuple (see "Flag for contracts" above).
- The unmapped-pre-allocation-exception-type question above (no typed consent/loopback
  exception exists yet in the contracts to special-case into 403) is worth a decision once
  I-3's real backend exists and its actual exception surface is known.

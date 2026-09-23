# Worker: polish (P1 GET-500s + P2 UI status/waiting/poll-stop)

## READ vs EXECUTED

READ:
- `src/gigai/scout_present_api.py` (full file, to place the exception boundary and confirm no `traceback` import already existed)
- `tests/behaviors/scout_find_jobs/test_present_ui.py` (full file, to match fixture/backend/test conventions)
- `ui/src/App.jsx`, `ui/src/api.js`, `ui/src/components/NodeStatusList.jsx`, `ui/src/components/ResultsView.jsx`
- `git log`/`git status` for these files: all are new/untracked on this branch (no prior history to diff against)

EXECUTED:
- `src/gigai/scout_present_api.py`: wrapped `do_GET`'s route dispatch in
  `try/except Exception`, printing `traceback.print_exc(file=sys.stderr)` and
  returning a JSON 500 `{"error": {"code": "internal_error", "message": "an
  internal error occurred"}}` (no traceback/paths in the body). `do_POST` was
  left untouched — its documented behavior already routes `pre_allocation_error`
  through typed mapping and re-raises anything else, and the task said to keep
  POST as documented.
- `tests/behaviors/scout_find_jobs/test_present_ui.py`: added `_BlowsUpBackend`
  (a `FakeBackend` subclass whose `read_config`/`run_status`/`run_results` each
  raise `RuntimeError` with a fake secret path in the message) and three new
  tests — one per GET route — asserting `500`, `error.code == "internal_error"`,
  and that neither the raw exception text nor `/Users/nope` nor `"Traceback"`
  appears anywhere in the JSON response body.
- `ui/src/components/NodeStatusList.jsx`: a node with no receipt yet now shows
  `"waiting"` instead of `"pending"`. The aggregate `Run status: {status}` line
  was already rendering the API's raw status verbatim (no change needed there
  — confirmed by reading `App.jsx`/`NodeStatusList.jsx` there was no code path
  that substituted a literal `"pending"` string for the aggregate status).
- `ui/src/App.jsx`: `pollStatus`'s `.catch` now calls `stopPolling()` before
  setting `resultsError`, so a failing status poll shows the error and halts
  further polling instead of silently scheduling another `setTimeout`.
  `stopPolling` was added to the `useCallback` dependency array.

## Verification

- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_present_ui.py -q`
  → 26 passed (23 pre-existing + 3 new 500-path tests).
- `cd ui && yarn build` → succeeded (`dist/` built in 79ms, no errors).
- No `git add`/`git commit` performed.

## Notes for the coordinator

- P2's aggregate-status half of the report ("UI shows Run status: pending"
  while API says "running") did not reproduce in the current source: the
  aggregate status is already rendered as-is from the API response. The
  concrete, fixable defect matching that symptom was the per-node label
  showing "pending" for nodes with no receipt yet — fixed to "waiting" as
  the ticket's acceptance text literally requested ("show nodes as
  'waiting' until their receipts arrive"). If the coordinator's UI dry run
  saw the aggregate line itself stuck on a stale "pending" (e.g. from
  `handleConfirm`'s optimistic `setRunStatus` before the first poll lands),
  that is expected/correct per the POST contract (`status: "pending"` at
  202-time) and resolves on the first successful poll tick — no code change
  was needed or made for that specific transient frame.

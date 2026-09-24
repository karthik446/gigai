# Worker: b4-progressive-cards

**Task:** UAT 0.1.8.1 ticket B4 (+ U18) — replace the Scout find-jobs UI's
long results table with progressive per-posting cards (acquired card
appears immediately, fills in when its assessment finishes), live
acquire/assess/present step status instead of "waiting" until a receipt
lands, and a visible assess cap + not-assessed-reason breakdown. Dispatched
under Orca task `task_dc8280312b38`.

**Status:** Done. All owned files changed; nothing outside the owned-files
list touched (confirmed via `git status`).

READ vs EXECUTED: READ the brief, U18's UAT row, `market_acquisition.py`,
`proposal_execution.py`, `bindings.py`, `present_api.py`, `contracts.py`,
and every existing UI source file before writing anything. EXECUTED every
pytest run and both `yarn build` runs below; no live Exa/ATS/model network
call was made anywhere (existing `MockTransport`/fixture seams only, per
the M1 end-to-end test's own convention).

## Design

Added a purely additive, non-authoritative progress layer next to the
sealed `outputs/`/`receipts/` a run already writes, per the brief's
constraint (sealed contracts, the run seam, and the scheduler are
untouched):

- `src/gigai/scout/find_jobs/progress.py` (new): the file format + a
  `ProgressWriter` (append-only `.jsonl` lines via a single `open(...,
  "a")` write per call; whole-file replace via write-temp-then-`os.replace`
  for `steps.json`/`cap.json`) and a `read_progress()` reader that folds
  the event log into one entry per posting/assessment. Every reader path
  tolerates a missing file, a truncated last `.jsonl` line (mid-append),
  and corrupt JSON — never raises; a progress read is best-effort by
  design, the sealed outputs stay the only authority.
  - `runs/<id>/progress/steps.json`: `{step: {status, started_at,
    finished_at}}`.
  - `runs/<id>/progress/acquire.jsonl`: one line per posting kept after
    the B1 acquire filter, appended as acquire produces it.
  - `runs/<id>/progress/assess.jsonl`: `started`/`finished`/`not_assessed`
    event lines per posting, appended as assess produces them.
  - `runs/<id>/progress/cap.json`: `{cap, candidate_count}`, written once
    acquire finishes its B2 selection (well before assess starts).
- `market_acquisition.py` / `proposal_execution.py`: `acquire_node`/
  `assess_node` are now thin try/finally wrappers around their existing,
  untouched bodies (renamed `_acquire_node_body`/`_assess_node_body`) that
  call `progress.start_step`/`finish_step` around the call and
  `posting_acquired`/`assessment_started`/`assessment_finished`/
  `not_assessed`/`cap_known` at the exact points those events already
  happen in the existing logic. The progress writer is resolved
  best-effort (`_progress_writer`/`_assess_progress_writer`, both `try:
  ... except Exception: return None`) so a progress-only failure (e.g. a
  bare unit test's `NodeContext` with no real workpad) can never change
  the sealed node's return value — confirmed by running every existing
  acquire/assess test unchanged (see Test results).
- `present_api.py`: new `GET /api/runs/{id}/progress` route (checked
  *before* the bare `/api/runs/{id}` status route in `do_GET`, same way
  `/results` already is, so the suffix match doesn't get shadowed).
  `ScoutFindJobsBackend.run_progress` reads `progress.read_progress()` and
  folds it additively with whatever sealed `PresentPayload` already exists
  (via the existing `_payload`/`build_present_payload`, which already
  degrades to empty tuples pre-acquire) — a posting/assessment already
  sealed is never missing just because its progress line predates a race
  with the sealed write landing. Same loopback guard and not-found error
  shape (`{"error": {"code": "not_found", ...}}`) as every other route.
  Left `contracts.py`'s `ROUTES` tuple untouched (not an owned file, and
  nothing in the codebase actually dispatches from it — grepped) rather
  than adding an entry there.
- UI (`ui/src/**`): a new flat row model (`boardRows.js`:
  `rowsFromProgress`/`rowsFromResults`/`mergeRows`, pure functions, no
  React) that both the live view and the final results view render
  through via one `PostingsBoard.jsx` component — so a run finishing is
  never a layout swap, just cards gaining their terminal `assessment`.
  - `PostingCard.jsx` (new): one card per posting; acquisition fields
    (title/company/location/countries/sponsorship/source) render
    immediately, a status badge shows acquired/assessing/assessed/failed/
    not_assessed, and the assessment body (matrix/suggestions/questions)
    fills in via a new shared `AssessmentBody.jsx` once it exists.
  - `AssessmentBody.jsx` (new): the matrix-table + suggestions + questions
    markup extracted out of the pre-existing `AssessmentCard.jsx` so the
    original component (kept, now delegating to `AssessmentBody`) and the
    new progressive `PostingCard` render identical assessed-state markup
    instead of two independent copies — this is the brief's "reuse
    `AssessmentCard.jsx`" literally satisfied by lifting its body into a
    shared piece rather than leaving a second table implementation.
    `MatrixBadge.jsx`/`SponsorshipBadge.jsx` are reused unchanged by both.
  - `PostingsBoard.jsx` (new): the compact filter/sort bar (search,
    sponsorship, assessed/pending/not-assessed, company) plus the cap
    banner ("Assessing N of M matches (cap N); K assessed so far. Not
    assessed: ... duplicate, ... over_cap.") and the card list itself.
  - `ProgressBoard.jsx` (new): thin wrapper feeding `PostingsBoard` the
    live merged rows + cap from a `/progress` poll.
  - `ResultsView.jsx` (rewritten): now renders `PostingsBoard` from
    `rowsFromResults(payload)` instead of the old `<table>`; kept the
    pinned-resume and failures panels unchanged.
  - `NodeStatusList.jsx` (U18): now takes `progressSteps` (from
    `/progress`'s `steps`) and shows "running"/"failed" from that the
    instant a step starts, falling back to "waiting" only when neither a
    receipt nor a progress step exists yet; a terminal receipt (the sealed
    authority) still always wins the label when present.
  - `App.jsx`: polls `/progress` on its own ~1.5s cadence independent of
    the existing ~2s `/runs/{id}` status poll (`getRunProgress`, new in
    `api.js`), merges each snapshot into the running row set via
    `mergeRows` (so a card already on screen never disappears on a
    transient partial read), and stops the progress poll once the run
    reaches a terminal status. A failed progress poll retries rather than
    surfacing an error (best-effort, non-authoritative, unlike the
    existing status/results polls).

## Card UI (no live run available to screenshot per the exclusions; described)

Each posting is one collapsible card in a vertical list (replacing the old
table): a header row with the linked title, a right-aligned status pill
(grey "Waiting to be assessed…" / blue "Assessing…" / green "Assessed" /
grey "Not assessed" / red "Assessment failed"), and a meta line below with
company, location, source badge, and the sponsorship badge (reused
unchanged `SponsorshipBadge`). Above the card list, a muted banner reads
"Assessing 5 of 42 matches (cap 5); 2 assessed so far. Not assessed: 30
over_cap, 5 duplicate." once acquire has written `cap.json`. Opening a card
(auto-open once assessed) reveals the requirement×resume matrix table with
per-row status badges, then Suggestions/Questions lists — identical markup
to the pre-existing `AssessmentCard`, since both now share
`AssessmentBody`. The three step pills above the board (acquire/assess/
present) show "running" the moment their step starts rather than sitting
on "waiting" for the step's whole duration (U18).

## Test results

- Exact acceptance command: `uv run --locked --extra test pytest
  tests/behaviors/scout_find_jobs/test_progress.py
  tests/behaviors/scout_find_jobs/test_present_ui.py
  tests/behaviors/scout_find_jobs/test_present_api_static.py
  tests/behaviors/scout_find_jobs/test_m1_end_to_end.py -q` → **58 passed,
  1 xfailed**. The xfail is the pre-existing, already-documented,
  operator-acknowledged M1 regression ("ship 0.1.8.1 and handle it in
  v0.1.9") — untouched by this packet, not caused by it; `xfail(strict=False)`
  is a passing outcome. Added progress-file assertions inside that same
  test (steps done, sealed rows/assessments are a subset of the progress
  view, cap present) so they start executing the moment that regression is
  fixed elsewhere; they cannot run today since the test fails before
  reaching them.
- `test_progress.py` (new, 17 tests): `progress.py`'s writer/reader round
  trip (steps start/finish, postings append in order, assessment lifecycle
  folding, not-assessed counting, a truncated last `.jsonl` line, a corrupt
  `steps.json`), `acquire_node`'s progress writes on a direct call (every
  kept posting gets a line, `cap_known` fires, `finish_step(ok=False)` on
  `AcquireAllSourcesFailedError`), and the `/progress` HTTP route's shape
  before/mid-acquire/mid-assess/after-completion plus its 404 shape and
  that it doesn't shadow `/results`.
- Full `tests/behaviors/scout_find_jobs/` directory: **588 passed, 1
  xfailed** (203.99s) — every existing acquire/assess/present/UI test
  still passes unchanged.
- `make unit-tests` → **922 passed**, same count as before this packet
  (this task's new/changed files are covered by the exact-acceptance
  command above and the full-directory run, not the `fast_unit` marker
  set).
- `yarn build` in `src/gigai/scout/ui`: builds clean (29 modules). Ran it
  twice and diffed the resulting `dist/` byte-for-byte (`diff -rq`) —
  **identical both times** (same hashed filenames, same content):
  deterministic.

## Not done / explicitly out of scope

- No sealed-contract, run-seam, or scheduler change: `acquire_node`/
  `assess_node`'s existing bodies are untouched (renamed and wrapped, not
  edited) other than the new progress calls interleaved at points that
  already existed; their sealed `AcquireOutput`/`AssessOutput` return
  values are byte-identical to before (every pre-existing acquire/assess
  test still passes with no assertion changes).
- No hook added to `run.py`/`bindings.py` — not needed. `acquire_node`'s
  `NodeContext` always carries a resolvable workpad path (via `home_root`/
  `target` or `context.workpad_path`), and `assess_node`'s always carries
  `context.workpad_path`, so both nodes can resolve their own run root and
  write progress from inside the existing node call, with no scheduler
  involvement. Per the brief's "if live step status needs a hook in
  run.py or bindings.py, STOP and ask first" — it didn't, so I didn't ask.
- `contracts.py`'s `ROUTES` tuple (documentation-only; nothing dispatches
  from it, grepped) and its `RouteSpec`/DTO classes were left untouched;
  the new `/progress` route returns a plain dict, not a new frozen DTO,
  consistent with the brief calling its response a plain `{steps,
  postings[], assessments[], cap, not_assessed_counts}` shape rather than
  a new sealed contract.
- No schema/storage migration; no live Exa/ATS/model network call (existing
  `MockTransport`/fixtures only); no `git add`/commit/stash/reset/clean.

---

## r1 (coordinator review fix): guard every write, not just construction

**Task:** `task_dd905e5cc032`. Coordinator accepted the r0 design (progress/
layer, `/api/runs/{id}/progress`, the card UI, live step status, no
`run.py`/`bindings.py` hook, deterministic dist) and found one gap in this
module's own stated contract ("progress must never break the sealed run"):
only `ProgressWriter` *construction* was guarded
(`_progress_writer`/`_assess_progress_writer` catch a resolution failure ->
`None`); a write itself (`start_step`/`posting_acquired`/etc ->
`_append_line`/`_replace_json`) could still raise `OSError` (disk full,
permissions, a removed run dir) straight into `acquire_node`/`assess_node`
and fail the sealed run. Worse, `acquire_node`'s `except BaseException:
progress.finish_step("acquire", ok=False)` meant a failing progress write
there would *replace* the real exception being propagated.

**Status:** Done.

READ vs EXECUTED: READ `progress.py`'s existing `_read_jsonl` before
touching anything — it already skips a truncated/corrupt trailing line and
tolerates a missing file (confirmed by the existing `r0`
`test_read_progress_tolerates_a_truncated_last_jsonl_line`/
`..._a_corrupt_steps_json` tests, still passing), so no reader change was
needed, only the writer side the ticket named. EXECUTED every test run
below.

### Change

- `progress.py` only (no call-site change needed in
  `market_acquisition.py`/`proposal_execution.py` — confirmed by
  `git diff --stat` showing zero lines changed in either file this
  dispatch): `ProgressWriter.__init__` gained `self._disabled = False`, and
  every public method (`start_step`, `finish_step`, `posting_acquired`,
  `assessment_started`, `assessment_finished`, `not_assessed`, `cap_known`)
  now routes its body through a new `_guard(operation)` helper instead of
  calling `_append_line`/`_replace_json` directly. `_guard`: no-ops
  immediately if already disabled; otherwise runs the operation inside
  `try/except Exception` (deliberately *not* `BaseException` — a
  `KeyboardInterrupt`/`SystemExit` raised while a progress write happens to
  be in flight must still propagate, never be swallowed as a "progress
  failure"); on any `Exception` it sets `self._disabled = True` and prints
  one warning to stderr naming the run's progress dir and the exception,
  then returns normally. Because `acquire_node`/`assess_node`'s own
  `except BaseException: progress.finish_step(..., ok=False); raise`
  handlers call a method that itself can no longer raise, the real
  exception from `_acquire_node_body`/`_assess_node_body` is always what
  reaches `raise` unmodified — this was the sharpest part of the bug
  (a failing `finish_step` silently replacing the real failure) and is now
  structurally impossible rather than merely untested.
- No change to `_read_jsonl`/`_read_json`/`read_progress` — already
  tolerant per the READ above; the ticket's "add it if not" was a no-op
  here since it already was.

### Tests added (`test_progress.py`, exact file only — no other test file
touched, per the operator's test-budget rule)

- `test_a_failing_write_disables_the_writer_instead_of_raising` — a
  monkeypatched `_append_line` raising `OSError("disk full")` does not
  propagate out of `posting_acquired`; `writer._disabled` becomes `True`;
  the warning lands on stderr (captured via `capsys`).
- `test_a_disabled_writer_stops_attempting_further_writes` — after the
  first failure, three more calls across two different methods result in
  exactly one attempted call to the patched `_append_line` and exactly one
  `"warning:"` line — proving the "stop after the first failure" half of
  the fix, not just "don't raise."
- `test_a_failing_replace_json_write_also_disables_the_writer` — same
  guard covers the whole-file-replace path (`start_step` ->
  `_replace_json`); a subsequent `cap_known` (also `_replace_json`) is a
  silent no-op once disabled, and `read_progress` afterward confirms
  nothing was ever actually written.
- `test_a_keyboard_interrupt_during_a_write_still_propagates` — a
  monkeypatched `_append_line` raising `KeyboardInterrupt` is **not**
  caught by `_guard`; `pytest.raises(KeyboardInterrupt)` around the call
  confirms `except Exception` (not `BaseException`) was the right choice.
- `test_acquire_node_completes_with_correct_sealed_output_when_progress_writes_fail`
  — both `_append_line` and `_replace_json` patched to always raise;
  `acquire_node` on a real (non-empty) input still returns the correct
  sealed `AcquireOutput` (row present, outcome `new`, one selected
  posting), and the run's `progress/` directory doesn't even exist
  afterward (proves the writes genuinely failed and were swallowed, not
  that they silently succeeded).
- `test_a_real_exception_propagates_even_when_finish_step_also_fails` — the
  ticket's named worst case: `_replace_json` patched to always raise
  *and* every acquisition source failing (`AcquireAllSourcesFailedError`,
  the real, pre-existing failure path). `pytest.raises
  (AcquireAllSourcesFailedError)` confirms the real exception, not an
  `OSError` from the doomed `finish_step("acquire", ok=False)` call inside
  the `except BaseException:` handler, is what the caller sees.
- `test_assess_node_wrapper_completes_and_propagates_correctly_when_progress_writes_fail`
  — the assess-side mirror of the two tests above, done by monkeypatching
  `proposal_execution._assess_node_body` to return a sentinel output (then,
  in a second phase, to raise a distinct `_BodyError`) rather than pulling
  in `test_assess_model_policy.py`'s full lifecycle-approved-workpad
  fixture (`run_setup`/`create_offline`/`approve_offline`/resume import) —
  that fixture is a private helper (`_assess_fixture`) in another test
  module the operator's per-dispatch owned-files/test-budget rules didn't
  authorize touching, and `assess_node`'s wrapper is generic over its body
  (identical `ProgressWriter`/`_guard` mechanism already proven end-to-end
  for acquire), so isolating just the wrapper is a faithful, much cheaper
  test of the same fix. Confirms both halves: the sentinel output reaches
  the caller unchanged with no `progress/` dir ever created, and a real
  `_BodyError` from the body propagates unmodified even though
  `finish_step` also fails.

### Test results

- Exact acceptance command: `uv run --locked --extra test pytest
  tests/behaviors/scout_find_jobs/test_progress.py
  tests/behaviors/scout_find_jobs/test_m1_end_to_end.py -q` → **24 passed,
  1 xfailed** (same pre-existing, unrelated M1 xfail as r0).
- `make unit-tests` → **922 passed** (unchanged from r0 — this fix and its
  tests live entirely in files/tests already inside that marker set's
  scope, no new fast_unit-marked test added or removed).
- Per the operator's explicit test-budget rule this round ("you ran the
  whole scout_find_jobs directory last time"), ran **only** the two named
  files above, not the full `scout_find_jobs/` directory.
- No `git add`/commit/stash/reset/clean; no live network call.

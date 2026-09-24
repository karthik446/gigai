# Worker: p0-ui-setup-discover

**Task:** PR #37 review fixes P0-2 (CONFIRMED by trace) and P0-5 (PLAUSIBLE,
now confirmed by a failing-then-passing test). Dispatched under Orca task
`task_c69d1712a004`. Source: `.orchestrator/reviews/pr37-review-findings.md`.

**Status:** Done. All owned files changed; nothing outside the owned-files
list touched, except that `git status` also shows other workers' in-flight
edits in the same worktree (`filters.py`, `test_filters.py`,
`proposal_execution.py`, `test_progress.py`) — not mine, left untouched.

READ vs EXECUTED: READ the review findings doc, `App.jsx`, `api.js`,
`RunConfirmDialog.jsx`, `DiscoverPanel.jsx`, `present_api.py` (the setup/run
digest path and the discovery snapshot path), `discovery/__init__.py` (to
see the real shape of `on_progress` events), and the existing
`test_present_setup_discover.py` fixtures before writing anything. EXECUTED
`uv run pytest tests/behaviors/scout_find_jobs/test_present_setup_discover.py`
(34 passed) both before and after temporarily reverting the P0-5 fix inline
to confirm the new test fails without it (`KeyError: 'status'`) and passes
with it restored; EXECUTED `make unit-tests` (946 passed, 6 failed — all in
`test_filters.py`, from another worker's in-progress P0-1 fix, not touched
by this dispatch); EXECUTED `yarn build` twice and diffed `dist/` — second
build byte-identical to the first (same hashed filenames). No live network
call anywhere.

## P0-2: setup save leaves the UI's config_digest stale

**Root cause confirmed exactly as described:** `App.jsx`'s `handleSaveSetup`
called `setupState.reload()` after `PUT /api/setup`, but that PUT also
rewrites `find-jobs.json` (`present_api.py`'s `_handle_put_setup` /
`_update_find_jobs_config`), so `configResponse.config_digest` — read from
the separate `useConfig()` hook — goes stale. The next `POST /api/run` then
409s (`config_digest_mismatch`, `present_api.py`'s `start_run`).

**Fix:** `handleSaveSetup` now also calls `reloadConfig()` (the `useConfig`
hook's reload), right after `setupState.reload()`, so
`configResponse.config_digest` is current before the next Run.

**409 → reload + re-confirm:** already implemented for the Run path.
`handleConfirm`'s catch block, on a 409 `ApiError`, already calls
`reloadConfig()` and sets `runError` to `` `${error.message} Reloading
configuration…` ``; the confirm dialog stays open (only closes on success)
and renders that error, so the operator sees the message and can just press
confirm again with the refreshed config. No behavior gap here — the
finding's "make a 409 reload the config and tell the user to confirm again"
was already true for the Run 409 case; the actual gap was solely the
setup-save path missing a config reload before the digest could ever be
used.

Files: `src/gigai/scout/ui/src/App.jsx` (`handleSaveSetup`).

Not unit-tested with a new automated test (no existing harness renders
`App.jsx`/hooks in this repo — no React Testing Library / jsdom dependency
present in `package.json`). Manual check instead, per the acceptance
criteria's "or describe the manual check":

1. Start the UI with no saved prefs → complete the first-run setup
   interview → `PUT /api/setup` succeeds.
2. Immediately open Run confirm and confirm → `POST /api/run` request body
   must carry the digest from the *post-save* `GET /api/config`, not the
   pre-save one. Before the fix: the pre-save digest is sent and the
   backend replies 409 `config_digest_mismatch` (`present_api.py`'s
   `start_run`, since `find-jobs.json` bytes now differ from what the
   client's digest was computed over). After the fix: `reloadConfig()` in
   `handleSaveSetup` refreshes `configResponse.config_digest` before the
   dialog can ever be opened with stale state, so the same click succeeds
   (202).

## P0-5: Discover panel blank/`$undefined` mid-run

**Root cause confirmed by a new failing-then-passing test** (reproduces
exactly the review's claim): `present_api.py`'s `_capture_progress` did
`self._discovery_progress = dict(event)`, where `event` is the *raw*
progress-step dict `run_discovery` emits (`discovery/__init__.py`'s own
`_progress({"stage": "discovery_start", ...})` /
`_progress({"stage": "discovery_done", ...})` calls) — never anything
shaped like `DiscoveryResult`. That overwrote the `DiscoveryResult`-shaped
running snapshot `start_discovery` had just set, so `GET
/api/discover/latest` mid-run returned a body missing `status`, `cost_usd`,
`sources`, `new_boards` — exactly the fields `DiscoverPanel.jsx` reads
unguarded (`result.cost_usd?.toFixed(4)` → "Cost $undefined").

**Fix (`present_api.py`, `start_discovery`/`_capture_progress` only):**
keep the initial `DiscoveryResult`-shaped snapshot (`status: "running"`,
`cost_usd: 0.0`, `sources: []`, `new_boards: []`, `skipped: {}`,
`started_at`, `finished_at: None`) as the value `latest_discovery()`
returns throughout the run; `_capture_progress` now merges the raw event
into a nested `"progress"` field on a copy of that snapshot instead of
replacing it outright. On completion the snapshot is still replaced
wholesale by `result.to_json()`, unchanged from before.

**UI hardening (`DiscoverPanel.jsx`):** added `formatCostUsd()` (renders
`"—"` for anything not a finite number, instead of `$undefined`) used for
both the top-line cost and each per-source cost; `result.status` falls back
to `"unknown"`; per-source `runs` falls back to `0`. `newBoards` and the
sources list were already null/shape-safe (`Array.isArray` /
`result.sources &&`).

**Test (repro-first):**
`tests/behaviors/scout_find_jobs/test_present_setup_discover.py::test_latest_discovery_mid_run_keeps_the_result_shape_with_progress_nested`
— a stub `run_discovery` that emits one raw `{"stage": "discovery_start",
...}` progress event (mirroring the real module's exact shape) then blocks;
asserts `latest_discovery()` mid-run still has `status`, `cost_usd`,
`sources`, `new_boards`, `skipped`, with the raw event nested under
`snapshot["progress"]`. Verified failing (`KeyError: 'status'`) against the
pre-fix code by temporarily reverting the fix inline and re-running just
this test, then restored and re-ran the full file (34 passed).

Files: `src/gigai/scout/find_jobs/present_api.py` (discover snapshot only),
`src/gigai/scout/ui/src/components/DiscoverPanel.jsx`,
`tests/behaviors/scout_find_jobs/test_present_setup_discover.py`.

## Build

`yarn build` run twice in `src/gigai/scout/ui/`; second build's `dist/`
byte-identical to the first (same content-hashed asset filenames, `diff -rq`
clean). `git status` on `dist/` shows the expected rebuild: old hashed JS
asset removed, new one added, `index.html` updated to reference it —
reflecting the `App.jsx`/`DiscoverPanel.jsx` source changes above.

## Out of scope / left alone

- P0-1 (country filter), P0-3 (assess progress path), P0-4 (resume resume
  operation_key) — other workers' dispatches; `filters.py`,
  `test_filters.py`, `proposal_execution.py`, `test_progress.py` showed as
  modified in `git status` from those, untouched by this dispatch.
- `make unit-tests`'s 6 failures are all `test_filters.py` (P0-1's
  in-progress fix), unrelated to the two files this dispatch owns.

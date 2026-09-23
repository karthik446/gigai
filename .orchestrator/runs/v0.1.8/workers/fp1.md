# Fix pack 1 (last fixes before M1)

## State
Done. All acceptance commands pass, including the coordinator's mid-task
addition (`--home` flag + runbook fixes).

## Files (all new/untracked; nothing else touched)
- `src/gigai/scout_market_acquisition.py` (W1BT-2 fix)
- `tests/behaviors/scout_find_jobs/test_acquire_network.py` (W1BT-2 tests)
- `tests/behaviors/scout_find_jobs/test_contracts.py` (one line: POST /api/run
  status tuple now includes 504)
- `src/gigai/scout_present_api.py` (`main()`: test-seam refusal/warning +
  `--home` flag)
- `tests/behaviors/scout_find_jobs/test_present_ui.py` (tests for both new
  `main()` paths plus `--home`)
- `docs/development/v0.1.8/runbooks/M1-find-jobs.md` (coordinator addition B)
- `.orchestrator/workers/fp1.md` (this file)

`git status --porcelain` confirms exactly these six source/test/doc paths
plus this report; no other file was touched.

## Acceptance output

Full directory (required — 0 failures):
```
$ uv run --locked --extra test pytest tests/behaviors/scout_find_jobs -q
........................................................................ [ 25%]
........................................................................ [ 51%]
........................................................................ [ 77%]
...............................................................          [100%]
279 passed in 62.11s (0:01:02)
```
(278 pre-existing + 1 new `--home` CLI test added after the coordinator's
mid-task addition; re-ran once more after that addition and it still shows
279 passed, 0 failed.)

Import smoke:
```
$ uv run --locked python -c "import gigai.scout_market_acquisition, gigai.scout_present_api"
(no output, exit 0)
```

## Changes and choices

### 1. W1BT-2 — acquire fails closed when every enabled source fails

`acquire_node` in `scout_market_acquisition.py` now tracks a per-source
success flag (`source_outcomes: dict[str, bool]`) while collecting Exa/ATS
rows:
- Exa: `source_outcomes["exa"]` is `True` only if `exa.search(...)` returned
  without raising.
- ATS: `source_outcomes["ats"]` is `True` if the watchlist had no active
  boards to check (nothing to fail — treated as vacuously fine, not a
  failure) **or** at least one board's `ats.list_board(...)` call succeeded.
  A per-board failure still records its own `FailureRow` as before; only a
  source with *zero* successes among everything it attempted counts as
  failed.
- After both blocks run, if `source_outcomes` is non-empty (i.e. at least one
  source was enabled) and `not any(source_outcomes.values())` (every enabled
  source failed), the function raises `AcquireAllSourcesFailedError` — a new
  `FindJobsContractError` subclass with code `acquire_all_sources_failed` —
  **before** `import_public_rows` is called, so no acquisition batch is
  written. The message is built only from `f"{source_kind.value}:{code}"`
  pairs already present in the (redacted) `FailureRow`s already collected —
  it never touches the original exception's `str()`, so nothing from a raw
  transport error (URLs, response bodies, or a credential's own message,
  e.g. `EXA_API_KEY is not set in the environment...`) can leak; only the
  exception's redacted `type(exc).__name__.lower()` (already the existing
  convention for `FailureRow.code`) surfaces.
- Partial success is untouched: if any enabled source got at least one
  success, the function proceeds exactly as before (failed sources still
  produce `FailureRow`s inside the returned `AcquireOutput`).
- Four new tests in `test_acquire_network.py`, using existing fixtures/test
  doubles style:
  - `test_all_exa_fail_with_only_exa_enabled_raises` — only Exa enabled,
    `exa.search` raises `RuntimeError("exa transport exploded")` ⇒
    `AcquireAllSourcesFailedError` with `.code == "acquire_all_sources_failed"`,
    and the raw message text never appears in the raised error. Also asserts
    `import_public_rows` is never called (monkeypatched to raise
    `AssertionError` if invoked), directly proving "no batch written."
  - `test_exa_fails_ats_succeeds_returns_rows_and_failure` — Exa fails, one
    active watchlist board (Greenhouse/acme) returns a row via ATS ⇒
    `acquire_node` returns normally with that row present and an
    `EXA`-sourced `FailureRow` in `out.failures`.
  - `test_missing_exa_api_key_with_only_exa_enabled_raises` — simulates the
    real `ExaClientError("exa_missing_api_key", "EXA_API_KEY is not set in
    the environment; Exa discovery cannot run")` (the actual message
    `scout_exa_client.py` raises when the env var is unset) as the injected
    Exa double's exception ⇒ raises `AcquireAllSourcesFailedError`, and the
    original message's distinguishing phrase ("is not set in the
    environment") is asserted absent from the raised error's `str()`, while
    the redacted type-name code (`exaclienterror`) is present.
  - Existing two tests (`test_supplied_rows_select_and_persist`,
    `test_live_exa_auto_adds_watchlist`) untouched and still pass.
  - Added a `_config(*, exa, ats)` helper (via `dataclasses.replace` on the
    fixture config) so tests can enable/disable sources independently
    without a new fixture file.

### 2. `test_contracts.py` — 504 in the POST /api/run status tuple

`scout_find_jobs_contracts.py`'s own `ROUTES` already had
`(202, 400, 403, 409, 422, 504)` for `POST /api/run` (added by an earlier
wave); only the test's literal comparison at line 479 was stale at
`(202, 400, 403, 409, 422)`. Changed that one line to match. Confirmed via
`grep` that no other line in the file changed.

### 3. `scout_present_api.py` `main()` — test-seam refusal + `--home`

- Added `_TEST_HTTP_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_HTTP"` and
  `_TEST_MODEL_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_MODEL"` module constants,
  matching the exact names already defined in `scout_find_jobs_bindings.py`
  (confirmed via grep before writing, so no drift between the two modules).
- `main()` now checks both env vars; if either is set (truthy, matching the
  existing `"1"`-flag convention) and `--allow-test-seams` was not passed, it
  prints `refusing to start: <VAR> [and <VAR>] is set in the environment;
  pass --allow-test-seams to start anyway` to stderr and raises
  `SystemExit(2)` before any server/backend construction. If a seam is
  active and the flag *was* passed, it prints
  `TEST SEAMS ACTIVE: results are fixture data` to stderr and continues.
- Added `--home` (default `None` ⇒ falls back to `setup.default_home_root()`
  exactly as before), per the coordinator's addition: without it the API
  always used `default_home_root()` regardless of a custom GigAI home,
  breaking the runbook's `--home "$GIGAI_HOME_DIR"` flow. `--home` is
  resolved the same way `--target` already was
  (`Path(...).expanduser().resolve(strict=False)`).
- Six tests added to `test_present_ui.py` (imports `main` alongside the
  existing `Backend`/`NotWiredBackend`/`_make_handler`/`serve`):
  - Refusal with `GIGAI_SCOUT_FIND_JOBS_TEST_HTTP=1` only, and with
    `GIGAI_SCOUT_FIND_JOBS_TEST_MODEL=1` only — both assert `SystemExit`
    with `.code == 2`, the variable name present in stderr, and
    `--allow-test-seams` mentioned; both monkeypatch `_run_forever` to raise
    `AssertionError` if called, directly proving the server is never
    started.
  - Started-with-warning: both env vars set, `--allow-test-seams` passed ⇒
    `_run_forever` is invoked exactly once and stderr contains
    `TEST SEAMS ACTIVE` + `fixture data`.
  - Normal start: no env vars set ⇒ `_run_forever` invoked once, no
    `TEST SEAMS ACTIVE` in stderr.
  - `--home` override: `setup.default_home_root` monkeypatched to raise if
    called (proving it's bypassed), `--home <tmp dir>` passed, asserts the
    constructed backend's `.home_root` equals the resolved custom path.
  - All six call `main()` directly (never a subprocess), per the task's
    "Test both paths by calling the CLI entry function directly" and the
    exclusion on live network/process spawning; `_run_forever` is always
    monkeypatched so no real `ThreadingHTTPServer` ever binds a socket in
    these tests.

### 4. Runbook (`M1-find-jobs.md`) — coordinator addition B

- Directories are now persistent (`$HOME/scout-search`,
  `$HOME/.gigai-scout`, `$HOME/.gigai-scout/workpads`) instead of `mktemp`;
  both the `git init` block and the `gigai setup` block are now wrapped in
  `if [ ! -d/-f ... ]` guards so re-running the runbook against existing
  directories skips re-initialization (confirmed `config.toml` — not
  `config.json` — is the actual sentinel file `gigai setup` writes, by
  reading `config.py`'s `CONFIG_FILENAME = "config.toml"` and
  `config_path()`, rather than guessing).
- The API start command now passes `--home "$GIGAI_HOME_DIR" --target
  "$TARGET_DIR"`.
- The `422` bullet now explains the new `acquire_all_sources_failed`
  behavior (fails when Exa is the only enabled source and the key is
  missing; degrades to a `FailureRow` if ATS is also enabled and succeeds).
- Default local model changed to `muse-glimmer:latest` with
  `qwen3.8:latest` noted as the alternative (both the `ollama pull` line and
  the `export OLLAMA_MODEL` line).
- Added a "Before you click" checklist under the step-3 heading covering:
  `ollama serve` running, `EXA_API_KEY` exported in the API terminal
  specifically, resume added, `find-jobs.json` present, both terminals
  running.
- Everything else in the runbook (target-commit requirement, Scout candidate
  helper, resume/config steps, results/failure-reading section, verification
  boundary) is unchanged.

## READ vs EXECUTED

**READ:**
- `.orchestrator/reviews/terra-w1b-triage.md`, `.orchestrator/reviews/terra-w1b.md`
  (W1BT-2 finding, exact file:line citations, verdict).
- `src/gigai/scout_market_acquisition.py` (full file, before editing).
- `src/gigai/scout_find_jobs_contracts.py` — `FindJobsContractError` (line
  39-49), `ROUTES`/`RouteSpec` (1721-1735) to confirm the *current* 504 was
  already present in contracts (not something I needed to add there).
- `tests/behaviors/scout_find_jobs/test_acquire_network.py` (full file,
  before editing) and `test_contracts.py` (grepped for the exact stale
  line) and `test_present_ui.py` (full file, before editing) for existing
  test-double/fixture conventions.
- `tests/behaviors/scout_find_jobs/fixtures/fixture-find-jobs-config-v1.json`.
- `src/gigai/scout_exa_client.py` (grepped for `EXA_API_KEY`/error classes to
  get the real `ExaClientError` code/message used in the missing-key test).
- `src/gigai/scout_ats_board_clients.py` (grepped for `ATSBoardClientError`
  to confirm the `.code`/message convention matches Exa's).
- `src/gigai/scout_find_jobs_bindings.py` (grepped for
  `GIGAI_SCOUT_FIND_JOBS_TEST_HTTP`/`_MODEL` and their existing constant
  names/`"1"`-flag convention, and the `test_m1_end_to_end.py` usage, to
  match naming exactly rather than inventing new names).
- `src/gigai/scout_present_api.py` (full file, before editing).
- `src/gigai/setup.py` and `src/gigai/config.py` (grepped for
  `default_home_root`, `config_path`, `CONFIG_FILENAME` — confirmed the
  runbook's new "skip if already set up" guard checks the right sentinel
  file, `config.toml`, not a guessed `config.json`).
- `docs/development/v0.1.8/runbooks/M1-find-jobs.md` (full file, before and
  after editing).

**EXECUTED:**
- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_acquire_network.py -q`
  (5 passed, before running the full-directory acceptance).
- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_contracts.py -q`
  (188 passed).
- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_present_ui.py -q -k "main"`
  (5 passed, then 6 passed after the `--home` addition).
- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_present_ui.py -q`
  (22 passed).
- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs -q`
  (run twice: 278 passed before the coordinator's `--home` addition, 279
  passed after adding the new `--home` CLI test — both 0 failures).
- `uv run --locked python -c "import gigai.scout_market_acquisition, gigai.scout_present_api"`
  (clean exit, no output) — run once more after the `--home` addition.
- `git status --porcelain -- <owned files>` (twice, to confirm scope both
  before and after the coordinator's addition).
- `orca orchestration check` (three times: at task start before beginning
  work, before writing this report the first time, and again after
  discovering and applying the coordinator's mid-task addition message) —
  found the addition message on the second check.
- No live network was used anywhere (all tests use in-process fakes/doubles
  or monkeypatched `_run_forever`; no socket was bound in any test). No git
  stash/reset/clean/add/commit was run. No permission prompt blocked this
  run, so no `orca orchestration ask` was needed.

## What's left

Nothing outstanding for fp-1 as scoped (including the coordinator's
mid-task addition). Out of scope, not attempted here:
- W1BT-1 (the `present`/`AcquireOutput.batch_ref` producer/consumer mismatch
  in `run.py`/`scout_projection.py`) — per the triage note this was already
  mitigated by I-3's `scout_find_jobs_bindings.py:250-264` normalization; a
  root fix in `run.py` is explicitly deferred to the 0.2.0 ledger and is not
  part of fp-1's owned files.
- No live Exa/ATS/Ollama call, no live UI click-through, no installed-wheel
  run — this fix pack is source-checkout unit/behavior-test level only, per
  the task's "no network" exclusion.

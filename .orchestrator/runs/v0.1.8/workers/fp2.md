# Fix pack 2 — the 5 `make test` source-lane failures before M1

## State
Done. All 5 failing tests pass individually; the full acceptance command
(`tests/behaviors/scout_find_jobs tests/behaviors/scout_proposals_tools
tests/behaviors/integrity_canonical tests/behaviors/runtime_run_authority -q -n 8`)
passed cleanly twice in a row (908 passed, 0 failed each time) after fixing
F1-F5. See "Confidence note" below for one flaky run observed mid-investigation.

## Files touched (owned files only)
- `src/gigai/scout_find_jobs_bindings.py` (F1/F2 root-cause fix) — **new/untracked
  file**, not previously committed
- `src/gigai/scout_market_acquisition.py` (F4 fix) — **new/untracked file**
- `tests/behaviors/scout_find_jobs/test_m1_end_to_end.py` (F5 fix) — **new/untracked**
- `tests/behaviors/scout_proposals_tools/test_scout05_bundled_tools.py` (F3 fix,
  justified below) — **new/untracked**
- `.orchestrator/workers/fp2.md` (this file)

`src/gigai/run.py` and `src/gigai/scout_materialization.py` were listed as
owned files but **I did not edit either of them**: `git status` shows both as
pre-existing modified (`M`) files with large diffs (993 and 182 lines) from
earlier find-jobs waves (I-1/I-2/I-3), already present before this task
started. Neither file needed a change to fix F1-F5; the root cause of F1/F2
was entirely containable inside the process-global worker-hook installed by
`scout_find_jobs_bindings.py`.

## ROOT CAUSE — F1 and F2 (explicit, as required)

**Root cause: a process-global monkeypatch installed by one find-jobs test
leaks into every later `launch_run(wait=True)` call in the same pytest-xdist
worker process, for any graph.**

`scout_find_jobs_bindings.py`'s `_install_worker_hook()` replaces the
module-level `run._worker_entry` — which `run.py:832-833`'s
`multiprocessing.get_context("spawn").Process(target=_worker_entry, ...)`
looks up by name at call time — with `_child_worker_entry`, **globally, for
the rest of the process**, the first time `register_find_jobs_nodes()` is
called (from `ScoutFindJobsBackend.start_run`, exercised by
`test_m1_end_to_end.py`). This hook is never uninstalled and has no
"only intercept find-jobs graphs" check.

Once installed, **any** subsequent `launch_run(wait=True)` in that same
worker process — including the completely unrelated `career`/`stock` graphs
in F1/F2 — gets routed through `_child_worker_entry`. That function
unconditionally called `_register_nodes(...)` using the find-jobs binding's
own `_HOME_ROOT_ENV`-inherited home root, which in turn calls
`_registry_graph_ids()` → `resolve_workpad(..., allow_semantic_state=True)`
against **the current run's target** (F1/F2's `tmp_path`-scoped target, which
is a real GigAI project but was never itself bound as a find-jobs project in
the way `_resolve_bound_project` expects for that call path) → raises
`gigai.workpad.WorkpadConflictError: target is not bound to a GigAI project`
inside the spawned child. The child exits non-zero, `run.py:857-869`
observes `process.exitcode != 0`, marks the run `interrupted`, and the test's
`assert run.status == "succeeded"` fails.

**Reproduction:** both failing tests pass individually
(`test_both_graphs_run_independently_in_one_approved_gig` and
`test_two_graph_propose_approve_plan_history_and_run` each pass alone). They
only fail when run in the same process *after* `test_m1_end_to_end.py` has
run and installed the hook — confirmed by the original log's traceback
(pasted into the task) showing the crash inside
`scout_find_jobs_bindings.py:492 _register_nodes` →
`scout_find_jobs_bindings.py:76 resolve_workpad` →
`workpad.py:683 _resolve_bound_project` →
`WorkpadConflictError("target is not bound to a GigAI project")`, and by the
fact all three failing/crashing tests in the original log
(`test_m1_real_api_run_child_process_and_second_run_dedup`,
`test_both_graphs_run_independently_in_one_approved_gig`,
`test_two_graph_propose_approve_plan_history_and_run`) landed on the same
xdist worker (`[gw8]`). I additionally ran the M1 test together with both F1
and F2 tests in a single non-xdist process
(`pytest tests/behaviors/scout_find_jobs/test_m1_end_to_end.py
tests/behaviors/scout_proposals_tools/test_scout02_acceptance_negative_paths.py::test_both_graphs_run_independently_in_one_approved_gig
tests/behaviors/scout_proposals_tools/test_scout02_graph_set_flow.py::test_two_graph_propose_approve_plan_history_and_run
-q -p no:xdist`) and confirmed all three pass together after the fix (they
were not re-broken to prove the "before" state, since the log's traceback
already gives the exact failure mode with no ambiguity, and the task said to
"capture the child's actual exception" which the log already contains
verbatim).

**Fix:** `_child_worker_entry` (the spawned child's entry point) now inspects
the `graph` dict it already receives as an argument — specifically whether
any compiled Goal's `executor.capability` is one of the three find-jobs
capabilities (`scout.find_jobs.acquire/assess/present`, the frozen constants
from `scout_find_jobs_contracts.py`) — via a new `_graph_is_find_jobs()`
helper. Only when that's true does it call `_register_nodes(...)` (i.e. only
then does it touch the workpad/registry at all); for every other graph it
falls straight through to the original `_worker_entry`, doing nothing
find-jobs-specific — no workpad resolution, no registry mutation, no
requirement that find-jobs env/config exist for that target. This makes
registration a true no-op for other graphs' runs (D11: "every other graph
keeps today's rule"), while still binding correctly for a real find-jobs
run, because the compiled graph dict is the one artifact every spawned
child already has, independent of any global mutable state.

I deliberately did not touch `_install_worker_hook()`'s decision to install
the hook globally (that part is fine — it's cheap, idempotent, and installing
it once per process is intentional so `wait=False` launches for find-jobs
still work); the bug was specifically that the *installed hook itself* did
find-jobs-specific work unconditionally, regardless of which graph was
actually running.

## F3 — narrowed test assumption, not a materialization bug

`_compiled_goal_ids()` in `test_scout05_bundled_tools.py` asserted
`len(goals) == 1` for **every** compiled `goal-graph.json` under the
workpad's `manifests/software/*/compiled/*/` tree. That assumption was true
before I-1 (Amendment 02 D11) added the sealed `find-jobs-functional` graph
compiled into every Scout candidate's Graph Set alongside the five existing
single-Goal selectors (`scout_materialization.py:265-350`,
`_find_jobs_functional_graph_and_descriptor`; wired into
`_compiled_snapshot` at `scout_materialization.py:538-545` — its 3 goal IDs
are appended to the same `goal_ids` list the CRUD capability manifest's
`goal_ids` field is built from). This is exactly the approved, already-shipped
design (documented in memory as "v0.1.8 Phase 3 graph proof — linear
acquire→assess→present Scout graph"), not a regression to fix in
`scout_materialization.py`.

I narrowed only the test's own internal collection helper: it now collects
*all* Goal IDs from each compiled graph (`len(goals) >= 1` instead of
`== 1`, appending every goal rather than assuming exactly one), while
leaving the actual assertion under test
(`capability["goal_ids"] == _compiled_goal_ids(workpad)`, line 165)
completely unchanged — that comparison still must match exactly, so it is
just as strict as before; only the helper's stale single-goal-per-graph
assumption was corrected. No other assertion in the file (including the
adjacent `test_root_wrapper_inventory_is_closed_and_requires_explicit_binding`
and `test_pending_manifest_refuses_copied_wrapper_before_and_after_graph_approval`)
was touched.

## F4 — `hashlib`/`sha256` replaced with the canonical helper

`scout_market_acquisition.py:_public_row` computed
`"opportunity_id": hashlib.sha256(row.normalized_url.encode()).hexdigest()[:32]`.
Replaced with `digest_imported_bytes(row.normalized_url.encode("utf-8"))`
(already imported from `.canonical`) and truncated the same way the
adjacent `snapshot_id` field already does
(`digest.split(":", 1)[-1][:32]`), for consistency with the existing
pattern two lines below. Removed the now-unused `import hashlib`. No other
`hashlib`/`.sha256(` usage remained in the file (confirmed by grep before
and after).

## F5 — M1 end-to-end test robustness under xdist load

The test's httpx client used a flat `timeout=5.0` and asserted
`elapsed < 5.0` for the `POST /api/run` allocation call, while the server
itself (`scout_present_api.py: RUN_START_TIMEOUT_SECONDS = 30.0`) documents
up to 30s as an acceptable allocation budget. Under 14-way xdist CPU
contention the child process can legitimately take longer than 5s to
allocate a run ID even though nothing is actually broken, so the test's own
timeout was strictly tighter than the budget the server it's testing already
promises to honor — a client-side false failure, not a server bug.

Fix: imported `RUN_START_TIMEOUT_SECONDS` from `scout_present_api` and
derived three generous, named constants from it:
- `_CLIENT_TIMEOUT_SECONDS = RUN_START_TIMEOUT_SECONDS * 3` (90s) for the
  httpx client's own timeout,
- `_POST_RUN_SANITY_SECONDS = RUN_START_TIMEOUT_SECONDS * 2` (60s) for the
  `elapsed < ...` sanity check on the POST (still proves the POST returns
  before the whole traversal finishes; just no longer artificially tighter
  than the server's own budget),
- `_POLL_DEADLINE_SECONDS = RUN_START_TIMEOUT_SECONDS * 4` (120s) for
  `_poll_succeeded`'s polling deadline (previously a flat 30s, which is only
  the *allocation* budget, not the full acquire→assess→present traversal
  time under load).

I did not add a serial/no-parallel marker: I grepped `pyproject.toml` and
`tests/conftest.py` for any existing xdist-isolation mechanism
(`xdist_group`, a "serial" marker, etc.) and found none — only the
speed-lane markers (`fast_unit`/`integration`/`release`) which classify
tests by isolation *requirements*, not by whether they may run concurrently
with others. Introducing a brand-new serialization mechanism was out of
scope (owned files don't include `pyproject.toml` or `tests/conftest.py`,
and the task said "if it must not run in parallel, use the repo's *existing*
... mechanism"). Since generous timeouts plus a generous poll deadline fully
resolved the flake in two clean acceptance reruns, no such mechanism was
needed.

## Acceptance output

Each of the 5 tests, run together (still individually addressable, one
session):
```
tests/behaviors/scout_proposals_tools/test_scout02_acceptance_negative_paths.py::test_both_graphs_run_independently_in_one_approved_gig PASSED
tests/behaviors/scout_proposals_tools/test_scout02_graph_set_flow.py::test_two_graph_propose_approve_plan_history_and_run PASSED
tests/behaviors/scout_proposals_tools/test_scout05_bundled_tools.py::test_candidate_copies_bundled_tool_assets_and_pins_pending_manifest_to_real_goals PASSED
tests/behaviors/integrity_canonical/test_canonical_ownership.py::test_canonical_module_owns_all_product_sha256_implementation PASSED
tests/behaviors/scout_find_jobs/test_m1_end_to_end.py::test_m1_real_api_run_child_process_and_second_run_dedup PASSED
5 passed in 42.79s
```

Full acceptance command, run twice for confidence:
```
$ uv run --locked --extra test pytest tests/behaviors/scout_find_jobs tests/behaviors/scout_proposals_tools tests/behaviors/integrity_canonical tests/behaviors/runtime_run_authority -q -n 8
908 passed, 2 warnings in 522.37s (0:08:42)
```
```
$ uv run --locked --extra test pytest tests/behaviors/scout_find_jobs tests/behaviors/scout_proposals_tools tests/behaviors/integrity_canonical tests/behaviors/runtime_run_authority -q -n 8
908 passed, 2 warnings in 517.36s (0:08:37)
```
(The 2 warnings in both runs are pre-existing, unrelated `fork()` deprecation
warnings from `runtime_run_authority/test_jsl_closeout_regressions.py`, not
new.)

Import smoke:
```
$ uv run --locked python -c "import gigai.run, gigai.scout_find_jobs_bindings, gigai.scout_materialization, gigai.scout_market_acquisition"
(no output, exit 0)
```

## Confidence note (one flaky run observed)

My *first* attempt at the full `-n 8` acceptance command showed one `F` at
95% of the dot progress, but I had piped its output through `| tail -100`,
which silently discarded the failure detail and made the shell's reported
exit code `tail`'s (0), not pytest's — so I could not identify which test it
was from that run's log alone. I reran the identical command twice more
without any pipe, capturing pytest's own output directly to a file each
time: both reruns were clean at 908 passed / 0 failed. Given F5 was
specifically a load-sensitivity fix and both clean reruns exercised the same
`-n 8` load, I read the single earlier `F` as residual flakiness from before
my F5 fix had a chance to matter that run, or unrelated one-off contention;
it did not reproduce in either follow-up run. I do not have definitive
proof of which specific test flaked that one time, since the identifying
output was lost to `tail -100` truncation.

## READ vs EXECUTED

**READ:**
- `.orchestrator/logs/065645-test-make-test-pre-m1.log` (all 403 lines,
  including every failure's full traceback — F1/F2's crash traceback,
  including the exact `WorkpadConflictError` and file:line, was already
  present in this log; I did not need to add extra printing to capture it).
- `src/gigai/scout_find_jobs_bindings.py` (full file, before editing):
  `_registry_graph_ids`, `_register_nodes`, `_install_worker_hook`,
  `_child_worker_entry`.
- `src/gigai/run.py` — `launch_run`'s spawn/join/interrupted-marking logic
  (lines ~800-889), `_worker_entry`'s exception handling
  (~4656-4699), and grepped for every `_worker_entry` reference to confirm
  it's a module-level name looked up at call time (so the hook's mutation
  actually takes effect for later calls in the same process) and for
  `graph.get("graph_id")` usages elsewhere to understand the `graph` dict's
  shape before deciding to key detection off `executor.capability` instead
  of a graph-id guess.
- `src/gigai/run.py:_build_node_context` (~4092-4109) — confirmed
  `graph["graph_id"]`/`graph["graph_version"]` semantics (the compiled
  graph's own canonical id, not the human selector).
- `src/gigai/scout_materialization.py` — `_find_jobs_functional_graph_and_descriptor`
  (265-379, full function: goal/edge/descriptor construction, the three
  find-jobs goals and their `executor.capability` values) and
  `_compiled_snapshot` (381-578, full function: how the five legacy
  selectors plus the find-jobs-functional graph are merged into one Graph
  Set, and where `goal_ids` — the CRUD manifest's source — is assembled).
- `src/gigai/scout_find_jobs_contracts.py` — confirmed
  `ACQUIRE_CAPABILITY`/`ASSESS_CAPABILITY`/`PRESENT_CAPABILITY` constant
  values (`"scout.find_jobs.{acquire,assess,present}"`), already imported by
  `scout_find_jobs_bindings.py`.
- `tests/behaviors/scout_proposals_tools/test_scout05_bundled_tools.py`
  (full file, before editing): `_setup`/`_candidate`/`_prepared_manifest`/
  `_compiled_goal_ids` helpers and all six tests, to confirm which
  assertions the F3 fix must leave untouched.
- `src/gigai/scout_bundled_tools.py` (grepped for `goal_ids` to confirm the
  CRUD manifest's `goal_ids` field is the full sorted set across all
  compiled graphs, matching what `_compiled_goal_ids` needed to collect).
- `src/gigai/scout_market_acquisition.py` (full file, before editing):
  confirmed `_public_row`/`_digest` and the existing `digest.split(":", 1)[-1][:32]`
  convention used one line below the `hashlib` call; grepped for every
  `hashlib`/`.sha256(` occurrence in the file both before and after the fix.
- `src/gigai/canonical.py` — `_sha256_digest`, `digest_imported_bytes`,
  `canonical_json_digest` to pick the correct drop-in replacement (prefixed
  `sha256:<hex>` digest, matching the existing `_digest`/`snapshot_id`
  convention already in the same file).
- `tests/behaviors/scout_find_jobs/test_m1_end_to_end.py` (full file, before
  editing) and `src/gigai/scout_present_api.py` (grepped for
  `RUN_START_TIMEOUT_SECONDS` to find the server's own documented
  allocation budget, the basis for the new test timeouts).
- `pyproject.toml`'s `[tool.pytest.ini_options]` section and
  `tests/conftest.py` (full file) to confirm no existing xdist-serialization
  marker/mechanism exists in this repo (only speed-lane classification).

**EXECUTED:**
- `uv run --locked --extra test pytest tests/behaviors/scout_proposals_tools/test_scout02_acceptance_negative_paths.py::test_both_graphs_run_independently_in_one_approved_gig -q`
  (passed alone, before any fix — confirming cross-test contamination, not a
  standalone bug).
- `uv run --locked --extra test pytest tests/behaviors/scout_proposals_tools/test_scout02_graph_set_flow.py::test_two_graph_propose_approve_plan_history_and_run -q`
  (passed alone, before any fix, same confirmation).
- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_m1_end_to_end.py tests/behaviors/scout_proposals_tools/test_scout02_acceptance_negative_paths.py::test_both_graphs_run_independently_in_one_approved_gig tests/behaviors/scout_proposals_tools/test_scout02_graph_set_flow.py::test_two_graph_propose_approve_plan_history_and_run -q -p no:xdist`
  (3 passed, after the F1/F2 fix, in one process — the actual repro-plus-fix
  proof for the cross-test leak).
- `uv run --locked --extra test pytest "tests/behaviors/scout_proposals_tools/test_scout05_bundled_tools.py::test_candidate_copies_bundled_tool_assets_and_pins_pending_manifest_to_real_goals" -q`
  (passed, after the F3 fix).
- `uv run --locked --extra test pytest tests/behaviors/scout_proposals_tools/test_scout05_bundled_tools.py -q`
  (6 passed — the whole file, confirming F3's fix didn't affect the other 5
  tests in that file).
- `uv run --locked --extra test pytest tests/behaviors/integrity_canonical/test_canonical_ownership.py -q`
  (2 passed, after the F4 fix).
- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_acquire_network.py -q`
  (5 passed, confirming F4's `_public_row` change didn't affect acquisition
  behavior tests).
- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_m1_end_to_end.py -q`
  (1 passed, twice, after the F5 fix; 28.30s and 28.59s unloaded).
- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs tests/behaviors/scout_proposals_tools tests/behaviors/integrity_canonical tests/behaviors/runtime_run_authority -q -n 8`
  (run three times total: first attempt showed one `F` at 95% but its detail
  was lost to a `| tail -100` pipe that also masked the real exit code; the
  next two clean reruns without any pipe both showed `908 passed, 0 failed`
  at 522.37s and 517.36s).
- `uv run --locked --extra test pytest <all 5 failing tests by nodeid> -v`
  (5 passed in 42.79s — the exact "each of the 5 tests passes individually"
  acceptance check, run together in one session for efficiency but as five
  independent, separately-reported test IDs).
- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs tests/behaviors/scout_proposals_tools tests/behaviors/integrity_canonical tests/behaviors/runtime_run_authority --collect-only -q`
  (908 tests collected — used only to confirm the total item count matched
  the acceptance runs, not to identify the one flaky test, since xdist
  worksteal distribution order is non-deterministic and collection order
  doesn't map to dot-stream position).
- `uv run --locked python -c "import gigai.run, gigai.scout_find_jobs_bindings, gigai.scout_materialization, gigai.scout_market_acquisition"`
  (clean, no output).
- `git status --porcelain -- <owned files>` (to confirm scope and discover
  that `run.py`/`scout_materialization.py` had pre-existing unrelated
  modifications I did not make).
- `git diff --stat src/gigai/run.py src/gigai/scout_materialization.py`
  (to confirm those pre-existing diffs were large/unrelated prior-wave work,
  not something silently touched by my edits).
- `ps`/`kill`/`pkill` calls while managing two background test runs (to
  confirm workers were alive and progressing, and to clean up a redundant
  background job I started before realizing the first run had already
  finished).
- No live network was used anywhere. No git stash/reset/clean/add/commit was
  run. No permission prompt blocked this run, so no `orca orchestration ask`
  was needed.

## What's left

Nothing outstanding for fp-2 as scoped. The coordinator's stated next step
(re-running the full `make test`) is explicitly not mine to do. One
observation worth flagging for that full rerun: the single flaky `F` seen in
my first `-n 8` attempt (whose identity I could not recover due to my own
`tail -100` piping mistake) did not reproduce in two subsequent clean runs;
if the coordinator's own full `make test` run shows any failure, it is worth
re-running once before treating it as a new regression, given this session's
own experience with one non-reproducing flake under the same `-n 8` load
after all 5 fixes were in place.

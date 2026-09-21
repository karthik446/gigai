# Scout test-timing implementation evidence

**Date:** 2026-09-09  
**Scope:** unit-test lane classification and bounded timing checks only  
**Status:** implemented, focused checks passed; no full-suite rerun

## Bottom line

The test configuration now has additive `fast_unit`, `integration`, and
`release` markers, assigned conservatively by a source-aware collection hook in
`tests/conftest.py`. The hook does not select, skip, reorder, mock, or weaken
tests: the existing `pyproject.toml` `testpaths` and ordinary `pytest -q`
command still discover the complete offline inventory. No safe shared-fixture
optimization was found; stateful tests remain in their required lanes, while a
measurable fast lane is available for deterministic tests.

## What changed

- `pyproject.toml` declares three strict markers. The existing unrelated
  `gigai.data` package-data edit was preserved unchanged.
- `tests/conftest.py` parses each collected test's source and statically
  reachable local helpers. Filesystem fixtures, persistence/SQLite, subprocess
  or concurrency primitives, network/HTTP, CLI, Git, mutable workpad/setup
  calls, and unknown/unparseable shapes are not promoted to `fast_unit`.
- Explicit `g30_live` module/function markers are kept out of the offline unit
  lane. Package/resource and release-check imports receive the separate
  `release` marker; this classification is based on actual imports/operations,
  not a filename or speed threshold.
- `tests/test_timing_classification.py` covers a pure test (including an
  incidental isolation-looking string), a helper using SQLite and a temporary
  path, a package-resource test, and an explicit live marker. The tests exercise
  the classifier rather than static fixture data.

No runtime source, Scout test, central schema fixture, provider, private
`.gigai` state, or persistence implementation was changed. There is no
Makefile in this worktree, so no Make target was added or changed.

## Measured selections

Commands were run through the existing local virtualenv; these are focused
checks, not full-suite reruns.

| Selection / evidence | Result | Duration | Interpretation |
| --- | ---: | ---: | --- |
| Pre-change equivalent contract selection (recorded before this implementation) | 164 passed, 7 subtests | 0.42s | Baseline for the same explicit file list; environment timing only |
| Post-change equivalent contract selection (18 explicit files) | 164 passed, 7 subtests | 0.57s | Same behavior after additive collection classification; no speedup claimed |
| `pytest -q -m fast_unit` | 236 passed, 604 deselected, 7 subtests | 3.18s | New deterministic unit lane; excludes live, integration, and release cases |
| `pytest -q tests/test_timing_classification.py` | 4 passed | 0.01s | Classifier regression checks |
| Representative integration: `tests/test_scout03_c1_acceptance.py::test_post_commit_runtime_failure_returns_pending_result` | 1 passed | 1.59s | Required authority/recovery behavior remains executable |
| Collection inventory, `-m integration` | 555 of 840 collected | 2.03s | Stateful/authority-boundary lane; collection only |
| Collection inventory, `-m release` | 49 of 840 collected | 2.03s | Package/resource/release-boundary lane; collection only |
| Saved full C1 JUnit `/tmp/gigai-scout-c1-suite.5Fbc8K/results.xml` | 783 testcase elements, 1 skip | 504.260s XML suite time | Existing full-run evidence; not rerun here |

The 840-test partition is 236 fast-unit + 555 integration + 49 release. The
saved JUnit's suite attribute reports 887 tests because parametrized/subtest
accounting differs from testcase elements; its 783 testcase elements and
504.260 seconds are the directly parsed values. The equivalent selection's
0.15s increase and representative integration's 0.16s decrease are too small
to establish a regression or improvement without repeated controlled runs.

## Why no fixture optimization was made

The saved JUnit shows the largest measured module totals in
`tests/test_g19_target_effect` (20 cases, 67.837s),
`tests/test_jsl_closeout_regressions` (15, 57.560s),
`tests/test_g16_review_loop` (22, 49.473s), and
`tests/test_g43_provider_run_status` (15, 42.749s). Other notable measured
groups include `tests/test_scout03_c1_acceptance` (11, 28.068s),
`tests/test_g43_provider_review` (10, 25.260s), and
`tests/test_scout03_private_records` (4, 19.739s).

Source inspection shows these hotspots use combinations of temporary
workspaces, SQLite, subprocesses, Git, loopback HTTP, multiprocessing, and
authority/recovery setup. Sharing them at session scope would introduce mutable
state and could erase the isolation and recovery behavior being tested; replacing
those seams with mocks would change the behavior under test. The safe measured
improvement in this change is therefore lane isolation, not a fabricated total
suite reduction. Fixture-level gains need separate profiling by fixture and
failure/recovery boundary.

## Measured bottlenecks versus unknowns

The module totals above are measured impact from the saved JUnit. Their likely
fixture/setup/process/persistence contribution is source-supported inference,
not a per-fixture profile. Full wall-clock gaps across correction rounds,
reviewer reruns, coordinator processing, user pauses, concurrency, and failed
Orca IPC cannot be attributed to compute here: timestamps are incomplete and a
prior read-only Orca timeline request returned `runtime_unavailable` with
`Could not connect to running Orca app. Restart Orca and try again.`; the app
was not restarted. Reviewer duplicate checks and correction rounds are recorded
in the existing Scout evidence, but this implementation did not rerun or
reconstruct their orchestration time.

Independent contract/unit lanes are safe to run concurrently when they do not
share mutable state. Integration and release checkpoints retain their serial
authority/setup dependencies; no blind `-n auto` parallelism is recommended.

## Proposed budgets (targets, not established facts)

- `fast_unit`: target <= 5s for the current 236-test lane on the local
  workstation.
- One representative authority/recovery integration case: target <= 2.5s.
- A bounded 70-test integration checkpoint: target <= 150s, retaining all
  persistence, subprocess, concurrency, and recovery coverage.
- Full offline release discovery and execution: target <= 600s, to be checked
  only at an authorized release gate against a fresh saved JUnit.

These are planning targets, not acceptance results or guarantees.

## Follow-up plan

1. **Fast correction loop:** run the affected deterministic files or
   `-m fast_unit`, plus classifier regression tests and scoped lint/compile.
2. **Integration checkpoint:** run the changed authority/recovery module and
   one representative targeted fixture; retain real filesystem, persistence,
   process, network, and concurrency behavior.
3. **Release checkpoint:** run the bounded combined integration/release
   selection when the coordinator authorizes it; use the saved JUnit for full
   suite comparison rather than repeated full reruns.
4. **Separate profiling:** if timing remains material, instrument fixture setup,
   subprocess/Git calls, SQLite setup, loopback HTTP, and multiprocessing
   boundaries independently. Do not infer those costs from whole-test wall
   time.

## Verification limits

Ruff and bytecode compilation were run only for `tests/conftest.py` and
`tests/test_timing_classification.py`; the focused tests and representative
integration test passed as listed above. No full test suite, provider, app
restart, private state read, schema registration, or package build was run for
this implementation.

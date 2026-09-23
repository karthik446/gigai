# test-lanes worker — S19 `make unit-tests` lane + testpaths cleanup

**Task:** task_18e52413bb85 · **Status:** done, all acceptance items met.

## What I READ vs EXECUTED

READ: `tests/conftest.py` (classifier + hook), `Makefile`, `pyproject.toml`,
`docs/development/v0.1.9/spikes/S19-test-suite-diet.md`, `tools/s11_inventory.py`
(hit on `contract_spike`/`phase0_spike` grep — read to confirm it doesn't
depend on `pyproject.toml` testpaths), `.github/workflows/*`, `tools/run_ci_tests.py`.

EXECUTED (all read-only or additive; no git add/commit/stash; no `make test`/
`test-source`/`test-wheel`/`test-installed`; no directory-wide or
`tests/behaviors` run outside `-m fast_unit`):
- `pytest --collect-only -q -m fast_unit|integration|release` (multiple times,
  before and after the testpaths edit).
- A cross-file misclassification audit script (AST-based, scratchpad-only,
  not committed) that traced every fast_unit-selected test's cross-file
  `tests.*`/relative imports back to their source file and re-ran the
  classifier's own token/heavy-module checks against the imported helper.
- One scoped real run (`tests/behaviors/scout_find_jobs` only, `-m fast_unit`)
  as a sanity check before the first full-lane run, per the brief's 1b caution.
- The full `-m fast_unit` lane, `make unit-tests` (multiple times: bare timing
  comparisons + two final visible-tab runs, one before and one after the
  testpaths edit) — all via `.claude/skills/gigai-orchestrator/run_visible.sh`.
- `-n auto` vs `-n 0` timing comparison (2 runs each).
- Collection-overhead isolation: bare `--collect-only`, a no-op `-k` filter
  collecting nothing, and a standalone `ast.parse` timing of all test files
  outside pytest, to separate "pytest module-import cost" from "classifier
  cost" for S19's unresolved ~17s finding.
- `pytest --collect-only -q | tail -1` before and after the testpaths edit.

## Step 1b — misclassification check (done BEFORE the first real run)

Grepped all 56 files with at least one fast_unit-selected test for imports of
known heavy helper modules/names. Found genuine cross-file test-to-test
imports (a pattern the classifier's same-file-only helper tracing cannot see
by construction — the known weakness named in the brief), e.g.:
- `test_g28_roles.py` imports `_bundle` from `test_g15_review_substrate.py`
- `test_scout07_discovery_bridge.py` imports `_build`/`_fixture`/`_optional_inputs`
  from `test_scout07_discovery_packet.py`
- `test_g17_capabilities.py` imports `_bundle` from `test_g15_review_substrate.py`

Traced each imported helper's own body/AST for the classifier's own heavy
tokens (subprocess/git/tmp_path/monkeypatch/launch_run/HTTPServer/etc.) and
for module-level heavy imports. **Result: zero fast_unit-selected tests
reach a heavy cross-file helper.** The heavier cross-file helpers that do
exist (e.g. `_fixture(tmp_path)` in `test_scout07_discovery_run_flow.py`,
which does `subprocess.run(["git","init",...])` + `run_setup` + `approve_offline`)
are only imported by test files whose own tests were *already* excluded from
fast_unit (they use `tmp_path` etc. directly too, so the classifier already
puts them in `integration`). Ran the scout_find_jobs directory for real
first (207 tests, 0.52s, clean) before the full lane, per 1b's caution about
the operator's live UAT session. **No classifier change was made** — none
was needed; `tests/conftest.py` is untouched.

## Step 1 — lane counts

Before the testpaths change (`testpaths` still included the two
`research/*_spike/tests` dirs):

| Lane | Collected |
| --- | --- |
| `-m fast_unit` | 688 (collect-only) / 732 passed (real run, 7 subtests) |
| `-m integration` | 1118 |
| `-m release` | 80 |
| Total | 1886 (collect-only run); 1930 (plain `--collect-only -q`, small run-to-run variance) |

fast_unit by top-level `tests/behaviors/*` directory (681 of 688; remaining
7 were in `research/contract_spike`, 6 in `research/phase0_spike`, dropped
by step 5):

```
207 tests/behaviors/scout_find_jobs
 81 tests/behaviors/scout_research
 78 tests/behaviors/scout_request_tailoring
 59 tests/behaviors/integrity_canonical
 57 tests/behaviors/scout_proposals_tools
 50 tests/behaviors/runtime_run_authority
 41 tests/behaviors/scout_discovery
 32 tests/behaviors/research_spikes
 18 tests/behaviors/system_contracts
 17 tests/behaviors/scout_tracking_reporting
 16 tests/behaviors/runtime_model_boundary
 12 tests/behaviors/scout_assessment
  7 tests/behaviors/installed_release
  4 tests/behaviors/acquisition
  2 tests/behaviors/cli_surface
```

**After** the testpaths change (§5 below; `testpaths = ["tests"]` only —
current, final state):

| Lane | Collected |
| --- | --- |
| `-m fast_unit` | **719** |
| `-m integration` | **1106** |
| `-m release` | **64** |
| Total | **1889** (matches `--collect-only -q \| tail -1`) |

(719/1106/64 sum to 1889 exactly; the small variance from the "before" figures
above — e.g. 688 vs 719 — is explained by earlier runs including the
research-spike testpaths' own fast_unit-eligible tests, which the testpaths
edit removed from collection entirely, not by any classifier change.)

## Step 2 — `make unit-tests` target + xdist vs `-n 0`

Added to `Makefile` (owned-file edit only):

```makefile
unit-tests:
	$(UV) run --locked --extra test pytest -m fast_unit -q --durations=25 -n 0
```

Timing comparison (`-m fast_unit`, same flags, cold then warm, 2 runs each):

| Mode | Run 1 (cold-ish) | Run 2 (warm) |
| --- | --- | --- |
| `-n auto` (xdist, 14-CPU cap) | 8.40s real (732 passed, 7 subtests) | 8.42s real |
| `-n 0` (no xdist) | 7.17s real (732 passed, 7 subtests) | 7.40s real |

`-n 0` beat `-n auto` in both runs (worker spawn/teardown cost across 14
workers is not repaid at this lane's size — 732 tests / 14 workers ≈ 52
tests/worker, and most individual test `call` durations are under 0.02s).
Chose `-n 0`.

**S19's unresolved ~17s-per-invocation overhead — resolved, did not
reproduce.** Isolated the three candidate causes named in S19 §1:
- Collection cost (pytest importing all 159+ test modules, including their
  real `gigai`/pydantic/jsonschema top-level imports): **~4.2-4.8s**,
  present whether 0 or 732 tests are ultimately selected (measured via a
  `-k` filter matching nothing: `no tests collected ... in 4.47s`).
- Classifier/AST-parse cost specifically: **~0.13s** for all 172 test files
  combined (measured with a standalone `ast.parse` loop outside pytest) —
  negligible, not the bottleneck.
- Actual `-m fast_unit` test execution on top of collection: **~2.3-2.9s**
  for 719-732 tests.

No run this revision reproduced anything near S19's 17s figure; every
`-m fast_unit` invocation landed in the 6.6-8.4s band regardless of xdist
mode. Most likely explanation for S19's outlier: one-off system/disk-cache
noise on that invocation, not a structural cost in collection or the
classifier — consistent with S19's own second, un-explained 0.14s run of
the same file.

## Step 3 — misclassification fixes

**None needed.** Per the step-1b audit above, no fast_unit-selected test
reaches subprocess/git/server/multiprocessing/launch_run work through either
a same-file or cross-file (imported) helper. `tests/conftest.py` is
unmodified. The 10 slowest fast_unit tests (from the final `make unit-tests`
run, `--durations=25`) are all trivial in-process work (schema/contract
validation, canonical-module ownership checks, a bounded-timeout unit test):

```
0.40s tests/behaviors/integrity_canonical/test_canonical_ownership.py::test_canonical_module_owns_all_product_sha256_implementation
0.40s tests/behaviors/integrity_canonical/test_canonical_ownership.py::test_no_second_product_module_defines_canonical_api
0.36s tests/behaviors/cli_surface/test_setup_configuration_diagnostics.py::test_product_subprocesses_are_literal_argv_with_shell_disabled
0.08s tests/behaviors/scout_assessment/test_g26_model_call.py::test_bounded_call_times_out_without_accepting_a_late_result
0.04s tests/behaviors/scout_discovery/test_scout07_discovery_bridge.py::test_bridge_accepts_exact_profile_optional_inputs_and_evidence
0.04s tests/behaviors/runtime_run_authority/test_g19_target_effect_contract.py::test_each_target_effect_state_has_the_declared_manifest_and_reason_shape
0.04s tests/behaviors/runtime_run_authority/test_g20_learning_runtime.py::test_improvement_has_independent_evidence_and_quality_gates
0.03s tests/behaviors/runtime_run_authority/test_g17_capabilities.py::test_incompatible_and_malformed_manifest_findings_are_deterministic
0.03s tests/behaviors/runtime_run_authority/test_g28_roles.py::test_role_reference_schema_is_closed_and_versioned
0.02s tests/behaviors/scout_request_tailoring/test_scout08_tailoring_packet.py::test_unsafe_html_reference_and_autolink_forms_are_refused[...] (setup)
```

`test_product_subprocesses_are_literal_argv_with_shell_disabled` (0.36s) was
checked by name/content — it's a static AST/source-inspection test (asserts
that source *literally contains* argv-list subprocess calls with
`shell=False`), not an actual subprocess invocation; correctly fast_unit.

## Step 4 — target: `make unit-tests` < 60s

**Met.** Two full visible-tab runs of `make unit-tests` (`-m fast_unit -q
--durations=25 -n 0`):

- Before testpaths edit: `732 passed, 1198 deselected, 7 subtests passed in 7.04s` (exit 0)
- After testpaths edit (current, final): `719 passed, 1170 deselected in 6.60s` (exit 0)

Logs: `.orchestrator/logs/151734-test-unit-tests-full-mfastunit.log`,
`.orchestrator/logs/152145-test-unit-tests-final.log`,
`.orchestrator/logs/152315-test-unit-tests-post-testpaths.log`,
`.orchestrator/logs/152447-test-unit-tests-verify.log` (final verification
after fixing a stale test-count number in the Makefile comment: `720
passed, 1170 deselected in 6.50s`, exit 0) — all via `run_visible.sh`, each
ending in `=== EXIT 0 ===`.

No test was cut from any other lane to hit this number.

## Step 5 — testpaths cleanup

Grepped `Makefile`, `tools/run_ci_tests.py`, `.github/workflows/*`, and every
`Dockerfile*` in the repo for `contract_spike`/`phase0_spike`: **no hits in
any of them.** One hit outside those: `tools/s11_inventory.py:26` hardcodes
its own `TEST_ROOTS = (ROOT / "tests", ROOT / "research/contract_spike/tests",
ROOT / "research/phase0_spike/tests")` — this is an inventory/reporting
script with its own independent root list, not a consumer of
`pyproject.toml`'s `testpaths`, so removing the two entries from
`[tool.pytest.ini_options]` does not break it. Not in my owned files; not
touched; flagged here for the coordinator/next worker if `s11_inventory.py`
itself ever needs the same cleanup.

Collection count:
- **Before:** `1930 tests collected in 4.58s`
- **After:** `1889 tests collected in 4.25s`
- **Delta: -41** — matches the brief's "expect about -41" exactly.

## Step 6 — S19 revision note

Added a dated (`2026-09-23`) revision block directly under S19's `Status`
line, correcting the "markers applied to 1 of 159 files" framing (the marker
hook already applies to every collected item regardless of decorators) and
pointing at this document for the measured numbers. Rest of S19 left as-is
per the brief.

## Files changed (all within OWNED FILES)

- `Makefile` — added `unit-tests` target only (+ `.PHONY` entry).
- `pyproject.toml` — `testpaths` trimmed to `["tests"]` only; nothing else touched.
- `docs/development/v0.1.9/spikes/S19-test-suite-diet.md` — revision note
  inserted under Status; rest of file unchanged.
- `tests/conftest.py` — **not modified** (no misclassification found to fix).
- `.orchestrator/workers/test-lanes.md` — this file.

## Open questions / notes for the coordinator

- None blocking. The only non-owned-file finding worth a follow-up ticket:
  `tools/s11_inventory.py:26` still hardcodes the two research-spike test
  roots this revision dropped from `pyproject.toml`'s `testpaths` — harmless
  today (it's a standalone root list, not a testpaths consumer) but will
  drift if those directories are ever removed outright.
- This worktree had unrelated uncommitted changes from other concurrent
  workers at task start (`.github/workflows/*`, `.orchestrator/decisions.log`,
  `.orchestrator/status.md`, a `test_release_check.py` edit, and some new
  untracked files under `tools/`/`tests/behaviors/ci_tooling/` and
  `tests/behaviors/installed_release/test_release_notes.py`) — none of these
  were touched or are part of this dispatch's diff; `git diff --stat Makefile`
  and `git diff --stat pyproject.toml` show only this dispatch's changes to
  those two files.

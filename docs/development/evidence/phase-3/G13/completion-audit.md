# G13 completion audit — sealed deterministic Run launch

Status: implementation evidence prepared on the G13 branch.

## Scope decision

This pass implements exactly one approved, journal-consistent Gig version and
one ready `local_capability` Goal. The worker writes only deterministic proof
bytes under `runs/run_.../`; it does not resolve credentials, call a provider,
use the network, invoke a shell, or mutate the bound target.

## Requirement-to-evidence matrix

| Requirement | Evidence |
|---|---|
| Approved active or explicit historical version only | `src/gigai/run.py:_resolve_authority`; `test_unapproved_version_fails_before_run_allocation` |
| Schema-valid Brief, manifest, and RunDetails | `_prepare_records` validates all three packaged schemas; `test_deterministic_run_is_schema_valid_and_target_preserving` |
| Committed preparation before worker | `record_transition(... transition="run_started")`; `test_preparation_failpoints_leave_no_uncommitted_run` |
| One deterministic capability and terminal outcome | `_worker_entry` / `_execute_deterministic`; `test_deterministic_run_is_schema_valid_and_target_preserving` |
| No target mutation and equal observations | `target_before.json` and `target-after.json` byte comparison in `test_deterministic_run_is_schema_valid_and_target_preserving` |
| Supervised worker and interruption path | `multiprocessing` worker plus `_mark_interrupted`; terminal state is durable in `run-details.json` |
| Repeated Runs remain addressable | `test_repeated_runs_have_disjoint_directories` |
| Installed distribution proof | `tools/verify_installed_g13.py`; CI wheel job invokes it |

The run-details terminal record deliberately reports zero token usage,
`cost_status: "not_applicable"`, empty resolved model/tool lists, non-null
terminal handoff evidence, and the existing completion-audit `missing` state.
Completion audits remain owned by a later goal.

## Verification

- Full suite: 294 passed, 22 subtests.
- G13 focused suite: 4 passed.
- Ruff check and formatting: passed for touched implementation, verifier, and tests.
- Fresh wheel: `uv build --no-sources`, installed into a clean Python 3.11 environment.
- Installed verifiers: schemas, CLI, and G13 deterministic Run all passed.

## Stop boundary

This evidence does not claim scheduling, provider invocation, user-facing Gig
examples, target writes, retries, or autonomous execution. Those remain later
goals.

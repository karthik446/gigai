# C-1 present node, payload builder, aggregate-status fix

## State

`complete` for the owned wave-1b surface. This is source-checkout
implementation and focused offline verification only; it is not
live/provider, model, installed, release, or M1 acceptance.

## Files

- `src/gigai/scout_projection.py`
  - Added `present_node(context: NodeContext, input: PresentInput, *,
    home_root, target) -> PresentOutput`: builds `PresentPayload` from
    `input.batch_ref`/`input.assessment_ref` (committed `AcquireOutput`/
    `AssessOutput` JSON) plus `input.node_receipts` (assembled by the
    caller), the run's sealed `FindJobsRunInput` for `config`, and
    `AssessOutput.pinned_resume` (falling back to the sealed
    `pinned_resume` before assess has run). No application/tracking
    mutation — verified in a test asserting the committed artifact set
    and journal head are unchanged after calling it.
  - Added `build_present_payload(*, home_root, target, run_id) ->
    PresentPayload`: the `run_id`-only rebuild path. Reads the sealed
    `FindJobsRunInput` at `runs/{run_id}/sealed/find-jobs-run-input.json`,
    each node's output at `runs/{run_id}/outputs/{acquire,assess}.json`,
    and each node's `NodeReceipt` at
    `runs/{run_id}/receipts/{acquire,assess,present}.json`. A goal that
    hasn't run yet is simply absent (`JournalArtifactMissingError` →
    `None`), not a projection failure; status is `pending` with zero
    receipts, else `aggregate_status()` over whichever receipts exist.
  - Both helpers call `resolve_workpad(home_root=..., requested_target=...,
    gig_id=None)` — the project's active Gig, matching the pattern I-2's
    `launch_find_jobs_run` will use.
- `src/gigai/scout_report_readers.py`
  - Fixed `_run_rows`'s local-run branch (was `scout_report_readers.py:328-337`):
    replaced "any `complete` state ⇒ succeeded, checked before
    failed/running" with `aggregate_status(states)` from the frozen
    contracts (imported `aggregate_status`), remapping its `"running"` to
    this reader's existing `"active"` vocabulary. `pending`, `failed`,
    `blocked`, `cancelled`, `interrupted`, `succeeded` pass through
    unchanged since they already match `AggregateStatus`'s string values.
- `tests/behaviors/scout_tracking_reporting/test_scout_r3_report.py`
  - Added `test_run_rows_uses_contract_aggregate_status_precedence`
    (parametrized: `complete+failed ⇒ failed`, `complete+running ⇒
    active`, `complete+complete ⇒ succeeded`) against a real committed
    `run-details.json` fixture built by hand (schema-valid, written via
    `record_transition` with the `run_started` transition).
  - Added `test_build_present_payload_round_trips_fixtures_via_present_payload`:
    commits a sealed run-input (from `fixture-run-input-v1.json`),
    `outputs/acquire.json`, `outputs/assess.json`, and all three
    `receipts/*.json` (from `fixture-node-receipts-v1.json`), then asserts
    `build_present_payload`'s result matches `fixture-present-payload-v1.json`
    shape (status `interrupted`, 3 receipts, pinned resume, rows/assessments/
    not_assessed counts) and round-trips through `PresentPayload.to_json()`/
    `from_json()` byte-for-byte.
  - Added `test_present_node_builds_output_from_batch_and_assessment_refs_with_no_mutation`:
    builds a `NodeContext`/`PresentInput` by hand (arbitrary `batch_ref`/
    `assessment_ref` paths, receipts from the fixture), calls `present_node`,
    and asserts the payload matches plus the journal snapshot (`records/`,
    `runs/`) and head are byte-identical before/after the call.
  - Added small local helpers: `_resolved_bound` (wraps `_bound_defaults` +
    optional `select_active_workpad`, needed because `gig_id=None`
    resolution requires an explicitly active Gig), `_commit_run_details`,
    `_committed_json`, `_run_input_payload`, `_fixture`.
- `tests/behaviors/scout_tracking_reporting/test_scout09_application_events.py`
  - Not modified; already covered by the acceptance command with no
    regressions (all pre-existing cases still pass).

## Verification

Focused tests (EXECUTED, no provider/model/network calls):

```text
rtk uv run --locked --extra test pytest tests/behaviors/scout_tracking_reporting/test_scout_r3_report.py tests/behaviors/scout_tracking_reporting/test_scout09_application_events.py -q
........................                                                 [100%]
24 passed in 34.86s
```

Ruff (EXECUTED, read-only probe matching I-0's pattern; no `[tool.ruff]`
section exists in `pyproject.toml`):

```text
rtk uv run --locked --extra test ruff check src/gigai/scout_projection.py src/gigai/scout_report_readers.py tests/behaviors/scout_tracking_reporting/test_scout_r3_report.py --output-format concise
All checks passed!
```

## Choices made

- **Storage convention (coordinator decision, via `orca orchestration
  ask`):** for every registered find-jobs node, I-2's `_execute_goal`
  writes, inside `resolved.path / "runs" / run_id`: `outputs/{goal_slug}.json`
  (canonical `to_json()` of the node output DTO) and
  `receipts/{goal_slug}.json` (canonical `NodeReceipt.to_json()`), both
  listed in that goal's run-details evidence `artifact_refs`.
  `goal_slug ∈ {acquire, assess, present}`. A missing file means the node
  hasn't run yet (pending), not a projection failure. This module never
  reads `run.py` internals or waits on I-2 — it only reads by this
  documented path convention.
- **Sealed run-input path (coordinator correction mid-session):** initially
  built against `runs/{run_id}/run-input.json`; corrected to
  `runs/{run_id}/sealed/find-jobs-run-input.json` per the coordinator's
  follow-up, and switched from a hand-rolled parse to the now-landed
  `FindJobsRunInput` contracts DTO (`FindJobsRunInput.from_json`, which
  itself validates `config_digest == config.digest()` and requires
  `pinned_resume`). Both `present_node` and `build_present_payload` use it;
  there is no fallback to re-reading `<target_root>/find-jobs.json` — the
  sealed snapshot is the run's sole authority, per the coordinator's
  explicit instruction.
- **`present_node` never re-derives `config`/`pinned_resume` from
  `batch_ref`/`assessment_ref`:** `AcquireOutput` doesn't carry `config`
  (frozen shape, confirmed against `scout_find_jobs_contracts.py`), so
  `config` always comes from the sealed run input; `pinned_resume` prefers
  `AssessOutput.pinned_resume` (present once assess has run) and falls back
  to the sealed input's `pinned_resume` otherwise (present is reachable
  before assess completes, e.g. after an acquire-only interrupt).
  `PresentPayload.__post_init__`/`from_json` in the contracts module
  already reject inconsistent aggregate status vs. receipts, so no
  duplicate validation was added here.
- **`_run_rows` remap:** `aggregate_status()` accepts `NodeStatus | str` and
  fails closed (`FindJobsContractError`) on an unrecognized value; every
  `goal_details.status` enum member in `run-details.schema.json` is a valid
  `NodeStatus` member, so this is safe with no new exception handling.
  Only `"running"` needed remapping (to this reader's pre-existing
  `"active"` vocabulary); every other precedence result already matches
  the reader's existing status strings.
- Test fixtures for `run-details.json` are written by hand (not produced by
  a real graph run) since a focused unit test for a status-precedence bug
  doesn't need full scheduler execution; the payload is schema-valid
  against `run-details.schema.json`'s exact required-field set.

## READ vs EXECUTED

READ: `AGENTS.md`; project RTK/CLAUDE.md instructions;
`scout_find_jobs_contracts.py` (full, both pages); Amendment 02 Rev 3;
the find-jobs roadmap (C-1 row, ownership/DAG, fixture table); wave-1b
worker task files (`c1-present-node.txt`, `i2-runner-seam.txt`,
`b1-assess-matrix.txt`, `b2-assess-node.txt`) and I-0's completed worker
report (`i0-contracts.md`) for established conventions; existing
`scout_projection.py`, `scout_report_readers.py`, `scout_report.py`;
`scout_acquisition_records.py` (evidence-ref/read-committed-artifact
pattern); `journal.py` (`read_committed_artifact`, `record_transition`,
`TRANSITIONS`, `JournalArtifactMissingError`); `workpad.py`
(`resolve_workpad`, `select_active_workpad`); `run.py:3438-3600` (existing
`_execute_goal`/`_terminal_status`/`_goal_front_matter`, read-only, not
edited — I-2's file); `run-details.schema.json`; all `tests/behaviors/
scout_find_jobs/fixtures/*.json` used (`fixture-present-payload-v1`,
`fixture-node-receipts-v1`, `fixture-run-input-v1`, `fixture-acquire-batch-v1`,
`fixture-assessment-v1`); `test_scout05_first_proposal.py::_bound_defaults`;
`test_scout09_application_events.py` (patterns only, not edited).

EXECUTED: read-only `git status`/`grep`/`find`; two `orca orchestration ask`
round trips (receipt/output storage convention; sealed run-input path/shape,
later corrected again by the coordinator directly) plus periodic `orca
orchestration check`/heartbeats; focused pytest (`test_scout_r3_report.py`
+ `test_scout09_application_events.py`, iterated to green); focused ruff
check; Python import smoke-test after a self-introduced syntax slip (stray
`@dataclass` decorator left over from a refactor, caught immediately by
`python -c "import gigai.scout_projection"` and removed). No provider/
network/model calls, no API server, no full suite (`make test` never run),
no schema changes, no edits outside the five owned files, and no git
add/commit/stash/reset/clean.

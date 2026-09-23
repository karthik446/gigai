# I-2 runner seam handoff

## State

Succeeded. The runner now admits only registered `local_capability` bindings
whose sealed graph identity/capability matches and whose effects are covered;
registered node execution writes canonical DTO output and full receipts under
the frozen `outputs/{slug}.json` and `receipts/{slug}.json` paths. Placeholder
Goals retain the existing offline evidence behavior, while registered node
failures are redacted and target-observation interruption remains run-level.

## Files changed

- `src/gigai/run.py`
  - Added the generic registered-node scheduler seam and DTO input construction.
  - Added UI-loopback consent gating, newest committed resume pinning, and
    `launch_find_jobs_run(...)` input/config sealing.
  - Switched terminal aggregation to the frozen contracts precedence.
- `src/gigai/graph_node_registry.py`
  - Added fail-closed registration/lookup with graph/version/slug/capability
    identity, declared effects, duplicate-binding refusal, and test clearing.
- `tests/behaviors/scout_find_jobs/test_run_seam.py`
  - Added coverage for callable dispatch, output/receipt round trips and
    run-details evidence refs, policy refusals, local assess narrowing, UI
    consent, config mismatch, resume resolution/no-resume, and aggregation.
- `src/gigai/schemas/run-details.schema.json`
  - Not changed; `NodeReceipt.to_goal_details()` already projects the existing
    schema shape.

## Exact executed checks

```text
uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_run_seam.py tests/behaviors/runtime_run_authority/test_g14_scheduler.py -q
..................                                                       [100%]
18 passed in 11.89s

uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_run_seam.py -q
........                                                                 [100%]
8 passed in 4.79s

uv run --locked --extra test pytest tests/behaviors/runtime_run_authority/test_g14_scheduler.py -q
..........                                                               [100%]
10 passed in 7.40s

uv run --locked --extra test pytest tests/behaviors/runtime_run_authority/test_g13_run.py tests/behaviors/runtime_run_authority/test_g40_runtime.py -q -k 'deterministic_run_is_schema_valid_and_target_preserving or detached_worker_failure_terminalizes_run or run_requires_explicit_consent_before_starting or repeated_direct_confirmations_redeem_distinct_run_consent_records'
....                                                                     [100%]
4 passed, 38 deselected in 5.87s

uv run --locked --extra test ruff check src/gigai/run.py src/gigai/graph_node_registry.py tests/behaviors/scout_find_jobs/test_run_seam.py
All checks passed!
```

Required grep (first 20 lines):

```text
grep -rl "from gigai.run import\|gigai.run" tests/behaviors | head -20
tests/behaviors/runtime_run_authority/test_g40_runtime.py
tests/behaviors/runtime_run_authority/test_g13_run.py
tests/behaviors/runtime_run_authority/test_jsl_closeout_regressions.py
tests/behaviors/runtime_run_authority/test_g14_scheduler.py
tests/behaviors/runtime_run_authority/__pycache__/test_g40_runtime.cpython-313.pyc
tests/behaviors/runtime_run_authority/__pycache__/test_g40_runtime.cpython-313-pytest-9.1.1.pyc
tests/behaviors/runtime_run_authority/__pycache__/test_g21_occurrence.cpython-313-pytest-9.1.1.pyc
tests/behaviors/runtime_run_authority/__pycache__/test_g14_scheduler.cpython-313-pytest-9.1.1.pyc
tests/behaviors/runtime_run_authority/__pycache__/test_g13_run.cpython-313-pytest-9.1.1.pyc
tests/behaviors/runtime_run_authority/__pycache__/test_g16_review_loop.cpython-313-pytest-9.1.1.pyc
tests/behaviors/runtime_run_authority/__pycache__/test_jsl_closeout_regressions.cpython-313-pytest-9.1.1.pyc
tests/behaviors/runtime_run_authority/__pycache__/test_g19_target_effect.cpython-313-pytest-9.1.1.pyc
tests/behaviors/runtime_run_authority/__pycache__/test_g16_review_loop.cpython-313.pyc
tests/behaviors/runtime_run_authority/__pycache__/test_jsl_closeout_regressions.cpython-313.pyc
tests/behaviors/runtime_run_authority/__pycache__/test_g14_scheduler.cpython-313.pyc
tests/behaviors/runtime_run_authority/__pycache__/test_g13_run.cpython-313.pyc
tests/behaviors/runtime_run_authority/__pycache__/test_g19_target_effect.cpython-313.pyc
tests/behaviors/runtime_run_authority/__pycache__/test_g21_occurrence.cpython-313.pyc
tests/behaviors/runtime_run_authority/test_g16_review_loop.py
tests/behaviors/runtime_run_authority/test_g19_target_effect.py
```

## Choices and evidence boundary

- Kept `run-details.schema.json` unchanged and used the frozen contracts
  `NodeReceipt.to_goal_details()` projection.
- Sealed the `FindJobsRunInput` DTO plus canonical config snapshot and digest;
  no mutable config or resume is reloaded by the worker.
- Used the frozen output/receipt paths and listed both refs in projected goal
  details; an output's `Producer`/`UsageBlock` is retained when present, with a
  scheduler producer fallback for acquire/present outputs.
- `resolve_newest_resume` filters committed reference imports to `kind=resume`,
  follows authenticated revision chains, invokes the Scout `_record_revision`
  guard with the documented G45 fallback, reads authenticated bytes, and pins
  the content digest. Missing or unusable resume state raises the clear
  `find_jobs_resume_required` error.
- **READ:** project instructions/RTK; orchestration and Orca skill docs;
  Amendment-02 Rev 3 and the functional roadmap; frozen find-jobs contracts
  and fixtures; `private_records`, `scout_inputs`, `scout_materialization`,
  `scout_present_api`, acquisition/assessment/presentation node signatures;
  existing G14/G13/G40 run tests and run-details schema.
- **EXECUTED:** read-only `git status`, `rg`/`grep`, `sed`, `py_compile`, the
  focused pytest commands above, the focused Ruff check, Orca status/heartbeat
  checks, and no provider/model/live-network calls. No schema/storage
  migration, commit, stash, reset, clean, or changes outside owned files.


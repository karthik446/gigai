# SCOUT R4 terminal recovery completion

Date: 2026-09-11 (America/Denver)

This handoff closes the invalid local proposal assessment lifecycle defect
identified after the source-bound R4 correction pass. It preserves the earlier
source-only findings and evidence in
[SCOUT-R4-review-corrections.md](SCOUT-R4-review-corrections.md); it does not
claim provider execution, activation, or a release-wide acceptance gate.

## Exact source cause

`execute_local_proposal` publishes invocation/result evidence and a single
`goal_failed` transition when the bounded model assessment is invalid. The
`launch_run` proposal branch then unconditionally called
`record_proposal_revision`, whose deliberate `proposal_result_incomplete`
refusal raised a second exception. Its recovery path called
`_mark_proposal_goal_failed`, received `None` because the Goal was already
terminal, and misclassified the still-running owning Run as a competing writer,
raising `proposal_run_authority_refused` and leaving `run-details.status` as
`running`.

## Minimal correction

- `launch_run` publishes a host-owned proposal revision only when the returned
  domain result is `complete`; failed/invalid assessments retain their already
  committed invocation request/response/result evidence and proceed directly
  to Run failure terminalization.
- Recovery distinguishes an already-terminal Goal owned by this proposal call
  from a genuinely terminal competing Run. It finds the exact latest Goal
  terminal handoff, finishes the owning Run once as `failed`, and uses existing
  interrupted recovery if target/journal publication races. Existing terminal
  Run states are returned unchanged, preserving cancellation and avoiding
  duplicate Goal events.
- Positive complete proposal publication remains unchanged, including the
  immutable proposal record and subsequent `run_succeeded` path.
- The committed invocation reader now parses the pinned request/response
  artifacts and checks request digest, selected IDs/descriptors, role, and
  resolved model against the sealed record in addition to the closed receipt
  references.

No schema, journal authority, CLI, provider, network, activation, or config
behavior was broadened by this lifecycle correction.

## Tamper evidence scope

The committed proposal invocation reader now has focused real-journal tamper
coverage for target, request bytes, response reference, record identity, actor,
source descriptor, and receipt-reference mutations; each is refused from one
pinned committed view. The combined run suite also exercises sealed selector
mutation, target mutation, competing cancellation, final publication conflict,
alias/extra-goal refusal, and malformed local output. The descriptor regression
adds missing, duplicate, unknown-family, closed-shape, and non-mapping cases and
asserts adapter transport is not called. This is bounded synthetic evidence;
it does not claim arbitrary-host compromise or live-provider proof.

## Verification

All commands ran in the dirty worktree with synthetic fixtures and injected local
transport only; no commits or publishing were performed by this lane.

```text
rtk .venv/bin/pytest -q tests/test_scout_proposal_run.py::test_supported_proposal_entry_invalid_result_finishes_run_failed -x
-> pre-fix reproduction: failed; proposal_result_incomplete was followed by
   proposal_run_authority_refused while Run details remained running

rtk .venv/bin/pytest -q tests/test_scout_proposal_run.py::test_supported_proposal_entry_invalid_result_finishes_run_failed -x
-> post-fix: 1 passed in 16.81s

rtk .venv/bin/pytest -q tests/test_scout_proposal_run.py::test_supported_proposal_entry_invalid_result_finishes_run_failed -x
-> post-fix terminal-count assertion: 1 passed in 17.23s; exactly one
   `goal_failed` and one `run_failed` transition were committed

rtk .venv/bin/pytest -q tests/test_scout_proposal_run.py::test_supported_proposal_entry_allocates_and_terminalizes_real_run -x
-> post-fix positive path: 1 passed in 20.30s

rtk .venv/bin/pytest -q --durations=0 tests/test_scout_proposal_run.py \
  tests/test_scout_r4_review_corrections.py tests/test_scout_r4_journey.py \
  tests/test_scout_discovery_job.py tests/test_scout_r2_document_records.py \
  tests/test_scout_r3_report.py tests/test_scout09_application_events.py
-> 43 passed in 430.41s (0:07:10); no failures

rtk .venv/bin/pytest -q tests/test_scout_proposal_run.py \
  -k committed_receipt_tamper --durations=0
-> 7 passed, 9 deselected in 145.79s (0:02:25); target, request, response,
   record, actor, source, and receipt-reference mutations were refused

rtk .venv/bin/python tools/verify_installed_schemas.py
-> verified 73 installed GigAI schemas

rtk ruff check src/gigai/run.py src/gigai/journal.py src/gigai/model_execution.py \
  src/gigai/scout_proposal_execution.py src/gigai/scout_proposal_records.py \
  src/gigai/scout_report_readers.py tests/test_scout_r4_review_corrections.py
-> passed

git diff --check -- src/gigai/run.py src/gigai/scout_proposal_execution.py \
  tests/test_scout_proposal_run.py
-> passed
```

## Remaining integrated review

The coordinator should independently review this narrow `run.py` lifecycle
change together with the earlier R4 reader correction, then run any release
acceptance checks required by the final integrated contract. The source lane is
complete; no known owned failure remains in the required grouped suite.

## IPC note

Orca heartbeat/completion IPC was unavailable in this environment
(`Could not connect to the running Orca app. Restart Orca and try again. Orca is
not running.`). No retry loop was used; the exact result and report path are
provided through the final handoff attempt.

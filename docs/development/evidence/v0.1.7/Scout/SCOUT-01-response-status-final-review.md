# SCOUT-01-RS2 final response-status review

**Date:** 2026-09-08  
**Scope:** Read-only confirmation of the RS2 terminal-publication correction
in `src/gigai/run.py`, its focused regression in
`tests/test_g43_provider_run_status.py`, and directly affected status callers.
This supersedes the RS2 finding in
[the earlier re-review](SCOUT-01-response-status-rereview.md) without altering
that historical report. It is not a live-provider result, a baseline decision,
or G43.1/release acceptance.

## Verdict

**Accepted for the previously reported RS2 publication boundary.**
`read_run_details` now reads the workpad file and the exact `HEAD` blob,
refusing with the typed `run_details_reconciliation_required` error if the
file is absent from `HEAD` or the bytes differ. Therefore a terminal artifact
replacement cannot be reported as success, blocked, failed, or interrupted
until the corresponding journal transaction has committed (or an operator has
explicitly reconciled it). The schema validator uses the authenticated
in-memory committed payload, and the status path repeats that check after
RS1 abandonment recovery.

## RS2 confirmation

The terminalizer still constructs terminal `run-details.json` before invoking
the journal writer, but `_read_committed_run_details` makes that order safe for
readers: it compares on-disk bytes with `git show HEAD:<relative-path>` before
deserializing ([`run.py:430-446`](../../../../../src/gigai/run.py)). A crash at
`after_artifact_replace`, `after_replace`, or `before_commit` consequently
returns the typed reconciliation-required refusal, never the replaced
`succeeded` payload. The existing explicit `reconcile_journal` workflow then
commits the declared transaction; only after that does status return the
authenticated success. At `after_commit`, the committed blob already matches,
so success is available immediately.

The new real forked-process parameterized regression exercises all four
boundaries and asserts that pre-commit states refuse, reconciliation succeeds,
the final status is `succeeded`, no offline Goal was executed, exactly one
terminal handoff exists, and the committed blob equals the workpad file
([`test_g43_provider_run_status.py:426-471`](../../../../../tests/test_g43_provider_run_status.py)).
This directly closes the unsafe status exposure identified in RS2.

## RS1 and caller regression check

RS1 remains intact: the existing process-death test still begins from the
committed `running` state, obtains recovery only after the POSIX lease holder
has exited, produces one `run_interrupted` record, keeps offline Goals
`pending`/`ready`, and adds the abandonment error without importing provider
evidence ([`test_g43_provider_run_status.py:353-379`](../../../../../tests/test_g43_provider_run_status.py)).
The additional post-recovery committed-byte read in `read_run_details`
([`run.py:400-405`](../../../../../src/gigai/run.py)) prevents recovery from
returning an uncommitted replacement as well.

The `run-details` CLI presents `RunError` verbatim as a command error rather
than emitting a status object ([`cli.py:2611-2622`](../../../../../src/gigai/cli.py)).
Occurrence reconciliation likewise receives the `RunError` before it can
transition a linked occurrence; its explicit terminalization path translates
this into a reconciliation prerequisite ([`occurrence.py:233-254`](../../../../../src/gigai/occurrence.py),
[`occurrence.py:283-294`](../../../../../src/gigai/occurrence.py)). Thus the
direct callers do not convert an incomplete terminal journal write into a
successful downstream outcome.

## Executed checks

| Command | Result | Evidence boundary |
| --- | --- | --- |
| `rtk proxy .venv/bin/pytest -q tests/test_g43_provider_run_status.py` | `15 passed in 46.48s` | Offline temporary-workpad and real fork-process tests; fake adapter at the model port; no provider call. |
| `rtk ruff check src/gigai/run.py tests/test_g43_provider_run_status.py` | Passed, no findings | Static lint only. |
| `rtk git diff --check -- src/gigai/run.py tests/test_g43_provider_run_status.py` | Passed | Whitespace check only. |

This proof is limited to the tested local journal, lease, and fake-adapter
paths. It does not prove a real provider invocation, live Gig state,
operator consent, a baseline, or full-suite/release behavior.

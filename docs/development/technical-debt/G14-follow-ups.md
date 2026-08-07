# G14 follow-up technical debt

These items are intentionally outside the G14 pull request. They should be
resolved in separate, reviewable changes before the related contracts or
concurrency surfaces expand.

## 1. Disable Git maintenance churn in workpad repositories

The scenario harness now ignores nested `.git` internals so Git maintenance
does not appear as a false workpad mutation. Add an independent hardening
change that configures `gc.auto=0` (or an equivalent narrowly scoped policy)
when GigAI initializes private workpad repositories. Prove that the setting
prevents background maintenance churn without weakening journal integrity
checks.

## 2. Make the journal race test distinguish slowness from failure

`test_eight_process_race_allocates_strict_committed_order` currently uses
`join(timeout=30)` and checks `exitcode`. A timeout can look like an ordering
failure when a worker is merely slow under load. Update the test to assert that
each process exited before checking its exit code, use an evidence-backed
timeout strategy, and preserve the strict committed-order assertions. This
test protects journal commit ordering, so a real ordering regression must
remain distinguishable from infrastructure timing noise.

## 3. Decide the post-approval contract-amendment rule

The G14 clarification commit `0d024dd` amended an approved goal contract
before merge. Establish the project rule for whether post-approval contract
amendments are permitted, and if so, what evidence, review, and versioning
requirements apply. Record the decision before the next goal contract is
written so the process is decided prospectively rather than after an edit.

# SCOUT-03 C1 — Storage correction handoff

**Date:** 2026-09-08  
**Status:** C1 implementation complete; independent review and verification pending.  
**Scope:** Storage/journal/layout/projection correction only. C2 native/default/archive
behavior and C3 tool binding/Plan selection are deliberately not claimed.

## Delivered C1 boundaries

- Private record, import, receipt, revision, and selected-content reads resolve
  exact bytes from one authenticated immutable journal publication. A shaped,
  uncommitted, redirected, foreign, multiply-published, or digest-mismatched
  working-tree artifact is refused rather than promoted to authority.
- Operation-key lookup, equivalent-import lookup, and parent comparison occur
  inside the private-Git writer critical section. Immutable private publication
  is no-clobber; an identical retry returns the prior committed IDs and receipt,
  while changed operation payloads and stale concurrent parents fail typed.
- Revision order follows its authenticated parent chain and rejects roots that
  are ambiguous, branched, cyclic, or disconnected; UUID text never supplies
  ordering. A post-commit projection failure returns the sealed operation with
  `projection_pending` and `rebuild_index`; retry rebuilds without appending a
  second authority event.
- Layout v2 admission checks exact committed marker and ignore bytes together
  with the migration handoff. Recovery may replace only the v2 ignore-policy
  file; all records, snapshots, and receipts remain no-clobber.
- Index publication now uses a transaction in the existing database inode,
  preserving G22 trace connections. All trace persistence paths acquire the
  common database lock, while malformed, redirected, and unknown database
  state refuses instead of being replaced.

## Focused evidence

The C1 tests include reverse-lexical UUID parent-chain ordering, working-tree
record tampering with committed-content reads, injected projection failure, and
a spawned-process same-parent race that yields exactly one winner. The focused
command was:

```text
.venv/bin/pytest -q tests/test_scout03_private_records.py \
  tests/test_index_projection.py tests/test_workpad_private_git.py \
  tests/test_journal_locking_recovery.py tests/test_g22_proposal_interview_contract.py
```

It passed `47` tests in `23.59s`; source compilation also passed. This is
focused offline evidence only. The coordinator owns the combined/full suite;
no provider, private UAT, C2, or C3 completion is implied.

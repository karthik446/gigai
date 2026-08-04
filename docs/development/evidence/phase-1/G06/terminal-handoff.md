# G06 Terminal Handoff

- Goal: [G06 — Journal Locking and Recovery](../../../goals/phase-1/G06-journal-locking-recovery.md)
- Date: 2026-08-03
- Outcome: Complete locally; hosted confirmation pending
- Transition: G06 completion

## Delivered surface

- Generic `record_transition` and explicit `reconcile_journal` production interfaces for an existing, caller-owned private workpad.
- One bounded repository-local POSIX writer lock at `.git/gigai-writer.lock`, with owner-aware timeout diagnostics.
- First-commit support for the unborn G05 repository and 12-digit sequence allocation from committed `HEAD` trailers.
- Canonical JSON-front-matter handoffs with fsync/replace/directory-fsync durability boundaries.
- Semantic local commits carrying `GigAI-Handoff-Sequence`, `GigAI-Handoff`, and previous-handoff trailers.
- Explicit reconciliation of only temporary or one valid uncommitted next handoff; ambiguous state fails closed.
- Mount re-probing, mount-identity checks, no-remote validation, trailer divergence detection, and an eight-process production race proof.

## Contract state

- Normal allocation is O(1) from the current committed `HEAD`; SQLite is not an allocator.
- Recovery scans only when called explicitly; normal writes do not scan journal history.
- The journal accepts caller-supplied identity and does not allocate project or Gig IDs.
- No remote, network, provider, tool, or public journal-writing command was added.
- The G05 resolver accepts the journal’s first committed state while preserving the empty-substrate checks for new provisioning.
- Frozen schemas and canonical vectors remain unchanged.

## Evidence

The [completion audit](completion-audit.md) maps all ten acceptance criteria to the production tests, local full suite, fresh-wheel verifier, and frozen-contract checks.

## Unresolved findings

None within G06. The lock backend is deliberately POSIX-only; unsupported platforms fail closed. G06 does not claim a tested network-filesystem backend, a complete Gig lifecycle, active-Gig selection, proposal creation, or public journal commands.

## Next transition

The goal commit uses:

```text
goal(G06): implement journal locking and recovery
```

After hosted CI passes that exact commit, G06 is terminally complete. G07 remains the only unmet G08 dependency. G08 must allocate its Gig ID itself, call G05 to provision the workpad, then call G06 to record sequence 1 before activating the Gig.

# SCOUT-06 — Independent hydration review

Date: 2026-09-10. Scope was source-only review of
`scout_research_inputs.py` against the frozen SCOUT-00 authority rules and the
hydration implementation report. No source edits, provider calls, full suite,
wheel/package proof, or caller integration were performed.

## Verdict

**Blocking corrections required before treating the helper as caller-ready.**
The resolver itself has strong exact identity, digest, terminal-receipt, and
checkpoint-tuple checks, but the new hydration wrapper narrows the evidence
set before those checks run and cannot be safely called from an already locked
caller.

## Findings

### B1 — Hydration does not authenticate all committed checkpoint history

`hydrate_research_input_snapshot` bootstraps only `runs/<run>/` and
`run-plans/` (`src/gigai/scout_research_inputs.py:616-627`), then adds checkpoint
JSON paths only for paths already discovered from the selected output/check
refs (`:639-669`). It passes that filtered snapshot to `_checkpoint_history`,
which enumerates `snapshot.artifacts` (`:358-391`); therefore unreferenced
checkpoint records, including an omitted predecessor or an extra competing
checkpoint/receipt evidence record, are never authenticated as part of the
history. The implementation report claims “complete immutable history” and
“every authenticated checkpoint,” while the wrapper’s own report says it
hydrates only exact refs; those claims are not equivalent under SCOUT-00’s
append-only history requirement.

**Reproducer (source-level, no mutation):** construct a committed Run with a
receipt check in checkpoint N whose `parent_checkpoint` points to checkpoint
N-1, then make the hydration discovery set contain only the receipt-referenced
N path (or a forged N record with a self-consistent sequence 1). The wrapper
never reads/authenticates the omitted N-1 path; `_checkpoint_history` can only
validate the paths in its filtered snapshot. Required correction: enumerate
and authenticate the complete committed `runs/<run>/checkpoints/*.json` chain
at the pinned HEAD (and all receipt evidence), or carry an authenticated
history index whose completeness is itself sealed. Do not rely on output/check
refs alone to prove history completeness.

### B2 — Combined helper deadlocks under caller writer lock

`resolve_research_input_from_journal` calls hydration, which calls
`run_with_journal_writer` (`:702-705`, `:713-718`). `run_with_journal_writer`
always acquires `gigai-writer.lock` (`src/gigai/journal.py:242-257`), while the
lock is non-reentrant and times out on a nested acquisition
(`journal.py:503-525`). A caller performing input-union sealing/publication
under that lock cannot call the documented combined helper; it must choose an
unsafe unlocked call or hang until timeout. This is a caller-boundary blocking
API defect even though the caller integration itself is explicitly not
delivered here.

**Probe:** nested `_writer_lock(path, 0.1)` then `_writer_lock(path, 0.05)`
returned `InterprocessLockUnavailable` / `interprocess_lock_unavailable`.
Required correction: expose a lock-aware operation that accepts the existing
`JournalWriter`/snapshot, or define and enforce that hydration occurs before
the caller lock and revalidation occurs inside it. The caller must still
revalidate exact equality immediately before publication.

## Confirmed nonblocking properties

The resolver checks exact Run/Plan/receipt identity and scope, requires one
unique succeeded terminal receipt, authenticates output/check refs and exact
checkpoint tuple, validates fixed schema/source bytes, and returns digest-pinned
refs (`:394-528`). `read_committed_artifact(head=bootstrap.head)` is used for
the discovered artifacts (`:676-699`), preserving immutable publication and
pinned-HEAD semantics for those artifacts. These positives do not cure the
incomplete-history and nested-lock findings.

## Verification boundary

One source-level review, one nested-lock disposable probe, and inspection of
the existing hydration tests/report were performed. The existing hydration
implementation’s 10-test / 77.45-second result was recorded evidence from its
own report, not rerun or independently accepted as this review’s suite proof.


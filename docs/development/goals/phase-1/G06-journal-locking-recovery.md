# G06 — Journal Locking and Recovery

- Status: Approved; blocked by G05
- Depends on: G05
- Unblocks: G08

## Outcome

Implement the atomic, durably ordered private journal: a bounded
interprocess writer lock, constant-time sequence allocation from committed
state, semantic text handoffs and commits, mount probing, and truthful crash
reconciliation.

## In scope

- Rewrite the Phase 0 lock and journal evidence behind production interfaces
  while preserving its fsync and atomic-replacement discipline.
- Use one repository-local advisory writer lock shared by all journal writers.
- Allocate 12-digit per-Gig handoff sequences from committed head state in
  O(1), without scanning the history.
- Write canonical text handoffs for every semantic transition and commit the
  transition with the approved identity and trailers.
- Bound lock acquisition and report owner/timeout diagnostics without stealing
  an active lock.
- Probe the configured mount before mutation and detect mount identity changes.
- Reconcile incomplete atomic replacements, uncommitted handoffs, committed
  head state, and rebuildable index state after interruption.
- Detect journal divergence and refuse ambiguous automatic recovery.

## Out of scope

- Network filesystems without proved locking and replacement semantics.
- Process-local locks, timestamp-derived ordering, or directory scans for the
  next sequence.
- Silent remote removal, history rewriting, or destructive repair.
- Proposal creation or public read commands beyond what recovery requires.

## Acceptance criteria

1. Concurrent writers serialize through the repository-local lock and allocate
   unique, monotonically increasing 12-digit sequences.
2. Sequence allocation reads bounded committed state and does not grow with
   handoff count.
3. A semantic transition becomes visible only with its durable text handoff
   and corresponding local commit.
4. Lock timeout, unavailable locking, remote detection, mount failure, and
   journal divergence fail closed with typed diagnostics.
5. Atomic replacement fsyncs the required file and directory boundaries on the
   supported POSIX platforms.
6. Crash scenarios before replacement, after replacement, before commit, and
   after commit reconcile to one truthful state without duplicate transition.
7. The eight-process race proof passes through production interfaces on the
   configured workpad filesystem.
8. Recovery never invents a successful paid or external action from process
   exit alone.

## Verification and evidence

- Multi-process race, bounded-timeout, and lock-owner scenarios.
- Instrumented proof that sequence allocation is independent of history size.
- Crash-point matrix with pre/post journal, filesystem, and Git manifests.
- Mount-unavailable, remote-detected, and divergence negative cases.
- Semantic commit inspection and a completion audit.

## Stop boundary

Stop once the journal can durably record generic semantic transitions and
recover them. Do not implement the proposal lifecycle until G07 has also
completed and G08 is dependency-ready.

# G06 — Journal Locking and Recovery

- Status: Approved; blocked by G05
- Depends on: G05
- Unblocks: G08

## Outcome

Implement the atomic, durably ordered private journal: a bounded
interprocess writer lock, constant-time sequence allocation from committed
state, semantic text handoffs and commits, mount probing, and truthful crash
reconciliation. The writer must support the unborn private Git repository that
G05 provisions without allocating or changing its Gig identity.

## In scope

- Rewrite the Phase 0 lock and journal evidence behind production interfaces
  while preserving its fsync and atomic-replacement discipline.
- Accept only a validated G05 workpad with matching caller-supplied project/Gig
  ownership and no remote; never provision a workpad or allocate an ID.
- Use one repository-local advisory writer lock shared by all journal writers.
- Treat an unborn repository with no `HEAD` as the valid initial journal state.
  A caller-supplied first semantic transition allocates sequence 1, writes the
  first canonical handoff, and creates the first local commit with the G05
  infrastructure files and required trailers.
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
- Project/Gig ID allocation, workpad provisioning, active-Gig selection, or
  reinterpretation of G05 registry authority.
- Proposal creation or public read commands beyond what recovery requires.

## Acceptance criteria

1. An unborn validated G05 repository accepts exactly one caller-supplied first
   semantic transition, allocates `000000000001`, and creates `HEAD` without
   generating or changing the project/Gig ID.
2. Concurrent writers serialize through the repository-local lock and allocate
   unique, monotonically increasing 12-digit sequences.
3. Sequence allocation reads bounded committed state and does not grow with
   handoff count.
4. A semantic transition becomes visible only with its durable text handoff
   and corresponding local commit.
5. Lock timeout, unavailable locking, remote detection, mount failure, and
   journal divergence fail closed with typed diagnostics.
6. Atomic replacement fsyncs the required file and directory boundaries on the
   supported POSIX platforms.
7. Crash scenarios for both the unborn first transition and later transitions
   before replacement, after replacement, before commit, and after commit
   reconcile to one truthful state without a duplicate transition.
8. The eight-process race proof passes through production interfaces on the
   configured workpad filesystem.
9. Static ownership tests prove the journal path does not allocate project or
   Gig IDs or provision a second workpad.
10. Recovery never invents a successful paid or external action from process
   exit alone.

## Verification and evidence

- Unborn-repository first-commit, multi-process race, bounded-timeout, and
  lock-owner scenarios.
- Instrumented proof that sequence allocation is independent of history size.
- Crash-point matrix for first and later commits with pre/post journal,
  filesystem, and Git manifests.
- Mount-unavailable, remote-detected, and divergence negative cases.
- Semantic commit inspection and a completion audit.

## Stop boundary

Stop once the journal can durably record generic semantic transitions and
recover them, including sequence 1 in an unborn G05 repository. Do not allocate
a Gig ID, activate a Gig, or implement the proposal lifecycle until G07 has
also completed and G08 is dependency-ready.

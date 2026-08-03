# G09 — Rebuildable Index and Read Commands

- Status: Approved; blocked by G08
- Depends on: G08
- Unblocks: G10

## Outcome

Provide the rebuildable SQLite projection and complete Phase 1 offline command
surface for inspecting, validating, opening, and diagnosing the authoritative
private journal.

## In scope

- Build `state.sqlite` as a disposable projection of committed workpad records.
- Rebuild and reconcile the index deterministically from the journal.
- Implement `workpad path`, `gigs`, `proposals`, `status`, `show`, `history`,
  `plan`, `check`, `open`, and complete offline `doctor`.
- Keep machine-readable output stable and distinct from human presentation.
- Report proposal and active-version state explicitly; never infer an ambiguous
  lexical latest version.
- Detect stale index state, journal divergence, missing mounts, remotes,
  malformed contracts, and incomplete transitions.
- Preserve no-write behavior for read-only commands on the observed target
  surface.

## Out of scope

- Making SQLite authoritative or storing unrecoverable state only in it.
- `run`, execution scheduling, live providers, or target mutation.
- Using formatted terminal output as the rebuild identity contract.
- Background sync, telemetry, or hosted history.

## Acceptance criteria

1. Deleting `state.sqlite` and rebuilding from the same journal produces the
   same canonical `status --json` projection.
2. Repeated rebuild and reconciliation are idempotent.
3. Every listed command resolves the explicit target, project, Gig, and version
   scopes defined by the command contract.
4. `plan` clearly labels proposed versus approved authority and starts no work.
5. `check` invokes the named contract validators without mutation.
6. `open` preserves the G05 structured path and editor behavior.
7. Complete offline `doctor` covers configuration, mount, lock, atomic replace,
   journal, remote, index, editor, and offline-adapter health.
8. Read commands use no network or credentials and produce no target delta.
9. Corrupt index state is rebuilt or reported; corrupt authoritative journal
   state is never concealed by the index.

## Verification and evidence

- Golden structured-output scenarios for each command.
- Index deletion/rebuild equivalence and idempotency tests.
- Stale-index, corrupt-index, journal-divergence, unavailable-mount, and remote
  negative scenarios.
- Exact target before/after manifests for every read command.
- Offline evidence and a completion audit.

## Stop boundary

Stop with a complete offline inspection and diagnostic surface. Do not add Run
execution, background scheduling, or live adapters.

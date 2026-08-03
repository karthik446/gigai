# G05 — Workpad and Private Git

- Status: Approved; blocked by G04
- Depends on: G04
- Unblocks: G06

## Outcome

Evolve the private registry to resolve Gig workpads, provision an empty private
Git journal substrate only for caller-supplied canonical project and Gig IDs,
fail safely when the configured mount is unavailable, and open an existing
workpad and optional target through structured commands. G05 creates no Gig
identity or semantic Gig state.

## In scope

- Migrate the exact G04 registry schema from version 1 to version 2 while
  preserving the existing `projects` table definition and every populated row
  with exact field values.
- Before migration, publish one durable mode-0600
  `registry.sqlite.v1.bak` containing a valid openable v1 snapshot. Never
  overwrite a conflicting backup.
- Serialize migration, validate the exact v1 schema before backup or mutation,
  add the exact versioned `workpads` and `active_workpads` tables in one SQLite
  transaction, and write `PRAGMA user_version = 2` last. A failed or interrupted
  migration must expose either complete v1 or complete v2, never a partial
  schema, and must leave no migration-lock or temporary artifact.
- Validate v2 with the same strictness as v1: exact application ID, user
  version, table-name set, `CREATE TABLE` definitions, columns, keys, foreign
  keys, uniqueness constraints, and absence of unexpected schema objects.
- Keep registry schema ownership centralized and coupled deliberately:
  `REGISTRY_SCHEMA_VERSION`, the expected table-name set,
  `WORKPAD_TABLE_SQL`, and `ACTIVE_WORKPAD_TABLE_SQL` advance together, while
  the existing `PROJECT_TABLE_SQL` remains exact and unchanged. Migration and
  validation must consume those same definitions rather than duplicate SQL.
- Treat v1 as the only migratable predecessor. Unknown, malformed, partial,
  future, or downgraded state fails closed; no automatic downgrade is provided.
- Resolve project and Gig identities through the configured user-local binding
  and v2 workpad registry. Canonical absolute workpad locators remain only in
  the owner-private registry.
- Require every registered locator to resolve strictly to its deterministic
  project/Gig location beneath the configured workpad root. A changed mount,
  locator escape, symlink redirection, or equivalent path on a different
  configured authority fails closed rather than being rewritten or adopted.
- Treat an unavailable configured mount as a hard stop; do not fall back to a
  new default history.
- Provide one internal provisioning primitive that accepts validated
  caller-supplied `project_id` and `gig_id` values, atomically publishes the
  deterministic workpad location, records its private registry row, and never
  generates or substitutes an ID.
- Initialize the provisioned location as an empty private local Git repository
  with repository-local GigAI identity, explicit project/Gig ownership markers,
  approved Git-ignore rules, and no remote. Reconcile only an exact interrupted
  G05 substrate for the same IDs; reject ambiguous or foreign state.
- Establish the approved workpad topology and Git-ignore rules for rebuildable
  or disposable state without materializing semantic proposal files.
- Provide typed operations that register an existing provisioned workpad and
  select an already-registered active Gig for later lifecycle callers. G05 does
  not invoke active selection as part of provisioning.
- For Git targets, treat `.gigai/project.toml.active_gig_id` as authoritative
  and the v2 active registry row as derived and reconcilable. For explicit
  non-Git targets, the committed v2 active registry row is authoritative.
- Implement `workpad path <gig-id>` and `open <gig-id>` for existing registered
  workpads. Their no-ID forms resolve only an explicit active Gig and return a
  typed `no_active_gig` failure without mutation before G08 activates one.
- Implement `open --target` and `open --with-target` through structured editor
  argv; the latter requires a successfully resolved workpad.
- Detect and reject an unexpected remote.
- Keep workpad locators out of `project.toml`, ordinary command output,
  diagnostics fields marked safe to share, scenario artifacts, and committed
  evidence.

## Out of scope

- Publishing, pushing, fetching, or adding a remote.
- Using the user’s global Git identity or mutating global Git configuration.
- Generating a Gig ID, choosing a replacement ID, or exposing a CLI command
  that provisions or activates an empty Gig.
- Writing `gig.md`, Goals, proposals, semantic handoffs, journal commits, Gig
  versions, or any other creation-lifecycle artifact.
- Full sequence allocation, journal-writer locking, semantic crash
  reconciliation, or proposal creation.
- Migrating any registry other than the exact supported v1 predecessor,
  deleting the retained v1 backup, or providing automatic downgrade behavior.
- Falling back to the target repository as the workpad.

## Acceptance criteria

1. A real populated v1 registry migrates to v2 with all project rows preserved,
   a valid retained v1 backup containing the same rows, the original `projects`
   table definition, both exact new tables, and `user_version = 2`. Structural
   tests fail if the version, expected table set, or either new table definition
   drifts alone.
2. Migration failpoints before backup publication, before the transaction,
   after each schema statement, before the version write, before commit, and
   after commit leave an openable complete v1 or complete v2 registry. Two
   concurrent migrators converge without a partial schema or lock artifact.
3. A v1 implementation refuses the live v2 registry, while G05 refuses unknown
   or malformed versions and never guesses a backward migration. The retained
   v1 backup is recovery evidence, not automatic compatibility.
4. A caller-supplied canonical project/Gig pair provisions only the empty
   private substrate below the configured workpad mount, registers exactly one
   workpad row, and creates no semantic file or commit.
5. Static ownership tests prove the provisioning path does not import or call
   `uuid4`, `generate_entity_id`, or another ID allocator.
6. Every provisioned workpad is a local Git repository with repository-local
   identity, matching ownership markers, approved ignore rules, and no remote.
7. Repeating provisioning is byte-idempotent. Interruption before or after
   atomic publication reconciles the same IDs or fails before replacing
   ambiguous state.
8. Missing, disconnected, read-only, repointed, or identity-changed mounts fail
   before a second or fallback history is created.
9. Resolution rejects ID conflicts, missing registry rows, locator aliases that
   resolve elsewhere, locators outside the configured mount authority,
   malformed repositories, ownership-marker mismatch, and remote configuration
   without mutating them.
10. `workpad path <gig-id>` returns the canonical registered path. No-ID
    `workpad path` and `open` fail with `no_active_gig` and no mutation until an
    active Gig exists; the completion audit must not claim that G05 makes those
    paths independently usable before G08.
11. Git active selection is authoritative in `project.toml` and reconciles its
    derived registry row; non-Git active selection is authoritative in one
    committed registry transaction. Neither path accepts an unregistered or
    mismatched workpad.
12. `open <gig-id>` launches the configured editor with structured argv;
    `--target` and `--with-target` resolve only the declared locations.
13. Headless recording-editor scenarios pass, the native-editor smoke test
    remains explicit rather than assumed, and no share-safe output contains an
    absolute workpad locator.

## Implementation gate

Before production migration or provisioning code changes, add a real populated
v1 registry fixture plus the v1-to-v2 success, rollback, backup, strict-schema,
and crash-failpoint tests. Record their expected pre-implementation failures.
No workpad provisioning implementation begins until that harness proves it can
distinguish a valid v1 file, a complete v2 file, and every forbidden partial
state.

## Verification and evidence

- Populated-v1 migration, retained-backup, exact-v2-schema, concurrent
  migration, and failpoint crash matrix.
- Alternate-mount, unavailable-mount, repointed-mount, and identity-conflict
  scenarios.
- Caller-ID ownership guard, idempotent provisioning, interrupted-publication,
  local repository config, ownership-marker, and no-remote proof.
- Explicit-ID, missing-active, Git-derived-active, and non-Git-authoritative
  workpad resolution plus recording-editor argv assertions.
- Secret/path-canary scans over binding bytes, doctor output, scenario
  artifacts, and committed evidence.
- Before/after target and workpad manifests and a completion audit.

## Completion evidence

- [Completion audit](../../evidence/phase-1/G05/completion-audit.md)
- [Terminal handoff](../../evidence/phase-1/G05/terminal-handoff.md)
- [Verification summary](../../evidence/phase-1/G05/verification-summary.json)

## Stop boundary

Stop with an empty, registered, correctly resolved private journal substrate
for a caller-supplied Gig ID. Do not allocate a Gig ID, expose a public
provisioning success path, select an active Gig during provisioning, write a
semantic handoff or commit, or claim journal-writer safety. G06 owns the first
and subsequent semantic commits; G08 is the first lifecycle caller that may
allocate an ID, provision a workpad, and activate it in that order.

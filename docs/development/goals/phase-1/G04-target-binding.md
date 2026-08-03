# G04 — Target Binding

- Status: Approved; blocked by G03
- Depends on: G03
- Unblocks: G05

## Outcome

Implement idempotent target initialization that creates the minimal ignored
GigAI binding, establishes the minimal versioned user-local project registry,
and proves the exact target and registry deltas without changing tracked user
content.

## In scope

- Bind a Git target to an opaque user-local project record.
- Resolve a Git target from `git rev-parse --show-toplevel`, canonicalize every
  existing target with `Path.resolve(strict=True)`, and compare existing paths
  with filesystem identity rather than input spelling.
- Write only `/.gigai/project.toml` in the target.
- Add exactly one idempotent `/.gigai/` entry to `.git/info/exclude`.
- Preserve machine-readable Git status before and after initialization.
- Refuse an already tracked `.gigai/` until the user resolves it explicitly.
- Support an explicit non-Git `--target` through the user-local registry without
  adding an implicit target directory.
- Create and version the minimal `GIGAI_HOME/registry.sqlite` schema on first
  successful binding, using SQLite transactions and uniqueness constraints for
  project IDs and canonical target locators.
- Require a valid G03 configuration before target or registry mutation.
- Serialize concurrent initialization of the same Git target with a temporary
  target-local interprocess lock and clean that lock after every outcome.
- Treat a valid Git `project.toml` as the authoritative project identity;
  reconcile derived registry and exclude state after interruption without
  allocating a replacement project ID. A non-Git binding is authoritative in
  its committed registry transaction because no target binding file exists.
- Detect and report malformed, conflicting, or stale bindings.

## Out of scope

- Editing tracked `.gitignore`, source files, project metadata, or Git config.
- Creating a Gig, workpad repository, journal, or remote.
- Hiding pre-existing target changes.
- Guessing which conflicting binding the user intended.
- Persisting device or inode numbers as durable target identity; those values
  are not stable mount locators.

## Acceptance criteria

1. A clean Git target gains only `.gigai/project.toml` and one exclude entry.
2. Machine-readable Git status is byte-equivalent before and after a successful
   initialization.
3. Repeating initialization produces no additional target delta.
4. Dirty Python and non-Python targets retain their exact pre-existing status
   and bytes.
5. Tracked `.gigai/`, conflicting IDs, malformed bindings, permission failures,
   and pre-commit write failures fail without partial replacement. An
   interruption after an authoritative Git binding lands is detected and
   reconciled idempotently without replacing its project ID.
6. A non-Git explicit target is registered externally and receives no implicit
   `.gigai` tree.
7. The binding contains no credential, prompt, absolute personal path, raw
   model selector, Gig history, or executable code.
8. `/tmp/x`, its macOS `/private/tmp/x` resolution, and ordinary symlink aliases
   converge on one project binding when they identify the same existing
   directory; raw path-string differences never allocate a second project.
9. Canonical absolute target locators are persisted only in the private
   user-local registry. Scenario artifacts normalize them to stable root tokens;
   they never enter `project.toml`, Git history, committed evidence, or
   share-safe output.
10. Two concurrent `init` processes for one target converge on one project ID
    and one registry row, or one fails with a typed conflict before mutation;
    neither outcome duplicates the exclude entry or leaves a lock artifact.
11. Missing or invalid G03 configuration, an unavailable target, a repointed or
    broken alias, registry corruption, and an incompatible registry version
    fail closed with actionable remediation and no guessed fallback.

## Verification and evidence

- Exact pre/post home, target, filesystem, and Git manifests for clean and
  dirty fixtures.
- Idempotent rerun and interrupted-write scenarios.
- Negative cases for tracked `.gigai/`, conflicts, malformed state, and
  permissions.
- Non-Git target evidence and a completion audit.
- Alias-identity scenarios covering `/tmp` versus `/private/tmp`, an ordinary
  directory symlink, a repointed alias, and a broken alias.
- Two-process initialization evidence proving project-ID and registry-row
  uniqueness with no persistent lock artifact.
- Registry schema/version, transaction rollback, corruption, and G03
  prerequisite evidence.

## Completion evidence

- [Completion audit](../../evidence/phase-1/G04/completion-audit.md)
- [Terminal handoff](../../evidence/phase-1/G04/terminal-handoff.md)
- [Verification summary](../../evidence/phase-1/G04/verification-summary.json)

## Stop boundary

Stop once binding identity and exact target-delta proof are complete. Do not
create or resolve the private workpad in this goal. The registry may store the
canonical target locator and the opaque future `workpad_locator`; it must not
materialize a workpad, Gig, journal, or remote.

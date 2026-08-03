# G04 — Target Binding

- Status: Approved; blocked by G03
- Depends on: G03
- Unblocks: G05

## Outcome

Implement idempotent target initialization that creates the minimal ignored
GigAI binding and proves the exact target delta without changing tracked user
content.

## In scope

- Bind a Git target to an opaque user-local project record.
- Write only `/.gigai/project.toml` in the target.
- Add exactly one idempotent `/.gigai/` entry to `.git/info/exclude`.
- Preserve machine-readable Git status before and after initialization.
- Refuse an already tracked `.gigai/` until the user resolves it explicitly.
- Support an explicit non-Git `--target` through the user-local registry without
  adding an implicit target directory.
- Detect and report malformed, conflicting, or stale bindings.

## Out of scope

- Editing tracked `.gitignore`, source files, project metadata, or Git config.
- Creating a Gig, workpad repository, journal, or remote.
- Hiding pre-existing target changes.
- Guessing which conflicting binding the user intended.

## Acceptance criteria

1. A clean Git target gains only `.gigai/project.toml` and one exclude entry.
2. Machine-readable Git status is byte-equivalent before and after a successful
   initialization.
3. Repeating initialization produces no additional target delta.
4. Dirty Python and non-Python targets retain their exact pre-existing status
   and bytes.
5. Tracked `.gigai/`, conflicting IDs, malformed bindings, permission failures,
   and interrupted writes fail without partial replacement.
6. A non-Git explicit target is registered externally and receives no implicit
   `.gigai` tree.
7. The binding contains no credential, prompt, absolute personal path, raw
   model selector, Gig history, or executable code.

## Verification and evidence

- Exact pre/post filesystem and Git manifests for clean and dirty fixtures.
- Idempotent rerun and interrupted-write scenarios.
- Negative cases for tracked `.gigai/`, conflicts, malformed state, and
  permissions.
- Non-Git target evidence and a completion audit.

## Stop boundary

Stop once binding identity and exact target-delta proof are complete. Do not
create or resolve the private workpad in this goal.

# G05 — Workpad and Private Git

- Status: Approved; blocked by G04
- Depends on: G04
- Unblocks: G06

## Outcome

Resolve each target to its configured workpad, fail safely when that mount is
unavailable, initialize the per-Gig private local Git journal with no remote,
and open the correct workpad and optional target using structured commands.

## In scope

- Resolve project and Gig identities through the configured user-local binding
  and workpad registry.
- Treat an unavailable configured mount as a hard stop; do not fall back to a
  new default history.
- Initialize one private local Git repository per Gig with repository-local
  identity and no remote.
- Establish the approved workpad topology and Git-ignore rules for rebuildable
  or disposable state.
- Implement `workpad path` resolution needed by later commands.
- Implement `open` for the active private workpad and `--with-target` for both
  locations through structured argv.
- Detect and reject an unexpected remote.

## Out of scope

- Publishing, pushing, fetching, or adding a remote.
- Using the user’s global Git identity or mutating global Git configuration.
- Full sequence allocation, lock behavior, crash reconciliation, or creation.
- Falling back to the target repository as the workpad.

## Acceptance criteria

1. The complete Gig tree is created only under the configured workpad mount.
2. Every Gig workpad is a local Git repository with repository-local identity
   and no configured remote.
3. Missing or disconnected mounts fail before a second or fallback history is
   created.
4. Resolution detects identity conflicts, malformed repositories, and remote
   configuration without mutating them.
5. `workpad path` returns the canonical active path for structured consumers.
6. `open` launches the configured editor with structured argv for the workpad;
   `--with-target` resolves both paths exactly.
7. Headless recording-editor scenarios pass, and the documented native-editor
   smoke test remains explicit evidence rather than an automated assumption.

## Verification and evidence

- Alternate-mount, unavailable-mount, and identity-conflict scenarios.
- Local repository config and no-remote proof.
- Workpad/target path-resolution and recording-editor argv assertions.
- Before/after target and workpad manifests and a completion audit.

## Stop boundary

Stop with an empty, correctly resolved private journal substrate. Do not write
semantic handoffs or claim concurrent journal safety until G06 completes.

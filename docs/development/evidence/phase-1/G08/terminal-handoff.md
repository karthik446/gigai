# G08 Terminal Handoff

- Goal: [G08 — Offline Create Lifecycle](../../../goals/phase-1/G08-offline-create-lifecycle.md)
- Date: 2026-08-04
- Outcome: Complete locally; hosted confirmation pending

## Delivered surface

- Offline `create`, `feedback`, `revise`, `approve`, and `reject` commands.
- One private, active G05 workpad per target lifecycle, with G06 semantic commits.
- Digest-pinned, G07-validated proposal artifacts and immutable Git history.
- Two-commit approval publication with a durable Commit-A hash and tag in the active pointer.
- Recovery after every persisted creation boundary and after sealed-but-unpublished approval.
- Installed-wheel verifier and CI gate for the entire offline lifecycle.

## Next transition

G09 may begin only after the G08 goal commit passes hosted CI. It may read the explicit active-version pointer and sealed proposal history, but must not infer active authority from tags, timestamps, or filenames.

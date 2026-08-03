# G04 Terminal Handoff

- Goal: [G04 — Target Binding](../../../goals/phase-1/G04-target-binding.md)
- Date: 2026-08-03
- Outcome: Complete locally; hosted confirmation pending
- Transition: G04 completion

## Delivered surface

- Installed `gigai init [--target PATH] [--home PATH] [--json]` command.
- Strict path-free Git binding schema `1.0` at `.gigai/project.toml`.
- One repository-local `/.gigai/` exclude entry with no tracked-file edit.
- Versioned owner-private `registry.sqlite` with exact schema validation,
  transactional project/target uniqueness, and no automatic migration.
- Strict existing-target resolution, Git top-level discovery, filesystem
  identity comparison, and alias revalidation.
- Explicit non-Git registry-only binding with no implicit target tree.
- Authoritative Git-binding reconciliation without replacement project IDs.
- Target-local concurrent-init lock with dead-owner recovery and complete
  cleanup.
- Clean, dirty, alias, conflict, corruption, permission, interruption,
  concurrency, and fresh-wheel verification.

## Contract state

- Project binding schema: `1.0`.
- Registry schema: SQLite application ID `0x47494741`, user version `1`.
- Project IDs: canonical `project_<lowercase-uuidv4>` values from G01.
- Binding locator: `registry:<project-id>`; no absolute target path.
- Git target authority: valid ignored `project.toml`.
- Non-Git target authority: committed private registry transaction.
- Registry target locators: strict canonical absolute paths, stored only in the
  owner-private registry; no durable device or inode identity.
- Console success surface: help, version, setup, doctor, and init only.
- Source suite: 163 tests plus 22 subtests on Python 3.11, 3.12, and 3.13.
- Wheel: 32 entries; no tests or research.
- Runtime dependencies: Click 8.1 or newer only.
- Frozen schemas: unchanged; all eight manifest checks pass.
- Canonical vectors: unchanged; digest
  `14461cff88552b9ec1a86b02f47619208d8a50c952a73e43e09407d2b074587f`.

## Evidence

The [G04 completion audit](completion-audit.md) maps all eleven acceptance
criteria to exact manifests, byte checks, registry invariants, hostile failure
paths, identity aliases, concurrent installed processes, locked interpreter
runs, and fresh-wheel execution. The [verification
summary](verification-summary.json) records the stable machine-readable result
without raw logs or workstation provenance.

## Unresolved findings

None within the G04 implementation boundary.

Before G05 implementation begins, its contract should state how a per-Gig
private repository can be initialized before G08 owns Gig creation. G04
provides only project identity and deliberately creates no Gig ID or workpad;
G05 must not manufacture either implicitly to bridge that graph boundary.

## Next transition

The goal commit uses:

```text
goal(G04): implement idempotent target binding
```

After that exact commit passes hosted CI, G04 is terminally complete and G05 is
dependency-ready subject to the contract clarification above. G07 remains
independently ready from G01 and G02; G04 does not alter its path.

# G05 Terminal Handoff

- Goal: [G05 — Workpad and Private Git](../../../goals/phase-1/G05-workpad-private-git.md)
- Date: 2026-08-03
- Outcome: Complete locally; hosted confirmation pending
- Transition: G05 completion

## Delivered surface

- Strict registry v2 with exact `projects`, `workpads`, and
  `active_workpads` tables.
- One-way migration from exact v1, preceded by a durable private
  `registry.sqlite.v1.bak` that is never overwritten on conflict.
- Exception, hard-crash, and concurrent-process migration convergence.
- Internal `provision_workpad` primitive accepting only caller-supplied
  canonical project and Gig IDs.
- Deterministic atomic publication of an empty unborn local Git workpad.
- Repository-local identity, project/Gig ownership markers, exact ignore
  rules, and no remote.
- Exact interrupted-stage and published-but-unregistered reconciliation.
- Explicit registration and active-selection operations for later lifecycle
  callers; provisioning performs neither identity allocation nor activation.
- Git-authoritative and non-Git-registry-authoritative active selection.
- Installed `workpad path [<gig-id>]` and `open [<gig-id>]` with target-only
  and workpad-plus-target editor behavior.
- Typed `no_active_gig` failure for no-ID forms before G08.
- Recording-editor, hostile mount, locator, symlink, ownership, remote,
  permission, and fresh-wheel verification.

## Contract state

- Registry application ID: `0x47494741`.
- Registry live schema: user version 2 with exact tables `projects`,
  `workpads`, and `active_workpads`.
- Migratable predecessor: exact G04 user version 1 only.
- Retained migration evidence: mode-0600 `registry.sqlite.v1.bak`; no automatic
  downgrade.
- Workpad path:
  `<workpad-root>/projects/<project-id>/gigs/<gig-id>`.
- Workpad initial top-level entries: `.git/` and `.gitignore` only.
- Workpad `HEAD`: unborn; G05 creates no commit.
- Workpad remote: none.
- Local Git identity: `GigAI Journal <local@gigai.invalid>`.
- Ownership markers: local `gigai.project-id` and `gigai.gig-id`.
- Ignore bytes: `/objects/`, `/scratch/`, and `/state.sqlite`, each on its own
  LF-terminated line.
- Git active authority: ignored target `.gigai/project.toml.active_gig_id`.
- Non-Git active authority: committed `active_workpads` registry row.
- Console success surface: help, version, setup, doctor, init, explicit
  workpad path, explicit workpad open, and target-only open.
- Public provisioning/activation surface: none.
- Source suite: 206 tests plus 22 subtests on Python 3.11, 3.12, and 3.13.
- Wheel: 33 entries; no tests or research.
- Runtime dependencies: Click 8.1 or newer only.
- Frozen schemas and canonical vectors: unchanged.

## Evidence

The [G05 completion audit](completion-audit.md) maps all thirteen criteria to
the populated-v1 fixture, exact DDL checks, backup proof, exception and
hard-crash matrices, concurrent migration, byte-idempotent provisioning,
allocator ownership guard, hostile mount/repository cases, active-authority
tests, installed scenarios, locked interpreter runs, and fresh-wheel
execution. The [verification summary](verification-summary.json) records the
stable machine-readable result without raw logs or workstation provenance.

## Unresolved findings

None within the G05 implementation boundary.

The lack of a public provisioning command is intentional, not missing product
surface. G08 is the first lifecycle that may allocate a Gig ID, pass that exact
ID to G05, have G06 create sequence 1 and the first semantic commit, and only
then activate the Gig. G05's no-ID `workpad path` and `open` remain typed
failures until that lifecycle exists.

## Next transition

The goal commit uses:

```text
goal(G05): implement private workpad substrate
```

After that exact commit passes hosted CI, G05 is terminally complete and G06 is
dependency-ready. G06 must accept this unborn repository, create handoff
sequence `000000000001` and the first semantic `HEAD`, and must not allocate a
Gig ID, provision another workpad, or select active state. G07 remains
independently ready from G01 and G02.

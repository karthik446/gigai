# G05 Completion Audit

- Goal: [G05 — Workpad and Private Git](../../../goals/phase-1/G05-workpad-private-git.md)
- Date: 2026-08-03
- Local result: Pass
- Verification host: macOS arm64
- Package version: 0.0.0

## Outcome

GigAI now owns a strict registry v2, a one-way migration from the exact G04 v1
registry, and an internal workpad primitive that accepts caller-supplied
canonical project and Gig IDs. The primitive atomically publishes only an
empty, unborn, local Git repository beneath the configured workpad authority,
sets repository-local identity and ownership markers, installs the approved
ignore rules, registers the private locator, and configures no remote.

The installed CLI adds `workpad path [<gig-id>]` and `open [<gig-id>]` with
`--target` and `--with-target` behavior. These commands resolve or open only
existing registered state. G05 exposes no command that allocates an ID,
provisions a workpad, or selects an active Gig. Before G08, both no-ID workpad
forms therefore fail honestly with `no_active_gig`.

## Test-first implementation gate

The populated-v1 fixture and migration harness landed in the worktree before
any registry production change. Its first run failed during collection because
`ACTIVE_WORKPAD_TABLE_SQL` and the other v2 contract symbols did not exist.
Only after that expected failure was recorded did registry implementation
begin. The resulting migration file contributes 27 passing tests over a real
SQLite v1 database containing two project rows.

The fixture preserves the G04 `projects` DDL independently, including
`WITHOUT ROWID`; it is not generated from the v2 implementation. Exact v2 SQL
literals are duplicated intentionally in the structural contract test so a
version, table-set, or DDL constant cannot drift alone without failing CI.

## Acceptance reconciliation

### 1. Populated v1 migrates exactly and retains a valid backup

Pass. A real mode-0600 v1 database with two canonical project rows migrates to
`user_version = 2`. Both rows retain exact field values, and the original
`PROJECT_TABLE_SQL` is not reconstructed or changed. The live database gains
only exact `workpads` and `active_workpads` tables.

Before mutation, migration atomically publishes
`registry.sqlite.v1.bak`. Tests reopen it as strict v1, verify the application
ID, exact `projects` DDL, both rows, and mode 0600. A malformed or conflicting
existing backup is rejected and its bytes are never replaced.

### 2. Every migration interruption exposes complete v1 or complete v2

Pass. Both exception injection and real subprocess termination cover seven
boundaries: before backup publication, before the transaction, after each new
table statement, before the version write, before commit, and after commit.
Every case reopens as exact v1 or exact v2; recovery converges to v2 with the
original project rows and no migration-lock or temporary artifact.

SQLite `BEGIN EXCLUSIVE` is the serialization point. The migration rechecks
version and exact schema inside that transaction. Two concurrent processes
converge on one v2 registry; backup publication uses an atomic no-overwrite
link, so the pre-transaction race cannot replace recovery evidence.

### 3. Unsupported, malformed, and backward states fail closed

Pass. Only exact v1 is migratable. Versions 0, 3, and 99, unexpected tables,
partial v2 tables, exact-schema drift, malformed project IDs, and relative
target locators fail before backup or mutation. Strict validation covers table
names, stored SQL, columns, keys, unique indexes, foreign keys, populated row
values, foreign-key integrity, and unexpected schema objects.

A frozen v1-reader test refuses the resulting v2 registry with an unsupported
version error. The retained backup is evidence for operator-directed recovery;
the product provides no automatic downgrade.

### 4. Caller IDs provision only an empty private substrate

Pass. Given one registered project ID and one caller-supplied Gig ID, the
primitive chooses exactly
`<workpad-root>/projects/<project-id>/gigs/<gig-id>`, atomically renames a fully
validated staged directory into place, and inserts one workpad row. The root
contains only `.git/` and `.gitignore`; there is no `gig.md`, Goal, handoff,
manifest, proposal, version, run, or commit.

Provisioning does not select an active Gig. The `active_workpads` table remains
empty until an explicit internal selection operation is called.

### 5. G05 cannot allocate identity

Pass. An AST ownership test scans the production workpad module and rejects
imports or references to `uuid4`, `generate_entity_id`, `allocate_id`, or a
Gig-ID constructor. Both IDs cross the API boundary as required strings and
are validated by the canonical G01 implementation. An unknown project or
malformed project/Gig ID fails before workpad publication.

### 6. Every workpad has exact local-only Git ownership

Pass. The repository has an unborn `main` branch, no `HEAD` commit, no remote,
and no dependence on global or system Git configuration. Its local config
contains `GigAI Journal <local@gigai.invalid>` plus exact
`gigai.project-id` and `gigai.gig-id` markers. Marker mismatch, a nonlocal Git
directory, an existing commit, or any remote blocks resolution.

The initial `.gitignore` bytes are exactly:

```gitignore
/objects/
/scratch/
/state.sqlite
```

Those paths are the rebuildable or disposable locations identified by V14;
G05 does not create them.

### 7. Provisioning is idempotent and interruption is reconcilable

Pass. A full-tree digest over paths, modes, and file bytes is identical after
an immediate rerun. The rerun reports no publication and no registry change.

Injected failures after staging, publication, and registry insertion prove the
three recovery shapes. A real hard exit after staging leaves one fully marked
candidate; the next call validates and publishes that exact candidate. A
published-but-unregistered destination is validated and registered. Multiple,
malformed, foreign, or marker-mismatched candidates fail rather than being
chosen or replaced.

### 8. The configured mount is authoritative

Pass. Missing, non-directory, read-only, and symlink-repointed configured roots
fail before fallback creation. Every topology component is checked for symlink
redirection and revalidated beneath the strict configured root. Tests confirm
that a replacement target behind a repointed mount remains untouched.

There is no target-repository fallback and no default-root retry. Once a
workpad is registered, a missing or identity-conflicting deterministic path
fails rather than creating a second history.

### 9. Resolution rejects conflicting or foreign state

Pass. Resolution requires a bound project, a workpad row for the same project
and Gig, an exact deterministic locator string, strict resolution beneath the
configured authority, and the exact unborn repository ownership contract.
Tests attack the private locator, Gig marker, remote list, and destination with
a symlink; every case fails without repair or mutation.

Unknown Gig IDs, cross-project IDs, unavailable targets, target-kind conflicts,
and authoritative Git-binding conflicts also fail closed.

### 10. Explicit path works; no-active remains honest

Pass. `gigai workpad path <gig-id>` returns the canonical registered path and
changes no target, workpad, or home state. Both no-ID `workpad path` and `open`
return exit 1 with the typed `no_active_gig` message and zero mutation when the
target has no active Gig.

This audit does not claim the no-ID forms are independently usable in G05.
Only G08 may allocate a Gig ID and activate the provisioned result.

### 11. Git and non-Git active authority remain distinct

Pass. Internal Git selection writes only path-free
`.gigai/project.toml.active_gig_id`; its registry row is derived. A test
deliberately changes the derived row to another registered Gig, then proves
resolution restores the authoritative binding value.

For non-Git targets, selection commits only the registry transaction and
touches no target content. Both forms reject an unregistered or
different-project workpad. Provisioning itself never invokes selection.

### 12. Editor invocation is structured and declared

Pass. `open <gig-id>` invokes configured editor argv as a Python list with
`shell=False`. Recording editors prove literal configured arguments precede
the exact workpad and optional target paths. `open --target` needs no active
Gig and opens only the bound target; `--with-target` first requires a resolved
workpad. Conflicting option shapes fail before invocation.

Successful ordinary `open` output reports only which declared roots were
opened and contains no private locator.

### 13. Installed scenarios and share-safe evidence are clean

Pass. Five G05 installed-process scenarios cover explicit path, both typed
no-active failures, workpad-plus-target editor argv, target-only editor argv,
and forbidden provisioning/activation command names. Across the complete
installed set, 30 scenarios pass against the fresh-wheel executable.

Scenario artifacts normalize home, workpad, target, fixture, guard, and
executable roots. A scan finds no workstation path, source-repository name,
credential token, or routable personal email in publishable changes. The only
email introduced is the intentional non-routable Git identity
`local@gigai.invalid`.

## Additional verification

### Locked source matrix

| Interpreter | Collected | Result |
|---|---:|---|
| CPython 3.11 | 206 | 206 passed |
| CPython 3.12 | 206 | 206 passed |
| CPython 3.13 | 206 | 206 passed |

Each final run used:

```text
uv run --isolated --locked --extra test --python <version> pytest -q
```

Pytest additionally reported 22 subtests per interpreter. G05 adds 43 tests to
the G04 baseline: 27 migration cases, 11 workpad unit/hostile tests, and 5
installed-process scenario tests.

### Built artifacts

`uv build` produced a wheel and source distribution. The wheel has 33 entries,
adds `gigai.workpad`, and contains no tests or research. A fresh CPython 3.11
environment installed only GigAI and its declared Click runtime dependency.

All six installed verifiers pass:

```text
verified 8 installed GigAI schemas
verified installed GigAI canonical identity API
verified installed GigAI CLI: help, version, setup, doctor, init, workpad path, and open only
verified installed GigAI G03 setup, idempotency, pack, and offline doctor
verified installed GigAI G04 Git and non-Git target binding
verified installed GigAI G05 private unborn workpad and read/open surface
```

Thirty selected CLI, G03, G04, and G05 installed-process scenarios pass against
the fresh-wheel executable rather than the editable command.

### Frozen contracts and dependency boundary

All eight frozen schema checksum validations pass. No schema, canonical vector,
`pyproject.toml`, or lockfile byte changed. The canonical-vector digest remains:

```text
14461cff88552b9ec1a86b02f47619208d8a50c952a73e43e09407d2b074587f
```

Runtime dependencies remain Click 8.1 or newer only. G05 adds no provider SDK,
network behavior, telemetry, background process, remote operation, Gig identity
allocation, semantic workpad file, or journal commit.

## Completion decision

G05 is locally complete. No acceptance criterion is waived and no frozen
contract byte changed. Hosted CI on the exact G05 commit is the publication
confirmation gate; G06 must not begin until all source-matrix and built-wheel
jobs pass that commit. G07 remains independently dependency-ready.

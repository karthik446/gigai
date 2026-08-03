# G04 Completion Audit

- Goal: [G04 — Target Binding](../../../goals/phase-1/G04-target-binding.md)
- Date: 2026-08-03
- Local result: Pass
- Verification host: macOS arm64
- Package version: 0.0.0

## Outcome

GigAI now provides an independently useful `gigai init` command. It binds a
Git target through one ignored, path-free `.gigai/project.toml`, records the
canonical absolute target locator only in an owner-private user-local SQLite
registry, and adds exactly one `/.gigai/` entry to the repository-local Git
exclude file. Explicit non-Git targets are registry-only and receive no
implicit target content.

Initialization requires a valid G03 configuration, is byte-idempotent,
preserves dirty user files and machine-readable Git status, serializes
concurrent Git initialization, recovers abandoned lock state, and reconciles
an authoritative Git binding after interruption without replacing its project
ID. It creates no Gig, workpad, journal, remote, provider call, or network
effect.

## Acceptance reconciliation

### 1. Clean Git init has only the approved target and exclude delta

Pass. Installed-process and unit scenarios begin from committed clean Python
fixtures. The target manifest gains exactly the `.gigai` directory and
`.gigai/project.toml`; no tracked file changes. A separate exact-byte check over
`.git/info/exclude` proves one new `/.gigai/` line and no other replacement.
The private home manifest gains only `registry.sqlite`.

The binding contains exactly `schema_version`, `project_id`, and
`workpad_locator`. It is canonical UTF-8/LF owned text with mode 0600. The
registry uses SQLite application identity `0x47494741`, schema version `1`, and
one exact `projects` table with transactionally unique project IDs and target
locators.

### 2. Machine-readable Git status is preserved

Pass. Product code captures `git status --porcelain=v1 -z
--untracked-files=all` before and after ordinary initialization and reports
success only when the bytes are identical. Clean and dirty installed scenarios
independently capture the same bytes and assert equality.

The only narrow exception is interruption reconciliation: if an authoritative
`.gigai/project.toml` already landed before its exclude entry, recovery may
remove exactly that internal file from untracked status. A test proves every
other dirty entry remains byte-identical; no general status difference is
accepted.

### 3. Immediate rerun is materially idempotent

Pass. The installed rerun returns the same project ID and reports
`binding_created=false`, `registry_changed=false`, and
`exclude_changed=false`. Binding and exclude bytes, registry row count, target
manifest, home manifest, workpad manifest, and Git state remain unchanged.

### 4. Dirty Python and non-Python targets are preserved exactly

Pass. Installed scenarios dirty a tracked Python or JavaScript file and add an
untracked binary file containing non-text bytes. Init preserves both exact
payloads and the complete NUL-delimited status bytes while adding only the
approved ignored binding and private registry.

### 5. Invalid, conflicting, interrupted, and unwritable state fails closed

Pass. Hostile tests cover tracked `.gigai`, malformed and unsupported binding
contracts, a project ID registered to another target, read-only target and
registry state, registry schema drift, corrupt SQLite, and unsupported registry
versions. Preflight validation occurs before replacement. Expected failures
produce one typed CLI error with no Python traceback.

A valid target binding is the Git authority. If it exists while registry or
exclude state is absent, init restores only the derived state and retains its
project ID. The target-local lock records a process owner token; a dead-owner
fixture is recovered, and success and failure paths leave neither the lock nor
an abandoned-lock artifact.

### 6. Explicit non-Git targets remain registry-only

Pass. An explicit existing non-Git directory receives one private registry row
and no `.gigai` tree. Repeating through a symlink and canonical spelling returns
the same project ID and one row. Running init implicitly from a non-Git current
directory fails with an instruction to provide `--target` and creates no
registry.

### 7. The target binding contains no sensitive or executable state

Pass. Exact binding bytes contain only schema version, opaque project ID, and
`registry:<project-id>`. Tests reject unknown fields, noncanonical IDs, locator
mismatches, and invalid optional Gig IDs. The file contains no slash, target or
home path, credential, prompt, model selector, history, or code.

### 8. Equivalent filesystem spellings converge

Pass. Identity tests use strict resolution plus `os.path.samefile` to prove an
ordinary symlink and its target converge. An installed macOS scenario invokes
the same non-Git directory through `/tmp/...` and its resolved
`/private/tmp/...` spelling and observes one project ID and one registry row.
Repointed and broken aliases fail identity revalidation rather than selecting a
fallback.

### 9. Absolute locators stay private

Pass. The only persisted absolute target locator is the mode-0600
`registry.sqlite` row. The Git binding and successful text/JSON output are
path-free. Scenario artifacts normalize all temporary roots to stable tokens,
and secret/path scans find no workstation locator in committed evidence.

The registry refuses symlinks, non-regular files, and group/world-readable
permissions so private locators cannot be redirected or exposed silently.

### 10. Concurrent init converges safely

Pass. A black-box test launches two installed `gigai init --json` processes
against one Git target. Both return the same project ID; the registry contains
one row; the exclude file contains one entry; status is unchanged; and no lock
artifact remains. SQLite `BEGIN IMMEDIATE` and unique constraints also
serialize non-Git alias convergence.

### 11. Prerequisite, identity, and registry failures are explicit

Pass. Installed scenarios cover missing and malformed G03 configuration,
implicit non-Git use, a broken alias, corrupt and incompatible registries, and
read-only state. Source tests cover repointed aliases, schema drift, registry
rollback, symlinked registry files, and conflicting authoritative identity.
None guesses a target, migrates state, or creates a fallback.

## Additional verification

### Locked source matrix

| Interpreter | Collected | Result |
|---|---:|---|
| CPython 3.11 | 163 | 163 passed |
| CPython 3.12 | 163 | 163 passed |
| CPython 3.13 | 163 | 163 passed |

Each run used:

```text
uv run --isolated --locked --extra test --python <version> pytest -q
```

Pytest additionally reported 22 subtests. G04 adds 20 focused production tests
and 15 installed-process tests to G03's 128-test baseline.

### Built artifacts

`uv build` produced a wheel and source distribution. The wheel has 32 entries,
includes the three G04 production modules, and contains no tests or research. A
fresh CPython 3.11 environment installed only GigAI and its declared Click
runtime dependency.

All five installed verifiers pass:

```text
verified 8 installed GigAI schemas
verified installed GigAI canonical identity API
verified installed GigAI CLI: help, version, setup, doctor, and init only
verified installed GigAI G03 setup, idempotency, pack, and offline doctor
verified installed GigAI G04 Git and non-Git target binding
```

Twenty-five selected CLI, G03, and G04 installed-process scenarios also pass
against the fresh-wheel executable rather than the editable source command.

### Frozen contracts and stop boundary

All eight frozen schema checksum validations pass. No schema or canonical
vector changed. The canonical-vector digest remains:

```text
14461cff88552b9ec1a86b02f47619208d8a50c952a73e43e09407d2b074587f
```

No workpad directory, private Git journal, Gig identity, journal record,
remote, provider SDK, network behavior, or new runtime dependency entered G04.

## Completion decision

G04 is locally complete. No acceptance criterion is waived and no frozen
contract byte changed. Hosted CI on the exact G04 commit is the publication
confirmation gate; G05 remains blocked until all source-matrix and built-wheel
jobs pass that commit.

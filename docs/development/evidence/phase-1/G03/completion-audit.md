# G03 Completion Audit

- Goal: [G03 — Setup, Configuration, and Diagnostics](../../../goals/phase-1/G03-setup-configuration-diagnostics.md)
- Date: 2026-08-03
- Local result: Pass
- Verification host: macOS arm64
- Package version: 0.0.0

## Outcome

GigAI now provides independently useful local `setup` and offline `doctor`
commands. Setup creates one strict, versioned machine configuration; preserves
the selected workpad mount as authority; records structured editor argv and
credential references; materializes one immutable deterministic standard pack;
and proves atomic replacement plus real two-process exclusion on that mount
before committing configuration.

Doctor reloads the same typed configuration and reports stable PASS, WARN, or
FAIL checks for configuration, paths, permissions, credentials, editor
resolution, the offline adapter, atomic replacement, and interprocess locking.
It performs no provider call, token use, target binding, Git initialization,
Gig creation, or persistent workpad mutation.

## Acceptance reconciliation

### 1. Complete typed configuration without raw credentials

Pass. `gigai.config` owns a strict `schema_version = "1.0"` TOML contract. It
requires exact top-level and nested fields; rejects missing, unknown,
duplicate, malformed, relative-path, and incompatible-version state; and
validates endpoint-to-target and target-to-profile references.

The configuration records only `environment` or explicitly named
`secret-manager` references. Secret-manager locators require a supported
reference URI shape; a raw token-shaped value is rejected. Tests inject a
secret canary and prove it is absent from `config.toml`, process output,
scenario artifacts, target/workpad manifests, and fixtures.

### 2. Rerunning setup is semantically and materially idempotent

Pass. Canonical TOML is rendered through the G01 owned-text boundary and is
replaced atomically only when its exact bytes change. The standard pack is
stored once under:

```text
packs/builtin/standard/1/<content-hash>/standard-pack.json
```

The fresh installed scenario reports both changes as true. The immediate rerun
reports both as false, preserves exact home/target/workpad manifests, retains
one credential entry, and finds one pack materialization.

### 3. The selected workpad root remains authoritative

Pass. Setup canonicalizes and persists the selected absolute path. Both source
and wheel-installed scenarios configure a nested alternate mount, verify that
the stored authority resolves to that exact location, execute mount probes
there, and prove no fallback `home/workpads` directory appears.

When the configured mount is later removed, doctor fails the path, atomic, and
lock checks. It does not create a default or replacement mount.

### 4. Missing, read-only, malformed, and incompatible state fails closed

Pass. Installed-process scenarios prove:

- doctor reports `config.valid = FAIL` for a missing configuration and creates
  no state;
- setup rejects malformed TOML before changing home, target, or workpad;
- setup rejects schema version `99.0` with an explicit unsupported-version and
  no-migration diagnostic;
- setup rejects an existing mode-0400 configuration before pack or directory
  materialization; and
- corrupt content-addressed pack bytes are refused without rewriting config.

No silent repair, interpretation, migration, or partial mutation occurs in
these paths.

### 5. Doctor is offline, zero-token, and structured

Pass. JSON output has explicit diagnostic schema `1.0`, command, installed
GigAI version, scope, overall status, and ordered checks containing stable ID,
subject, status, summary, share-safe evidence, remediation, and duration.

Tests exercise all three statuses: a healthy installation passes; a missing
environment reference warns without reading its value; and invalid or missing
configuration and mounts fail. The installed scenario audit hook rejects every
Python socket event, and the successful doctor scenario records no guard event.
The adapter check explicitly reports `network_used=false` and
`credential_used=false`.

### 6. Atomic replacement and two-process exclusion use the actual mount

Pass. Both setup and doctor create unique temporary probes directly below the
configured workpad root and remove them afterward.

The atomic probe writes and fsyncs both sides, uses `os.replace`, verifies exact
readback, and fsyncs the containing directory. The lock probe holds a POSIX
advisory lock in the parent, launches the active installed interpreter as a
second process, and succeeds only when the contender reports it was blocked.
The subprocess uses a literal argv list, `shell=False`, a timeout, and an exact
scenario allowlist. Before/after workpad manifests are identical.

### 7. Editor and subprocess commands remain structured argv

Pass. Editor configuration is a non-empty TOML string array. Explicit CLI
values remain literal argv items; the conventional `VISUAL` or `EDITOR` string
is tokenized once into argv without shell execution. Literal spaces or
shell-substitution text remain data. G03 owns no `open` behavior and therefore
does not launch the editor during setup or doctor.

The real lock contender is invoked through structured argv. An AST ownership
test walks every shipped product module and requires each `subprocess.run` call
to use a literal list and explicit `shell=False`. G02's recording-editor proof
continues to establish that the stored argv form can be invoked without a
shell when the later `open` goal owns that effect.

### 8. Behavior passes through the installed CLI scenario harness

Pass. Nine G03 installed-process scenario cases invoke only the generated
`gigai` executable. Together they cover fresh setup, exact rerun, offline
doctor, alternate and disappeared mounts, malformed and incompatible config,
read-only config, missing config, and interactive effect review.

The built-wheel lane targets the wheel environment's executable and interpreter
rather than importing the source checkout's Click command. Ten selected CLI
and G03 cases pass against that fresh-wheel executable.

## Additional verification

### Locked source matrix

| Interpreter | Collected | Result |
|---|---:|---|
| CPython 3.11 | 128 | 128 passed |
| CPython 3.12 | 128 | 128 passed |
| CPython 3.13 | 128 | 128 passed |

Each run used:

```text
uv run --isolated --locked --extra test --python <version> pytest -q
```

Pytest additionally reported 22 subtests. The 128 tests comprise 59 G01
production tests, 19 G02 CLI/harness tests, 19 G03 setup/diagnostic tests, 14
contract tests, and 17 Phase 0 tests.

### Built artifacts

`uv build` produced a wheel and source distribution. The wheel has 29 entries,
including the immutable standard-pack resource and all G03 production modules;
it contains no tests or research. A fresh CPython 3.11 environment installed
only GigAI and its declared Click runtime dependency.

All four installed verifiers pass:

```text
verified 8 installed GigAI schemas
verified installed GigAI canonical identity API
verified installed GigAI CLI: help, version, setup, and doctor only
verified installed GigAI G03 setup, idempotency, pack, and offline doctor
```

### Frozen contracts and scope boundary

All eight frozen schema checksum validations pass. No schema or canonical
vector changed. The canonical-vector digest remains:

```text
14461cff88552b9ec1a86b02f47619208d8a50c952a73e43e09407d2b074587f
```

No target binding, registry database, Git workpad, journal, live provider,
network probe, or Gig creation behavior entered G03. The only runtime
dependency remains Click.

## Completion decision

G03 is locally complete. No acceptance criterion is waived and no frozen
contract byte changed. Hosted CI on the exact G03 commit is the publication
confirmation gate; G04 remains blocked until all source-matrix and built-wheel
jobs pass that commit.

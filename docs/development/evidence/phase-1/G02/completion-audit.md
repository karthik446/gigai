# G02 Completion Audit

- Goal: [G02 — Minimal CLI and Installed Scenario Harness](../../../goals/phase-1/G02-minimal-cli-and-scenario-harness.md)
- Date: 2026-08-03
- Local result: Pass
- Verification host: macOS arm64
- Package version: 0.0.0
- uv version: 0.5.25

## Outcome

GigAI now has one real installed console entry point limited to truthful
`--help` and `--version` behavior, plus a repository-only black-box scenario
harness that invokes installed executables without importing CLI internals.

The harness allocates explicit home, target, workpad, fixture, artifact, and
guard roots. It captures structured process inputs and outputs, exact file and
Git manifests, timing, and normalized reproduction artifacts. It rejects
undeclared filesystem effects, real-home reads, Python network access, and
undeclared subprocesses.

## Acceptance reconciliation

### 1. Wheel install provides the console script and runtime Click

Pass. `pyproject.toml` registers `gigai = "gigai.cli:cli"` and declares
`click>=8.1` in `project.dependencies`; Click no longer appears in the test
extra. The lockfile records Click as a direct runtime dependency.

The wheel was installed without extras into a fresh CPython 3.11 environment.
The install resolved exactly GigAI and Click. Installed metadata reported the
console entry point and `Requires-Dist: click>=8.1`. See the
[installed-wheel transcript](installed-wheel-check.txt).

### 2. Installed help and version are truthful

Pass. Both the installed-wheel verifier and the scenario harness invoke the
generated executable through `subprocess.run(..., shell=False)`.

- `gigai --help` exits zero and advertises only `--help` and `--version`.
- `gigai --version` exits zero and reports `gigai 0.0.0`.
- Click resolves that version from installed distribution metadata through
  `package_name="gigai"`; no package `__version__` or duplicate literal exists.

The normalized [help](scenario-help.json) and
[version](scenario-version.json) records preserve the exact observed output.

### 3. No operational command or success path is exposed

Pass. The installed verifier and process tests prove that:

- bare `gigai` exits 2 with an explicit no-operational-command error;
- `gigai init` exits 2 as an unexpected argument;
- help has no `Commands:` section; and
- none of the planned command names appear in help.

`src/gigai/cli.py` contains no setup, initialization, validation, journal,
creation, or other later-goal behavior.

### 4. Scenarios invoke the installed distribution

Pass. Product behavior tests resolve the generated console script beside the
active environment's interpreter and execute it as a child process. They never
import `gigai.cli` or a Click command object.

The wheel CI lane additionally sets `GIGAI_TEST_EXECUTABLE` to the executable
inside the fresh wheel environment and runs the same installed-help scenario.
The local equivalent passed and is recorded in the wheel transcript.

### 5. Roots and ambient state are isolated

Pass. Every scenario receives independent home, target, workpad, fixture,
artifact, and guard directories. The child environment is constructed from a
small allowlist instead of copying the parent environment.

Tests prove that ambient `HOME`, `GIGAI_HOME`, `GIGAI_WORKPAD_ROOT`, and an
`OPENAI_API_KEY` are not inherited. The scenario's explicit roots replace the
first three and the credential is absent. A direct read under the real home is
rejected and recorded as `real_home_access`.

### 6. Before/after manifests include content and Git state

Pass. `TreeManifest` records relative path, type, permission mode, byte count,
SHA-256 content identity, and symlink target. Git state records HEAD, symbolic
branch, porcelain status, and SHA-256 identities for working-tree and staged
binary diffs without walking `.git` internals.

Tests prove an exact file edit changes both the content digest and Git digest,
and that unchanged installed help/version scenarios preserve byte-identical
target, workpad, home, and fixture manifests. The durable scenario artifacts
contain both sides of each comparison.

### 7. Network and undeclared writes fail closed

Pass. A child-only `sitecustomize` audit hook rejects and records Python socket
events, real-home access, writes outside declared roots, and subprocesses not
named in the scenario contract. A separate exact-manifest allowlist catches
writes inside target, workpad, or home that were possible but undeclared.

Negative tests prove rejection of:

- DNS/network access;
- a real-home file read;
- a write outside every declared writable root;
- an undeclared external network-client process; and
- a target write not named in the expected change set.

The equivalent target write passes only when its exact relative path is
declared. This guard is for the Python GigAI process; later container acceptance
lanes retain the V14 requirement for operating-system-level `--network none`
proof.

### 8. Recording boundaries require structured argv

Pass. Deterministic editor, adapter, and tool substitutes record JSON arrays of
arguments. Their invoker rejects strings and NUL-containing values, always
passes a sequence to `subprocess.run`, and fixes `shell=False`.

Tests preserve spaces and a literal shell-substitution string without
execution. An integrated scenario proves only the exact declared recording
executable is admitted by the subprocess guard.

### 9. Python and non-Python repositories use equivalent fixtures

Pass. Tracked Python and JavaScript fixture repositories are staged through the
scenario fixture root, copied into a distinct target root, initialized as
deterministic Git repositories, and observed through the same manifest path.
Both installed-help scenarios preserve clean `main` branches and unchanged
file/Git identities.

### 10. Failure artifacts are reproducible and sanitized

Pass. Every scenario writes normalized JSON containing structured argv,
selected environment, stdout, stderr, exit code, timeout state, nanosecond
duration, guard events, violations, and all before/after manifests.

Paths are represented by stable tokens such as `$TARGET`, `$WORKPAD`, `$HOME`,
and `$RUNTIME_1`. Credential-shaped environment values are replaced with
`<redacted>`. Negative tests inject sentinel workstation and credential values
and prove neither appears in persisted artifacts or the raised failure text.

## Additional verification

### Locked source matrix

| Interpreter | Collected | Result |
|---|---:|---|
| CPython 3.11 | 109 | 109 passed |
| CPython 3.12 | 109 | 109 passed |
| CPython 3.13 | 109 | 109 passed |

Each run used:

```text
uv run --isolated --locked --extra test --python <version> pytest -q
```

The 109 tests comprise 59 G01 production tests, 19 G02 CLI/harness tests, 14
contract tests, and 17 Phase 0 tests. Pytest additionally reported 22 subtests.

### Built artifacts

`uv build` produced both distributions. The wheel has 20 entries: the prior
G01 inventory, `gigai/cli.py`, and the entry-point metadata. It contains no
tests or research. All three installed verifiers pass.

`MANIFEST.in` excludes repository tests from the sdist because those tests
deliberately depend on research evidence and scenario support that are not
published. This closes the prior partial-test-artifact finding rather than
shipping a non-runnable subset.

### Frozen contracts

All eight schema checksum validations pass. No frozen schema or canonical
vector changed. The canonical-vector digest remains:

```text
14461cff88552b9ec1a86b02f47619208d8a50c952a73e43e09407d2b074587f
```

## Completion decision

G02 is locally complete. No acceptance criterion is waived, no later command
behavior is exposed, and no frozen contract byte is changed.

Hosted CI on the exact G02 goal commit is the publication confirmation gate.
G03 and G07 remain blocked until that pushed commit passes every source-matrix
and built-wheel job.

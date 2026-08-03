# GigAI `check` versus `doctor` CLI spike

**Date:** 2026-07-30
**Status:** research spike complete; command contract recommended
**Scope:** command naming, responsibility boundary, composition, output, and exit behavior
**Parent plan:** `docs/architecture/v14-implementation-plan.md`
**Related research:** `docs/research/runtime-contract-hardening.md`

This is a documentation spike. It compares established public CLIs and proposes
a testable GigAI command contract. It does not change the implementation plan or
implement either command.

## 1. Question

Are `gigai check` and `gigai doctor` redundant? If both remain, what exactly
does each command own, what should be shared, and when should a user run them?

The concern is valid because the current plan gives both commands responsibility
for imports and configuration, and assigns local database compatibility to
`check`. Without a sharper boundary, users would reasonably ask why two
preflight commands exist.

## 2. Short answer

Keep both commands, but change the boundary:

```text
gigai check   -> Is this GigAI project/workflow definition valid?
gigai doctor  -> Can this installation and machine execute it live now?
```

The commands must not contain two independent implementations of the same
validation. `doctor`, `plan`, `rehearse`, and `run` reuse the project validator
owned by `check`. Users do not have to invoke `check` manually before every
other command.

The current plan should move state/database compatibility out of `check` and
into `doctor`. Role validation should split in two:

- `check` validates the declared role and capability requirements;
- `doctor` validates that a concrete installed adapter satisfies them.

`doctor` remains offline and zero-token by default. `doctor --live` is the only
diagnostic form allowed to contact a provider or incur provider usage.

## 3. Method and success criteria

This spike reviewed official current documentation for mature developer CLIs
that expose one or more of these concepts:

- project/configuration validation;
- source analysis or compilation checking;
- installation/system health diagnostics;
- environment/debug information;
- composition of validation into a higher-level command;
- machine-readable diagnostics and meaningful exit status.

The spike succeeds if it can establish:

1. a recognizable user-facing distinction between project correctness and host
   readiness;
2. a single primary owner for every check proposed in the GigAI plan;
3. a way for higher-level commands to reuse validation without making users run
   several commands manually;
4. deterministic CI behavior separate from optional live diagnostics;
5. output and exit semantics that can be tested without model spend.

## 4. External CLI evidence

### 4.1 Terraform: reusable validation versus run-context planning

[`terraform validate`](https://developer.hashicorp.com/terraform/cli/commands/validate)
checks whether configuration is syntactically valid and internally consistent.
The official documentation explicitly says it does not validate remote services
and is safe to run automatically in an editor or CI.

Terraform also demonstrates composition: `terraform plan` includes an implied
validation check. Users may run `validate` directly for fast feedback, but do
not have to remember it before every plan.

Relevant lessons for GigAI:

- validation is project/configuration-facing;
- it should be safe and useful in CI;
- a higher-level command should reuse it automatically;
- machine-readable output needs an explicit format version.

### 4.2 Poetry: project consistency versus environment debugging

[`poetry check`](https://python-poetry.org/docs/cli/#check) validates
`pyproject.toml` and its consistency with `poetry.lock`. Poetry documents it as
available for pre-commit use and provides a strict mode for promoting warnings
to failures.

The same CLI keeps environment investigation under
[`poetry debug info`](https://python-poetry.org/docs/cli/#debug-info), which
reports Poetry and virtual-environment information. It does not overload
`check` with every property of the current host.

Relevant lessons:

- `check` is a natural name for repository-owned consistency;
- warning severity and strict CI behavior should be explicit;
- the diagnostic command need not be called `doctor`, but it is a separate
  concern.

### 4.3 Cargo: fast project checking with stated limitations

[`cargo check`](https://doc.rust-lang.org/cargo/commands/cargo-check.html)
checks a local package and its dependencies without performing final code
generation. Cargo explicitly documents that some diagnostics can only appear
during the omitted stage.

Relevant lessons:

- a fast check can remain valuable without claiming to prove everything;
- the command should state its blind spots rather than absorb full execution;
- project/package selection belongs naturally on `check`.

This matches GigAI's need to say that trusted Python import and structural
validation do not prove every runtime path or side effect.

### 4.4 uv: `check` is project-facing

The current [`uv check`](https://docs.astral.sh/uv/reference/cli/#uv-check)
command runs checks on project Python code. Its exact behavior differs from
GigAI and may synchronize a project environment, so it is not a model for
GigAI's offline guarantee. It is still evidence that users recognize `check` as
an operation over project content rather than general installation health.

Relevant lesson: familiar naming is useful, but GigAI must define its own effect
contract instead of inheriting assumptions from another CLI's verb.

### 4.5 Flutter: project analysis and installed-tooling diagnosis coexist

Flutter exposes both concepts in one CLI. Its
[`flutter analyze`](https://docs.flutter.dev/reference/flutter-cli) command
analyzes project source, while `flutter doctor` reports information about
installed tooling.

This is the closest direct precedent for GigAI:

```text
project source/workflow  -> analyze/check
installed toolchain      -> doctor
```

The names are not redundant because they inspect different subjects.

### 4.6 npm: `doctor` diagnoses prerequisites outside package correctness

[`npm doctor`](https://docs.npmjs.com/cli/v12/commands/npm-doctor/) checks
whether the npm installation has what it needs: Node and Git executability,
registry connectivity, configured registry, directory permissions, and cache
integrity. The documentation says it diagnoses conditions outside npm's own
code that can prevent the tool from working.

Relevant lessons:

- `doctor` is a familiar home for executable, connectivity, permission, and
  cache checks;
- diagnostic checks should report remediation, not merely a boolean;
- network checks must be clearly classified. Unlike npm, GigAI keeps them out
  of the default command because a model-provider probe can have privacy and
  billing consequences.

### 4.7 Homebrew: doctor is system diagnosis, not a project validator

The [`brew doctor`](https://docs.brew.sh/Manpage)
manual describes the command as checking the system for potential problems. It
can list individual checks, run selected checks, and returns nonzero when it
finds potential problems. Homebrew's troubleshooting guide asks users to attach
`brew config` and `brew doctor` output when reporting issues.

Relevant lessons:

- stable check identifiers help support and targeted reruns;
- diagnostic output is an evidence bundle for troubleshooting;
- warnings need context because not every host warning blocks every operation;
- output intended for issue reports must be sanitized.

### 4.8 mise: read-only doctor with JSON output

[`mise doctor`](https://mise.jdx.dev/cli/doctor.html) is documented as a
read-only installation check and offers JSON output. It can report a concrete
problem such as an uninstalled plugin.

Relevant lessons:

- default doctor behavior can be read-only;
- structured output is useful for automation and support;
- checks should identify the exact missing runtime component.

## 5. Comparative result

| CLI | Project/config command | Host/install command | Boundary demonstrated |
|---|---|---|---|
| Terraform | `validate` | separate init/provider diagnostics | configuration consistency versus remote/run context |
| Poetry | `check` | `debug info` | project metadata/lock consistency versus environment information |
| Cargo | `check` | n/a | fast package correctness with documented omissions |
| uv | `check` | separate environment commands | project code versus environment operations |
| Flutter | `analyze` | `doctor` | project source versus installed tooling |
| npm | n/a | `doctor` | executable, registry, permissions, and cache health |
| Homebrew | scoped checks/audits | `doctor` | package definitions versus system problems |
| mise | config/tool operations | `doctor` | configured tools versus installation problems |

The research does not show that every CLI must have a command literally named
`doctor`. It shows a stable semantic split:

```text
artifact validity != execution-environment readiness
```

Mature CLIs either use separate commands for those subjects or keep one subject
out of scope. They do not make a CI validator unpredictably depend on network,
credentials, local cache health, and every optional tool.

## 6. Findings against the current GigAI plan

The existing plan is directionally correct but has four overlaps.

### 6.1 State/database compatibility is assigned to the wrong command

The plan says `gigai check` checks state/database schema compatibility. That is
machine-local runtime health. A clean checkout in CI may intentionally have no
GigAI state database.

Recommendation: move database existence, migration compatibility, WAL support,
writeability, and state-directory size to `doctor`.

`check` may validate migration definitions as package tests, but it does not
inspect or mutate a user's live state database.

### 6.2 Role binding combines declaration and resolution

The plan says `check` validates role bindings without calling providers. This
needs two layers:

```text
check:
  role name exists
  required capabilities are well-formed
  workflow references a declared role

doctor:
  configured model/adapter exists on this machine
  installed version was capability-probed
  concrete adapter satisfies the role requirements
```

This permits a public workflow package to pass CI without every contributor
having every provider CLI or credential.

### 6.3 Imports and configuration appear under both commands

Project package import/discovery belongs to the project validator. `doctor`
should invoke that shared validator when it is run inside a GigAI project and
display its result as one diagnostic section. It must not reimplement import or
schema checks.

Global installation diagnosis must still work outside a project. In that case,
the project section is `SKIP`, not `FAIL`.

### 6.4 A live run should not require a memorized command ritual

Documentation may recommend `check` during authoring and `doctor` during setup,
but `gigai run` must execute its required preflight checks itself. This follows
Terraform's composition precedent.

Explicit commands still matter:

- `check` gives fast editor, pre-commit, and CI feedback;
- `doctor` gives a focused troubleshooting report;
- `run` reuses only the checks relevant to its resolved workflow and adapters.

## 7. Recommended GigAI contract

### 7.1 `gigai check [<workflow>]`

Primary subject: repository-owned GigAI definitions.

Properties:

- deterministic for the same source, configuration files, and package set;
- zero token;
- no provider, network, target-tool, Snowflake, or AWS invocation;
- no credential requirement;
- no writes except explicitly documented disposable interpreter caches, which
  should be redirected to GigAI scratch or disabled;
- suitable for editor, pre-commit, and CI use;
- supports human and versioned JSON diagnostics.

Owned checks:

1. configured workpad packages import under the trusted-authoring contract;
2. workflows and tools are discoverable;
3. names and aliases are unique;
4. workflow inputs and outputs are valid supported Pydantic types;
5. tool inputs, outputs, declared effects, and execution definitions are valid;
6. workflow role names and required capability declarations are coherent;
7. referenced fixtures, eval suites, prompts, resources, and tools exist;
8. generated catalogs and schemas match their declared source hashes;
9. fixture schemas match current public contract versions;
10. planning diagnostics flag known unsupported result-inspection patterns;
11. ordinary tests do not resolve a live adapter by construction.

Not owned:

- whether Codex, Claude, Git, or another binary is installed;
- whether a provider CLI version still has a required capability;
- whether the user is authenticated;
- whether a network endpoint is reachable;
- whether the local GigAI state directory or SQLite database is healthy;
- whether a configured enforced-sandbox backend is available;
- whether a live target currently satisfies run-specific preconditions.

Important limitation: Python package import can execute module-level Python.
GigAI itself makes no live calls during `check`, and the trusted-workpad contract
forbids import-time external effects, but v1 does not claim that importing
arbitrary malicious Python is side-effect-proof.

### 7.2 `gigai doctor [--workflow <workflow>]`

Primary subject: this GigAI installation and host.

Default properties:

- read-only and zero-token;
- no model or provider network call;
- safe credential-presence detection without printing secret values;
- runnable outside a project;
- scoped to a workflow when requested so unused optional adapters do not block;
- supports human and versioned JSON diagnostics.

Owned checks:

1. GigAI, Python, and uv versions and supported ranges;
2. resolved executable paths and exact versions for configured adapters/tools;
3. cached capability evidence freshness for those exact versions;
4. authentication presence using a safe provider-native status mechanism when
   one exists, without revealing tokens or session identifiers;
5. state and artifact paths, ownership, writeability, available space, and size;
6. SQLite openability, schema/migration compatibility, foreign-key mode, and
   WAL capability;
7. platform and architecture support;
8. availability of the requested enforcement level;
9. stale/interrupted run reconciliation status;
10. project `check` result when a project is present;
11. workflow-specific readiness when `--workflow` is supplied.

Default `doctor` may invoke local `--version`, `--help`, or provider-native
authentication-status commands only when their effect contract is known and
tested. Unknown diagnostic behavior is skipped with remediation rather than
executed optimistically.

### 7.3 `gigai doctor --live`

Primary subject: actual provider/integration connectivity.

Additional contract:

- requires an explicit flag and resolved adapter/integration scope;
- prints the exact probes, data exposure, token/cost upper bound, and target
  before confirmation in a TTY;
- requires an additional noninteractive confirmation flag in scripts;
- records probe version, sanitized evidence, usage, and timestamp;
- performs the minimum request needed to validate the claimed capability;
- does not silently fall back to another adapter or model;
- distinguishes authentication, connectivity, capability, schema, timeout, and
  billing-limit failures.

The syntax may begin as:

```text
gigai doctor --live --adapter codex
gigai doctor --live --adapter claude
gigai doctor --live --workflow review
```

Do not make an unscoped `doctor --live` spend against every configured provider.

## 8. Composition model

Use one registry of diagnostic checks, not command-specific copies:

```text
ProjectValidator
  -> imported by check
  -> reused by plan
  -> reused by rehearse
  -> reused by doctor when project is present
  -> reused by run preflight

HostDiagnostics
  -> exposed by doctor
  -> required subset reused by run preflight

LiveProbes
  -> exposed only by doctor --live
  -> never run implicitly by check, plan, rehearse, or ordinary tests
```

Each check has a stable ID, owner, scope, effect class, and remediation. Example
IDs:

```text
project.workflow_names_unique
project.schemas_valid
project.fixture_contract_current
host.state_directory_writable
host.sqlite_schema_compatible
adapter.codex.executable_supported
adapter.codex.capability_evidence_fresh
adapter.claude.authentication_present
live.codex.structured_output_probe
```

Stable IDs make focused tests, support reports, documentation links, and
targeted reruns possible.

## 9. User workflow

Users should not be told to run every preflight command before every action.

```text
during authoring or CI:
  gigai check

after installing GigAI or when live execution fails:
  gigai doctor --workflow review

when cached capability evidence is missing or stale:
  gigai doctor --live --adapter codex

normal execution:
  gigai run review ...
  # internally reuses relevant check and host-readiness gates
```

Example: structurally valid workflow on a machine without Claude:

```text
$ gigai check review
PASS  project.workflow.review

$ gigai doctor --workflow review
PASS  project.workflow.review
PASS  adapter.codex.executable_supported
FAIL  adapter.claude.executable_supported
      Claude CLI was not found on PATH.
      Required because role `challenger` resolves to adapter `claude`.
```

If `challenger` is optional and disabled for the requested run, the missing
Claude adapter should be `SKIP` or `WARN`, not a blocking failure.

## 10. Output and exit contract

Every result uses one of four statuses:

```text
PASS  requirement satisfied
WARN  actionable concern that does not block the selected operation
FAIL  selected operation cannot proceed safely or correctly
SKIP  check is not applicable or was not authorized
```

Recommended exit behavior:

| Condition | Exit |
|---|---:|
| no `FAIL` results | 0 |
| one or more `FAIL` results | 1 |
| command usage error | 2 |
| warning with `--strict` | 1 |

Unexpected internal exceptions must still produce a stable diagnostic envelope;
the implementation may reserve another documented exit code for internal
failure. It must not conflate an invalid project with a GigAI crash.

Both commands should support `--json`. The JSON document contains:

```text
schema_version
command
gigai_version
scope
overall_status
checks[]:
  id
  subject
  status
  summary
  evidence_safe_to_share
  remediation
  duration_ms
```

The JSON schema is versioned separately from the package version. Unknown
additive fields are ignored within a compatible schema version.

Human output ends with a short copyable summary and the command required for
more detail. It never prints credential values, raw provider session IDs, or
private prompt/source content.

## 11. Alternatives considered

### A. Merge everything into `gigai doctor`

Rejected. CI validation would become host-dependent, and project authors would
need provider installations and credentials to validate reusable workflows.

### B. Merge everything into `gigai check`

Rejected. A familiar fast project check would become slow and potentially
networked, and its output would vary across machines.

### C. Keep both exactly as currently written

Rejected. State/database compatibility and concrete adapter resolution blur the
boundary and create duplicate-looking configuration checks.

### D. Rename `check` to `validate`

Viable but not recommended as a necessary change. Terraform shows that
`validate` works; Poetry, Cargo, and uv show that `check` is equally familiar.
GigAI's verb is less important than publishing the subject and effect contract.
Keeping `check` avoids churn.

### E. Rename `doctor` to `debug info`

Rejected for now. `doctor` communicates active checks with PASS/WARN/FAIL and
remediation better than a passive information dump. Poetry's `debug info` is
useful evidence that the diagnostic concern is separate, not that GigAI needs
the same spelling.

### F. Require users to run `check`, then `doctor`, then `run`

Rejected. Explicit commands are for fast feedback and troubleshooting; `run`
must compose its own required preflight gates.

## 12. Decision

Recommend the following decision for incorporation into the GigAI plan:

1. Retain `gigai check` and `gigai doctor`.
2. Define `check` as project/workflow contract validation.
3. Define `doctor` as installation, host, state, and concrete-adapter readiness.
4. Move local state/database health from `check` to `doctor`.
5. Split abstract role validation from concrete adapter resolution.
6. Implement one shared project validator and one host-diagnostic registry.
7. Have `plan`, `rehearse`, `doctor`, and `run` reuse applicable validation;
   never require a manual command chain.
8. Keep default doctor offline, read-only, and zero-token.
9. Put all provider/integration contact behind scoped `doctor --live` consent.
10. Publish stable diagnostic IDs, severity, remediation, JSON schema, and exit
    semantics.

Decision shorthand:

```text
check the workpad; doctor the workstation; preflight automatically.
```

## 13. Consequences

### Positive

- Public contributors can validate workflows in CI without provider accounts.
- Host-specific failures have one predictable troubleshooting command.
- `run` can fail early with the same stable diagnostics users can reproduce.
- The CLI surface remains familiar and does not require a new verb.
- Optional adapters do not make unrelated workflows appear unhealthy.
- Live probes are visible, scoped, auditable, and consented.

### Costs

- Checks need stable identities and dependency metadata.
- The runtime must select only relevant host checks for a resolved workflow.
- Authentication-presence commands require adapter-specific safety research.
- JSON diagnostics become a small public compatibility contract.
- Importing trusted Python remains less pure than validating a declarative file.

### Deliberate limitations

- `check` does not prove arbitrary Python has no import-time side effects.
- Offline `doctor` cannot prove network connectivity or a real provider response.
- `doctor --live` proves only the captured provider/version/capability at that
  time.
- PASS means ready for the selected operation, not that every optional GigAI
  integration on the machine works.

## 14. Acceptance tests for implementation

The command contract is ready to implement when free tests prove:

1. a duplicate workflow name fails `check` with a stable ID;
2. a missing fixture fails `check` without reading provider credentials;
3. a valid project passes `check` when no provider CLI is installed;
4. an incompatible local database fails `doctor`, not `check`;
5. a missing required adapter fails workflow-scoped `doctor`;
6. a missing unused optional adapter does not block the selected workflow;
7. default `doctor` makes no network or model call;
8. `doctor --live` refuses an unscoped or unconfirmed paid probe;
9. `run` surfaces the same diagnostic ID as the equivalent explicit preflight;
10. `--json` conforms to a versioned fixture for PASS, WARN, FAIL, and SKIP;
11. `--strict` promotes warnings to a nonzero exit without changing their
    recorded severity;
12. sanitized doctor output contains no credential or provider session value.

## 15. Revisit when

- users still cannot predict which command owns a failure;
- `check` begins depending on host state to validate ordinary projects;
- default `doctor` needs network access for a required check;
- most support incidents require a different diagnostic entry point;
- a second consumer needs a stable library API for diagnostics;
- the command count becomes a measured usability problem rather than an
  aesthetic concern.

## 16. Sources

### Repository context

- `docs/architecture/v14-implementation-plan.md`, Sections 5 and 9
- `docs/research/phase-0-spikes.md`
- `docs/research/runtime-contract-hardening.md`, Decisions 4 and 6

### Official external documentation

- [Terraform validate](https://developer.hashicorp.com/terraform/cli/commands/validate)
- [Terraform configuration CLI workflow](https://developer.hashicorp.com/terraform/cli/code)
- [Poetry check and debug commands](https://python-poetry.org/docs/cli/#check)
- [Cargo check](https://doc.rust-lang.org/cargo/commands/cargo-check.html)
- [uv check](https://docs.astral.sh/uv/reference/cli/#uv-check)
- [Flutter CLI analyze and doctor](https://docs.flutter.dev/reference/flutter-cli)
- [npm doctor, CLI v12](https://docs.npmjs.com/cli/v12/commands/npm-doctor/)
- [Homebrew manual: doctor](https://docs.brew.sh/Manpage)
- [Homebrew troubleshooting](https://docs.brew.sh/Troubleshooting)
- [mise doctor](https://mise.jdx.dev/cli/doctor.html)

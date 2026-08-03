# GigAI runtime contract hardening research

- **Date:** 2026-07-30
- **Status:** research complete; decisions recommended for incorporation
- **Scope:** safety, source immutability, run durability, planning semantics,
  evidence retention, and public contracts
- **Related plan:** `docs/architecture/v14-implementation-plan.md`
- **Related spikes:** `docs/research/phase-0-spikes.md`

## 1. Question

What is the smallest technically honest way to close the remaining GigAI
runtime-contract gaps without turning GigAI back into a general agent platform?

This record exists so that a future maintainer can tell:

- what problem each decision solves;
- which alternatives were considered;
- what the selected v1 contract does and does not guarantee;
- which evidence and external specifications informed the decision;
- what implementation consequences follow;
- what event should cause the decision to be revisited.

It is a research and decision-rationale artifact. It does not itself modify the
implementation plan.

## 2. Short answer

GigAI should preserve the focused design and make six contract decisions:

1. Treat ordinary subprocess execution as **containment, not sandboxing**. V1
   accepts only trusted workpad tools, exposes the actual enforcement level, and
   never claims that metadata alone prevents writes or network access.
2. Execute workflow and tool code from a **run-scoped immutable source
   snapshot**, not from the mutable authoring workpad.
3. Give runs and calls a **small durable lifecycle with explicit attempts and
   crash reconciliation** before creating the first SQLite migration.
4. Define `plan` as a **best-effort observed path**, never as an exact static
   proof of arbitrary Python. Hard budgets and rehearsal carry the guarantees
   that planning cannot.
5. Keep **sanitized primary probe evidence** in the repository and derive
   summaries from it. A file containing asserted booleans is not the primary
   evidence for those booleans.
6. Since GigAI is public, explicitly version the **small public contract** while
   keeping plugin discovery and compatibility promises narrower than the
   internal module tree.

These decisions are intentionally less ambitious than implementing containers,
a durable scheduler, static Python verification, SLSA compliance, or a plugin
marketplace in v1.

## 3. Decision method

The decisions use four tests:

1. **Honesty:** the documented guarantee must match what the runtime can enforce.
2. **Replayability:** a completed or interrupted run must be explainable from
   durable identifiers, exact source bytes, artifacts, and state transitions.
3. **Narrowness:** new machinery must be required by the first `review` caller,
   not by a hypothetical hosted platform.
4. **Public maintainability:** an external contributor must be able to identify
   the stable contract, reproduce evidence, and understand when a decision may
   change.

The book summary's durable premise remains the architectural anchor: the model
is a fallible policy inside deterministic, observable, permissioned, and
evaluated software. The decisions below apply that premise to the deliberately
smaller GigAI product.

---

## 4. Decision 1 — Separate process containment from sandbox enforcement

### 4.1 Problem

The Phase 0 tool spike proves that a one-shot child process can isolate Python
interpreter state, contain a crash, receive a controlled current directory and
environment, capture output, and be terminated as a process group. Those are
valuable runtime properties.

They are not a filesystem or network sandbox.

Python's subprocess interface defines `cwd`, `env`, file-descriptor handling,
timeouts, and process-session controls. It does not claim that those controls
restrict which host paths or network destinations the child can access. An
omitted `env` inherits the parent environment; a supplied mapping replaces it.
The documentation also recommends argv sequences and a fully qualified
executable for reliable invocation. See the
[Python subprocess documentation](https://docs.python.org/3/library/subprocess.html).

Declaring a tool `READ_ONLY` therefore records intent and enables policy checks,
but it does not make the callable read-only. A post-run Git check detects only
the surfaces it measures and acts after the attempted change.

### 4.2 Alternatives considered

#### A. Call the existing subprocess boundary a sandbox

Rejected. It would turn a convention into a security claim. A child can still
open any path and socket allowed to the parent account.

#### B. Require containers for every v1 tool

Rejected for the first release. Containers can provide separate filesystems,
process trees, and networks, but their safety depends on mounts, network mode,
capabilities, daemon configuration, and image provenance. Docker documents both
the isolation model and the risks of unsafe host mounts and privileged modes in
its [container security documentation](https://docs.docker.com/engine/security/)
and [container run documentation](https://docs.docker.com/engine/containers/run/).
Making Docker mandatory would also make the first local Python workflow depend
on a second runtime and image lifecycle.

#### C. Implement one cross-platform host sandbox immediately

Rejected as a v1 requirement. Available mechanisms do not form one portable
contract. Linux can use mechanisms such as
[Landlock](https://www.kernel.org/doc/html/latest/userspace-api/landlock.html) or
[bubblewrap](https://github.com/containers/bubblewrap), while macOS and Windows
require different integration choices. Each backend has capability and version
limits that must be probed, not assumed.

#### D. Trust arbitrary downloaded workflow tools

Rejected. A public repository does not imply that unreviewed third-party Python
should execute with the user's account authority.

### 4.3 Decision

V1 has three explicit execution-enforcement levels:

```text
native     provider CLI read-only mode is requested and capability-probed
observed   trusted tool runs with containment plus before/after verification
enforced   an OS/container sandbox backend enforces the declared policy
```

The initial release supports:

- `native` for provider CLIs whose installed-version probe passes;
- `observed` for trusted `PythonTool` and `CommandTool` implementations;
- no built-in promise that `enforced` is available.

Every live-run header and manifest records the enforcement level per model and
tool call. A workflow may require a minimum level. If the host cannot supply it,
the run fails before execution; it never silently downgrades.

V1 tool trust is:

```text
trusted:   installed GigAI code, configured workpad code, reviewed dependencies
untrusted: target source/docs, model output, retrieved content, tool input data
```

Untrusted target content never registers executable Python. V1 registers no
tool intended to mutate the target or an external system.

### 4.4 Required observed-mode controls

Observed mode still applies every cheap control the subprocess boundary can
reliably provide:

- structured argv and `shell=False`;
- resolved executable path;
- controlled cwd;
- environment allowlist constructed from empty, rather than inherited ambient
  environment;
- `close_fds=True`, with no unexpected passed descriptors;
- separate stdout and stderr;
- timeout, graceful process-group termination, then forced termination;
- run-scoped scratch directory;
- typed JSON input/output with size limits;
- before/after target verification;
- a recorded statement that prevention was not enforced.

For a Git target, before/after verification includes:

- HEAD and index/tree identity;
- exact status using a machine-readable, NUL-delimited form;
- hashes of modified tracked files;
- the path and content hash of every nonignored untracked file present before
  and after the run.

This catches more than a tracked-tree check. It is still detection, not
prevention, and the documentation must say so.

Verification has configured path-count and byte ceilings so a target with a
large generated tree cannot consume unbounded time or storage. Crossing a
ceiling produces an explicit `incomplete` postcondition and fails any workflow
that requires a clean target; GigAI never silently truncates the observation.

### 4.5 Public contract consequence

Effect metadata is not named `Permissions` without qualification. The public
record separates:

```text
declared_effects
requested_policy
enforcement_level
observed_postcondition
```

That prevents a public consumer from interpreting a declaration as proof.

### 4.6 Revisit when

- a workflow must execute third-party or model-generated tool code;
- target confidentiality requires enforced egress denial;
- observed-mode postconditions catch an attempted write;
- two real consumers need the same platform sandbox backend;
- a public claim requires enforced rather than observed read-only behavior.

---

## 5. Decision 2 — Execute from a run-scoped immutable source snapshot

### 5.1 Problem

The workpad is intentionally writable during authoring. If the live runtime
imports a workflow, hashes files, and later starts a tool subprocess that imports
from the workpad again, the child may execute different bytes from those
recorded at run start.

A Git SHA is insufficient because the workflow may be modified or untracked.
A Git diff is also insufficient because it is not the full byte content of every
untracked input. A lockfile improves dependency reproducibility but does not
freeze the workpad source tree.

The provenance problem is conceptually similar to software build provenance:
identify the invocation, resolved inputs and dependencies, execution platform,
outputs, and their digests. GigAI does not need to claim SLSA compliance, but the
[SLSA provenance model](https://slsa.dev/spec/v1.2/build-provenance) and
[in-toto Statement model](https://github.com/in-toto/attestation/blob/main/spec/v1/statement.md)
provide useful vocabulary for immutable subjects, resolved dependencies,
invocation identity, builder version, and byproducts.

### 5.2 Alternatives considered

#### A. Record only Git SHA and dirty status

Rejected. It cannot recover exact modified and untracked bytes.

#### B. Hash before execution but import from the workpad

Rejected. The hash can become stale before a later worker import.

#### C. Snapshot only tools after they are called

Rejected. Capturing after execution can record different bytes from those the
worker imported.

#### D. Copy the entire repository

Rejected for v1. It captures irrelevant or sensitive files and makes artifact
growth depend on repository size.

#### E. Build a wheel for every run

Deferred. It creates a build lifecycle and still requires explicit treatment of
prompt/resource files and consumer-owned tool code.

### 5.3 Decision

Every `run`, paid `eval`, and authoritative `rehearse` creates a run-scoped
source snapshot before any model or live tool call.

The snapshot contains exact bytes for:

1. the resolved workflow package;
2. every tool definition available to that workflow at launch;
3. declared prompts, schemas, and resources;
4. `pyproject.toml` and `uv.lock` when present;
5. generated contract schemas used by the run.

The runtime executes the workflow and Python tool workers from this snapshot.
The mutable workpad is not on the worker import path.

The process is:

```text
discover trusted workpad objects
        ->
resolve complete snapshot file set
        ->
copy bytes while checking for concurrent change
        ->
hash and verify copied bytes
        ->
seal source manifest
        ->
execute only from snapshot
        ->
finalize run manifest with actually used tools and artifacts
```

### 5.4 Concurrent-change rule

For each source file, the snapshotter records file identity and metadata before
and after reading, then verifies the resulting hash against a second stable read
when metadata changed during capture. A symlink that resolves outside the trusted
workpad is rejected. Any file that cannot be captured consistently causes
preparation to fail; GigAI does not launch and hope.

An advisory workpad lock may reduce accidental concurrent GigAI authoring, but it
is not treated as sufficient because editors and other processes do not honor it.

### 5.5 Two manifests

`source-manifest.json` is sealed before execution and contains:

- schema version;
- run UUID;
- workpad identity and Git HEAD when available;
- source-relative path, role, byte length, media type, and SHA-256;
- workflow and discoverable tool identities;
- Python, GigAI, uv, platform, and installed-distribution versions;
- hashes of `pyproject.toml` and `uv.lock`;
- snapshot creation timestamps and result.

`run-manifest.json` is finalized after execution and references:

- the immutable source-manifest hash;
- actual model, tool, and repair attempts;
- tools actually called;
- rendered prompts and result artifact hashes;
- target/reference identities;
- terminal verification and status.

The separation avoids pretending the runtime knows the used-tool subset before
ordinary Python executes.

### 5.6 Canonical hashing

The manifest schema defines a canonical byte representation before hashing.
[RFC 8785](https://www.rfc-editor.org/rfc/rfc8785.html) explains why JSON used
for cryptographic hashes needs invariant serialization. GigAI may adopt JCS or a
smaller versioned canonical serializer limited to its manifest value types, but
must not hash whichever pretty-printed representation happens to be emitted.

### 5.7 Dependency claim

Capturing `uv.lock` and installed distribution versions makes a run explainable;
it does not promise that every native wheel, system library, remote service, or
model will remain available forever.

`uv run` normally may update the project lock and environment. For an execution
snapshot, GigAI should first require an up-to-date lock with `uv lock --check`
or use `uv run --locked`; it must not mutate dependency resolution as a side
effect of running the workflow. The relevant behavior is documented in
[uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/).

### 5.8 Revisit when

- snapshot size or latency becomes material in measured runs;
- workflows legitimately require dynamic undeclared resources;
- dependency conflicts require pack-specific environments;
- a public reproducible-build or signed-attestation claim is proposed;
- native dependencies become necessary to reproduce evaluation results.

---

## 6. Decision 3 — Define a minimal durable run and call lifecycle

### 6.1 Problem

SQLite can atomically persist a transition, but it cannot make a filesystem
artifact write and a paid provider call part of the same transaction. The
runtime therefore needs explicit states and ordering so a crash is explainable.

WAL mode lets readers and a writer proceed concurrently, but still permits only
one writer and requires local-host shared memory. The WAL file is part of the
database's persistent state and must stay with the database during copying or
recovery. See the [SQLite WAL documentation](https://www.sqlite.org/wal.html).
Transactions provide the atomic boundary for related ledger changes; they do
not encompass an external model process or HTTP request. See the
[SQLite transaction documentation](https://www.sqlite.org/lang_transaction.html).

Retries create a second problem: after a timeout, the caller may not know
whether the provider completed and charged the request. AWS's idempotency
guidance recommends a caller-provided request identifier and atomic recording
when a service supports that contract. Provider CLIs do not expose one common
idempotency contract. See
[Making retries safe with idempotent APIs](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/).

### 6.2 Alternatives considered

#### A. Store only a final run row

Rejected. A process crash leaves no durable indication of what started or what
may have spent tokens.

#### B. Automatically retry every interrupted call

Rejected. An ambiguous provider timeout can turn one intended call into two paid
calls, and native model CLIs do not provide one common idempotency key.

#### C. Implement a daemon and resumable scheduler

Rejected for v1. Foreground execution still needs durable attempts and recovery,
but it does not need queue ownership, leases, or background wake-up.

#### D. Put all payloads in SQLite

Rejected. Large prompts, responses, raw events, source, and tool output remain
filesystem artifacts; SQLite stores metadata and relationships.

### 6.3 Decision

Use the following v1 states.

```text
run:
  preparing -> running -> verifying -> succeeded
       |          |           |
       +----------+-----------+-> failed | cancelled | interrupted

call:
  prepared -> started -> succeeded
      |          |
      +----------+-> failed | cancelled | interrupted
```

There is no `queued` state until detached execution exists. There is no generic
`waiting_for_user` state until a real workflow requires human continuation.

Every external invocation has:

- `call_id`: stable logical call within the run;
- `attempt_id`: unique UUID for one execution attempt;
- `attempt_number`: monotonic within the call;
- `request_hash`: canonical hash of the exact request envelope;
- start and terminal timestamps;
- adapter/tool identity and version;
- process identity when applicable;
- artifact references;
- terminal classification and retry disposition.

For a local process, process identity is more than a bare PID: record the PID
and, where the host exposes it, a process start-time or boot-scoped identity.
Recovery must not mistake an unrelated process that reused the PID for the
original attempt owner.

### 6.4 Write ordering

Before a provider or tool process starts:

1. Create the run in `preparing` and commit.
2. Seal and record the source manifest.
3. Move the run to `running` and commit.
4. Create the call and `prepared` attempt with request hash and artifact
   destination; commit.
5. Move the attempt to `started`; commit.
6. Launch the external process.

On completion:

1. Stream raw output into a temporary artifact in the run directory.
2. Flush, close, hash, and atomically rename the artifact.
3. In one SQLite transaction, insert artifact metadata, terminate the attempt,
   terminate or advance the call, and append the corresponding run event.

If the process crashes after the final file rename but before the database
transaction, the file is an orphan and may be reconciled or collected later.
The database never points at a partially written final artifact.

### 6.5 Startup recovery

On the next GigAI command that opens the project state:

1. Find nonterminal runs and attempts whose owner process is no longer alive.
2. Mark their active attempts `interrupted` in one transaction.
3. Mark the run `interrupted` unless a deterministic reconciliation rule proves
   a later state.
4. Preserve every artifact and diagnostic; do not automatically relaunch.
5. Surface the exact inspection and explicit rerun command.

An interrupted model attempt is considered ambiguous unless a complete,
validated response artifact and matching request hash were durably attached.
Ambiguous attempts never retry automatically.

### 6.6 Retry rules

- Contract validation failures are not retried unchanged.
- A structured-output repair is a separate recorded model call, not a hidden
  continuation of the first attempt.
- Deterministic read-only tools may retry only when their contract declares the
  operation idempotent and the policy sets a finite retry bound.
- Model adapters may retry a pre-launch failure that proves no provider process
  or HTTP request started.
- All other model retry is explicit user action and creates a new attempt.
- Reusing an `attempt_id` with a different `request_hash` is rejected.

### 6.7 SQLite operating contract

- foreign keys enabled on every connection;
- local filesystem only;
- one process-local serialized writer;
- WAL mode verified rather than assumed;
- bounded busy timeout and classified `SQLITE_BUSY` failures;
- numbered forward-only migrations using `PRAGMA user_version`;
- no transaction held open while waiting for a model or tool;
- database, `-wal`, and `-shm` treated as one live state set for backup purposes;
- recovery and migration tests use process termination, not only Python
  exceptions.

### 6.8 Trace compatibility without trace infrastructure

A run is the trace; each model/tool/repair attempt is a span-shaped record with
parent identity, timestamps, attributes, events, artifacts, and status. This is
compatible with the basic
[OpenTelemetry trace model](https://opentelemetry.io/docs/concepts/signals/traces/)
without requiring an exporter or telemetry backend in v1.

### 6.9 Revisit when

- detached/background runs become a real requirement;
- multiple GigAI writer processes must coordinate;
- provider APIs expose dependable idempotency keys;
- a workflow requires human suspension and later continuation;
- remote/shared state replaces the machine-local project ledger.

---

## 7. Decision 4 — Make `plan` explicitly best-effort

### 7.1 Problem

A Python object can intercept many operations through data-model methods, but it
cannot redefine object identity. Python specifies `is` and `is not` as identity
tests, while truth conversion is separately customizable with `__bool__`. See
the [Python expression reference](https://docs.python.org/3/reference/expressions.html#is-not).

Therefore a planning placeholder can raise a useful `CaseRequired` for:

- boolean conversion;
- supported comparisons;
- attribute or item access;
- iteration and length;
- selected arithmetic or conversion operations.

It cannot prevent this branch from resolving silently:

```python
result = await run.tool(...)
if result is None:
    ...
```

Nor can a wrapper guarantee interception after arbitrary helper functions,
native extensions, serialization libraries, reflection, or identity-based
logic. Static AST inspection can flag common syntax using Python's
[`ast.NodeVisitor`](https://docs.python.org/3/library/ast.html#ast.NodeVisitor),
but sound whole-program taint analysis over arbitrary imports is not a v1 tool.

### 7.2 Alternatives considered

#### A. Claim that placeholders make arbitrary Python statically plannable

Rejected as technically false.

#### B. Replace Python workflows with a declarative graph DSL

Rejected. It would reverse the central GigAI decision and rebuild orchestration
syntax that ordinary Python already supplies.

#### C. Require workflow authors to provide a second declarative plan

Rejected for v1. The duplicated representation can drift from execution and
would require its own currentness proof.

#### D. Drop `plan`

Rejected. A zero-effect preview still provides useful model resolution,
permissions, known calls, missing configuration, source identity, and an
observed call prefix.

### 7.3 Decision

`gigai plan` is a **best-effort observed path for the supplied inputs**, not an
exact control-flow proof.

Every result is labelled:

```text
planning_semantics: best_effort_python_v1
path_status: observed | blocked_on_result
authoritative: false
```

`PlanValue` implements common inspection hooks so normal result-dependent code
fails early with `CaseRequired` and the producing call identity. `check` also
flags common unsupported result-inspection syntax, including identity tests,
subscripts not covered by the placeholder, pattern matching, and conversions.
Those diagnostics improve usability; they are not marketed as a proof that no
hidden result-dependent branch exists.

Only `rehearse --case ...` executes an authoritative zero-token path with real
fixture values.

### 7.4 Budgets do not come from the observed plan

An observed path cannot safely define an upper bound for loops or dynamic
branches. Workflow and run policy therefore provide separate hard limits:

- maximum model calls;
- maximum tool calls;
- maximum repair calls;
- maximum wall time;
- maximum output/artifact bytes;
- token and monetary limit where the adapter can enforce one;
- cancellation behavior when any limit is reached.

`plan` may estimate the observed path, but the live runtime enforces hard limits
independently.

### 7.5 CLI language

Use:

```text
Observed zero-effect path for these inputs.
This is not a proof of every path through arbitrary Python.
Use rehearsal with a case to execute an authoritative fixture-backed path.
```

Do not use `complete plan`, `exact plan`, or `static proof` for ordinary Python
workflow output.

### 7.6 Revisit when

- users repeatedly misunderstand best-effort planning;
- a measured workflow needs exact preflight cost calculation;
- authors ask for a restricted, statically analyzable workflow subset;
- maintaining planning placeholders costs more than their diagnostic value.

---

## 8. Decision 5 — Preserve auditable, sanitized primary spike evidence

### 8.1 Problem

A public evidence package must allow a reviewer to trace a conclusion back to
the captured observation. A fixture that says `capability=true` and tests that
the same field remains `true` verifies file shape, not the original provider
behavior.

Temporary raw reports also break the review chain when the machine is cleaned or
the repository is copied. Conversely, committing unsanitized provider output can
leak session IDs, nonces, paths, prompts, credentials, or sensitive source.

### 8.2 Alternatives considered

#### A. Commit only prose conclusions

Rejected. It leaves no machine-checkable path from evidence to conclusion.

#### B. Commit raw reports without redaction

Rejected. Public reproducibility does not justify publishing secrets or durable
provider session identifiers.

#### C. Retain only a hash of private raw output

Rejected as sufficient public proof. A hash can show that a private file did not
change, but a public reviewer still cannot inspect what it contained.

#### D. Require every contributor to rerun paid probes

Rejected. Ordinary tests must remain free, and historical decisions need their
original version-stamped evidence.

### 8.3 Decision

Each binding spike keeps this repository-relative package:

```text
spikes/<spike-id>/
  README.md
  probe.py
  fixtures/
  evidence/
    raw-sanitized.json
    summary.json
    redactions.json
  tests/
```

The exact final organization may change, but the relationships do not.

`raw-sanitized.json` contains the smallest retained primary observation needed
to audit the result:

- probe schema version;
- capture timestamp;
- executable path and exact version output;
- sanitized argv;
- working-directory role, not a private absolute path;
- exit code and elapsed time;
- structured provider envelope or event sequence;
- structured output;
- usage field names and values where safe;
- observed filesystem postcondition;
- explicit omitted fields.

`redactions.json` identifies each redaction class and count without retaining
the removed value:

```text
session_id
nonce
credential
private_absolute_path
sensitive_prompt_or_source
```

`summary.json` is generated from `raw-sanitized.json`. Tests recompute the
summary and fail if the derived capability results differ. Hand-authored
booleans are not accepted as the only evidence.

### 8.4 Redaction rules

- Sanitize structurally by field/path before serializing; do not depend only on
  regex over a finished log.
- Replace secret values with typed markers such as
  `<redacted:session_id>`, not an empty string that changes shape.
- Scan the final committed bytes for known nonce/session/key material.
- Never include environment variable values.
- Preserve field names and event ordering when safe; those are part of adapter
  compatibility evidence.
- If the useful evidence cannot be safely sanitized, mark the decision as
  `observed_private_not_independently_auditable` rather than pretending the
  public fixture proves it.

### 8.5 Version and freshness rule

Provider capability evidence is keyed by:

```text
adapter
resolved executable path
exact version output
probe schema version
operating system and architecture
```

`gigai doctor` may use a cached probe only for an exact key match. A changed
binary version is `unknown` until reprobed; support is never inferred from the
provider name.

Historical evidence remains in the repository because it explains the decision
at that time. A newer probe supersedes it through a new dated evidence file; it
does not rewrite history.

### 8.6 Documentation rule

Every research or decision record includes:

- status and date;
- question/context;
- alternatives considered;
- decision and consequences;
- exact local evidence paths;
- external primary sources;
- limitations;
- revisit triggers;
- superseding record when the decision changes.

This follows the core Architecture Decision Record principle: capture an
important decision together with its context and consequences. The public
[ADR reference collection](https://github.com/architecture-decision-record/architecture-decision-record)
and the UK Government's
[architecture decision guidance](https://gds-way.digital.cabinet-office.gov.uk/standards/architecture-decisions.html)
describe the same durable-record pattern.

### 8.7 Revisit when

- a provider report adds new potentially sensitive fields;
- redaction removes data needed to reproduce the decision;
- an evidence schema changes incompatibly;
- signed attestations become a public release requirement;
- probe cost or rate limits make routine refresh impractical.

---

## 9. Decision 6 — Define and version the public contract explicitly

### 9.1 Problem

A public Python repository exposes many importable names accidentally. If GigAI
does not identify its supported extension surface, external consumers may bind
to internal ledger rows, artifact layout details, adapter implementation classes,
or discovery internals and later treat cleanup as a breaking change.

Semantic Versioning starts by requiring a declared public API. See
[Semantic Versioning 2.0.0](https://semver.org/). Python packaging entry points
provide a standardized discovery mechanism when separately installed adapter or
tool packages become necessary; they do not require a marketplace. See the
[PyPA entry points specification](https://packaging.python.org/en/latest/specifications/entry-points/).

### 9.2 Alternatives considered

#### A. Treat every importable module as public

Rejected. It makes internal iteration prohibitively expensive.

#### B. Promise third-party plugins in v1

Rejected. Initial consumer-owned tools and three built-in model adapters do not
prove a plugin lifecycle, conflict policy, trust model, or compatibility need.

#### C. Keep all contracts undocumented until 1.0

Rejected. The first public consumers still need to know which seams are intended
for use and which may change during `0.x` development.

### 9.3 Decision

During `0.x`, GigAI documents a provisional public surface containing only:

- `Workflow`, `Run`, `PythonTool`, and `CommandTool` authoring contracts;
- Pydantic input/output expectations;
- model adapter request, response, capability, and failure contracts;
- run, call, attempt, artifact, and manifest schema versions;
- configured consumer-package discovery;
- CLI exit-code and machine-readable output contracts explicitly marked public.

Everything else is internal unless named in public API documentation.

Every serialized contract contains an explicit schema version. Every JSON Schema
declares its dialect with `$schema`; JSON Schema describes this as the mechanism
for identifying which keyword semantics apply. See
[JSON Schema dialect declarations](https://json-schema.org/understanding-json-schema/reference/schema).

Python package version and serialized-schema version are separate. A package
minor release may add a backward-compatible schema version; reading an unknown
major schema fails closed with an upgrade/migration instruction.

### 9.4 Adapter capability contract

The public `ModelAdapter` contract includes a machine-readable capability report,
not just `invoke()` and `probe()`:

```text
adapter API version
adapter implementation/version
resolved executable or endpoint dialect
native structured-output mechanisms
read-only enforcement level
session capture/resume support and compatibility fields
streaming support
usage/cost fields
cancellation mechanism
probe evidence reference and timestamp
```

Role resolution binds only to an adapter/model whose required capabilities pass.
No provider-name branch is a substitute for capability matching.

### 9.5 Future discovery

Consumer workpad workflows and tools continue to be discovered from explicitly
configured trusted packages. If separately distributed adapters become real,
use a namespaced PyPA entry-point group such as `gigai.adapters.v1`, validate the
loaded object's API version before use, reject name conflicts, and never auto-
install a missing package.

This is an extension mechanism, not a marketplace or trust decision.

### 9.6 Revisit when

- the first external consumer imports GigAI outside this repository;
- a third-party adapter has a real maintainer and caller;
- the package approaches `1.0` stability;
- public schemas require formal compatibility fixtures across releases.

---

## 10. Resulting v1 boundary

These decisions leave GigAI narrow:

```text
trusted Python workpad
        |
        | check + best-effort plan
        v
immutable run source snapshot
        |
        | authoritative rehearsal or bounded live run
        v
Run
  +-- model adapter with capability-probed enforcement
  +-- trusted tool subprocess with recorded enforcement level
        |
        v
durable attempts + filesystem artifacts + metadata ledger
        |
        v
verification, evaluation, and explicit human judgment
```

It still does not provide:

- arbitrary untrusted Python execution;
- a cross-platform sandbox guarantee;
- automatic recovery by replaying ambiguous paid calls;
- exact static planning of arbitrary Python;
- a daemon or durable scheduler;
- a plugin marketplace;
- signed supply-chain attestations;
- automatic target or workflow mutation.

## 11. Implementation gates derived from the research

### Gate A — Offline skeleton

May begin when:

- public authoring contracts and schema-version conventions are written;
- `plan` language says best-effort;
- fixtures prove common `CaseRequired` diagnostics and known blind spots;
- source snapshot manifests can be produced and verified without execution.

### Gate B — Metadata ledger

May begin when:

- run/call/attempt states and transition table are accepted;
- write ordering and orphan-artifact reconciliation are accepted;
- migration 001 is derived from those contracts;
- process-kill recovery tests are specified.

### Gate C — Live deterministic tools

May begin when:

- workers import only from the immutable snapshot;
- observed-mode controls and target postconditions are implemented;
- the run header exposes `observed` rather than claiming sandbox enforcement;
- no v1 registered tool mutates the target or an external system.

### Gate D — Live model adapters

May begin when:

- installed-version capability probes are refreshed;
- sanitized primary evidence is repository-owned and summary-derived;
- capability matching fails closed;
- every invocation creates and commits an attempt before process launch;
- ambiguous interruption never auto-retries.

### Gate E — Enforced sandbox claim

Remains closed until one backend proves filesystem, process, credential, and
network guarantees against an explicit test matrix. It is not required for the
trusted-workpad v1 if the product claim remains `observed`.

## 12. Consequences

### Positive

- Public claims match enforceable behavior.
- Dirty and untracked workflow runs have exact executed source bytes.
- Interrupted paid calls remain inspectable without accidental duplicate spend.
- `plan` stays useful without becoming a DSL or false proof.
- Provider decisions can be independently reviewed from repository evidence.
- External contributors know which contracts are stable enough to build against.

### Costs

- Every authoritative run copies and hashes a bounded source set.
- The ledger needs attempt and event records earlier than the prior plan implied.
- Target postcondition verification is more expensive than checking only the Git
  tree.
- Provider evidence requires deliberate structural redaction and regeneration.
- Some users will want enforced sandboxing before GigAI supplies it.

### Risks retained deliberately

- Trusted workpad Python can still escape observed-mode conventions.
- Native provider read-only behavior remains version-specific.
- Exact model behavior cannot be reproduced from source and prompt bytes alone.
- Static checks cannot prove arbitrary Python is free of result-dependent
  branches or direct side effects.
- Local artifact deletion can remove payloads while leaving metadata history.

## 13. Decision summary

| ID | Decision | V1 status |
|---|---|---|
| GRC-01 | Subprocess is containment; trusted tools run at recorded `observed` enforcement unless a real sandbox backend is present. | recommended |
| GRC-02 | Authoritative runs execute workflow/tools from a sealed run-scoped source snapshot. | recommended |
| GRC-03 | Runs and calls use explicit durable states, attempts, write ordering, and crash reconciliation. | recommended |
| GRC-04 | `plan` is labelled best-effort; rehearsal and hard runtime budgets provide authority. | recommended |
| GRC-05 | Sanitized primary evidence is committed; summaries and capability booleans are derived. | recommended |
| GRC-06 | GigAI declares a narrow provisional public API and separately versions serialized schemas. | recommended |

## 14. Sources

### Repository evidence

- `docs/architecture/v14-implementation-plan.md`
- `docs/research/phase-0-spikes.md`
- `research/phase0_spike/tool_boundary.py`
- `research/phase0_spike/planning.py`
- `research/phase0_spike/source_bundle.py`
- `research/phase0_spike/fixtures/provider-capabilities-2026-07-30.json`
- `research/experiments/resume/resume-spike`
- `research/experiments/resume/findings.md`

The original raw session evidence and harness-level planning documents are
intentionally excluded from the standalone public repository because they
contain workstation, session, and unrelated-checkout provenance.

### Primary external sources

- [Python subprocess management](https://docs.python.org/3/library/subprocess.html)
- [Python expressions and identity comparisons](https://docs.python.org/3/reference/expressions.html#is-not)
- [Python AST visitor](https://docs.python.org/3/library/ast.html#ast.NodeVisitor)
- [SQLite write-ahead logging](https://www.sqlite.org/wal.html)
- [SQLite transactions](https://www.sqlite.org/lang_transaction.html)
- [AWS Builders' Library: Making retries safe with idempotent APIs](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/)
- [OpenTelemetry traces](https://opentelemetry.io/docs/concepts/signals/traces/)
- [Linux Landlock](https://www.kernel.org/doc/html/latest/userspace-api/landlock.html)
- [bubblewrap](https://github.com/containers/bubblewrap)
- [Docker Engine security](https://docs.docker.com/engine/security/)
- [SLSA build provenance](https://slsa.dev/spec/v1.2/build-provenance)
- [in-toto Statement v1](https://github.com/in-toto/attestation/blob/main/spec/v1/statement.md)
- [RFC 8785 JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785.html)
- [uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/)
- [PyPA entry points specification](https://packaging.python.org/en/latest/specifications/entry-points/)
- [Semantic Versioning 2.0.0](https://semver.org/)
- [JSON Schema dialect declaration](https://json-schema.org/understanding-json-schema/reference/schema)
- [Architecture Decision Record reference collection](https://github.com/architecture-decision-record/architecture-decision-record)
- [The GDS Way: Architecture decisions](https://gds-way.digital.cabinet-office.gov.uk/standards/architecture-decisions.html)

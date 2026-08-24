# G40 — Seamless Local Runtime and CLI Adapters

**Version:** v0.1.7  
**Status:** Complete — contract accepted, implementation committed, and
acceptance evidence recorded  
**Depends on:** S40 complete research finding; S41 complete with ADOPT WITH
AMENDMENT; v0.1.6 configuration, adapter, lifecycle, journal, workpad,
capability, and Run authorities  
**Unblocks:** G41 project-local package boundary and the remaining v0.1.7
implementation lane

## Outcome

G40 makes GigAI's local runtime feel seamless from the terminal. A user or
invoking agent should not need to locate installed CLIs manually, understand
the difference between a GUI PATH and a login-shell PATH, or interpret a raw
traceback to know why a runtime is unavailable.

The resulting path is:

```text
effective local runtime
  -> GigAI discovery snapshot
  -> explicit setup choices
  -> typed runtime/configuration states
  -> optional bounded readiness verification
  -> explicit role selection
  -> agent invocation envelope
  -> proposal/approval/Run authorities remain unchanged
```

G40 does not make a detected executable authoritative, does not import an
agent's conversation, and does not make the companion or adapter an
orchestrator. It supplies runtime facts and bounded invocation capability to
the existing GigAI lifecycle.

## v0.1.6 amendment

v0.1.6 exposed a browser-first setup and creation direction. The v0.1.7
product amendment makes CLI, JSON, Markdown, and durable local evidence the
primary surfaces because:

- the invoking agent already operates in the terminal;
- local executable discovery and configuration belong together in one
  inspectable runtime seam;
- clean-environment failures must be diagnosable without a second process or
  interaction surface; and
- proposal, approval, capability, and Run authority must be visible as typed
  local transitions rather than being coupled to presentation state.

This is an interaction-surface change only. G40 preserves the v0.1.6
configuration, registry, journal, workpad, proposal/version, capability, and
Run authorities. It does not migrate or rewrite existing GigAI state; the
v0.1.6 upgrade and rollback contract is owned by G41.

## Current evidence and implementation seams

The contract is grounded in the current tree:

- [`src/gigai/model_discovery.py`](../../../src/gigai/model_discovery.py)
  owns the fixed `codex`/`claude` catalog, login-shell PATH hydration, bounded
  `--version` probing, immutable `DiscoverySnapshot` persistence, and the
  distinct `DetectedModel` and `ModelReadiness` values.
- [`src/gigai/adapters/process.py`](../../../src/gigai/adapters/process.py)
  already provides explicit argv, `shell=False`, an environment allowlist,
  timeout/cancellation handling, process-group cleanup, and structured
  authentication failure detection. G40 retains and tests this boundary.
- [`src/gigai/cli.py`](../../../src/gigai/cli.py) is the G40 product-surface
  seam: setup is terminal-native, detected local CLIs are persisted as typed
  targets, agent-backed creation requires an explicit invocation envelope, and
  approval exposes the capability-manifest reference accepted by the lifecycle
  function. The retired presentation paths remain historical implementation
  material only where they serve later contracts.
- [`src/gigai/lifecycle.py`](../../../src/gigai/lifecycle.py) already owns the
  proposal-to-approved-version transition through `approve_offline`. G40
  repairs the CLI boundary so it invokes that authority rather than creating
  a new one.
- [`src/gigai/run.py`](../../../src/gigai/run.py) resolves Run authority from
  committed journal/workpad state. G40 must not move that authority into the
  runtime snapshot, adapter, or agent envelope.
- [S40](../spikes/S40-cli-first-seamless-local-runtime.md) and
  [S41](../spikes/S41-cli-only-runtime-seam-and-gig-creation-uat.md) contain
  the pinned Orca research and controlled UAT evidence. Orca behavior is
  adopted as a behavioral reference only; no Orca code or dependency enters
  GigAI.

## Contract gate

Before runtime implementation, the following decisions must be reviewed and
accepted in the G40 contract evidence:

1. the discovery snapshot schema and immutable-evidence boundary;
2. the login-shell PATH hydration command, timeout, output delimiters, output
   cap, refresh behavior, and failure taxonomy;
3. the closed local runtime catalog, executable-resolution algorithm, and
   bounded install-directory fallback list;
4. the state vocabulary and permitted evidence for each state;
5. setup's five explicit decisions and its human/JSON output contract;
6. adapter argv, cwd, environment, timeout, cancellation, authentication,
   version, and orphan-cleanup rules;
7. the complete CLI approval contract and its authority proof;
8. the agent invocation envelope, explicit triggers, actor identity, consent
   points, and transcript exclusion rules;
9. documentation and command-surface reconciliation across v0.1.6 and
   v0.1.7; and
10. the acceptance matrix, fixture strategy, live-provider opt-in boundary,
    and release evidence locations.

Runtime implementation remains provisional until each contract item has
passing evidence in the acceptance matrix below.

## Product and command contract

### `gigai setup`

Setup is a CLI-native, explicit, idempotent operation. Its compact human
summary may use color when attached to a terminal; `--json` is stable,
color-free, and machine-readable.

The normal setup interaction has at most five decision groups:

1. **Workpad:** select or confirm the GigAI home and authoritative workpad
   location;
2. **Runtime access:** select the permitted local/provider boundary and
   network policy;
3. **Agents and models:** review detected runtimes, versions, authentication
   state, compatible targets, and usable targets;
4. **Role defaults:** select explicit reviewer, verifier, researcher, and Gig
   creator defaults from usable targets; and
5. **Budgets and confirmation:** confirm bounded calls, wall time, usage, and
   effect policy before publishing configuration.

Setup may infer safe facts. It may not infer consent, select an unverified
   target, create a Gig, approve a proposal, install a capability, or start a
   Run. A clean environment must either complete with safe defaults or return a
   structured diagnostic with a stable non-zero exit code. It must never expose
   a traceback as the normal failure contract.

### `gigai models` and `gigai doctor`

`models` reports the latest discovery snapshot and configured target states.
`doctor` reports configuration, runtime, workpad, lock, adapter, and policy
issues without making provider calls unless the user explicitly requests a
bounded probe.

Ordinary discovery is read-only, local, bounded, and provider-free. A probe
that can resolve credentials, use network access, or incur provider cost is a
separate explicit action and must report that fact before execution.

### `gigai approve`

The CLI approval command must expose exactly the inputs its implementation
accepts, including an optional capability-manifest reference when the
contract requires one. The command must:

1. resolve the target and workpad;
2. load and validate the named proposal;
3. require the explicit user approval actor;
4. invoke the existing lifecycle authority;
5. seal the approved version and active pointer; and
6. return the proposal, Gig, version, tag, journal, and commit identities.

It must not start a Run, mutate a target, or accept approval supplied only by
the invoking agent. A direct library call is not CLI acceptance evidence.

### Agent invocation envelope

GigAI accepts agent-driven work only through an explicit trigger:

```text
gigai:
$gigai
/gigai
gigai run
```

The exact trigger form is recorded in the invocation evidence. Ordinary agent
conversation is never captured or inferred as GigAI intent.

The versioned envelope contains only typed fields:

```json
{
  "protocol_version": "1",
  "invocation_id": "inv_...",
  "actor": {
    "kind": "agent",
    "id": "codex",
    "session_id": "opaque-local-id"
  },
  "command": "create",
  "target": {"home": "...", "project": "..."},
  "input": {"intent": "...", "answers": {}, "proposal": {}},
  "requested": {"roles": [], "models": [], "capabilities": []},
  "consent": []
}
```

The final schema must define redaction, size limits, allowed commands,
identity semantics, target references, and consent records. The envelope must
not contain raw credentials, ambient repository contents, hidden prompt text,
or the surrounding transcript.

The actor boundary is normative:

```text
invoking agent = interviewer and configuration partner
user = consent and approval actor
GigAI = validation, authority, execution, and evidence owner
```

Missing or malformed triggers, unsupported fields, unknown actors, or
unauthorized effects fail closed with typed diagnostics. Proposal creation is
non-authoritative and may carry an empty consent list; an effectful `run`
invocation requires an explicit operator consent record. The agent cannot
approve its own proposal.

## Runtime discovery contract

### Discovery snapshot

Each discovery operation produces one immutable GigAI-owned snapshot. It is
evidence for that operation, not a live cache that silently becomes
configuration or Run authority.

The snapshot must contain:

- snapshot schema version and operation ID;
- capture time and refresh reason;
- local runtime identity and execution-host kind;
- effective PATH source and whether login-shell hydration succeeded;
- catalog revision and each attempted command;
- resolved executable path and resolution method;
- bounded version output and version parse status;
- state values and failure code/detail for every candidate; and
- redacted evidence metadata suitable for JSON, Markdown, and portable reports.

Absolute paths may exist in local diagnostic evidence when required for
operation, but shareable reports and portable packages must redact them. No
credential, transcript, private Run, provider payload, or installed tool bytes
may enter the snapshot.

### PATH hydration

G40 adopts the behavior demonstrated by S40's pinned Orca evidence, not its
implementation. The GigAI contract is:

- invoke a fixed login-shell discovery command, never user-provided shell text;
- delimit output with an unambiguous sentinel;
- strip terminal noise before parsing;
- enforce a bounded timeout, output size, and process cleanup;
- preserve the hydrated PATH ordering and record its source;
- merge only with an explicitly defined baseline PATH; and
- make refresh explicit and idempotent.

Shell failure, timeout, malformed sentinel output, empty PATH, and permission
failure are distinct bounded diagnostics. Failure to hydrate does not authorize
broad filesystem scanning.

### Executable resolution

The initial local catalog contains Codex and Claude. Each entry records the
command name, aliases accepted by contract, executable resolution result,
version evidence, and state. Resolution must distinguish:

- a real executable;
- a missing command;
- a shell alias or function without an executable path;
- a bounded supported install-directory fallback; and
- an executable whose version or adapter contract is unsupported.

G40 does not scan the home directory, inspect arbitrary shell state, forward to
WSL/SSH hosts, or silently substitute another provider.

## State contract

The following states are distinct and cannot be inferred from one another:

| State | Meaning | Minimum evidence |
|---|---|---|
| `detected` | A supported command resolves to a real executable | Resolution result and executable identity |
| `configured` | GigAI has a typed endpoint/model target and credential reference | Valid configuration record |
| `compatible` | GigAI's adapter/version contract matches the target | Adapter and version compatibility evidence |
| `authenticated` | Provider-owned authentication is available for the selected invocation boundary | Explicit provider/auth evidence; never inferred from executable presence |
| `verified` | An explicit bounded GigAI verification succeeded | Probe result, contract identity, and timestamp |
| `usable` | The target satisfies the requested role's capability, policy, budget, and network constraints | Role-specific readiness decision |
| `selected` | A user or approved configuration explicitly chose the target for a role/profile | Selection record and actor |

Failure states such as `missing_executable`, `path_unavailable`,
`version_unavailable`, `unsupported`, `authentication_required`,
`host_unavailable`, `probe_timeout`, and `policy_blocked` are typed evidence,
not aliases for `unusable` or `detected`.

## Adapter lifecycle and security

Every local adapter invocation must define:

- explicit argv with no shell string;
- a non-target working directory unless a narrower read-only context is
  accepted;
- the allowlisted environment and explicitly named transient credential
  references;
- provider-specific timeout and total budget;
- stdin, stdout, stderr, and output-size handling;
- cancellation and process-group cleanup;
- authentication, timeout, malformed-output, and non-zero-exit categories;
- version drift behavior; and
- orphan recovery and terminal evidence.

The existing `process.py` environment and process boundary is retained unless
the contract evidence proves an additive change. A discovery timeout must not
be copied blindly into a longer-running model invocation. No adapter may retry,
fallback, race providers, mutate a target, or create GigAI authority.

## Documentation and carry-forward reconciliation

G40's contract gate includes a written reconciliation record for:

| Source | Required treatment |
|---|---|
| `README.md` | Describe the v0.1.7 CLI surface and mark stale v0.1.6 behavior without presenting it as the v0.1.7 contract |
| `docs/development/v0.1.7/plans/v0.1.7-codex-handoff.md` | Replace the stale surface guidance and point to this roadmap and G40 contract |
| `src/gigai/cli.py` | Change setup defaults/help and create/approve behavior only after this contract is accepted |
| G30 and S27 historical documents | Preserve as historical evidence; do not inherit their surface or completion claims |
| G33/G35/G36 and S36 carry-forward material | Redirect to G41/G44/G47 according to the v0.1.7 roadmap matrix |
| S33/S34 evaluation and routing evidence | Reuse only through G43/G46/S48 contract review |

The reconciliation record must list each changed statement, its replacement,
and the acceptance evidence that proves the runtime and documentation agree.

## Acceptance evidence plan

### Contract and documentation

- reviewed G40 contract and contract-impact record;
- repository-wide surface reconciliation for the handoff, README, CLI help,
  command defaults, and historical references;
- no implementation claim in the documentation without executable evidence;
- explicit v0.1.6 preservation boundary and G41 migration handoff.

### Discovery and state

- inherited PATH versus login-shell PATH comparison;
- clean, noisy, malformed, timed-out, empty, and permission-failed hydration;
- executable, missing, alias/function, fallback, version, and unsupported cases;
- immutable snapshot replay and explicit refresh;
- local runtime identity and redacted shareable evidence;
- complete state transition matrix, including authentication-required and
  host-unavailable cases; and
- proof that discovery does not make configuration, approval, or Run authority.

### Setup and adapters

- clean-home setup with no editor or optional field supplied;
- compact colored terminal summary and stable JSON summary;
- idempotent rerun and existing-config preservation;
- Codex and Claude version/readiness/authentication behavior;
- explicit argv/cwd/environment/timeout/cancellation tests;
- process-group cleanup and orphan recovery;
- malformed output, non-zero exit, provider auth, host failure, and no-fallback
  classification; and
- provider-free ordinary discovery with explicitly opt-in live probes.

### Approval and agent invocation

- CLI approval succeeds through the existing lifecycle authority;
- the missing capability-manifest parameter cannot recur silently;
- proposal, approved version, journal, pointer, and commit identities are
  returned and independently verified;
- each accepted trigger parses the typed envelope;
- missing trigger, malformed field, unsupported command, unknown actor,
  missing consent, transcript field, credential field, and unauthorized effect
  all fail closed; and
- agent-backed proposal creation is tested without ambient transcript import or
  a separate presentation state machine.

## Evidence recorded for the current implementation

The current source worktree has these passing evidence runs:

- the focused G40/runtime/CLI/discovery/adapter suite: **41 passed**;
- the loopback compatibility suite, run with local socket permission:
  **18 passed**;
- the installed-scenario suites for G03, G04, and G11, pointed at this
  source worktree's executable: **25 passed**;
- the complete repository suite, pointed at this source worktree's executable
  and run with local socket permission: **623 passed, 1 skipped**;
- direct isolated UAT: terminal setup, persisted local targets, explicit agent
  envelope, proposal-only creation, and existing-authority CLI approval; and
- hydration failure classification, redacted `models --json`, source
  compilation, and `git diff --check` pass.

The earlier isolated journal-lock failure was a host-level test-run
interruption; the final full suite passed and it did not exercise or alter the
G40 runtime seam.

### Stop conditions

Stop G40 before implementation acceptance if:

- PATH hydration requires unrestricted shell execution or broad scanning;
- discovery cannot remain immutable evidence separate from configuration and
  Run authority;
- setup cannot provide structured clean-environment diagnostics;
- the CLI approval transition cannot be proven end to end;
- creation still requires the legacy interview server rather than the typed
  agent envelope;
- provider identity or authentication failure cannot be preserved;
- the envelope permits transcript capture, hidden prompts, raw credentials, or
  self-approval; or
- documentation and command behavior cannot be reconciled to the v0.1.7
  product surface.

## Completion evidence

G40 may be marked complete only when the contract is accepted, the
reconciliation record is complete, all S41 carry-forward blockers have passing
evidence, and the implementation has passed the acceptance matrix. A green
unit test or an offline fixture alone cannot close G40.

The completion handoff must identify the accepted discovery snapshot contract,
state matrix, adapter boundary, invocation envelope, CLI approval proof,
documentation reconciliation, known host limitations, and the exact G41
inputs. No G41 implementation begins from an unreviewed G40 contract.

# S40 — CLI-First Seamless Local Runtime Discovery

**Date:** 2026-08-24  
**Status:** Complete — research finding; no implementation authorization
**Baseline:** v0.1.6 / `7f9ceb5`  
**Related:** G40 tiny companion and agent adapters; G47 background Runs and reports

## Outcome

Determine how GigAI can provide a seamless local runtime for Codex CLI, Claude
Code, and future local agents without requiring onboarding HTMX, a browser
window, manual executable paths, or a second orchestration authority.

The proposed product direction is CLI-first:

```text
gigai setup
  -> discover the local runtime automatically
  -> show a compact colored summary
  -> ask only unresolved policy questions
  -> write configuration atomically
  -> exit
```

The browser is not required for setup, model discovery, readiness checks, Gig
execution, or ordinary reports. A later optional presentation surface may
render evidence, but it must not be a runtime dependency or authority store.

This spike does not remove the existing setup UI or change runtime code. It
records the research and the contract that should precede that work.

## Problem statement

GigAI already has most of the semantic layers required for model-backed Gigs:

```text
detected -> configured -> compatible -> verified -> usable -> selected
```

The local-runtime layer is less seamless than Orca's because GigAI currently
discovers `codex` and `claude` from the Python process environment with
`shutil.which`, while an application launched outside a login shell may not
inherit the PATH that makes those commands available in Terminal.

The result is a misleading experience:

```text
Terminal:          claude and codex work
GigAI process:     one or both appear unavailable
User:              asked to configure something that already exists
```

This is not a reason to copy Orca's UI or make Orca a GigAI dependency. It is a
runtime-context problem. GigAI needs its own host discovery substrate with the
same level of seamlessness, then it must apply GigAI's stricter configuration,
capability, credential, budget, approval, and evidence rules.

## Research questions

1. What does Orca actually detect when it reports Claude or Codex?
2. Which Orca mechanisms make detection reliable outside a terminal-launched
   process?
3. Which facts belong to local runtime discovery, and which require an
   explicit GigAI readiness probe?
4. Which current GigAI surfaces are unnecessarily coupled to browser setup?
5. What evidence is required before replacing browser-first setup with a
   compact CLI flow?

## Orca research findings

### 1. Orca detects launchable agent CLIs, not fully usable GigAI models

Orca's agent preflight starts with a closed registry of known TUI agents. The
registry describes executable names, aliases, required companion commands,
unsupported runtimes, launch commands, expected processes, and prompt-delivery
behavior. The relevant source is
[`src/shared/tui-agent-config.ts`](</Users/kar/Developer/projects/opensource/orca/src/shared/tui-agent-config.ts>).

For Claude and Codex, the important facts are straightforward:

```text
claude -> claude
codex  -> codex
```

An Orca detection result means approximately:

> A known command resolves to a real executable in this runtime and Orca knows
> how to launch or host it.

It does not by itself prove that a particular model selector is available,
that the provider account is authenticated, that a Gig's required capabilities
are supported, or that a provider call is authorized.

The detection command construction and resolution are in
[`src/shared/tui-agent-detection-commands.ts`](</Users/kar/Developer/projects/opensource/orca/src/shared/tui-agent-detection-commands.ts>).
The runtime detector is
[`src/main/preflight/agent-detection.ts`](</Users/kar/Developer/projects/opensource/orca/src/main/preflight/agent-detection.ts>).

### 2. Orca solves the cold GUI PATH problem explicitly

The strongest lesson is not the command list. It is PATH hydration.

Orca recognizes that a GUI-launched Electron process may not inherit the
user's shell profile. It re-spawns the relevant login shell, emits a private
sentinel-delimited PATH result, strips shell noise and ANSI escapes, merges the
new segments, and then runs detection. The implementation is
[`src/main/startup/hydrate-shell-path.ts`](</Users/kar/Developer/projects/opensource/orca/src/main/startup/hydrate-shell-path.ts>).

The detector calls that hydration path before its normal scan:

```text
hydrate login-shell PATH
  -> resolve known commands
  -> try bounded install-directory fallback for misses
  -> classify detected agent IDs
```

Orca also exposes a refresh path for the case where a user installs a CLI
after the app starts. The refresh invalidates cached WSL environment state or
re-hydrates the local shell PATH before scanning again. See
[`refreshShellPathAndDetectAgents`](</Users/kar/Developer/projects/opensource/orca/src/main/preflight/agent-detection.ts>).

This is the behavior GigAI should reproduce as a local runtime property, not as
a browser feature.

### 3. Orca validates executable resolution rather than shell aliases

Orca distinguishes a real executable from a shell alias, function, or unrelated
command. Its local path resolver uses the effective preflight environment, and
its WSL path lookup returns a sentinel-marked absolute path while excluding
Windows interop paths that would make host and guest answers disagree.

The command boundary is in
[`src/main/ipc/preflight-command-exec.ts`](</Users/kar/Developer/projects/opensource/orca/src/main/ipc/preflight-command-exec.ts>).
The important properties are:

- no free-form shell string for local executable lookup;
- bounded five-second preflight operations;
- platform-specific Windows handling;
- WSL-specific command lookup;
- explicit distinction between PATH resolution and `command --version`;
- a failed probe becomes unavailable/unknown rather than an optimistic success.

For commands missed on PATH, Orca has a bounded install-directory resolver in
[`src/shared/local-agent-install-dir-detection.ts`](</Users/kar/Developer/projects/opensource/orca/src/shared/local-agent-install-dir-detection.ts>).
That fallback is a recovery mechanism, not permission to scan the entire home
directory.

### 4. Orca treats host/runtime location as part of discovery

The same detection operation can target:

- the native host;
- a WSL distro;
- an SSH execution host.

Remote detection is forwarded to the execution host rather than incorrectly
checking the desktop machine. A disconnected remote host returns no current
detection result instead of being treated as proof that the agent is absent.

GigAI should represent this explicitly as a runtime target, for example:

```text
runtime = local-host | wsl:<distro> | ssh:<connection>
```

The first v0.1.7 implementation may be local-host only, but the data model must
not accidentally make a desktop PATH authoritative for a future remote Run.

### 5. Orca has a separate live-status path

Orca does not infer agent status from terminal titles. Its normalized status
model receives provider hook events and keeps bounded state/history. The source
states this directly in
[`src/shared/agent-status-types.ts`](</Users/kar/Developer/projects/opensource/orca/src/shared/agent-status-types.ts>).

The main hook server accepts provider events, stamps observation origin and
ordering, reconciles provider-specific lifecycle details, persists status, and
notifies subscribers. See
[`src/main/agent-hooks/server.ts`](</Users/kar/Developer/projects/opensource/orca/src/main/agent-hooks/server.ts>).

The lesson for GigAI is to keep these facts separate:

```text
runtime discovery  = what can be launched here?
readiness probe    = can this configured target complete one bounded call?
Run state           = what happened in this authorized GigAI Run?
```

An installed CLI, an authenticated CLI, an active agent process, and a
successful GigAI Run are four different facts.

### 6. Orca's plugin API is not a GigAI runtime contract

Orca plugins can contribute panels, commands, events, agent profiles, language
packs, keybindings, and VM recipes through
[`src/shared/plugins/plugin-manifest.ts`](</Users/kar/Developer/projects/opensource/orca/src/shared/plugins/plugin-manifest.ts>).

The current host API exposes bounded capabilities including workspace context,
explicit terminal input, notifications, plugin-private storage, plugin
secrets, settings, and selected host events. See
[`src/shared/plugins/plugin-host-api.ts`](</Users/kar/Developer/projects/opensource/orca/src/shared/plugins/plugin-host-api.ts>)
and [`src/shared/plugins/plugin-capabilities.ts`](</Users/kar/Developer/projects/opensource/orca/src/shared/plugins/plugin-capabilities.ts>).

There is no first-class host API for:

- GigAI configuration authority;
- model target resolution;
- sealed Run creation;
- Review/Verify/Adjudicate decisions;
- GigAI occurrence scheduling;
- GigAI journal or workpad authority.

Therefore a future Orca integration should be a front door or status bridge.
It must call an explicit GigAI CLI/IPC contract and display GigAI-owned facts.
It must not recreate the Goal Graph, credential policy, Run state, or review
verdict inside an Orca plugin.

Orca's skill discovery is also a different subsystem. It scans provider and
project skill roots, including Claude plugin skill roots, and reports discovered
Markdown skills. See
[`src/main/skills/discovery.ts`](</Users/kar/Developer/projects/opensource/orca/src/main/skills/discovery.ts>).
That is useful research for portable skill packaging, but it is not the model
runtime discovery contract in this spike.

## Current GigAI findings

### What already exists

GigAI already has useful foundations:

- [`src/gigai/model_discovery.py`](</Users/kar/orca/workspaces/gigai/gigai-v0.1.7/src/gigai/model_discovery.py>) reports
  detected Codex/Claude executables and bounded version evidence;
- [`src/gigai/adapters/factory.py`](</Users/kar/orca/workspaces/gigai/gigai-v0.1.7/src/gigai/adapters/factory.py>)
  resolves configuration to deterministic, Codex CLI, Claude CLI, OpenAI API,
  or OpenRouter adapters;
- [`src/gigai/adapters/process.py`](</Users/kar/orca/workspaces/gigai/gigai-v0.1.7/src/gigai/adapters/process.py>)
  provides bounded non-shell JSON process execution;
- [`src/gigai/adapters/codex_cli.py`](</Users/kar/orca/workspaces/gigai/gigai-v0.1.7/src/gigai/adapters/codex_cli.py>)
  and [`src/gigai/adapters/claude_cli.py`](</Users/kar/orca/workspaces/gigai/gigai-v0.1.7/src/gigai/adapters/claude_cli.py>)
  use provider-specific structured command surfaces;
- `gigai models` already separates detected targets from configured targets and
  supports an explicit `--probe TARGET` action;
- `gigai doctor` already has an offline diagnostic path and an explicit live
  probe path;
- credential configuration stores references rather than raw values.

The semantic direction is therefore sound. The missing work is a seamless
runtime context and a simpler primary setup surface.

### Current browser coupling

The current CLI declares browser-first setup as the default. In
[`src/gigai/cli.py`](</Users/kar/orca/workspaces/gigai/gigai-v0.1.7/src/gigai/cli.py>), `gigai setup`:

- has a legacy `--terminal` escape hatch;
- defaults `--open` to true;
- launches the local setup server when neither `--non-interactive` nor
  `--terminal` is supplied;
- describes itself as “Open browser-first setup.”

The current G30 goal document records this browser flow as complete historical
v0.1.6 evidence. v0.1.7 should treat that as prior implementation context,
not as a requirement to preserve browser-first onboarding.

The current CLI already contains a compact terminal summary path, including
credential references, detected candidates, readiness, selected target, and an
explicit apply confirmation. That is the better starting point for the new
flow. It needs to become the normal path rather than a legacy fallback.

### Immediate implementation gap

GigAI's current discovery function calls `shutil.which` directly and probes
`--version` with a restricted environment. That is a good safety baseline but
does not yet hydrate a login-shell PATH or identify the execution runtime in a
reusable abstraction.

The next runtime layer should not scatter `shutil.which` calls across setup,
model discovery, adapters, and diagnostics. It should produce one immutable
per-operation discovery snapshot that those consumers interpret differently.

The discovery snapshot should answer only local facts:

```json
{
  "runtime": {"kind": "local-host", "platform": "darwin"},
  "path_source": "login-shell-hydrated",
  "path_status": "ready",
  "agents": [
    {
      "name": "codex",
      "executable": "...",
      "version": "...",
      "detected": true,
      "probe_status": "not_run"
    }
  ]
}
```

Absolute paths may be used locally for execution diagnostics, but they must be
redacted or omitted from portable packages, share-safe reports, and evidence
that leaves the machine.

## Proposed CLI-first runtime contract

### `gigai setup`

The default command should be a short, colored, terminal-native flow:

```text
GigAI setup

Runtime
  ✓ local host · macOS · login-shell PATH ready

Agents
  ✓ Codex       detected · version ...
  ✓ Claude      detected · version ...
  ! OpenAI API  not configured

Defaults
  reviewer     Codex
  verifier     Claude
  researcher   Codex
  gig creator  Codex

Storage
  home         ~/.gigai
  workpads     ~/.gigai/workpads

1. Accept detected defaults
2. Change role assignments
3. Configure API credential references
4. Change storage
5. Write configuration
```

The exact five prompts remain a contract decision, but setup should generally
resolve only:

1. GigAI home/workpad location;
2. detected local agents and optional API providers;
3. machine-wide role defaults;
4. credential references and network policy when needed; and
5. explicit confirmation of the resulting local configuration.

Everything discoverable should be automatic. Everything security-sensitive or
materially spend-affecting should be explicit. No browser launch is needed.

Color is presentation only. `--json`, non-TTY output, and CI mode must remain
stable, path-safe, and color-free.

### `gigai models`

This command should become the detailed runtime report:

```text
detected       executable/version/runtime facts
configured     endpoint and target configuration
compatible     required capability evidence
verified       explicit bounded probe result
usable         target can currently be selected
```

`gigai models --probe TARGET` remains an explicit provider action. Ordinary
discovery must not send a provider request, consume an account, or read a raw
secret merely to render setup.

### `gigai doctor`

`doctor` should diagnose without becoming a second state authority. It may
report:

- runtime and platform;
- PATH hydration source and failure reason;
- executable resolution;
- CLI version evidence;
- configuration validity;
- credential-reference presence, never values;
- workpad and lock health;
- adapter support;
- stale or unavailable runtime state.

Live provider checks remain opt-in and named. A zero-cost local discovery pass
must never silently become a network readiness probe.

### Run invocation

An explicit Run still follows GigAI's existing authority chain:

```text
approved Gig version
  -> validated Run request
  -> selected model targets
  -> sealed Run Plan
  -> supervised adapter calls
  -> Review -> Verify -> Adjudicate
  -> journal, evidence, and terminal decision
```

The CLI is the operator surface. The journal, workpad, schemas, and sealed Run
records remain the authority. A future Orca plugin may invoke or observe this
surface, but cannot replace it.

## Runtime components to build or evaluate

The following are the research-to-implementation seams. They are not all
authorized by this spike.

| Seam | Required behavior | Orca research reference | GigAI question |
| --- | --- | --- | --- |
| Shell environment | Hydrate login-shell PATH with sentinel parsing, timeout, and failure reason | `startup/hydrate-shell-path.ts` | Can the Python CLI resolve the same commands as Terminal without importing shell state unsafely? |
| Command catalog | Closed provider/agent command definitions, aliases, required commands, runtime support | `shared/tui-agent-config.ts` | Which agents are in v0.1.7, and what does each detection claim mean? |
| Executable resolution | Real executable, not alias/function; bounded install-dir fallback | `preflight-command-exec.ts`, `local-agent-install-dir-detection.ts` | Can discovery remain deterministic and avoid broad home scans? |
| Runtime target | Local host first, with future WSL/SSH identity | `preflight/agent-detection.ts` | Does every snapshot identify where the executable was found? |
| Version evidence | Bounded `--version`, captured as metadata | Orca preflight command boundary | Which version fields affect adapter compatibility? |
| CLI invocation | Non-shell argv, restricted environment, timeout, cancellation, structured output | Orca process/agent command conventions | Does each adapter preserve provider-specific evidence without leaking secrets? |
| Auth state | Provider-owned auth remains separate from executable detection | Orca account services and runtime auth paths | Can GigAI report unknown/configured/usable without copying credentials? |
| Live status | Event/hook facts, not terminal-title inference | `agent-hooks/server.ts`, `agent-status-types.ts` | Which events are needed for a future companion, and which are Run facts? |
| UI boundary | No browser required for setup or execution | Orca host APIs are bounded, not Gig authority | Can every required operation be represented as CLI/JSON/Markdown? |

## Security and privacy boundary

The seamless runtime must not become ambient automation.

- Login-shell PATH hydration is a bounded environment probe, not arbitrary
  shell execution.
- Discovery never reads the full agent transcript.
- Discovery never injects hidden prompts into Codex or Claude sessions.
- Raw API keys and OAuth tokens never enter argv, JSON output, durable config,
  manifests, logs, or share-safe evidence.
- Provider-owned CLI authentication may be used only inside the explicitly
  selected adapter process and only under its documented environment contract.
- Discovery does not start a Run, mutate a target, install a capability, or
  make a network request.
- A missing or failed probe is represented as unavailable/unknown, not as
  usable by inference.
- Portable packages contain no absolute paths, credentials, private Runs,
  caches, or automatic install/execute hooks.
- Runtime status is observational. A process being present or an agent being
  idle does not authorize GigAI execution.

## Acceptance evidence for the follow-up contract

Before implementing the CLI-first replacement, the reviewed contract should
require at least these proofs:

### Discovery matrix

- Codex present on the inherited PATH.
- Claude present only on the login-shell PATH.
- Both commands absent.
- A shell that prints banners, ANSI prompts, or malformed output.
- Login-shell hydration timeout/failure.
- A CLI installed in a supported user-local location.
- A command name that resolves to an alias/function rather than an executable.
- Version command success, non-zero exit, malformed output, and timeout.
- Repeated discovery is bounded and cacheable; refresh invalidates the cache.
- The snapshot records runtime identity and path source.

### CLI setup matrix

- Fresh install with Codex only.
- Fresh install with Claude only.
- Both local CLIs present.
- No local CLIs and no API references.
- Existing valid config rerun is idempotent.
- Existing invalid config fails before mutation.
- TTY output is colored and concise.
- Non-TTY and `--json` output is stable and color-free.
- Setup does not open a browser or start a loopback server.
- User cancellation writes nothing.
- The summary never prints credential values or private absolute paths in
  shareable output.

### Readiness and invocation matrix

- Detected but unconfigured target.
- Configured but unverified target.
- Explicit local readiness probe succeeds.
- Authentication-required failure remains distinct from missing executable.
- Missing executable fails closed.
- Timeout and cancellation are terminal and bounded.
- Malformed provider output is rejected.
- Provider-specific resolved model identity is captured.
- No automatic fallback or hidden retry occurs.
- A completed adapter process does not imply a passing Review/Verify loop.

### UI-removal evidence

The CLI-first acceptance run should prove that a fresh installation can:

```text
setup -> models -> doctor -> init -> create/plan -> approve -> run -> report
```

without importing or launching the setup HTTP server, opening a browser, or
requiring a renderer. Any remaining web surface must be demonstrably optional
and must consume GigAI-owned projections rather than write competing state.

## Stop conditions

This spike stops before:

- deleting the existing setup server or HTMX templates;
- adding a background daemon or OS scheduler;
- adding automatic weekly Runs;
- making Orca a runtime dependency;
- adding an Orca plugin implementation;
- reading provider transcripts or ambient agent conversations;
- silently probing paid providers;
- replacing journal/workpad authority with a runtime cache;
- claiming that executable detection proves model compatibility;
- expanding from local-host discovery to WSL/SSH without a separate execution
  host contract.

The next contract should be split cleanly:

```text
S40: companion/runtime consent, lifecycle, event, and footprint contract
G40: local CLI discovery, adapter startup, health, cleanup, and doctor
G47: bounded background Runs, reports, and call-chain projections
```

The first implementation can remain entirely CLI-native. A future Orca
integration should consume the same explicit GigAI contract that the terminal
uses.

## Decision recommendation

Adopt the following v0.1.7 direction for review:

1. Make CLI setup the primary and complete setup surface.
2. Make browser opening opt-in, or remove it from the normal setup path after
   a replacement contract is accepted.
3. Introduce a reusable local runtime discovery snapshot with login-shell PATH
   hydration, bounded command resolution, version evidence, runtime identity,
   and refresh semantics.
4. Preserve the separate detected/configured/compatible/verified/usable states.
5. Keep GigAI's adapters, credential references, approval, Run, review, and
   evidence authority independent of Orca.
6. Treat Orca as research evidence for seamless local runtime behavior and as
   a possible future host, not as a library or authority dependency.

## Sources inspected

### GigAI

- `docs/development/v0.1.7/plans/v0.1.7-codex-handoff.md`
- `docs/architecture/v15-roadmap.md`
- `docs/development/goals/phase-5/G30-cli-model-adapters-and-browser-setup-onboarding.md`
- `src/gigai/cli.py`
- `src/gigai/model_discovery.py`
- `src/gigai/setup.py`
- `src/gigai/setup_interview.py`
- `src/gigai/adapters/factory.py`
- `src/gigai/adapters/process.py`
- `src/gigai/adapters/codex_cli.py`
- `src/gigai/adapters/claude_cli.py`

### Orca

- `src/main/preflight/agent-detection.ts`
- `src/main/ipc/preflight-command-exec.ts`
- `src/main/startup/hydrate-shell-path.ts`
- `src/shared/tui-agent-config.ts`
- `src/shared/tui-agent-detection-commands.ts`
- `src/shared/local-agent-install-dir-detection.ts`
- `src/shared/node-cli-command-resolution.ts`
- `src/shared/agent-status-types.ts`
- `src/main/agent-hooks/server.ts`
- `src/shared/plugins/plugin-manifest.ts`
- `src/shared/plugins/plugin-capabilities.ts`
- `src/shared/plugins/plugin-host-api.ts`
- `src/main/skills/discovery.ts`

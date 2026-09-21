# BUG-002 — Release CLI surface and runtime configuration are not human-UAT ready

**Status:** Implemented — awaiting review and human UAT sign-off

**Affected surfaces:** top-level `gigai --help`, project-scoped read commands,
`gigai setup`, `gigai models`, and API-backed model-target configuration

**Related research:** [S42 release CLI surface and runtime configuration UAT](../spikes/S42-release-cli-surface-and-runtime-configuration-uat.md)

## Bug summary

GigAI exposes commands that a new operator cannot successfully use, while
setup and model output do not clearly explain how to configure a real runtime
without exposing credentials or internal target names.

## Review recommendation

S42 is complete with the outcome **ADOPT WITH AMENDMENT**. Its command
inventory, runtime-state algebra, credential-flow conclusion, human-output
vocabulary, and UAT findings are recorded in the linked spike.

The implementation incorporates S42's amendments:

- establish the explicit public/internal command boundary without reducing the
  complete v0.1.7 release scope;
- provide stable, actionable prerequisite guidance;
- report missing credential references and readiness states distinctly;
- document the exact supported API reference path and its no-secret boundary;
- suppress internal target IDs from ordinary help and human output; and
- complete the clean-environment, redaction, and command-surface UAT matrix.

S42 was research input only. Its amendments are now implemented here without
changing authority semantics or activating a goal.

## Implementation record

The S42 amendments have now been implemented without changing GigAI's
proposal, approval, journal, workpad, capability, or Run authorities:

- `eval` and `improve` are available through `gigai internal ...` and are
  omitted from ordinary top-level help;
- project-scoped prerequisite failures—including `status`, `workpad path`,
  `check`, and `open`—return stable JSON errors or actionable terminal guidance
  pointing to `gigai init`, `gigai create`, or `gigai gigs`;
- `gigai models` reports stable IDs in JSON, human display labels in terminal
  output, explicit `not_configured`, `credential_reference_missing`,
  `configured`, `fixture`, and probe-related states, plus safe next actions;
- API setup remains reference-only and records no secret values or provider
  calls; and
- regression coverage proves internal-command access, help suppression,
  missing-reference behavior, label suppression, and unbound-project guidance.

Current worktree verification:

```text
677 passed, 1 skipped, 70 subtests passed
Ruff: clean
git diff --check: clean
```

Remaining release work is human UAT over the documented command inventory,
including provider-specific credential references and each public lifecycle
prerequisite. BUG-002 should be closed only after that evidence is reviewed.

## UAT observations

The release help exposes a large set of commands including `history`,
`status`, `show`, `plan`, `run`, `occurrence`, `workpad`, `invoke`, and
`upgrade`. From a normal UAT directory, apparently ordinary discovery commands
fail immediately:

```shell
uv run gigai history
# Error: target is not bound to a GigAI project

uv run gigai status
# Error: target is not bound to a GigAI project
```

The help text does not clearly divide public, UAT-ready workflows from
internal/lifecycle commands that require a pre-existing bound project, Gig,
proposal, approved version, Run, or fixture. The result is a release CLI that
looks much more complete than a new operator can actually use.

The same UAT cannot discover a clear, supported place to enter or reference an
API key for configured API model targets. `models` can report configured
targets, but a human needs an explicit setup/configuration path that explains
whether credentials are absent, referenced, or usable without printing secret
values.

Internal fixture terminology also remains visible to the operator:

```text
offline-default
```

This is an internal target identifier, not human-facing product language. It
continues to make a test fixture appear to be a normal agent/runtime choice.

## Product decision

The release CLI must expose only commands and options that are intentionally
supported for the current human-UAT/release workflow. Unsupported, incomplete,
or developer-only lifecycle surfaces must remain callable only through an
explicit internal/developer interface; they must not appear in ordinary
`gigai --help` or be presented as a normal next step.

This is not permission to delete lifecycle functionality. It is a release
surface decision: preserve the code and its tests behind an explicit internal
boundary until its operator workflow, prerequisites, error recovery, and UAT
are complete.

## Required resolution

### 1. Define and enforce the public command contract

1. Audit every current top-level command and each public subcommand. Record:
   owner/goal, intended operator, prerequisites, destructive or external
   effects, UAT readiness, and whether it belongs in the v0.1.7 public CLI.
2. Publish one intentional public command set for v0.1.7. Do not infer this
   set from what happens to have a Click decorator.
3. Move every non-public command behind an explicit developer/internal entry
   point, for example `gigai internal ...`, or mark it hidden from normal help
   under a documented developer-mode gate. The exact mechanism must retain
   test access without silently expanding the release surface.
4. For every retained public command, either make its prerequisite discoverable
   in the normal workflow or replace the raw failure with a short actionable
   message that identifies the next public command. A user typing a discovery
   command must not need to understand registry, workpad, proposal, or active
   Gig internals to learn why it cannot run.
5. Do not hide `catalog list` or change G42 catalog separation. Do not make
   `gigs` a global fallback; BUG-001 remains the contract for its
   project-scoped listing behavior.

### 2. Provide a supported credential-configuration flow

6. `gigai setup` must offer a clear, optional API-runtime configuration path
   for each supported API provider/target. It must state the target/provider,
   credential reference mechanism, and whether entering or changing a value
   will make a network call. It must never echo a key.
7. Credentials must remain outside repository targets, packages, journals,
   proposals, catalog artifacts, and normal human output. Prefer an existing
   OS/keychain or environment-reference mechanism where one is already
   supported; if v0.1.7 cannot securely accept a value, setup must explicitly
   say where the operator sets the required external reference instead of
   implying that the target is ready.
8. `gigai models` must distinguish `not configured`, `credential reference
   missing`, `configured but unprobed`, and `ready after explicit probe`. It
   must show a concise next action for the first two states and must not
   disclose credential values or filesystem locations that identify secret
   stores.

### 3. Remove internal target identifiers from ordinary UX

9. Never render `offline-default`, `codex-default`, or `claude-default` in
   ordinary interactive prompts, setup review, standard help, or default
   human output. Use `Codex CLI` and `Claude Code`, including detected CLI
   version where available. Deterministic test adapters are not part of the
   operator-facing model inventory.

## Implementation amendment from S42

S42's **ADOPT WITH AMENDMENT** outcome is now the implementation contract for
this bug. The following decisions are normative.

### Public command boundary

The v0.1.7 public command set is:

```text
setup doctor models
init upgrade
catalog list|inspect|validate|install
package inspect|install|export
create invoke
approve reject revise feedback
gigs proposals status show history plan
run run-details
occurrence declare|trigger|reconcile|mark|close|compare
check open workpad path
```

`eval contract`, `eval behavior`, and `improve` remain available through the
hidden developer namespace:

```text
gigai internal eval contract ...
gigai internal eval behavior ...
gigai internal improve ...
```

They must not appear in ordinary `gigai --help`. This is a presentation/access
boundary only; their underlying capability is not removed from the complete
v0.1.7 release scope. Existing authority and test seams remain unchanged.

### Stable prerequisite guidance

Project-scoped commands must preserve their existing error codes and JSON
shape while adding an actionable message. An unbound target must say that the
operator should run `gigai init --target PATH`; a target with no proposal must
point to `gigai create NAME --target PATH`; a proposal requiring approval must
point to `gigai approve PROPOSAL_ID`; and a Run-dependent command must identify
the missing approved Gig or Run prerequisite. Guidance must never create state.

### Runtime and credential projection

`gigai models` must expose a safe projection containing the stable target ID,
human display label, primary state, ordered state set, safe reason, next action,
fixture flag, and credential-reference status. Human output uses labels only;
JSON may include stable IDs. Environment references are the supported usable
credential path in this implementation. Other reference syntax may be
validated and recorded, but it must not be reported usable until a resolver is
implemented and evidenced.

Recording or changing a credential reference makes no provider call. Only an
explicit readiness probe may resolve the external value or incur provider
cost. Secret values must never enter configuration, packages, journals,
proposals, reports, tests, or CLI output.

### Acceptance mapping

Implementation is complete only when tests prove:

- the public help snapshot contains the public set and omits the developer
  namespace and its commands;
- developer commands remain callable through `gigai internal ...`;
- unbound and missing-prerequisite errors provide stable actionable guidance;
- API references report missing, configured, and explicitly probed states;
- human output never renders internal target IDs; and
- clean-home, credential-redaction, no-provider-call, and stable JSON UAT pass.

## Acceptance evidence

- A clean v0.1.7 `gigai --help` shows only the approved public command set;
  the command inventory documents every omitted/internal command and its
  explicit access boundary.
- For each public command requiring a project, Gig, proposal, approval, or
  Run, UAT transcripts prove either a complete public path to that prerequisite
  or a concise error pointing to the next public action.
- A new operator can configure an API-backed target through the documented
  setup flow, or is clearly directed to the supported external credential
  reference. No transcript, JSON output, project file, package, journal, or
  test fixture contains the raw key.
- `gigai models` reports the defined credential/readiness states and a safe
  next action without printing secrets.
- Interactive UAT never displays `offline-default`, `codex-default`, or
  `claude-default`; fixture mode remains explicit and clearly tests-only.
- Regression tests cover the public help snapshot, internal-command access
  gate, no-bound-project guidance, API credential-reference setup, secret
  redaction, model-state output, and internal-ID suppression.

## Non-goals

- This bug does not implement model-provider calls, relax consent boundaries,
  or make every historical lifecycle command public in v0.1.7.
- This bug does not place credentials in `.gigai`, project configuration,
  catalog packages, or version-controlled files.
- This bug does not replace BUG-001's `gigs` listing, project-resolution,
  registry-authority, or catalog-separation contract.

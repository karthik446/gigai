# S42 — Release CLI Surface and Runtime Configuration UAT

**Date:** 2026-08-30
**Status:** Complete — ADOPT WITH AMENDMENT; no implementation authorization
**Depends on:** BUG-002, G40, G41, and G42 evidence at the current v0.1.7 baseline
**Output:** One bounded research outcome and recorded contract implications
**Related:** [BUG-002 release CLI and runtime configuration](../bugs/BUG-002-release-cli-surface-and-runtime-configuration-are-not-human-uat-ready.md)

## Purpose

S42 determines whether the v0.1.7 command surface and runtime configuration
flow are understandable and usable by a new operator. It turns BUG-002's UAT
observations into a reviewable command contract, runtime-state vocabulary, and
evidence plan.

S42 does not decide the v0.1.7 roadmap, activate a goal, or authorize runtime
changes. It does not reduce the complete v0.1.7 release scope. It determines
which released operations are public, which require explicit prerequisites, and
which need a controlled developer boundary until their operator workflow is
ready.

## Research questions

1. What exact command and subcommand set is public for the complete v0.1.7
   release?
2. Which current commands are operator-facing, lifecycle-facing, evaluation-
   facing, or developer-only, and what evidence supports each classification?
3. What prerequisite and effect information must be visible before a command
   is presented as a normal next step?
4. What should an unbound, unconfigured, unapproved, or unavailable state tell
   the operator to do next?
5. Which API providers and credential-reference forms are supported by the
   v0.1.7 setup flow, and how are their states reported without reading or
   printing secret values?
6. What exact model-state algebra is required for `gigai models`, including
   detection, configuration, credential availability, probing, verification,
   usability, selection, and fixture mode?
7. Which internal target identifiers must be suppressed from human output, and
   what stable display vocabulary replaces them?
8. What clean-environment, configured-environment, and failure-path evidence is
   sufficient for human UAT and regression tests?

## Evidence baseline

The source and tests are reviewed at commit `b48c41c`:

- `src/gigai/cli.py` for Click registration, help text, setup, models,
  lifecycle commands, error handling, and human/JSON output;
- `src/gigai/model_discovery.py` for discovery and readiness states;
- `src/gigai/credentials.py` for credential-reference validation and
  availability checks;
- `src/gigai/setup.py` and `src/gigai/setup_interview.py` for setup persistence
  and operator-facing setup support;
- `tests/test_g40_runtime.py`, `tests/test_setup_browser.py`, and relevant
  CLI tests for existing contract evidence; and
- `docs/development/v0.1.7/goals/G40-seamless-local-runtime-and-cli-adapters.md`
  and `docs/development/v0.1.7/roadmaps/v0.1.7-roadmap.md` for accepted
  boundaries and release obligations.

## Scope

### Included

- inventory the current top-level commands and command groups;
- classify each command by owner, intended operator, prerequisites, authority,
  destructive or external effects, and UAT readiness;
- define the exact public command set and the explicit boundary for commands
  that remain developer-only;
- define actionable prerequisite diagnostics for public commands;
- inspect terminal setup and non-interactive configuration paths for API
  provider references;
- define safe human and JSON representations for model/runtime states;
- define display labels for local CLIs, API providers, and the offline fixture;
- define clean-environment UAT scenarios, expected output, exit behavior, and
  secret-redaction checks; and
- record recommendations for BUG-002 without changing implementation or
  authority.

### Explicitly excluded

- implementation changes to the CLI, adapters, setup, or configuration;
- provider calls, credential acquisition, or changes to credential ownership;
- changes to proposal, approval, journal, workpad, capability, or Run
  authority;
- replacement of BUG-001's project-scoped `gigs` contract;
- redesign of the complete v0.1.7 goal sequence; and
- a release declaration or completion claim for any implementation goal.

## Current observations to verify

These are research hypotheses grounded in the baseline, not accepted contract
decisions:

1. The ordinary help surface exposes setup, discovery, package/catalog
   operations, proposal lifecycle, Runs, occurrences, evaluation, and workpad
   operations together. The release needs an explicit classification rather
   than relying on Click registration as the public contract.
2. `gigai setup` is terminal-native at the current baseline, but API provider
   configuration is primarily represented by explicit advanced flags. S42 must
   determine whether that is a sufficient supported human path and document
   the exact invocation and resulting state.
3. `gigai models` already reports redacted runtime evidence in JSON, but its
   human output currently includes configured target identifiers and does not
   consistently expose the credential-reference-missing and next-action
   states required by BUG-002.
4. Readiness currently distinguishes deterministic fixtures, configured
   targets, and successful explicit probes, but the mapping for missing API
   references, failed authentication, and unavailable local executables needs
   one operator-facing vocabulary.
5. Some command options and historical lifecycle paths retain implementation
   terminology or legacy defaults. S42 must identify every ordinary help and
   human-output leak, not only the three identifiers named in BUG-002.

## Required research artifacts

S42 must produce the following artifacts as part of its outcome:

### Command inventory

One table covering every current top-level command and subcommand with these
columns:

| Command | Owner/goal | Intended operator | Prerequisites | Effects | Public disposition | UAT evidence |
|---|---|---|---|---|---|---|

The inventory must name the public set. “Public” means a command shown in
ordinary help and supported by a complete operator path; “internal” means its
access mechanism, help visibility, and test access are explicitly defined.

The inventory must preserve the complete v0.1.7 release scope. A command is
not made internal merely because its implementation is incomplete; its
classification must identify the contract or evidence still required.

### Runtime-state contract

Define one state algebra and its precedence for each configured or discovered
target. At minimum, the research must settle the meaning and transitions for:

```text
not_configured
detected
configured
credential_reference_missing
compatible
authenticated
verified
usable
selected
fixture
```

The result must specify which states are observational, which require an
explicit probe, which are persisted, and which may authorize no operation.
It must define the human label, safe next action, JSON field, and exit behavior
for each terminal or blocked condition.

### Credential-flow contract

Document the supported provider examples and exact CLI path for recording a
credential reference. The contract must state:

- whether the input is a secret value or a reference name;
- where the reference is stored;
- when a value may be resolved;
- whether setup or probing makes a network/provider call;
- how missing, invalid, unavailable, and successful references appear; and
- what evidence proves that raw values do not enter configuration, packages,
  journals, proposals, reports, test fixtures, or normal output.

### Human-output vocabulary

Define the display label for every supported target class, including local
runtime version presentation, API provider presentation, and the explicit
tests-only fixture. The inventory must identify every command/help path that
can render an internal target ID and provide a suppression test.

### UAT matrix

The matrix must cover at least:

- clean home with no configured editor or provider reference;
- detected local CLI with and without version evidence;
- configured API target with missing and available reference;
- explicit readiness probe success, authentication refusal, and bounded
  failure;
- unbound target guidance for project-scoped commands;
- proposal, approval, and Run prerequisite guidance;
- public help and internal-command visibility; and
- JSON redaction and stable error shapes.

Every case needs setup state, exact command, expected human/JSON result, exit
code, side effects, and the authority that must remain unchanged.

## Security and authority boundaries

S42 must preserve these boundaries:

1. Runtime discovery is local observational evidence, not configuration,
   selection, approval, or Run authority.
2. Credential references are metadata; secret values may be resolved only at
   the adapter boundary and must not be returned to setup, diagnostics, or
   serialization callers.
3. A readiness or authentication result does not approve a Gig, select an
   active version, allocate a Run, or authorize a target effect.
4. Human guidance may explain a prerequisite but cannot silently create it.
5. The public command boundary must not create a second journal, workpad,
   proposal, approval, capability, or Run authority.

## Research findings

### Command surface

The current Click surface contains the following observed commands. The
recommended disposition distinguishes the release-facing contract from the
developer/evaluation boundary; an internal disposition is an access decision,
not a removal or deferral of the underlying v0.1.7 capability.

| Surface | Commands | Recommended disposition | Evidence still required |
|---|---|---|---|
| Installation/runtime | `setup`, `doctor`, `models` | Public | Clean-home setup, state output, safe redaction, and explicit probe behavior |
| Project/package bootstrap | `init`, `upgrade`, `package inspect`, `package install`, `package export` | Public | Target/package prerequisites, migration recovery, and no-authority-import evidence |
| Catalog | `catalog list`, `catalog inspect`, `catalog validate`, `catalog install` | Public | G42 package identity, installation, and symlink-boundary evidence |
| Agent seam | `create`, `invoke` | Public agent-facing | Strict envelope, actor/consent, proposal validation, and transcript exclusion evidence |
| Proposal lifecycle | `approve`, `reject`, `revise`, `feedback` | Public operator-facing | Complete prerequisite guidance and authority-transition evidence |
| Project/Gig projections | `gigs`, `proposals`, `status`, `show`, `history`, `plan` | Public operator-facing | BUG-001 behavior, project guidance, and deterministic output evidence |
| Run lifecycle | `run`, `run-details`, `occurrence declare`, `occurrence trigger`, `occurrence reconcile`, `occurrence mark`, `occurrence close`, `occurrence compare` | Public operator-facing | Consent, occurrence identity, bounded execution, and terminal-state evidence |
| Validation/editor | `check`, `open`, `workpad path` | Public with explicit prerequisites | Read-only validation and clear target/editor prerequisite guidance |
| Improvement interview | `improve` | Internal until its CLI contract is complete | Agent/CLI seam, authority, and operator UAT evidence |
| Evaluation | `eval contract`, `eval behavior` | Internal/developer | Versioned evaluation contract and release-gate documentation |

The public command set is therefore the first eight rows, with the
validation/editor row available only where its explicit prerequisites are met.
`improve` and `eval` remain callable through a documented developer boundary
until their own operator contracts are evidenced. This does not authorize
changing command registration; it records the surface decision for BUG-002
implementation.

### Current UAT result

The research was run against `b48c41c` using the current help surface, isolated
temporary homes, and existing focused tests. No repository target, journal,
proposal, approval, or Run authority was changed.

| Scenario | Result | Finding |
|---|---|---|
| `gigai --help` inventory | FAIL | All observed commands are shown together; no public/internal distinction exists. |
| `gigai improve --help` | FAIL | The default `offline-default` target identifier is exposed in standard help. |
| Clean non-interactive setup without editor | PASS | Structured `setup_editor_invalid` failure is returned without a traceback. |
| Non-interactive setup with local editor | PASS | Setup completes and persists only machine-local configuration. |
| Local CLI discovery | PASS | Codex and Claude detection/version evidence is bounded and provider-free. |
| Human `gigai models` output | FAIL | Configured target identifiers are printed instead of display labels. |
| JSON `gigai models` output | PASS with limitation | Runtime paths are redacted; credential availability and next-action states are absent. |
| API credential-reference recording | PASS with limitation | Environment references can be recorded without values; the terminal flow does not offer a named provider step. |
| Missing API credential reference | BLOCKED | Current readiness resolution reports `configured`, not `credential_reference_missing`. |
| Unbound project command | FAIL | The error identifies the binding problem but does not provide the next public action. |
| Focused runtime/setup UAT | PASS | 54 tests passed across G40, setup, creation, and model-discovery coverage. |

### Runtime-state conclusion

The required operator model is a state projection, not a replacement for
configuration or Run authority:

| State | Meaning | Evidence | Safe next action |
|---|---|---|---|
| `not_configured` | No target exists for the requested provider/role | Configuration inventory | Add a supported reference/target through setup |
| `detected` | A supported local executable was found | Discovery snapshot | Configure or select the corresponding target |
| `configured` | A typed endpoint and target resolve | Configuration plus adapter binding | Run the explicit bounded probe if needed |
| `credential_reference_missing` | An API reference is absent or unavailable | Reference presence check, never secret value | Set the named external reference, then refresh |
| `compatible` | Target configuration satisfies adapter/capability shape | Factory and capability validation | Authenticate/probe explicitly |
| `authenticated` | Provider-owned authentication succeeded | Bounded probe evidence | Continue to verification result |
| `verified` | The explicit readiness check passed | Probe result and evidence | Target may become usable/selected under role policy |
| `usable` | The target is permitted for the requested operation | Verified capability and policy | Use only within the selected operation |
| `selected` | An operator/configuration policy assigned the target to a role | Profile/configuration authority | Does not itself approve or start work |
| `fixture` | Deterministic local test path with no model/provider call | Target adapter identity | Use only when explicitly requested as fixture mode |

The current implementation proves `configured` for unresolved API references,
and its human `models` output does not expose this algebra. S42 therefore
recommends adding a stable display projection with `state`, `next_action`, and
safe reason fields. A discovery snapshot remains observational evidence and
must not be upgraded into selection, approval, or Run authority.

### Credential-flow conclusion

The supported v0.1.7 path is reference-only configuration:

```text
operator sets external value
    -> gigai setup records NAME=KIND:REFERENCE
    -> gigai models reports reference state without resolving the value
    -> explicit probe may resolve at the adapter boundary
```

The current parser accepts environment references and validates secret-manager
reference syntax, while the local resolver only resolves environment values.
The implementation contract must therefore either limit the public usable path
to environment references or add and evidence a real supported secret-manager
resolver. It must not present a syntactically valid but unusable reference as
ready.

Setup and models must state that recording a reference makes no provider call;
only an explicit readiness probe may resolve a value or incur provider cost.
The reference name may appear in configuration diagnostics only when safe; the
secret value must never appear in any GigAI artifact or output.

### Human-output conclusion

Use these labels in ordinary human output:

| Internal class | Human label |
|---|---|
| `codex-default` | `Codex CLI` plus detected version when available |
| `claude-default` | `Claude Code` plus detected version when available |
| `offline-default` | `Offline fixture` |
| API endpoint target | Provider label, for example `OpenAI API` or `OpenRouter API` |

The suppression rule applies to help, prompts, summaries, errors, and default
human output. Stable IDs remain available in configuration, JSON, and explicit
developer diagnostics. The current `models` human output and `improve --help`
fail this rule and are direct BUG-002 implementation targets.

## Outcome

**ADOPT WITH AMENDMENT**

Adopt the CLI-first release-surface and reference-only runtime-configuration
direction, with these mandatory amendments recorded for BUG-002:

1. implement the explicit public/internal command boundary from the inventory;
2. make every public prerequisite failure actionable and stable;
3. define and render the complete runtime-state algebra, including missing
   credential references and safe next actions;
4. document the exact supported API reference path and keep unresolved
   secret-manager references from appearing usable;
5. suppress internal target IDs from all ordinary help and human output; and
6. complete the clean-environment, credential, redaction, and command-surface
   UAT matrix before claiming BUG-002 resolved.

This outcome is research evidence only. It authorizes no code, authority,
roadmap, or goal-status change.

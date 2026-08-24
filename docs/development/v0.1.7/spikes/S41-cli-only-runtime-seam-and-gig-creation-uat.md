# S41 — CLI-Only Runtime Seam and Gig-Creation UAT

**Date:** 2026-08-24  
**Status:** Complete — ADOPT WITH AMENDMENT; no implementation authorization  
**Depends on:** S40 completed research findings; v0.1.6 baseline  
**Output:** One terminal research outcome and recorded contract implications  
**Related:** [S40 CLI-first runtime discovery](S40-cli-first-seamless-local-runtime.md), [v0.1.7 handoff](../plans/v0.1.7-codex-handoff.md)

## Purpose

S41 determines the exact local runtime seam GigAI needs to support a reliable,
CLI-only workflow with Codex CLI, Claude Code, and later compatible agents. It
also validates that an invoking agent can use GigAI to configure and create
Gigs through explicit commands and durable GigAI authority.

S40 established the research finding: Orca's useful contribution is a set of
bounded host-runtime techniques, especially login-shell PATH hydration,
closed command catalogs, executable resolution, runtime identity, and
separate live-status observation. S41 now decides which of those techniques
GigAI adopts, adapts, or rejects, and proves the resulting seam through
repository-local and operator-run UAT.

S41 does not make Orca a dependency. It does not copy Orca's UI, plugin model,
skill system, orchestration, or authority semantics into GigAI.

## Candidate product direction under research

The candidate direction is CLI-first and browser-independent setup and Gig
creation:

```text
agent CLI
    -> explicit GigAI command or `$gigai` invocation
    -> GigAI CLI/JSON contract
    -> local discovery and readiness facts
    -> agent-configured draft
    -> GigAI validation and proposal
    -> explicit user approval
    -> approved Gig/version and optional Run
```

HTMX and browser setup/create surfaces are outside this spike's candidate path.
They must not be required, launched implicitly, or treated as a parallel
authority. HTML generation for a Gig index, Run report, or other projection is
outside this spike.

CLI/JSON/Markdown output is sufficient for setup, configuration, proposal,
approval, execution controls, and evidence inspection within this research
boundary. Existing journal, workpad, schema, proposal, approval, capability,
and sealed Run authorities remain unchanged.

## Research questions

1. Which specific Orca runtime mechanisms are sufficiently evidenced and safe
   for GigAI to adopt or adapt?
2. What is the smallest GigAI-owned interface between an agent CLI and GigAI?
3. Can GigAI reliably distinguish executable detection, configuration,
   compatibility, authentication, readiness, selection, and Run success?
4. Can a cold-start or non-login process discover local Codex and Claude
   commands without broad home-directory scanning or unsafe shell execution?
5. Can an agent explicitly invoke GigAI to create a Gig, configure a proposal,
   obtain approval, and leave durable evidence without browser or HTMX use?
6. Which runtime facts must be captured in a discovery snapshot, and which
   must remain configuration, journal, workpad, or Run authority?
7. Which local-host limitations must be explicit before any support claim?

## Scope

### Included

- local-host runtime discovery;
- bounded login-shell PATH hydration and failure reporting;
- closed command/agent catalog and supported aliases;
- executable resolution and optional bounded user-local install-directory
  fallback;
- version evidence and runtime identity;
- explicit local readiness probes and authentication-required outcomes;
- CLI-first `setup`, `models`, and `doctor` behavior;
- CLI/JSON/Markdown interaction as a target scenario for Gig proposal and
  creation;
- explicit agent invocation and user consent;
- Codex CLI and Claude Code adapter evidence;
- creation of representative Gigs through the GigAI CLI path, where the
  current implementation or a controlled fixture permits the scenario;
- proposal validation, approval, version creation, and optional Run launch;
- evidence redaction and portable-artifact checks;
- a decision matrix for adopting, adapting, or rejecting Orca mechanisms.

### Explicitly excluded

- any Orca runtime, package, plugin, or skill dependency;
- Orca UI or host orchestration;
- hidden prompt injection or ambient transcript capture;
- HTMX/browser setup or Gig creation as an S41 implementation dependency;
- HTML report/index generation;
- background daemon, scheduler, or automatic recurring Runs;
- WSL and SSH execution-host support without a separate host contract;
- automatic provider fallback, silent retry, or account-consuming discovery;
- changes to journal/workpad, proposal/version, capability, approval, or Run
  authority without a separately reviewed contract;
- broad domain-Gig quality claims. S41 validates the creation/runtime seam,
  not the quality of every future built-in Gig.

## Orca adoption matrix

S41 records the completed version of this matrix with pinned source revision
evidence. A source reference alone is not proof that GigAI should adopt the
behavior.

| Orca mechanism | GigAI decision | GigAI-owned boundary | Required evidence |
|---|---|---|---|
| Login-shell PATH hydration | ADOPT WITH AMENDMENT | Bounded local discovery environment only | Noisy-shell, timeout, malformed-output, and refresh cases |
| Closed agent command catalog | ADOPT WITH AMENDMENT | GigAI-supported target registry | Alias, unsupported-agent, and version cases |
| Real executable resolution | ADOPT | No shell alias/function authority | Executable-vs-alias/function cases |
| Bounded install-directory fallback | ADOPT WITH AMENDMENT | No broad home scan; explicit paths only | Missed-PATH and false-positive cases |
| Runtime target identity | ADOPT WITH AMENDMENT | Snapshot metadata, never Run authority | Local-host identity and unavailable-host cases |
| WSL/SSH forwarding | REJECT | Separate future execution-host contract | Explicit stop evidence |
| Hook-derived live status | ADOPT WITH AMENDMENT | Observational status only; Run state remains GigAI-owned | Event provenance and stale-status cases |
| Orca plugin API | REJECT | Future bridge may call GigAI CLI/IPC | No duplicate proposal, Goal, or Run state |
| Orca skill discovery | REJECT | Separate future packaging research | No effect on model readiness |
| Orca UI/workspace orchestration | REJECT | CLI/JSON/Markdown first | Browser-free UAT evidence |

Every adopted mechanism must record its source repository, pinned revision,
file/section, behavior reproduced, security adaptation, and reason it belongs
in GigAI. “Similar to Orca” is not sufficient evidence.

## Pinned Orca evidence audit

The audited Orca source was clean at commit
`8d08d0078deffb9b3c164013d2bc4a0a2a1ad6c1` (`fix(codex): stop an unreadable
legacy hooks.json from clearing managed trust`). The relevant source paths at
that revision are:

- `src/main/startup/hydrate-shell-path.ts`;
- `src/main/preflight/agent-detection.ts`;
- `src/main/ipc/preflight-command-exec.ts`;
- `src/shared/tui-agent-config.ts`;
- `src/shared/tui-agent-detection-commands.ts`;
- `src/shared/local-agent-install-dir-detection.ts`;
- `src/shared/agent-status-types.ts`;
- `src/main/agent-hooks/server.ts`;
- `src/shared/plugins/plugin-manifest.ts`;
- `src/shared/plugins/plugin-host-api.ts`;
- `src/shared/plugins/plugin-capabilities.ts`; and
- `src/main/skills/discovery.ts`.

The source audit confirms these adoptable runtime techniques:

1. login-shell PATH hydration uses sentinel-delimited output, strips ANSI
   noise, preserves shell ordering, caches the result, and bounds the shell
   operation at five seconds;
2. detection uses a closed command catalog and distinguishes executable
   resolution from shell aliases/functions;
3. install-directory fallback is bounded and only used for missed commands;
4. refresh explicitly invalidates or refreshes discovery state after an agent
   installation;
5. runtime context can distinguish local, WSL, and remote execution hosts; and
6. live status is an observation stream, not proof of GigAI Run authority.

The audit rejects these as GigAI runtime dependencies or authority sources:

- Orca's Electron UI and workspace orchestration;
- Orca's plugin host API and plugin-private storage;
- Orca's skill discovery and package roots;
- WSL/SSH forwarding without a separate GigAI execution-host contract; and
- any status, process, or agent identity fact as a substitute for GigAI's
  configuration, proposal, approval, journal, workpad, or Run records.

## Controlled CLI-only UAT evidence

The UAT used an isolated temporary GigAI home, workpad root, and non-Git target.
No credentials, private Runs, provider payloads, or machine-specific evidence
were committed.

| Scenario | Result | Evidence and limitation |
|---|---|---|
| Discovery of installed Codex and Claude | PASS | Both executables and bounded version strings were detected; no provider call was made. |
| Clean non-interactive setup without editor | FAIL | Setup raised an uncaught `ValueError` requiring `--editor` instead of returning a CLI diagnostic. |
| Non-interactive setup with explicit safe editor | PASS | Configuration and standard pack were created with `--no-open`; atomic replacement and lock checks passed. |
| Setup rerun | PASS | Second run reported `config_changed=false` and `standard_pack_changed=false`. |
| `models --json` | PASS | Offline target was `usable`; Codex and Claude targets were `configured` with explicit probe required. |
| Offline `doctor --json` | PASS | Typed configuration, paths, editor argv, deterministic adapter, atomic replacement, lock, and journal checks passed. |
| Missing-home `doctor` | PASS | Returned a structured configuration failure rather than claiming readiness. |
| Explicit non-Git target binding | PASS | `init --json` created a registry binding without creating a Gig or Run. |
| Offline CLI Gig proposal creation | PASS | `create --offline --no-open --json` produced a proposed Gig and durable proposal artifacts. This is a controlled fixture path, not agent-backed creation. |
| CLI approval | FAIL | `approve` failed before transition with `TypeError: approve_command() missing 1 required positional argument: capability_manifest_id`; the Click option is absent from the command declaration. |
| Underlying approval authority | PASS | The existing lifecycle function sealed an approved version when invoked directly with no capability manifest; this proves core authority only, not CLI approval. |
| CLI status, gigs, proposals, and plan after approval | PASS | CLI projections reported the approved version and Goal Graph without changing authority. |
| CLI deterministic Run and run-details | PASS | Run succeeded, target-before/after digests matched, usage was zero/not-applicable, and durable run details were readable. |
| Explicit Codex readiness probe | FAIL/CURRENT HOST LIMITATION | Codex was detected but its bounded probe failed with a permission error while creating PATH aliases; no GigAI fallback occurred. |
| Explicit Claude readiness probe | AUTHENTICATION REQUIRED | Claude was detected, but the restricted child reported `authentication_required: Not logged in`; this remained distinct from detection. |
| Non-offline `create --no-open` path | FAIL FOR CLI-ONLY TARGET | Static audit shows it still instantiates `InterviewHTTPServer`; `--no-open` suppresses browser opening but does not remove the HTTP dependency. |

The UAT therefore proves the existing offline lifecycle and authority boundary,
but does not prove a CLI-only agent-backed Gig creation path. Codex/Claude
executable detection is usable evidence; provider readiness and agent-backed
creation remain explicitly unresolved rather than inferred from detection.

## GigAI authority result

The current boundaries remain coherent:

- discovery and readiness are reports/probes, not configuration authority;
- `config.toml` remains machine configuration authority;
- the registry binds a target to a project/workpad;
- proposal artifacts remain non-authoritative until approval;
- the journal/workpad and active-version record remain Gig/version authority;
- the sealed Run manifest and run-details remain execution/evidence authority;
- CLI projections do not approve, execute, or adjudicate; and
- no Orca process, plugin, hook, or status event is allowed to create GigAI
  authority by itself.

The missing piece is not a competing authority. It is a missing CLI seam: the
runtime discovery snapshot, CLI approval contract, and agent-backed proposal
path have not yet been implemented or contractually accepted.

## GigAI runtime seam

The candidate seam is deliberately narrow:

```text
runtime discovery
    -> discovery snapshot
    -> configuration/readiness interpretation
    -> explicit agent or CLI invocation
    -> GigAI proposal/version/Run authority
```

The discovery snapshot is immutable evidence for one discovery operation. It
may include runtime kind, platform, PATH source/status, executable identity,
version evidence, probe status, and bounded failure details. It is not:

- configuration authority;
- credential authority;
- capability-installation authority;
- Gig or version authority;
- journal or workpad authority;
- approval authority;
- Run-Plan authority; or
- proof that a provider call or Review/Verify loop succeeded.

The required state distinctions are:

```text
detected -> configured -> compatible -> verified -> usable -> selected
```

These are not interchangeable. A local executable can be detected while its
provider is unauthenticated, its requested model is unavailable, or its
capabilities are incompatible with a Gig.

## CLI-only UAT target scenario

S41 should test the following target path without importing, starting, or
depending on HTMX/browser setup. Any unimplemented segment must use a
controlled fixture or be recorded as an evidence failure; passing a fixture
does not establish runtime support:

```text
setup
  -> models
  -> doctor
  -> project-local init
  -> agent-invoked Gig creation
  -> draft validation
  -> explicit proposal approval
  -> approved Gig/version inspection
  -> optional explicit Run request
  -> CLI/JSON/Markdown evidence inspection
```

Where the target path is available, the UAT must use representative creation
tasks rather than only a help path. At minimum, the target scenarios should
cover:

- Codex CLI as the invoking/configuring agent;
- Claude Code as a second supported agent or an explicitly recorded
  unavailable case;
- a deterministic/offline path for structural validation;
- a project-local `.gigai` package, as a target package boundary or controlled
  fixture rather than a current support claim;
- a draft that requires user clarification or approval;
- a rejected or invalid draft;
- a successful approved Gig creation, as a target lifecycle scenario or
  controlled fixture rather than a current support claim;
- a repeated/idempotent setup or creation attempt; and
- a case where discovery succeeds but readiness or authentication fails.

The UAT records the exact command surface, actor, consent point, input class,
authority transition, durable artifact, and terminal outcome. It must not
capture the surrounding agent transcript or claim that an agent's ordinary
conversation was imported into GigAI.

## Required evidence

### Runtime evidence

- inherited PATH and login-shell PATH comparison;
- noisy shell and hydration failure behavior;
- Codex/Claude present, absent, and installed in supported user-local paths;
- executable-versus-alias/function distinction;
- bounded version probing and malformed/timeout outcomes;
- runtime identity in every discovery snapshot;
- refresh behavior after a CLI becomes available;
- detected/configured/compatible/verified/usable state separation;
- authentication-required versus missing-executable distinction;
- no network or paid-provider activity during ordinary discovery;
- no hidden fallback, retry, or provider substitution.

### Gig-creation evidence

- explicit agent invocation of GigAI;
- CLI-only setup and configuration;
- draft JSON or equivalent proposal input remains non-authoritative;
- validation rejects unsupported fields, effects, or capabilities;
- approval is required before immutable Gig/version authority exists;
- approved version and optional Run retain existing lineage and journal rules;
- repeated setup/creation behavior is idempotent or explicitly refuses;
- user cancellation leaves no partial authority;
- invalid or unavailable targets fail closed;
- no browser, HTMX server, hidden prompt, ambient transcript, or raw secret
  is required.

### Portability and privacy evidence

- no credentials, tokens, private Runs, caches, absolute machine paths, or
  installed tool bytes enter portable packages;
- local absolute paths are redacted from share-safe reports;
- discovery snapshots are clearly labeled local evidence;
- Orca source references contain pinned, reproducible provenance;
- generated CLI output is stable and color-free in JSON/non-TTY modes.

## Decision outcomes

S41 ends with one explicit outcome:

- **ADOPT:** the mechanism and GigAI boundary are sufficiently evidenced;
- **ADOPT WITH AMENDMENT:** the mechanism is useful but requires a recorded
  security, authority, or scope amendment;
- **REJECT:** the mechanism is not needed, safe, or compatible with GigAI;
- **BLOCKED:** required host, agent, or UAT evidence cannot be obtained.

The outcome must include:

1. the completed Orca adoption matrix;
2. the GigAI runtime seam and authority map;
3. the CLI-only UAT report and failure cases;
4. the exact G40/G41/G43 contract implications;
5. unresolved limitations and explicit stop conditions; and
6. contract implications recorded for later consideration.

## Stop conditions

Stop S41 before implementation authorization if:

- Orca behavior cannot be pinned to reproducible source evidence;
- PATH hydration requires unrestricted shell execution or broad filesystem
  scanning;
- discovery cannot remain separate from configuration, credentials, or Run
  authority;
- CLI-only setup cannot safely represent the required consent decisions;
- Gig creation requires importing the surrounding agent transcript;
- Codex/Claude adapter behavior cannot preserve provider identity and failure
  evidence;
- browser/HTMX remains necessary for ordinary setup or Gig creation;
- a proposed runtime cache starts acting as a second authority; or
- the UAT would require credentials, private Runs, or machine-specific data
  to be committed as evidence.

## Research completion order

S41 completes independently in this order:

1. pin and audit the Orca source evidence;
2. complete the adopt/adapt/reject matrix;
3. define the GigAI-owned runtime snapshot and state boundary;
4. run the CLI-only setup, discovery, and readiness matrix;
5. run agent-invoked Gig-creation UAT with Codex and Claude where available;
6. record the decision outcome and contract implications; and
7. stop. Any later planning or implementation work considers the recorded
   implications separately.

## Terminal outcome

**ADOPT WITH AMENDMENT**

This is a behavioral adoption decision, not permission to copy Orca code. Orca
is pinned to commit `8d08d0078deffb9b3c164013d2bc4a0a2a1ad6c1`.

### Adopt from Orca

The following specific Orca behaviors are useful to GigAI:

- **Login-shell PATH hydration:** adopt the bounded shell-path discovery,
  sentinel-based output parsing, ANSI stripping, caching, and explicit refresh
  behavior evidenced by `src/main/startup/hydrate-shell-path.ts`.
- **Closed runtime catalog:** adopt an explicit catalog of supported agent
  commands and runtime identities, as evidenced by
  `src/main/preflight/agent-detection.ts` and
  `src/shared/tui-agent-detection-commands.ts`.
- **Real executable resolution:** adopt the distinction between a real
  executable and an alias, shell function, missing command, or unusable
  installation, as evidenced by `src/main/ipc/preflight-command-exec.ts`.
- **Bounded fallback lookup:** adopt narrowly scoped install-directory fallback
  behavior from `src/shared/local-agent-install-dir-detection.ts`; do not add
  unrestricted filesystem scanning.
- **Separated runtime status:** adopt separate evidence for detection,
  authentication, readiness, and live status from
  `src/shared/agent-status-types.ts`. These statuses remain observations, not
  GigAI authority.

### Amend for GigAI

Those behaviors must be implemented as GigAI-owned contracts at these existing
seams:

- `src/gigai/model_discovery.py` currently uses a fixed Codex/Claude catalog,
  `shutil.which`, and bounded version probing. Amend this seam to produce a
  GigAI-owned discovery snapshot containing runtime identity, resolved
  executable, PATH source, version evidence, refresh behavior, and bounded
  failure states. The snapshot is immutable evidence for one discovery
  operation; it does not replace configuration, the registry, journal,
  workpad, proposal/version, capability, or Run authority.
- `src/gigai/adapters/process.py` already provides explicit argv,
  `shell=False`, environment allowlisting, timeouts, and structured process
  failures. Retain those GigAI controls. Orca's bounded discovery timeout must
  not be copied blindly into longer-running model execution.
- `src/gigai/cli.py` currently makes setup browser-oriented and can raise an
  editor-related traceback in a clean environment. Amend setup so CLI setup is
  the primary v0.1.7 surface and failures are structured diagnostics.
- `src/gigai/cli.py` currently routes non-offline creation through
  `InterviewHTTPServer`. Amend creation so an agent-backed CLI flow preserves
  explicit questions, consent, proposal validation, and approval without an
  HTTP interview server, browser, HTMX, hidden prompt, or ambient transcript.
- `src/gigai/cli.py` currently has an approval decorator/signature mismatch.
  Amend CLI approval until it proves the complete proposal-to-approved-version
  authority transition already represented by `approve_offline` in
  `src/gigai/lifecycle.py`.
- Keep the state vocabulary explicit: detected, configured, compatible,
  verified, authenticated, usable, and selected are different states and must
  not be collapsed into one readiness value.

### Reject from S41

S41 rejects the following Orca responsibilities and surfaces:

- Orca as a GigAI dependency, imported library, service, or authority;
- Orca plugins, plugin host APIs, and skill discovery;
- Orca UI, workspace orchestration, and terminal ownership;
- WSL and SSH forwarding as part of this local-host runtime decision;
- live status as a source of GigAI configuration, approval, or Run authority;
- browser/HTMX setup and Gig creation for v0.1.7; and
- HTML indexes or HTML Run projections, which are outside this runtime seam.

This outcome records research findings and required contract amendments only;
it does not authorize implementation or create a roadmap.

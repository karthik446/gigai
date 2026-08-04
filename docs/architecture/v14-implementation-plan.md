# GigAI CLI implementation plan

> Standalone repository note: artifact paths in this approved V14 design
> snapshot were updated to the public repository layout. The authority,
> serialization, and product contracts are unchanged. Python version support is
> superseded by [ADR 0001](../adr/0001-python-version-range.md).

**Date:** 2026-07-30
**Updated:** 2026-08-02
**Revision:** 14
**Status:** approved implementation contract; Phase 0 complete
**Implementation target:** the standalone `gigai` repository
**Project target:** any initialized source repository or explicit non-repository target

This is the authoritative implementation-plan draft for GigAI. It replaces
revision 12's engineering-workflow framing while retaining the provider,
runtime-hardening, source-snapshot, and `check`/`doctor` evidence in:

- `docs/research/phase-0-spikes.md`;
- `docs/research/runtime-contract-hardening.md`;
- `docs/research/check-doctor-command-spike.md`;
- `docs/research/phase-0-contract-closure.md`;
- `research/phase0_spike/`.

Revision 14 retains revision 13's private workpad, `RunDetails`, provider, and
local-only contracts and corrects the execution model:

- a **Gig** is a finite, user-approved commission, not a reusable Python
  workflow;
- an approved Gig version is represented internally as a user-specific
  **Goal Graph**;
- each Goal defines its own proof, tools, effects, executor, budget, and
  transition policy;
- dependency edges express sequencing, independent ready Goals may execute in
  parallel, and multi-parent Goals form joins;
- `create` is the critical deliberative authoring path and produces a
  non-executable Gig Proposal containing an inspectable Markdown plan plus
  validated Goal Graph;
- `run` is the operator's execution decision: it resolves and seals a concrete
  Run Brief, then starts execution;
- every Gig owns a private, local-only Git workpad on a user-selected mount;
- every semantic transition produces a text handoff and local Git commit;
- completed evidence may feed explicit local improvement proposals, but GigAI
  sends no telemetry and learns nothing outside the user's machine.

The Forge goal package informed the goal, evidence, activation, and stop-boundary
shape. It is a design reference, not product-specific runtime content.

---

## 1. Core decision

GigAI is a private local operating layer for designing, agreeing upon,
executing, reviewing, and auditing bounded Gigs.

A Gig may be:

- a technical spike;
- specialized market or scientific research;
- a security, code, design, document, or database review;
- implementation or migration work;
- a product investigation;
- a planning and goal-decomposition engagement;
- any other finite task whose outcome can be made explicit and evidenced.

GigAI does not replace the engineer, researcher, domain expert, Codex, Claude,
API model, deterministic tool, or operator. It gives those participants one
agreed contract, bounded execution environment, durable handoff history, and
evidence-backed stopping point.

The core lifecycle is:

```text
commission
  -> bounded creation research
  -> Gig Proposal with user-specific Goal Graph
  -> independent review
  -> operator feedback and agreement
  -> operator approval creates an immutable Gig version
  -> `run` resolves questions and seals a concrete Run Brief
  -> one Gig Run starts
  -> ready Goals execute sequentially or in parallel
  -> joins, declared review gates, and recovery edges
  -> Gig completion audit and operator review
  -> Gig completion
  -> optional local graph-improvement proposal
```

GigAI provides:

- an interactive, evidence-producing `create` process;
- human-readable Markdown Gig and Goal contracts;
- explicit model, tool, data, mutation, and spend decisions;
- equal first-class API and CLI model adapters;
- a validated Goal Graph with user-specific acceptance and verification;
- a private per-Gig Git journal and immutable text handoffs;
- free structural validation and fixture-backed rehearsal;
- supervised local execution with durable `RunDetails` and invocation evidence;
- inspectable prompts, outputs, tools, usage, cost, and target changes;
- explicit review, amendment, recovery, and completion flows;
- opt-in local improvement based on the user's own Gig history.

GigAI does not provide:

- a chat client or replacement coding agent;
- an unbounded autonomous model loop;
- undeclared Goal activation, dependency changes, or parallel execution;
- silent plan, target, profile, or tool changes;
- a hosted control plane, account, telemetry service, or cloud history;
- a long-lived daemon, scheduler, TUI, or marketplace in v1;
- a claim that arbitrary Python or an external coding CLI is sandboxed;
- a claim that multi-model agreement is truth;
- automatic publication, synchronization, push, or remote backup.

## 2. Canonical vocabulary

The system must not overload these terms.

| Term | Meaning |
|---|---|
| **Gig Proposal** | Reviewable, non-executable candidate Gig contract and Goal Graph produced by `create`. |
| **Gig** | One finite commissioned body of work for a concrete outcome. |
| **Gig version** | One immutable, operator-approved version of the Gig contract and its Goal Graph. |
| **Goal Graph** | Versioned directed acyclic graph of Goals, dependencies, joins, gates, recovery edges, budgets, and transition policies that defines how one Gig runs. |
| **Goal** | One verifiable node in the Goal Graph with its own objective, tools, executor, effects, proof, evidence, and transition policy. |
| **Goal version** | One immutable, operator-approved version of a Goal contract. |
| **Run Brief** | Durable human-readable preflight and audit record for one Run, pinning its Gig version, target, models, tools, effects, exposure, cost, verification, and expected evidence before external execution. |
| **Run** | One invocation and execution of one approved Gig version and its complete Goal Graph. |
| **RunDetails** | Small durable aggregate status and result record for one Run, including per-Goal state, errors, and paths to its full local evidence. |
| **Handoff** | Immutable text written at a semantic transition, sufficient for the next actor to understand state and authority. |
| **Completion audit** | Requirement-to-evidence reconciliation for a Goal or completed Gig. |
| **Capability** | Reusable implementation supplied by GigAI, a pack, or the user for model calls, tools, review, or a goal-execution shape. |
| **Pack** | Versioned collection of reusable capabilities, prompts, schemas, fixtures, and evaluation cases. |
| **Workpad** | The private local directory and Git repository that owns one Gig's plan, journal, and evidence. |
| **Target** | Repository, directory, document set, database reference, or other subject on which the Gig operates. |

Consequences:

- a Gig ends; a capability or template may be reused;
- a completed Run never resumes as though its history changed;
- two Gigs in the same domain may have different Goal Graphs because their
  users, targets, tools, constraints, and verification differ;
- users approve a readable Gig plan; they do not have to draw or program a
  graph manually;
- `create` produces a Gig Proposal, not a Gig version;
- invoking `run` is the operator's instruction to execute an approved Gig
  version; it writes the Run Brief before launching external work;
- explicit operator approval turns only a Gig Proposal into a Gig version;
- feedback creates a new proposal or version rather than rewriting accepted
  history;
- completed Goals remain pinned when later Goals are revised;
- a reusable `security-review` implementation is a capability or template;
  `review service X before release Y` is a Gig;
- ordinary users do not need to author Python to create or run a Gig.

## 3. Authority, privacy, and trust

### 3.1 Authority invariants

1. A Gig Proposal is reviewable data and has no execution authority.
2. The operator approves a Gig Proposal before it becomes an immutable Gig
   version.
3. Invoking `run` grants execution authority for one Run of the selected
   approved Gig version; GigAI writes and seals its concrete Run Brief before
   the worker starts.
4. The approved `gig.md`, Goal files, and graph manifest are the work authority.
5. The approved Goal Graph determines dependencies, automatic transitions,
   parallel-ready Goals, joins, review gates, and recovery edges.
6. Multiple independent Goals may be active concurrently within the approved
   concurrency, effect-conflict, and budget limits.
7. A Goal may transition automatically only when its dependencies, evidence,
   outcome, and declared transition policy permit it.
8. Only an explicit operator gate pauses a healthy Run for review; ordinary
   Goal completion writes evidence and allows the graph to continue.
9. Unplanned graph changes, undeclared tools, or weakened proof stop the Run and
   may produce a new Gig Proposal.
10. The approved graph is immutable during a Run. Preapproved recovery edges are
   execution, not graph mutation.
11. A material amendment creates a Gig Proposal; approval creates a new version
    and records which prior evidence remains valid.
12. A Run may do only what its approved Goal Graph and aggregate effect policy
    authorize.

### 3.2 Local-only product contract

GigAI has no service-side user identity and no GigAI telemetry endpoint.

The user owns every Gig Proposal, Gig version, Goal Graph, Goal, Run Brief,
tool, verification rule, Run, handoff, evidence object, learned
preference, and graph-improvement proposal. GigAI runs those user-owned
artifacts locally; it does not acquire a copy or an interest in them.

GigAI never:

- uploads Gig history, prompts, evidence, preferences, or usage to its
  maintainers;
- creates or configures a Git remote for a Gig workpad;
- invokes `git fetch`, `pull`, `push`, or remote synchronization for a workpad;
- uses Gig material for product analytics or training;
- performs background network requests;
- copies a Gig into the target repository.

Explicit provider calls are different from GigAI telemetry. OpenAI, Anthropic,
OpenRouter, Codex CLI, and Claude CLI may transmit selected context when the user
authorizes a Run. Before dispatch, GigAI shows the provider, model target, data
classes, estimated calls, budget, and which target/workpad material can leave
the machine. The resulting handoff records the actual exposure.

Offline commands remain offline. Catalog refresh, live diagnostics, creation
research, model review, execution, and paid evaluation are individually marked
network actions and require the applicable policy confirmation.

### 3.3 Trust boundary

Trusted inputs are limited to:

- the installed GigAI runtime;
- immutable built-in packs shipped with that runtime;
- capabilities the user explicitly installed or authored;
- the operator-approved Gig version;
- exact capability and prompt bytes sealed for the Run.

The target is data, not executable GigAI code. Model output, generated code,
downloaded content, and newly proposed capabilities are untrusted until
validated and explicitly accepted.

Credentials are referenced, never copied into Gig files, Git history, argv,
prompts, artifacts, manifests, or SQLite. Final bytes are structurally redacted
and scanned before a handoff commit.

### 3.4 Effects and enforcement

Every Goal declares one or more effects:

```text
read_target
write_workpad
write_target
external_read
external_write
```

`read_target` and `write_workpad` are the default creation/research boundary.
`write_target` and `external_write` require explicit Goal language, preflight,
operator confirmation, and stronger before/after evidence.

Enforcement is reported as:

- `native`: the selected adapter provides the restriction;
- `enforced`: GigAI blocks the effect at an owned boundary;
- `observed`: GigAI can compare before/after state but cannot prevent it;
- `unavailable`: the restriction cannot be claimed.

Containment and observation are never described as a security sandbox.

## 4. Storage and project topology

### 4.1 Machine-wide home

`gigai setup` creates `GIGAI_HOME`, defaulting to `~/.gigai`:

```text
~/.gigai/
  config.toml
  registry.sqlite                 mount locators and rebuildable indexes
  credentials/                    references only; never raw secret values
  catalogs/                       provider metadata caches
  packs/
    builtin/<pack>/<version>/<content-hash>/
  capabilities/                   editable user-owned reusable capabilities
  learning/                       approved local user-level preferences
```

`GIGAI_HOME` is not the authoritative store for Gig content. It may index a
Gig's IDs, mount locator, current status, and last known commit, but it must be
possible to rebuild those entries by reopening the workpad.

### 4.2 Target-repository binding

`gigai init` works in any Git repository without modifying application source,
dependency files, or tracked `.gitignore`:

```text
target-repository/
  .gigai/
    project.toml
```

`git rev-parse --git-path info/exclude` receives exactly one idempotent entry:

```gitignore
/.gigai/
```

`project.toml` contains only stable local binding data:

```toml
schema_version = "1.0"
project_id = "project_<lowercase-rfc9562-uuidv4>"
active_gig_id = "gig_<lowercase-rfc9562-uuidv4>" # optional
workpad_locator = "registry:project_<lowercase-rfc9562-uuidv4>"
```

It contains no credential, prompt, absolute personal path, raw model selector,
Gig history, or executable code. An already tracked `.gigai/` is refused until
the user resolves it explicitly. Machine-readable Git status before and after
initialization must be identical.

An explicit `--target <path>` supports non-Git directories and documentation or
data-only work. Such targets use a user-local registry binding and never receive
an implicit `.gigai` directory.

Target identity is resolved filesystem identity, not raw path spelling. Git
targets begin at `git rev-parse --show-toplevel`; every existing target is
canonicalized with strict symlink resolution, and existing aliases are compared
with filesystem identity. Thus `/tmp/x`, its macOS `/private/tmp/x` resolution,
and an ordinary symlink alias converge on one binding. Canonical absolute target
locators remain only in the private user-local registry and never enter
`project.toml` or share-safe evidence. Durable records do not use device or inode
numbers as target identity.

G04 owns first-use creation of the minimal versioned `registry.sqlite` binding
schema. Project IDs and canonical target locators are transactionally unique.
For Git targets, a valid ignored `project.toml` is the authoritative project
identity; registry and `.git/info/exclude` state are derived and idempotently
reconciled after interruption without allocating a new project ID. For explicit
non-Git targets, the committed registry transaction is authoritative because no
target binding file is written. Concurrent initialization is serialized and
must converge on one identity or fail before mutation. Missing configuration,
unavailable or repointed aliases, corrupt registry state, and incompatible
registry versions fail closed rather than selecting or creating a fallback.

G05 owns the one-way registry upgrade from the exact G04 v1 schema to v2. The
v1 `projects` table and all populated project rows remain unchanged; v2 adds
only exact versioned `workpads` and `active_workpads` tables:

```text
workpads
  gig_id             canonical Gig ID; primary key
  project_id         existing project ID; foreign key
  workpad_locator    canonical absolute private path; unique
  unique(project_id, gig_id)

active_workpads
  project_id         primary key
  gig_id             registered Gig ID
  foreign key(project_id, gig_id) -> workpads(project_id, gig_id)
```

Before mutating any valid v1 registry, G05 serializes migration and atomically
publishes a durable mode-0600 `registry.sqlite.v1.bak` that remains a valid
openable v1 snapshot. It then creates both new tables and writes
`PRAGMA user_version = 2` last in one SQLite transaction. Every schema version
has an exact expected application ID, table-name set, `CREATE TABLE` text,
column shape, key, foreign-key, and uniqueness contract. A crash exposes either
complete v1 or complete v2, never a hybrid. A conflicting backup, unexpected
schema object, partial schema, or version other than exact v1 or v2 fails
closed. The migration provides no automatic downgrade; a G04-era binary
correctly refuses the live v2 file.

The implementation centralizes the coupled v2 constants: registry schema
version, exact expected table-name set, workpad-table SQL, and active-workpad
SQL. Migration and validation consume the same definitions. The existing
project-table SQL remains unchanged rather than being reconstructed from rows,
which preserves its `WITHOUT ROWID` and uniqueness contract.

Absolute target and workpad locators remain private registry data. For Git
targets, `project.toml` remains authoritative for project identity and its
optional `active_gig_id`; corresponding registry state is derived and
reconcilable. For explicit non-Git targets, the committed registry transaction
is authoritative for both binding and active-Gig selection. Diagnostics and
share-safe evidence never emit either absolute locator.

### 4.3 User-selected workpad mount

Setup asks for a workpad root. The default is `~/.gigai/workpads`, but an
external disk, encrypted volume, or other user-controlled local mount is a
first-class choice.

```text
<workpad-root>/
  projects/<project-id>/
    gigs/<gig-id>/
      .git/                        private local repository; no remote
        gigai-writer.lock          advisory interprocess writer lock
      .gitignore
      gig.md                       proposed until an approval commit freezes it
      goals/
        README.md
        00-<name>.md
        01-<name>.md
      handoffs/
        000000000001-creation-started.txt
        000000000002-gig-proposal-ready.txt
        000000000003-gig-proposal-approved.txt
        000000000004-run-started.txt
        000000000005-goal-00-started.txt
        000000000006-goal-00-complete.txt
      reviews/
      decisions/
      evidence/
        goal-00/
          completion-audit.md
      manifests/
        gig-proposal.json           proposed digest-pinning envelope
        creation-manifest.json      inputs and creation evidence
        goal-graph.json             machine projection of the Markdown Goals
        active-gig-version.json    explicit approved version used by default
      runs/
        <run-id>/
          run-brief.md             mandatory preflight and audit surface
          run-manifest.json        sealed before external execution
          run-details.json         atomically updated during execution
          summary.txt              terminal human-readable summary after Run
      objects/                     content-addressed raw payloads; Git-ignored
      scratch/                     disposable; Git-ignored
      state.sqlite                 rebuildable execution index; Git-ignored
```

The workpad is the only authoritative location for the Gig. If its mount is
unavailable, GigAI fails closed and prints the expected locator; it does not
silently create a second home-directory copy.

G05's provisioning primitive accepts canonical caller-supplied project and Gig
IDs and never allocates, replaces, or infers either one. It atomically publishes
only the empty private Git substrate, local repository identity, ownership
markers, and approved ignore rules at the deterministic path, then registers
that exact location. Each locator must resolve strictly beneath the configured
workpad-root authority; mount changes, escapes, and symlink redirection fail
closed. G05 exposes no public command that provisions or activates an empty
Gig. Before G08, explicit-ID workpad resolution is real, but a no-ID `workpad
path` or `open` returns `no_active_gig`. Only a G05 verification fixture may
seed active state before G08 becomes the first production lifecycle caller.
The initial `.gitignore` contains exactly `/objects/`, `/scratch/`, and
`/state.sqlite`; G05 creates none of those paths and writes no semantic file.

The Git-tracked journal contains the human contract, text handoffs, decisions,
reviews, stable manifests, and durable evidence. Large or sensitive raw model,
tool, and binary payloads live in content-addressed `objects/` and are referenced
by hash from committed manifests. `state.sqlite` is an index, not the truth.

### 4.4 Private Git journal

Every Gig gets a new local Git repository. GigAI configures a repository-local
identity such as `GigAI <local@gigai.invalid>` so global Git identity is not
required or leaked.

GigAI-managed workpad history is linear and append-only in v1:

- no remote is created;
- an existing remote is a blocking diagnostic until explicitly removed or the
  workpad is detached from GigAI management;
- GigAI never amends, rebases, resets, force-updates, or deletes journal
  history;
- one exclusive per-Gig interprocess writer lock serializes sequence
  allocation, stable-file replacement, handoffs, commits, and index updates;
- Git object cleanup may run only if it preserves every reachable journal
  commit;
- the user may inspect the repository with ordinary local Git commands.

GigAI commits semantic transitions rather than every streaming event. Model and
tool events accumulate as durable artifacts and are summarized by the next
handoff commit.

The lock lives at `.git/gigai-writer.lock` and every CLI, worker, and recovery
writer participates. Acquisition uses nonblocking lock attempts and a monotonic
10-second default deadline; timeout fails with
`interprocess_lock_unavailable` rather than waiting forever.

Handoff sequence is a per-Gig unsigned integer allocated at durable commit time
and formatted as 12 zero-padded digits. Normal allocation is O(1): while holding
the lock, the writer reads `GigAI-Handoff-Sequence` and `GigAI-Handoff` from the
current `HEAD` commit, verifies the named predecessor, and allocates the next
integer. The first commit allocates 1. SQLite never allocates sequence. A
conflicting next file, missing predecessor, invalid trailer, or uncommitted
orphan stops with `journal_reconciliation_required`; only the explicit recovery
path may scan and reconcile the directory.

The empty G05 repository has no `HEAD`. G06 treats that unborn state as the one
valid predecessor for a caller-supplied first semantic transition: under the
same writer lock it allocates sequence 1, writes the first canonical handoff,
and creates the first commit with the G05 infrastructure and required identity
trailers. G06 never allocates a project or Gig ID, provisions another workpad,
or selects an active Gig.

Parallel Goal completion therefore has one strict committed journal order;
parent handoff IDs, Goal IDs, Run IDs, timestamps, and invocation IDs preserve
causality. `setup` and `doctor` prove two-process exclusion and atomic
replacement on the selected mount or fail writes with
`interprocess_lock_unavailable`.

Commit subjects are readable:

```text
gig: create initial research proposal
gig: incorporate operator feedback
gig: approve proposal as version 2
run: start version 2 against target HEAD abc123
goal(G00): activate research contract
goal(G00): complete with evidence
goal(G01): block on missing point-in-time data
gig: add recovery goal after operator review
gig: close with completion audit
```

Commit trailers include Gig, Gig version, Goal, Goal version, Run, handoff,
handoff sequence, previous handoff, outcome, evidence manifest, target HEAD,
and cost status. The commit SHA is then written back to the rebuildable index.

### 4.5 Opening a Gig

Setup records the user's default IDE. Resolution fallback is configured IDE,
`$VISUAL`, `$EDITOR`, then an explicit error with the resolved path; GigAI does
not silently select an editor.

From anywhere inside an initialized target repository:

```bash
gigai open                   # active Gig's complete private workpad repository
gigai open <gig-id>          # selected Gig
gigai open --target          # target repository
gigai open --with-target     # IDE workspace containing both locations
gigai workpad path [<gig-id>]
```

If no Gig is active, `open` presents local Gigs for that project and offers to
start `create` once the G08 lifecycle exists. Before G08, the same condition is
a typed `no_active_gig` result with no mutation; the G05 completion audit must
not imply otherwise. GigAI grants filesystem-capable adapters explicit access
to the target and workpad as separate roots. It does not copy or symlink the
workpad into the target.

## 5. Setup, endpoints, model targets, and profiles

### 5.1 Interactive setup

`gigai setup` is rerunnable and shows existing state before changing anything.
It asks for:

1. GigAI home and the user-controlled workpad root;
2. default IDE and whether combined target/workpad opening is preferred;
3. installed Codex and Claude CLIs and authentication presence, without reading
   or copying token values;
4. optional OpenAI, Anthropic, and OpenRouter API access;
5. secure credential storage or an environment/secret-manager reference;
6. provider catalog refresh, clearly marked as a metadata network action;
7. one or more named model targets per endpoint;
8. a default role profile and optional named profiles;
9. materialization of immutable built-in packs and editable user capabilities;
10. a final review of paths, adapters, model identities, data policy, and files
    that will be created.

Raw API keys use hidden input and go to the OS credential store. When secure
storage is unavailable, setup records only a named environment or external
secret-manager reference. Raw keys are never accepted on argv.

### 5.2 Model-access contract

The permanent relationship is:

```text
endpoint -> model target -> profile -> Gig role
```

- An **endpoint** owns adapter, transport, authentication reference, service
  URL where applicable, and endpoint-scoped settings.
- A **model target** owns an endpoint reference, configured selector, selector
  policy, inference settings, required capability evidence, and optional cost
  ceiling.
- A **profile** owns a default target plus role-to-target mappings.
- A **Gig version** records roles and required capabilities, not raw provider
  credentials.

OpenAI API, Anthropic API, OpenRouter API, Codex CLI, and Claude CLI are equal
first-class v1 adapters. API access is not fallback-only, and CLI subscription
access is not privileged over metered APIs.

Catalog records distinguish:

```text
DISCOVERED       provider or CLI metadata reports the selector
COMPATIBLE       versioned GigAI evidence supports required capabilities
LIVE-VERIFIED    an explicit scoped probe passed for this target identity
```

Catalog presence is never capability proof. Runs retain the configured
selector, resolved model identity, and resolution source. Large catalogs are
searched, filtered, or paginated rather than dumped.

Role resolution is deterministic:

1. explicit run role override;
2. explicit profile;
3. approved Gig version profile;
4. user default profile;
5. interactive selection when allowed;
6. fail with remediation.

No provider or model fallback is silent.

## 6. `create`: the critical authoring path

### 6.1 Core role

`create` is a versioned built-in authoring capability. It is the most
important GigAI path because it discovers how this user wants this specific Gig
bounded, decomposed, executed, verified, recovered, reviewed, and improved.

```bash
gigai create research-gigai
gigai create security-review --spec security-review.md
```

The first action uses the sole G01 identity API to allocate one Gig ID after
checking both the v2 registry and configured workpad. G08 passes that unchanged
ID to G05, which provisions the empty private repository. G08 then asks G06 to
write and commit `creation-started` as sequence 1 before any model, research,
editor, or proposal effect. Only after that durable commit does G08 select the
Gig active through G05: authoritative `project.toml` plus derived registry state
for Git targets, or one authoritative registry transaction for explicit
non-Git targets. No Gig version exists yet. Creation then operates entirely
inside that workpad and treats the target as context.

If creation is interrupted after provisioning but before the first commit, a
retry resumes the sole exact managed unborn workpad and preserves its Gig ID.
After the first commit but before active selection, recovery completes selection
for that same ID. Ambiguous, foreign, or multiple unjournaled workpads fail
closed; creation never guesses which one succeeded, silently deletes user
state, or allocates a replacement ID to hide an incomplete transition.
When invoked in an uninitialized repository, it offers the offline `init`
operation and shows its exact effect before continuing.

The primary output is a reviewable `GigProposal` containing Markdown and a
validated Goal Graph. It is not generated Python, and it has no execution
authority until the user approves it as a Gig version.

```text
Gig Proposal:
  drafting -> proposed -> approved as Gig version
                    \-> rejected | superseded
```

Routine creation can remain entirely offline by using an inspected template and
manual answers. Automated deliberative or critical drafting requires resolvable
review roles; missing model access never prevents the user from writing and
approving the same contract manually.

### 6.2 Intake interview

The normal TTY path asks questions and offers inspected options instead of
requiring a wall of flags:

1. What concrete outcome should exist when this Gig is finished?
2. Why is the work needed, and what decision or user depends on it?
3. What target, sources, repositories, documents, or datasets are relevant?
4. What must not change or leave the machine?
5. What would make the result truthful and verifiable?
6. What domain expertise or critical reasoning is required?
7. What is uncertain enough to justify creation research or a spike?
8. Which tools, provider calls, searches, or external data may be considered?
9. What creation-time and execution-time cost ceilings apply?
10. Which configured profile should supply planner, domain critic, execution
    critic, and adjudicator roles?
11. Which parts depend on earlier results, and which could safely proceed in
    parallel?
12. Where must results join before a conclusion or action is valid?
13. Which transitions can be automatic, and where does this user want a review
    gate?
14. What failures have known recovery paths, and which must stop the Gig?
15. Are the needed verification tools already available, or must the Gig create
    and prove new ones first?

GigAI summarizes the answers and asks the user to confirm or correct them before
spend.

Advanced noninteractive use accepts typed input, `--spec`, `--profile`, repeated
role-to-model-target overrides, explicit policies, and explicit budget
acceptance. It never accepts raw API keys or unregistered provider selectors as
ordinary overrides.

### 6.3 Creation rigor

Creation rigor is based on consequence, novelty, reversibility, domain
specialization, evidence availability, and external exposure—not merely a
`quick` versus `thorough` preference.

```text
routine       known template, reversible work, strong existing proof
deliberative  meaningful uncertainty or multiple plausible approaches
critical      specialized domain, expensive search, high consequence,
              weak evidence, external mutation, or difficult rollback
```

The operator may raise rigor at any time. Lowering an automatically recommended
rigor level requires a recorded reason.

Critical creation uses a fixed inspectable authoring shape:

```text
commission and constraints
  -> context/research planner
  -> candidate Gig contract and goal decomposition
  -> independent domain critic
  -> independent execution/evidence critic
  -> adjudicator with explicit disagreements
  -> operator review
```

Roles may use different providers or independent sessions of one provider.
Agreement is not treated as correctness; disagreements and unresolved concerns
remain visible in the proposal.

### 6.4 Bounded creation research

Specific Gigs may be expensive to create. That expense is justified when it
prevents the Goal Graph from rediscovering scope, tools, domain rules,
dependencies, or proof during every Run.

Creation has a budget separate from every execution Goal:

- maximum model and tool calls;
- token and monetary ceilings where enforceable;
- search and retrieval limits;
- permitted providers, sources, and data classes;
- time and artifact-size ceilings;
- hard-stop behavior when price, retention, source freshness, or remaining
  budget cannot be established.

Search is an explicit tool. A model may propose searches, but GigAI performs
only those allowed by the confirmed creation policy. Results record URL or
source identity, retrieval time, content hash, and applicability. Paid data and
authenticated sources require separate disclosure.

### 6.5 Proposal artifact

The proposed workpad contains at least:

```text
gig.md                         status: proposed
goals/README.md
goals/NN-<name>.md
reviews/creation-review.md
decisions/creation-decisions.md
manifests/gig-proposal.json
manifests/creation-manifest.json
manifests/goal-graph.json
handoffs/000000000002-proposal-ready.txt
```

`gig.md` defines:

- commissioned outcome and why it matters;
- target and context boundaries;
- known facts, assumptions, and unresolved questions;
- in-scope and out-of-scope work;
- Goal Graph, dependency edges, parallel-ready nodes, joins, recovery edges,
  transition policies, and operator gates;
- creation and execution budgets;
- data, network, mutation, and credential policies;
- required roles, capabilities, tools, and evidence;
- Gig-level acceptance criteria and valid terminal outcomes;
- amendment, feedback, and stop policy.

The creation manifest pins every source, prompt, model target, tool, context
artifact, review, and cost record used to produce the proposal.

`manifests/gig-proposal.json` is the schema-defined `GigProposal` envelope. It
does not duplicate the proposal contents: its `gig_document`, `goal_graph`, and
`creation_manifest` artifact references name `gig.md`,
`manifests/goal-graph.json`, and `manifests/creation-manifest.json` respectively
and pin each file's exact `content_sha256`. Before approval its status is
`drafting` or `proposed`; an `approved` status belongs only to the later
approval transition.

The Markdown correspondence is mechanical. Each graph Goal has exactly one
Markdown contract at `goals/NN-<name>.md`: `NN` is the zero-padded decimal part
of its `GNN` display ordinal and `<name>` is its exact graph `slug`. The
proposal also contains `gig.md` and `goals/README.md`. The required creation
review and decision Markdown files must exist, but the v1 `GigProposal` schema
does not digest or otherwise reference them.

The user does not have to design graph syntax. `create` derives the graph
from the interview, target inspection, domain research, candidate tools, and
verification discussion, then presents it as readable Goals and a compact graph
view. Before approval, `goal-graph.json` is the validated machine projection of
the proposed Markdown. Approval pins both at one commit and makes them the
immutable authority for the new Gig version.

Before review, graph validation proves:

- duplicate-free canonical Goal IDs and internally valid versions; cross-version
  stability is checked during the later approval comparison against a prior
  approved graph;
- no dependency or recovery cycle;
- at least one entry and one terminal path;
- every required Goal is reachable;
- joins name exact predecessor outcomes;
- every automatic edge has a typed source outcome;
- parallel-ready Goals have compatible effects or declared isolated surfaces;
- aggregate and per-Goal budgets are satisfiable;
- every terminal path produces the required Gig completion evidence;
- referenced tools and executors are installed, materialized by an earlier
  Goal, or explicitly blocking.

Each Goal declares its executor and tools with one exact resolution:
`installed`, `materialized`, or `blocking`. A materialized executor or tool
names its producer Goal; a blocking one records why it cannot yet resolve. The
graph separately declares its required Gig completion-evidence identifiers.
Every terminal path must end at a Goal whose declared evidence includes every
such identifier.

### 6.6 Review, feedback, and agreement

After producing the proposal, interactive `create` opens the complete Gig
proposal workpad in the configured IDE and stops. It never approves its own
proposal and never starts a Run.

```bash
gigai feedback <gig-proposal-id>
gigai revise <gig-proposal-id>
gigai approve <gig-proposal-id>
gigai reject <gig-proposal-id>
```

Feedback is stored verbatim in a text handoff. Revising produces new files,
review evidence, and a new commit linked to the previous proposal. Approval
validates the plan, seals the version manifest, tags the local commit, changes
the proposal state to `approved`, and creates the first immutable Gig version.
The approval handoff records exactly what the user approved.

Approval does not mean every conclusion is correct. It means scope, graph,
authority, proof, tools, budgets, transitions, and stop conditions are
sufficient to start a Run of the complete Gig.

### 6.7 Capability materialization

Most Gigs should use existing built-in or user capabilities. `create` may
also produce candidate prompts, command-tool definitions, fixtures, or custom
capability source when the Goal Graph needs a verification or execution tool
the user does not already have.

A declarative binding to an installed trusted tool may be checked during
creation. Generated executable code remains an untrusted proposal. The graph
must insert a materialize-and-prove Goal before any dependent Goal can use it.
Only that approved Goal may review, check, rehearse, and install the tool into
the private Gig workpad or user capability library.

Creation never imports, executes, installs, or activates generated code.

## 7. Goal Graph contract

### 7.1 User-specific verification

GigAI standardizes how proof is declared, stored, and audited; it does not
standardize what counts as proof for every Gig.

Two Python projects may use different test runners, type systems, build tools,
repository policies, and release evidence. Two research Gigs may require
different sources, freshness windows, statistical checks, domain reviewers, or
`NOT_EVALUABLE` rules. Article-writing and home-automation Gigs have different
truth, safety, and completion criteria again.

`create` must discover and encode the user's verification method. Templates
are starting hypotheses, never universal acceptance rules.

### 7.2 Required Goal shape

Every Goal node is independently readable and contains:

```text
Title and stable Goal ID
Approved Gig and Goal versions
Objective
Inputs and authoritative context
Dependencies and accepted predecessor outcomes
Transition policy: automatic | operator_gate
Parallel and isolation policy
Failure policy and typed recovery edges
In Scope
Out Of Scope
Executor, roles, and required tools
Allowed effects and data exposure
Per-Goal execution budget
Implementation or research sequence where useful
Required Proofs
Acceptance Criteria
Evidence destinations and schemas
Valid Outcomes
Stop conditions
```

Domain-neutral structure is mandatory; proof content remains user- and
Gig-specific.

### 7.3 Graph semantics

The approved Goal Graph is a directed acyclic graph:

```text
sequential:
  G00 -> G01 -> G02

parallel research with a join:
           -> G01 source research --\
  G00 scope                         -> G04 synthesis -> G05 final audit
           -> G02 data analysis ---/
           -> G03 domain critique -/
```

The scheduler applies these rules:

1. a Goal is ready only when all declared dependency conditions are satisfied;
2. every ready `automatic` Goal may start, subject to concurrency, effect,
   executor, and aggregate budget limits;
3. ready Goals with no dependency path between them may run in parallel;
4. a multi-parent Goal is a join and starts only when its exact predecessor
   conditions are satisfied;
5. an `operator_gate` Goal or edge pauses only that gated continuation; other
   independent branches may continue when policy permits;
6. a typed failure may follow only a recovery edge already approved in the
   graph;
7. an undeclared outcome, missing tool, effect conflict, budget conflict, or
   impossible join blocks the affected branch and may block the Run according
   to the approved failure policy;
8. the Run succeeds only when every required terminal condition and Gig-level
   acceptance requirement is satisfied.

The graph manifest is an execution contract, not a generic user-authored
workflow language. Markdown remains the review surface; ordinary users never
need to program nodes or edges directly.

### 7.4 Goal state within a Run

```text
pending -> ready -> running -> verifying -> complete
             |         |           |
             |         +-----------+-> failed | blocked | cancelled
             +-> waiting_for_gate
```

Multiple Goals may be `running` concurrently. Each terminal Goal writes its own
evidence and handoff. An automatic successful outcome updates dependency state
and may make downstream Goals ready without stopping the whole Gig.

`complete`, `failed`, `blocked`, and `cancelled` are lifecycle classes, not
substitutes for the Goal's typed domain outcome.

### 7.5 Parallel safety and failure policy

The Gig version declares `max_parallel_goals` and an aggregate resource budget.
The scheduler will not overlap Goals when:

- either Goal has an incompatible target or external-write effect;
- their declared write surfaces overlap;
- an executor or tool has exclusive ownership;
- their combined maximum spend exceeds the remaining Gig budget;
- the approved graph requires deterministic ordering.

For v1 proposal validation, a Goal with `write_target`, `write_workpad`, or
`external_write` declares one or more nonempty `write_surfaces`. Distinct
surfaces are the declaration that otherwise-independent Goals are isolated. An
overlap is invalid unless both Goals declare the same `exclusive_resources`
entry: that entry is an explicit mutual-exclusion declaration, so the pair is
valid but must be serialized rather than run concurrently. A Goal with no
write effect declares no write surface.

Initial failure policies are:

```text
fail_fast             stop launching new Goals and finish active Goals safely
continue_independent  allow unaffected branches to finish, then wait for review
follow_recovery       take an approved typed recovery edge
```

No model invents a recovery node or rewires dependencies during execution.

### 7.6 Versions, gates, and recovery

The complete Goal Graph is frozen when a Run starts. A review gate records the
Run state and exits the per-Run worker; `gigai continue <run-id>` resumes the
same Run only after the gate decision is committed.

When execution exposes a bad graph:

- existing evidence and completed Goal handoffs remain immutable;
- the current Run follows an approved recovery edge or stops blocked;
- GigAI may create a Gig Proposal with changed Goals, edges, tools, proof,
  budgets, or parallelism;
- the user reviews that proposal and approval creates the version a future Run
  may use.

This is the safe basis of a self-healing Goal Graph: recovery within a Run is
preapproved; structural learning happens between immutable versions.

## 8. Handoffs and audit history

### 8.1 Every semantic transition writes text

Each creation, review, approval, activation, completion, block, recovery,
amendment, and final-close transition writes an immutable `.txt` handoff.

Each handoff includes:

```text
schema version
Gig ID and version
Goal ID and version, when applicable
Run ID and relevant invocation IDs
Goal Graph hash and graph-state summary
timestamp
actor, adapter, endpoint, and resolved model target where applicable
parent handoff and previous journal commit
approved source-manifest hash
what was attempted
what changed
decisions and disagreements
tools and models used
target HEAD and before/after status
evidence and raw-object hashes
verification results
usage and cost provenance
failures and unresolved questions
operator feedback
valid outcome
next permitted actions
explicitly unauthorized actions
```

The text is optimized for the next human or agent, not for reconstructing hidden
reasoning. Private chain of thought is never requested or stored.

### 8.2 “Did I break it?” status

`gigai status` reconciles the approved plan, private journal, execution index,
raw objects, and target state. It answers with facts rather than reassurance.

When attention is required it may offer:

1. inspect unexpected target changes;
2. inspect missing or failed evidence;
3. run an already-approved missing proof;
4. ask configured reviewers to diagnose, after spend confirmation;
5. follow an available approved recovery edge;
6. approve or reject a declared review gate;
7. allow independent branches to finish;
8. propose a better Goal Graph version;
9. mark the affected branch or whole Run blocked and stop.

GigAI never resets the target, rewrites the journal, weakens acceptance, or
continues automatically.

### 8.3 Target Git evidence

When the target is a Git repository, every Run records:

- repository root and object format;
- branch or detached state;
- start and end `HEAD`;
- machine-readable before/after worktree status;
- submodule and worktree identity when applicable;
- patch or changed-path hashes within the declared observed surface;
- target commits created by the Run only when explicitly authorized.

A private workpad commit is never represented as a target-repository commit.
Target commits follow the user's repository identity and policy; GigAI's local
journal uses its private local identity.

## 9. CLI contract

### 9.1 Primary surface

```text
gigai setup
gigai init [--target <path>] [--workpad-root <path>]
gigai create <name> [--spec <path>] [--profile <profile>]

gigai open [<gig-id>] [--target | --with-target]
gigai workpad path [<gig-id>]
gigai gigs
gigai proposals [--gig <gig-id>]
gigai status [<gig-id>]
gigai show [<gig-id>]
gigai history [<gig-id>]

gigai feedback <gig-proposal-id>
gigai revise <gig-proposal-id>
gigai approve <gig-proposal-id> [--json]
gigai reject <gig-proposal-id>

gigai goals [<gig-id>] [--version <version>]
gigai plan [<gig-id>] [--version <version>]
gigai run [<gig-id>] [inputs...] [--version <version>] [--wait] [--json]
gigai run-details [<run-id>] [--json]
gigai wait <run-id> [--json]
gigai continue <run-id> [--wait] [--json]
gigai stop <run-id>
gigai verify <run-id> [--goal <goal-id>] [--json]
gigai review <run-id> [--goal <goal-id>]
gigai accept <run-id> [--goal <goal-id>]
gigai block <run-id> [--goal <goal-id>] --reason <text>

gigai check [<gig-id>] [--json]
gigai doctor [--json]
gigai doctor --live --endpoint <name>
gigai doctor --live --model-target <name>
gigai preview [<gig-id>] [--version <version>] [--goal <goal-id>] [inputs...]
gigai rehearse <gig-id> --goal <goal-id> --case <name> [--version <version>]
gigai eval <gig-id> --goal <goal-id> [--suite <name>] [--version <version>]

gigai models [--endpoint <name>] [--search <text>] [--refresh]
gigai profiles
gigai tokens [<run-id>] [--json]
gigai costs [<run-id>] [--json]

gigai improve <gig-id> ["<change request>"]
```

Exact names remain provisional until this plan is approved; the companion
command sheet mirrors the current draft. The semantic separation is binding:

- `create` produces a reviewable Gig Proposal and stops;
- `plan` renders a proposed or approved human Goal Graph and labels its state;
- `preview` is the best-effort zero-effect observation of executable control
  flow;
- `check` validates portable Gig structure and approved contracts;
- `doctor` diagnoses this installation, mount, credentials, adapters, IDE, Git
  journal, and live compatibility;
- `rehearse` executes a fixture-backed Goal path;
- `run` resolves any blocking questions, writes and seals a mandatory Run Brief,
  and starts its supervised worker;
- `approve` turns only a Gig Proposal into an immutable Gig version and starts
  no Run;
- `run-details` and `wait` expose the same small result contract to humans,
  Codex, Claude, scripts, and API callers;
- `continue` resumes the same nonterminal Run after a committed operator-gate
  decision; it never creates a second Run;
- `verify` executes only the proof declared by the approved Goal Graph and
  reports whether the existing evidence satisfies it; it never substitutes a
  universal GigAI test.

Commands that accept `--version` require a positive approved Gig version.
Without it, they resolve the explicit `manifests/active-gig-version.json`
pointer; they never infer authority from timestamps, filenames, tags, or a
lexical "latest". `--goal` accepts a stable Goal ID in that selected Gig
version. Display ordinals such as `G00` are presentation only and are rejected
as noninteractive identity.

### 9.2 Command effects

| Command family | Network by default | Mutation |
|---|---:|---|
| `setup`, `models`, `profiles`, `doctor` | no | documented machine configuration only |
| `init` | no | ignored project binding plus registry entry |
| `create` | explicit | private proposal workpad only |
| `open`, `show`, `history`, `status`, `proposals`, `plan`, `check`, `preview`, `run-details`, `wait` | no | none, except the IDE process launched by `open` |
| `feedback`, `revise`, `reject`, `block` | no unless review requested | proposal or private journal commit |
| `approve` | no | immutable Gig version only; no Run launch |
| `rehearse` | no | fixture/workpad only |
| `run` | explicit approved Gig effects | sealed Run Brief and manifest, handoff, and supervised Run launch |
| `continue`, `stop` | explicit for continuation; no new network for `stop` | gate continuation or supervised-worker cancellation |
| `verify` | only when the approved verifier requires it | approved verification effects and evidence |
| `eval` | explicit when a model judge is selected | evidence only |
| `improve` | no by default | Gig Proposal and private journal commit only |

Higher-level commands execute their required validation and diagnostics. Users
do not need to memorize `check`, then `doctor`, then `run` chains.

### 9.3 Inputs and help

CLI help and noninteractive input schemas derive from typed public contracts.
Interactive prompts, file inputs, enums, lists, secret references, and target
references have one validation path. Unknown inputs fail rather than being
silently converted into prompt text.

## 10. Capabilities, tools, and execution recipes

### 10.1 Capabilities, not Python-defined Gigs

A Gig is an on-disk commissioned project. Reusable executable behavior is a
Capability. This avoids making Python authorship the user model.

Built-in packs may provide capabilities such as:

```text
deliberative-create
bounded-research
independent-review
coding-cli-goal
fixed-role-api-goal
deterministic-check
completion-audit
```

Capabilities use a small provisional Python extension contract:

```python
from gigai import Capability, Run

from .models import Findings, GoalInput, GoalResult


async def execute(run: Run, goal: GoalInput) -> GoalResult:
    checks = await run.tool("project/checks", goal.checks_input())
    findings = await run.model(
        role="reviewer",
        prompt_resource="prompt.md",
        input={"goal": goal, "checks": checks},
        output=Findings,
    )
    return GoalResult.from_findings(findings)


capability = Capability(
    name="independent-review",
    input=GoalInput,
    output=GoalResult,
    execute=execute,
    resources=("prompt.md",),
)
```

`GoalInput`, `GoalResult`, and `Findings` are capability-local typed models
shipped with this capability and imported from its `.models` module. They are
not undeclared `gigai` root exports. The runtime validates them through the
public `Capability` contract and sealed source snapshot.

The initial `Run` contract exposes only:

```python
await run.model(...)
await run.tool(...)
run.artifact(...)
run.note(...)
```

Ordinary Python supplies internal control flow. GigAI does not recreate a
generic workflow DSL. Capability code routes effects through `Run`; trusted
Python can bypass that convention, so validation and subprocess execution are
risk reduction rather than an airtight boundary.

Every resolved capability, prompt, fixture, schema, and resource byte is sealed
into the Run source manifest. Built-ins are immutable and versioned. User-owned
capabilities are editable local files and are never overwritten by setup or
upgrade.

### 10.2 Tools

A tool is a capability-selected deterministic operation. Models do not receive
all installed tools automatically.

Every tool declares:

- typed input and output;
- structured argv or direct Python entry point, never a free-form shell string;
- working directory and empty-base environment allowlist;
- timeout, output limit, and cancellation behavior;
- effects and enforcement level;
- idempotency and retry policy;
- target before/after observation requirements.

Tool discovery is ownership-based: built-in tools live in packs, user tools live
with user capabilities, and Gig-local tools require an approved materialization
Goal. No PATH-wide plugin scanning ships in v1.

### 10.3 Goal executors

Every ready Goal resolves one executor capability. Different Goals in the same
Run may use different executor kinds, tools, model targets, and verification.
Initial executor kinds are:

```text
codex_cli
claude_cli
fixed_role_api
local_capability
```

For Codex or Claude CLI, GigAI launches the adapter as a supervised subprocess
with sanitized structured arguments, the approved Goal contract, relevant graph
context, target root, private workpad root, and required evidence destinations.
The adapter owns its exact supported command-line shape; the Gig contract does
not hard-code provider CLI flags.

The executor may draft evidence and a completion audit. GigAI owns process
status, output capture, `RunDetails`, artifact hashing, handoff finalization, and
verification that required files exist and match the approved Goal. A zero
process exit does not by itself validate the Goal's proof.

## 11. Planning, rehearsal, and execution

### 11.1 Human plan versus execution preview

`gigai plan` renders the approved Goal Graph, dependencies, possible
parallelism, joins, gates, recovery edges, budgets, evidence requirements, and
terminal conditions. It is authoritative for what the operator approved.

`gigai preview` replaces revision 12's overloaded `plan` command. It simulates
graph scheduling and exercises selected capabilities with a zero-effect
planning `Run`:

- model and tool calls are recorded rather than executed;
- typed placeholders are returned;
- observed roles, policies, artifacts, and calls are reported;
- missing configuration is surfaced before spend.

Arbitrary Python cannot be previewed exactly. Every preview declares:

```text
preview_semantics: best_effort_python_v1
path_status: observed | blocked_on_result
authoritative: false
```

Fixture-backed `rehearse` is authoritative only for the selected case. Hard
budgets remain independent of preview completeness.

### 11.2 Run invocation, brief, and background reporting

`gigai run` is the operator's instruction to execute one approved Gig version;
there is no second approval command. It first resolves the current target,
inputs, profile, model targets, tools, and approved policy. If required typed
inputs, policy choices, or material facts are unresolved, interactive use asks
the operator and noninteractive use returns `needs_input` without creating a
Run.

Without `--version`, `run` selects the version named by the Gig's explicit
`manifests/active-gig-version.json` record. `--version <positive-int>` selects
that exact older approved version. Missing, unapproved, mismatched, or
journal-divergent version records fail before Run creation; "latest" is never
guessed from time, filename, or Git history.

Once resolution succeeds, GigAI reserves a Run ID, revalidates the resolved
facts, writes the human-readable Run Brief, seals `run-manifest.json`, writes
initial `RunDetails`, commits a `run-started` handoff, and launches the local
worker. The brief, manifest, and handoff are durable before any model, tool,
target-write, or other external-effect call.

```text
run command:
  resolving -> needs_input
           \-> preparing -> running -> terminal
```

The Run Brief contains at least:

- exact Gig version, Goal Graph hash, and initial ready set;
- target root, Git HEAD/status, and observed-surface baseline;
- sequential paths, possible parallel Goals, joins, gates, and recovery edges;
- resolved executors, tools, endpoints, model targets, and compatibility proof;
- data that may leave the machine and each recipient;
- filesystem, network, target, and external effects;
- per-Goal and aggregate call, token, cost, time, and concurrency budgets;
- user-specific verification and expected evidence paths;
- material risks, resolved operator answers, warnings, and reasons the Run could
  block;
- exact invocation, resolved inputs, and facts sealed for execution.

The Markdown brief is the human audit surface. It begins with the canonical
JSON front matter defined by `src/gigai/schemas/run-brief-frontmatter.schema.json`:
an opening `---gigai-json` line, one restricted-JCS JSON object, a closing `---`
line, then normalized UTF-8/LF human text. The metadata carries the body digest;
the sealed manifest carries the whole-file digest. The manifest and
`run-started` handoff record the same identity. GigAI does not write a parallel
Run Proposal artifact.

Default output is deliberately small:

```text
Run: run_77777777-7777-4777-8777-777777777777
Status: preparing
Brief: <workpad>/runs/run_77777777-7777-4777-8777-777777777777/run-brief.md
RunDetails: <workpad>/runs/run_77777777-7777-4777-8777-777777777777/run-details.json
Handoff: <workpad>/handoffs/000000000004-run-started.txt
Wait: gigai wait run_77777777-7777-4777-8777-777777777777
Open: gigai open
```

A calling Codex, Claude, script, or API client receives this pointer envelope
and may inspect or summarize the brief and `RunDetails`. The explicit `run`
invocation is the authority; there is no `run --yes` or `approve <run-id>`.
`gigai run --wait` waits on the same Run after durable launch. `run --wait`,
`wait`, and `continue --wait` return when that Run is terminal or durably enters
`waiting_for_gate`; they never block on a worker that has exited for a gate. A
gate pause returns exit 0 and exposes `waiting_for_gate` plus `next_actions`.
Failed, blocked, cancelled, or interrupted Runs return 1; invalid usage returns
2; unresolved noninteractive questions return 3.

This is detached execution ownership, not a long-lived daemon or recurring
scheduler. The worker owns one Gig Run, schedules its ready Goal nodes, and
makes no network call other than calls authorized by the approved Gig version
and sealed Run manifest.

The worker may supervise multiple Goal executors concurrently. It terminates
when the Run is terminal or durably paused at an operator gate. `gigai continue`
acquires a new worker lease for that same Run after the gate decision; no idle
daemon remains alive while waiting for the user.

Terminal `--json` output emits a stable pointer envelope suitable for an
enclosing Codex or Claude session:

```json
{
  "run_id": "run_77777777-7777-4777-8777-777777777777",
  "status": "succeeded",
  "run_details": ".../runs/run_77777777-7777-4777-8777-777777777777/run-details.json",
  "completion_audit": ".../evidence/completion-audit.md",
  "handoff": ".../handoffs/000000000012-gig-run-finished.txt",
  "workpad": ".../gigs/gig_22222222-2222-4222-8222-222222222222",
  "target": ".../target-repository"
}
```

This gives both callers the same contract:

- Codex or Claude can read `RunDetails` and the completion audit, then summarize
  what happened and where evidence lives;
- a human can use `gigai open`, `run-details`, `show`, or `history`;
- scripts can consume JSON;
- advanced local inspection can read committed documents, raw objects, or the
  rebuildable SQLite index.

`RunDetails` is a small atomically updated materialized view, not the complete
event log. It contains at least:

```text
schema_version
run_id
Gig ID and version
Goal Graph hash
status: preparing | running | waiting_for_gate | verifying | succeeded | failed | blocked | cancelled | interrupted
started_at and finished_at
pending, ready, active, complete, failed, blocked, and gated Goal IDs
per-Goal version, executor, status, typed outcome, errors, evidence, usage, and cost
critical path and realized parallelism summary
execution summary
tool_errors and model_errors
aggregate usage, cost, and remaining budget
target before/after summary
result and evidence paths
Goal-audit paths
Gig completion_audit path and status: missing | draft | valid | accepted | rejected
terminal handoff path
workpad commit when terminal
next permitted actions
```

While running, `run-details.json` may advance atomically and remain uncommitted.
At a semantic terminal transition it is finalized, referenced by the terminal
handoff, and committed to the private journal. Raw stdout, stderr, provider
events, tool results, and retry lineage stay in content-addressed objects and
SQLite rather than bloating this record.

### 11.3 Run preparation

As part of `gigai run` and before authoritative external execution, GigAI:

1. resolves the target, approved Gig version, Goal Graph, every Goal version,
   capabilities, profiles, model targets, tools, and policies;
2. verifies the private journal has no unresolved divergence or remote;
3. runs structural `check` and required installation diagnostics;
4. validates dependencies, joins, recovery edges, transition policies, effect
   conflicts, executors, aggregate budgets, and target preconditions;
5. displays the graph, initial ready set, possible parallelism, effects,
   exposure, budgets, gates, and stop conditions;
6. seals exact graph, Goal, capability, prompt, tool, schema, fixture, and
   relevant target-reference bytes;
7. writes and commits the Gig-Run-started handoff;
8. commits the Run in `preparing`, writes initial `RunDetails`, and persists the
   first external invocation record before launching any process or provider
   request.

Concurrent mutation of sealed inputs fails preparation. Execution reads owned
code only from the sealed snapshot.

### 11.4 Runtime and recovery

```text
run:
  preparing -> running <-> waiting_for_gate -> verifying -> succeeded
       |          |                 |             |
       +----------+-----------------+-------------+-> failed | cancelled | interrupted

goal:
  pending -> ready -> running -> verifying -> complete
               |         |           |
               |         +-----------+-> failed | blocked | cancelled
               +-> waiting_for_gate

invocation:
  prepared -> started -> succeeded
      |          |
      +----------+-> failed | cancelled | interrupted
```

Every external invocation has a stable `invocation_id`, canonical request hash,
adapter/tool identity, optional `retry_of` link, start and terminal times, raw
artifact references, usage, and retry disposition.

Before an external launch, the prepared invocation is durable. On completion,
payloads are streamed to a temporary object, flushed, closed, hashed, atomically
renamed, attached in one SQLite transaction, and referenced by the next stable
manifest and handoff.

Startup recovery:

1. finds nonterminal Runs, active Goals, or invocations without a live worker;
2. classifies them as interrupted unless deterministic reconciliation proves a
   terminal result;
3. preserves all artifacts and diagnostics;
4. writes an interruption handoff and local journal commit;
5. prints inspection, recovery, and explicit rerun choices.

No interrupted model invocation relaunches automatically. Contract failures are
not retried unchanged. A model repair is a separate bounded call. Deterministic
tools may retry only when declared idempotent and bounded.

A succeeded Goal executor does not complete its Goal by exit code alone. The
Goal's named proof and typed outcome must validate. A successful automatic Goal
may then unlock downstream nodes without operator review.

A succeeded Run means all required graph terminal conditions and the Gig
completion-audit contract validated. Final operator acceptance closes the Gig;
it does not retroactively change Run evidence.

### 11.5 Budgets and postconditions

Every Run enforces aggregate limits and every active Goal enforces its own:

- model, tool, repair-call, and parallel-Goal counts;
- wall time and cancellation deadline;
- tokens and monetary spend where enforceable;
- output, artifact, and context bytes;
- target and external effect policy.

Target observation captures declared pre/post surfaces. An incomplete
observation is visible and blocks any claim that the target remained clean.
V1 may support `write_target` only after its approval, idempotency, rollback,
and patch-evidence Goal passes; the initial slice remains read-target and
write-workpad only.

## 12. Evidence, manifests, and cost

The workpad Git repository stores stable text and manifests. Content-addressed
objects store payloads. Per-Gig SQLite stores rebuildable indexes and
relationships.

Every Run retains:

- approved Gig and Goal versions;
- exact Goal Graph manifest, scheduler decisions, ready-set transitions, joins,
  gates, recovery edges taken, and realized concurrency;
- terminal `RunDetails` and its human summary;
- source and capability manifests;
- inputs and validated result;
- rendered prompts and raw provider events;
- tool requests, structured argv, stdout, stderr, and results;
- configured and resolved model identities;
- adapter, endpoint, CLI, SDK, and capability versions;
- target before/after evidence;
- raw usage and normalized token categories;
- provider-reported, derived, or unavailable monetary cost;
- reviews, ratings, and completion evidence;
- exact handoff and workpad commit identity.

Derived price evidence records source URL, retrieval date, schema version, and
content hash. CLI subscription access without defensible per-call price records
`unavailable`, never zero. Planning estimates remain estimates.

Every completed Goal writes `evidence/goal-NN/completion-audit.md` with:

- valid outcome;
- requirement-to-evidence table;
- verification commands and results;
- implemented or researched boundary;
- explicit present and absent scope;
- unresolved limitations;
- spend and exposure reconciliation;
- stop boundary and next actions requiring operator approval.

The Run also writes `evidence/completion-audit.md`, which reconciles the Gig's
terminal criteria across every required Goal, records skipped or recovery
paths, identifies incomplete optional branches, summarizes graph execution and
critical-path cost, and states whether the approved Gig outcome is valid.

No automatic retention or deletion ships in v1. `doctor` reports mount size,
largest children, missing objects, journal/index disagreement, and configurable
warning thresholds. Deleting an object preserves its manifest identity and a
visible `deleted` status.

## 13. Local Gig improvement

Gig history is valuable because it records how the user's own verification
method actually performed. That history stays on the user's machine and remains
owned by the user.

### 13.1 One improvement command

```bash
gigai improve research-gigai
gigai improve research-gigai "make source-freshness proof stricter"
```

`improve` is the only adaptation command in v1; there is no separate `adapt`,
`learn-from`, or background-learning surface. With no change request, it asks
what should work better and which local Runs or feedback should inform the
proposal. With a quoted change request, that text becomes the improvement
commission. In both forms it first shows the baseline Gig version, local
evidence scope, model/data exposure, and improvement budget.

`improve` examines the approved baseline, `RunDetails`, per-Goal handoffs,
completion audits, explicit user feedback, and selected local evidence. It
considers:

- which Goal outcomes were accepted, rejected, blocked, or not evaluable;
- which proof actually convinced the user and which proof was missing;
- node duration, cost, errors, retries, and executor/model/tool performance;
- dependency waits, joins, unused edges, and critical-path bottlenecks;
- safe parallelism that was missed or parallel work that conflicted;
- recovery edges that worked, failed, or were repeatedly requested manually;
- operator edits, gate decisions, and accepted/rejected recommendations.

It may then produce a new Gig Proposal with:

- narrower or reordered Goals;
- added, removed, split, or joined Goal nodes;
- better dependencies, parallelism, gates, or typed recovery edges;
- verification changed to match what this user actually accepts;
- missing proofs, tools, or stop conditions;
- better creation and execution budgets;
- different executors, model targets, review roles, or capability choices;
- a new version of a reusable template, tool, or capability when the evidence
  supports reuse.

The proposal cites which parts came from the operator's prompt and which came
from local evidence. Candidate user preferences may be proposed only from
explicit feedback and accepted/rejected outcomes; one silent action is not a
durable preference.

Improvement stops after committing the proposal. It uses the ordinary
`feedback`, `revise`, `approve`, and `reject` commands. Approval creates a new
immutable Gig version; it does not start a Run.

### 13.2 Self-healing boundary

The Goal Graph can improve without becoming self-modifying authority:

- inside a Run, it may follow only approved typed recovery edges;
- between Runs, `improve` may create a new Gig Proposal from an approved
  baseline;
- deterministic checks and fixture replay compare the candidate with prior
  accepted Runs where possible;
- the user reviews, revises, approves, or rejects the candidate before it can
  become a Gig version.

Thus “self-healing” means evidence-driven local graph evolution under user
ownership, not a model silently rewriting the active Gig.

### 13.3 Proposal-only learning

Improvement never edits approved history, an active graph, user profiles,
prompts, tools, or capabilities automatically. It writes a Gig Proposal with
source handoffs and asks the user to review, revise, approve, or reject it.

Deterministic local analysis is the default and uses no network. If the user
selects a local CLI or API model for improvement, GigAI performs the ordinary
exposure and spend preflight and records the call in the private journal. No
call is ever made to a GigAI-owned service.

Per-Gig lessons remain in that Gig's workpad. Accepted cross-Gig preferences
live in the user-controlled local learning directory and retain provenance back
to the source handoff and approval.

## 14. Public contracts and compatibility

The initial stable public surface is intentionally small:

```python
from gigai import (
    Capability,
    CommandTool,
    Effects,
    PythonTool,
    Run,
)
```

During `0.x`, only root exports and explicitly documented modules are public.
Internal imports emit no compatibility promise. Breaking public Python changes
require a minor release and migration note.

Serialized boundaries version independently:

- config and credential references;
- project binding and workpad locator;
- Gig Proposal, Gig version, Goal Graph, Goal, handoff, review, and decision
  documents;
- source, creation, Run Brief, sealed Run, `RunDetails`, evidence, and completion
  manifests;
- capability and tool schemas;
- endpoint, model-target, profile, catalog, usage, and cost records;
- SQLite schema.

Unknown major versions fail with remediation. Unknown fields are not silently
dropped and rewritten. The executable Draft 2020-12 schemas and compatibility
rules in `src/gigai/schemas/` are binding Phase 0 contracts; generated runtime models
must validate against them rather than reinterpret their prose descriptions.

Hash-bearing logical JSON uses the restricted RFC 8785 JCS profile defined in
`src/gigai/schemas/README.md`: ASCII identifier member names, no duplicates or floats,
interoperable-range integers, decimal strings for decimal quantities, exact
Unicode preservation, UTF-8, no whitespace, and no trailing newline. Its digest
is `sha256:<lowercase-hex>`. GigAI-owned text uses UTF-8 without BOM, LF, no NUL,
and exactly one final LF; imported user artifacts retain exact input bytes.
Pretty printing is never the identity contract.

Persistent entity IDs are an ASCII entity prefix plus canonical lowercase RFC
9562 UUIDv4. IDs are opaque and not sortable. Goal IDs persist across Gig
versions while changed Goals increment `goal_version`; display ordinals such as
`G00` are not identity. Gig versions are positive integers allocated under the
per-Gig writer lock. Approval advances `active-gig-version.json` and tags the
same journal commit `gig-v000001`, `gig-v000002`, and so on.

Capability packs receive no privileged runtime behavior. They use the same
public contracts, source sealing, model adapters, tool executor, and evidence
rules as user capabilities.

## 15. Core repository architecture

```text
gigai/
  pyproject.toml
  uv.lock
  README.md
  docs/
    public-api.md
    schemas/
    decisions/
  fixtures/
    canonical-vectors.json
  packs/
    standard/
      capabilities/
        create/
        bounded_research/
        independent_review/
        coding_cli_goal/
        completion_audit/
  src/gigai/
    cli.py
    canonical.py
    config.py
    credentials.py
    registry.py
    projects.py
    workpads.py
    journal.py
    handoffs.py
    gigs.py
    goals.py
    goal_graph.py
    scheduler.py
    proposals.py
    approvals.py
    capabilities.py
    tools.py
    run.py
    run_briefs.py
    run_details.py
    workers.py
    snapshots.py
    manifests.py
    objects.py
    ledger.py
    lifecycle.py
    recovery.py
    validation.py
    diagnostics.py
    preview.py
    rehearsal.py
    evaluation.py
    improvement.py
    usage.py
    pricing.py
    catalogs.py
    model_targets.py
    profiles.py
    adapters/
      port.py
      capabilities.py
      openai_api.py
      anthropic_api.py
      openrouter_api.py
      codex_cli.py
      claude_cli.py
      deterministic.py
    execution/
      process.py
      policy.py
      target.py
    tooling/
      catalog.py
  tests/
    contracts/
    workpads/
    journal/
    creation/
    goals/
    lifecycle/
    adapters/
    integration/
```

Dependency direction:

```text
CLI
 |
 v
project binding -> workpad resolver -> Goal Graph validator -> approval gate
                                                            |
                                                            v
                                                    Gig version
                                                            |
                                                            v
                                             run resolver + sealed Run Brief
                                                            |
                                                            v
                                                     Run scheduler
                                                    /      |      \
                                                   v       v       v
                                              Goal A    Goal B    Goal C
                                                   \       |      /
                                                    v      v     v
                                             adapters + tools + objects
                                                            |
                                                            v
                                  RunDetails -> handoffs -> local Git commit
```

Rules:

- adapters never import CLI code;
- capabilities never import concrete provider adapters;
- only model-target resolution selects an endpoint;
- the scheduler executes only nodes and edges from the sealed approved graph;
- effect and budget intersection occurs before Goals run in parallel;
- the workpad journal is canonical and SQLite is rebuildable;
- execution reads owned code only from a sealed snapshot;
- target application code is never imported as capability/tool code;
- credentials never enter Gig-owned serialization;
- diagnostics are shared services, not duplicated command logic;
- no upgrade overwrites user capability, Gig, or learning files.

### 15.1 Supported platforms

The initial package declares `requires-python = ">=3.11,<3.12"`. V1 supports
macOS and Linux where the configured workpad filesystem passes GigAI's
interprocess-lock, atomic-replacement, durability, and permission probes.

Ubuntu and Debian are the continuous Linux verification baselines. Other Linux
distributions using Python 3.11 are supported by the same runtime contract, but
GigAI does not claim that every filesystem or mount type is safe merely because
the operating system is Linux. Network and removable mounts remain conditional
on the live mount probe.

Windows is explicitly unsupported in v1 and returns `unsupported_platform`
before mutation. The journal lock owns a backend interface so Windows support
can be added and proved later; v1 does not ship an untested implementation.

### 15.2 Command verification architecture

The black-box scenario harness is created with the repository spine and invokes
the installed `gigai` executable as a subprocess. Every scenario receives three
separate temporary roots:

```text
target/
workpad-root/
gigai-home/
```

For every command added, the harness records and asserts:

- structured argv, exit code, stdout, stderr, and `--json` output;
- target Git status and exact file-hash manifest before and after;
- the command-specific allowlist of target changes;
- workpad files, journal head, commits, and index changes separately;
- an idempotent second invocation where the command promises idempotency;
- malformed input, corrupt state, missing mount, and interruption behavior
  applicable to that command;
- absence of undeclared network and credential access.

Automated assertions are the regression gate. Durable scenario transcripts,
manifests, and diffs are the completion evidence. Neither a passing test count
without evidence nor a transcript without assertions is sufficient.

The same scenario suite runs directly on macOS, in Ubuntu CI, and in a Debian
Python 3.11 container. The container uses a non-root user, separate target,
workpad, and home mounts, and `--network none` for the offline acceptance lane.
Containerization is a verification environment, not a prerequisite for
implementing the third CLI command. Kubernetes is outside the v1 verification
boundary.

Headless `open` scenarios inject a recording editor executable and assert its
structured argv. One native IDE smoke test remains explicit evidence rather
than an assumption hidden inside the automated suite.

## 16. Delivery phases and gates

Revision 14 is approved by its final harness commit. Implementation begins only
after this contract is copied into the standalone repository and converted into
the verifiable Goals in Section 17. Phase gates remain stop boundaries.

### Phase 0 - Lock core and serialized contracts

Completed in this draft before moving to the new repository:

1. define the Gig-as-Goal-Graph, workpad, private-Git, handoff, and privacy
   contracts;
2. synchronize the command sheet with the plan;
3. define executable Draft 2020-12 schemas for Gig Proposal, active Gig
   version, Goal Graph, Run Brief front matter, sealed Run manifest,
   `RunDetails`, handoff front matter, and shared types;
4. define restricted canonical bytes, digests, IDs, version selection,
   interprocess ordering, and CLI wait behavior;
5. define mount-unavailable, journal-divergence, remote-detected, and
   interprocess-lock-unavailable behavior;
6. prove the contracts with golden hash vectors, valid and invalid schema
   cases, semantic graph checks, and an eight-process journal race.

Evidence: `docs/research/phase-0-contract-closure.md`,
`src/gigai/schemas/`, and `research/contract_spike/` (14 tests passing on the
recorded baseline).

Exit gate: satisfied for the document package. The plan and command sheet have
no revision-12 Gig-as-Python, home-state-authority, overloaded-`plan`, bare
Goal-scope, inferred-version, per-Run-approval, or process-local-lock
contradictions. The final revision-14 harness commit records operator approval;
repository transfer and Goal materialization are the next boundary.

### Phase 1 - Offline local spine

Implement:

1. installable CLI and provisional public schemas;
2. idempotent setup with home, workpad mount, IDE, deterministic offline
   endpoints, model targets, profiles, and standard-pack materialization;
3. idempotent target binding with `.git/info/exclude` proof;
4. exact populated-registry v1-to-v2 migration with a retained v1 backup,
   workpad resolver, caller-ID-only empty substrate provisioning, and
   mount-unavailable failure;
5. per-Gig local Git initialization with no remote, repository-local identity,
   and no semantic commit or ID allocation;
6. atomic handoff writer, unborn-repository first commit, exclusive
   interprocess writer lock, 12-digit per-Gig sequence allocation, and semantic
   journal commits;
7. Gig Proposal, Gig version, Goal Graph, Goal, Run Brief, sealed Run manifest,
   and completion-audit validators;
8. `open`, `workpad path`, `gigs`, `proposals`, `status`, `show`, `history`,
   `plan`, `check`, and offline `doctor`;
9. deterministic offline creation fixtures that allocate one Gig ID through
   G01, provision it through G05, commit `creation-started` through G06, select
   it active, and then propose, revise, approve, or reject through the real
   persisted lifecycle;
10. rebuildable SQLite index and journal reconciliation.

Exit gate:

- Python and non-Python fixture targets remain visibly unchanged after init;
- each target contains only the ignored binding;
- the complete Gig lives only on the configured workpad mount;
- `gigai open` opens the active private workpad and `--with-target` resolves both;
- every creation transition writes text and a local commit;
- setup and doctor prove two-process exclusion and atomic replacement on the
  configured workpad mount;
- the workpad has no remote;
- deleting the index and rebuilding from the journal preserves Gig status;
- no network or tokens are used.

### Phase 2 - Deliberative `create`

Implement:

1. typed intake and creation-policy interview;
2. routine, deliberative, and critical rigor selection;
3. fixed planner/critic/adjudicator authoring pipeline with deterministic
   offline adapters;
4. bounded creation research/tool interface;
5. user-specific Goal discovery, graph synthesis, tool/verification discovery,
   review, feedback, versioning, and approval flows;
6. creation usage, cost, exposure, and provenance manifests;
7. background-worker interruption and recovery across every semantic boundary;
8. first opt-in OpenAI API live proof after its adapter gate passes.

Exit gate:

- one technical-spike fixture, one domain-research fixture, and one article
  fixture produce materially different Goal Graphs and verification through the
  same domain-neutral structural schema;
- graph validation rejects cycles, unreachable required Goals, invalid joins,
  undeclared automatic edges, unsafe parallel effects, and impossible budgets;
- critical creation preserves reviewer disagreement;
- no generated capability executes during creation;
- Gig Proposal approval starts no Run;
- an interrupted creation recovers with a truthful handoff and no ambiguous
  automatic retry.

### Phase 3 - Read-only and workpad-only Goal Graph execution

Implement:

1. capability resolution and sealed Run source;
2. dependency scheduling, joins, automatic transitions, operator gates, typed
   recovery edges, and bounded parallel Goals;
3. blocking-question resolution, mandatory Run Brief generation, and sealed
   Run launch from the `run` command;
4. `preview`, `rehearse`, `run`, `run-details`, `wait`, `continue`, `stop`, and
   user-specific `verify`;
5. structured Python and command tools without shell strings;
6. target read observation and workpad-write effects;
7. durable RunDetails/Goal/invocation/object lifecycle and startup recovery;
8. per-Goal and Gig-level completion audits;
9. repository-neutral execution in Python and non-Python fixtures.

Exit gate:

- a sequential code-review graph, a parallel research graph with a join, and a
  gated safety graph all execute according to their approved structure;
- every successful `run` invocation commits its brief and manifest before
  starting an executor;
- target, profile, or budget drift during resolution fails the Run before any
  external execution;
- independent Goals run concurrently while conflicting effects remain
  serialized;
- automatic transitions do not require operator review, and declared gates do;
- one SWE Gig and one specialized research Gig complete with different
  user-defined evidence;
- exact executed inputs, prompts, source, tools, outputs, usage, cost, and
  target state are recoverable;
- target repositories remain unchanged on the observed surface.

### Phase 4 - Required model-adapter matrix

Implement and independently gate:

1. OpenAI API;
2. Anthropic API;
3. OpenRouter API;
4. Codex CLI;
5. Claude CLI.

Each adapter proves discovery separation, exact capability evidence, structured
output or declared limitation, cancellation, ambiguity, usage, cost status,
session behavior, and redaction. Prove one critical creation using multiple
providers and one using mixed API/CLI transports.

### Phase 5 - Controlled target mutation

Only after read-only execution is complete:

1. define target-write approval and idempotency contracts;
2. record complete Git before/after evidence and patch manifests;
3. provide explicit user-owned commit policy without using workpad identity;
4. prove failure, cancellation, dirty-target, partial-write, and recovery paths;
5. run one disposable technical implementation Gig end to end.

No external-system mutation is implied by this phase.

### Phase 6 - Local improvement

Implement the deterministic `improve` proposal flow with interactive or
prompt-supplied change requests. Prove that it:

- operates offline by default;
- cites exact local handoffs;
- never edits approved history or preferences automatically;
- cites node, edge, executor, verification, cost, and user-feedback evidence for
  every proposed graph change;
- can propose safer recovery, better verification, and better parallelism
  without mutating the active graph;
- stores accepted user-level learning only in the configured local learning
  root;
- makes no GigAI-owned network request.

## 17. Implementation Goal Graph and first slice

### 17.1 Phase 1 implementation Goal Graph

The standalone repository begins with the approved, independently verifiable
Goals below. Their canonical outcome, scope, acceptance, evidence, and stop
contracts live in the [Phase 1 development goal
graph](../development/goals/phase-1/README.md); this section retains the
architecture-level dependency summary.

| Goal contract | Depends on |
|---|---|
| [G00 — Standalone contract baseline](../development/goals/phase-1/G00-standalone-contract-baseline.md) | - |
| [G01 — Canonical serialization](../development/goals/phase-1/G01-canonical-serialization.md) | G00 |
| [G02 — Minimal CLI and installed scenario harness](../development/goals/phase-1/G02-minimal-cli-and-scenario-harness.md) | G00 |
| [G03 — Setup, configuration, and diagnostics](../development/goals/phase-1/G03-setup-configuration-diagnostics.md) | G01, G02 |
| [G04 — Target binding](../development/goals/phase-1/G04-target-binding.md) | G03 |
| [G05 — Workpad and private Git](../development/goals/phase-1/G05-workpad-private-git.md) | G04 |
| [G06 — Journal locking and recovery](../development/goals/phase-1/G06-journal-locking-recovery.md) | G05 |
| [G07 — Contract validators](../development/goals/phase-1/G07-contract-validators.md) | G01, G02 |
| [G08 — Offline create lifecycle](../development/goals/phase-1/G08-offline-create-lifecycle.md) | G06, G07, G11 |
| [G09 — Rebuildable index and read commands](../development/goals/phase-1/G09-index-and-read-commands.md) | G08 |
| [G10 — Phase 1 completion audit](../development/goals/phase-1/G10-phase-1-completion-audit.md) | G09 |
| [G11 — Model invocation foundation](../development/goals/phase-1/G11-model-invocation-foundation.md) | G03 |

G01 and G02 may proceed in parallel after G00. G07 may proceed once the
canonical contracts and scenario harness exist; it need not wait for setup or
journal implementation. No later Goal weakens an earlier contract without an
operator-approved replacement Goal version.

Every Goal stops with:

- its automated unit, contract, and black-box scenario assertions passing;
- exact target and workpad before/after manifests;
- applicable idempotency, corrupt-state, interruption, and offline evidence;
- a requirement-to-evidence completion audit;
- a durable terminal handoff before declared graph transitions are evaluated.

Dependency-ready Goals advance according to the approved graph. They do not
pause after every Goal; only declared operator gates and blocked states require
operator action.

G10 specifically proves that `init` changes only `.gigai/project.toml` and one
idempotent `/.gigai/` exclude entry; offline read commands produce no target
delta; the workpad has no remote; and deleting `state.sqlite` then rebuilding
it produces the same canonical `status --json` projection. Human presentation
output is not used as the rebuild identity contract.

### 17.1A Approved model-port pivot

G11 adds one transport-neutral model-invocation port. Domain code resolves
`configuration -> model target -> endpoint -> factory -> port`; only factory
wiring selects a concrete adapter. Capability differences are declared data,
not transport-specific method signatures. The port carries structured
invocation input and normalized result/status, resolved identity, and raw plus
normalized usage.

G11 migrates the deterministic fixture adapter behind the factory and adds
OpenAI API and OpenRouter API adapters over an internal HTTP base. Anthropic
API, Codex CLI, and Claude CLI remain planned v1 adapters, but each requires a
separate future evidence goal before any compatibility or live-verification
claim. A native-process base is introduced with the first CLI adapter, not as
an unused abstraction.

Live OpenAI Platform API and OpenRouter checks are explicit local
`doctor --live --model-target <name>` actions. They use configured credential
references and target budget policy, are excluded from CI and offline scenarios,
and produce only redacted share-safe evidence. They are limited adapter-local
proofs, not completion of the five-adapter matrix.

G11 extends strict TOML configuration through an explicit v1-to-v2 migration;
it adds no packaged schema or canonical vector. G08 additionally depends on
G11 and uses only the factory-resolved deterministic path. G09's complete
offline doctor uses that same deterministic path; it does not invoke live
checks. G10 audits all Phase 1 goals including G11 and distinguishes its
network-denied scenario suite from G11's separate operator live evidence.

### 17.2 Port and rewrite boundary

The Phase 0 spike code is evidence, not an undifferentiated production source
tree:

- port `research/contract_spike/canonical.py` into
  `src/gigai/canonical.py` with validate-before-render, duplicate-member
  rejection, and front-matter byte comparison intact;
- document that the ASCII identifier restriction makes Python code-point key
  ordering equivalent to JCS UTF-16 ordering for the accepted member-name
  domain; do not broaden or tighten that domain during G01;
- expose differently named APIs for canonicalizing GigAI-owned text and hashing
  imported exact bytes so imported content cannot be normalized accidentally;
- route every canonical JSON and exact-byte digest through the named
  `gigai.canonical` APIs; no other module implements hashing independently;
- rewrite `journal_lock.py` behind the production POSIX backend, timeout,
  committed-head allocation, and recovery contracts while preserving its fsync
  and atomic-replace discipline;
- rewrite `graph_validation.py` as the named validators required by Section
  6.5 while retaining its proven reference, outcome, cycle, and reachability
  cases.

Until GigAI deliberately declares its first public release,
`src/gigai/schemas/` and `fixtures/canonical-vectors.json` are editable
pre-release source contracts. A reviewed change updates affected bytes, tests,
and `SHA256SUMS` together while retaining versioned identifiers, exact-version
readers, closed schemas, and installed verification. At that release, ADR 0003
activates the immutable/additive regime: generated models may add convenience
methods but must not reinterpret published field identity, defaults, ordering,
canonical bytes, or digest semantics.

### 17.3 End-to-end first implementation slice

The first end-to-end slice is deliberately narrower than the full runtime:

1. standalone package;
2. setup with a configurable workpad root and IDE;
3. tiny ignored target binding;
4. private per-Gig Git workpad with no remote;
5. text handoffs and semantic local commits;
6. the Phase 0 schema/canonicalization conformance suite plus Gig/Goal Markdown
   and semantic Goal Graph validators;
7. deterministic offline `create` graph
   proposal/review/feedback/approval lifecycle using the real persisted
   proposal contracts;
8. mandatory Run Brief and sealed Run-manifest creation before launch, with no
   parallel proposal JSON or second approval transition;
9. deterministic background Goal Graph scheduler with sequential nodes,
   parallel nodes, a join, one operator gate, and one typed recovery edge;
10. user-specific verifier execution and per-Goal/Gig completion audits;
11. `RunDetails`, `open`, `status`, `history`, `plan`, `check`, `verify`, and
    `doctor`;
12. rebuildable index and crash recovery;
13. deterministic `improve` producing a reviewable Gig Proposal from a prompt
    and selected local evidence;
14. one sequential technical-spike fixture and one parallel
    specialized-research fixture with a join and different verification.

It answers:

> Can a user enter any repository, review and approve a proposed private Gig,
> invoke it with `run`, inspect its Run Brief, execute the Goal Graph's
> sequential and parallel structure, verify it in the user's declared way,
> preserve every handoff locally, and propose an evidence-backed improvement
> without modifying the target or contacting a network service?

If no, stop before live models, target execution, additional capabilities,
local learning, or repository migration claims.

## 18. Acceptance criteria

### 18.1 Core and privacy

1. A Gig is represented as one finite commissioned workpad, not a Python class
   or reusable workflow, and one approved Gig version pins one Goal Graph.
2. A Goal is a verifiable graph node and owns objective, dependencies,
   transition policy, executor, tools, effects, proofs, evidence, and outcomes.
3. GigAI contains no telemetry, analytics, hosted account, background sync, or
   GigAI-owned runtime network call.
4. Explicit provider calls disclose and record provider, model, exposure, and
   spend.
5. No command creates a workpad Git remote or publishes Gig material.

### 18.2 Storage and journal

6. The target receives only an ignored local binding and no workpad content.
7. The authoritative Gig exists only under the user-selected workpad root.
8. Missing mounts fail closed without creating a second copy.
9. Each Gig has its own local Git repository, local identity, and linear
   GigAI-managed history.
10. Every semantic transition writes an immutable text handoff and local commit.
11. Raw objects are content-addressed and manifest-linked; credentials never
    enter committed or raw artifacts.
12. SQLite indexes can be rebuilt from stable workpad records.
13. `gigai open` from the target opens the full active Gig workpad in the
    configured IDE; `--with-target` resolves both roots.

### 18.3 Creation and agreement

14. `create` commits creation state before external spend.
15. Its primary output is a non-executable Markdown Gig Proposal.
16. Intake distinguishes outcome, scope, proof, domain expertise, uncertainty,
    data policy, tools, dependencies, parallel work, joins, recovery, creation
    cost, and execution cost.
17. Critical creation uses independent domain and execution criticism and
    preserves disagreement.
18. Search and paid sources require explicit bounded creation policy.
19. Feedback and versioning preserve earlier proposals and handoffs.
20. `create` derives user-specific verification and any needed tool-creation
    Goals rather than imposing one domain template.
21. Graph validation rejects cycles, unreachable required Goals, invalid joins,
    undeclared automatic edges, unsafe parallel effects, and impossible budgets.
22. No Gig version exists until the operator approves the Gig Proposal.
23. Approval seals an exact Gig version and Goal Graph but starts no Run.

### 18.4 Goals and execution

24. Only Goals from the sealed approved graph can run.
25. Dependency-satisfied automatic Goals may start without operator review
    after `run` has sealed the Run Brief and manifest.
26. Independent ready Goals may execute concurrently within graph, effect, and
    aggregate budget limits.
27. Joins wait for their exact declared predecessor outcomes.
28. After Gig Proposal approval and `run` invocation, in-execution operator
    review occurs only at declared gates, final Gig acceptance, or an unhandled
    blocked state.
29. A Run may follow approved typed recovery edges but never invent or rewire a
    node during execution.
30. Active graph structure and acceptance criteria cannot be silently weakened.
31. Completed Goals and their evidence remain immutable across later versions.
32. `plan` renders either a proposed or approved Goal Graph and labels that
    state; best-effort Python observation is named `preview` and labelled
    non-authoritative.
33. Rehearsal is authoritative only for its fixture case.
34. `verify` runs the approved Goal or Gig proof rather than a global fixed test
    suite.
35. Every external invocation is durable before launch and ambiguous interruption
    never retries automatically.
36. Commands use structured argv and explicit environments rather than shell
    strings.
37. Target effects and enforcement limitations are visible before execution.
38. `run` revalidates current facts, creates one committed Run Brief and sealed
    Run manifest, then starts the worker without a parallel Run Proposal or
    second approval command.
39. A calling model or script receives the same Run ID, brief, `RunDetails`, and
    evidence pointers as an interactive operator.
40. No model, tool, target-write, or external-effect call begins before the Run
    Brief, manifest, initial `RunDetails`, and start handoff are durable.
41. `continue` resumes the same Run after a committed gate decision.
42. Terminal `RunDetails` aggregates per-Goal states, errors, typed outcomes,
    evidence, realized parallelism, cost, completion-audit status, terminal
    handoff, and private workpad commit.
43. A Codex or Claude caller can consume the same JSON pointer envelope that a
    human can inspect through documents, JSON, and the local SQLite index.

### 18.5 Providers and evidence

44. Endpoint, model target, and profile remain separate contracts.
45. Discovery, compatibility, and live verification remain separate states.
46. All five v1 adapters pass independent versioned evidence gates.
47. Raw usage and normalized usage are both retained.
48. Monetary cost is provider-reported, derived with provenance, or unavailable;
    missing is never rendered as zero.
49. Every completed Goal has its own evidence, and every terminal Run has a
    Gig-level requirement-to-evidence completion audit.

### 18.6 Local improvement

50. Improvement reads only local approved history unless the user explicitly
    authorizes a provider call.
51. Learned verification preferences cite explicit feedback or accepted,
    rejected, blocked, and not-evaluable outcomes.
52. `improve` records the operator's change request and cites evidence for every
    proposed node, edge, executor, tool, proof, budget, recovery, or parallelism
    change.
53. Improvement creates a Gig Proposal and never silently edits an
    active graph, plans, prompts, profiles, capabilities, or user preferences.
54. Accepted learning remains user-local and is never published by GigAI.

### 18.7 Contract portability and ordering

55. Every stable serialized boundary validates against its checked-in Draft
    2020-12 schema and rejects unknown top-level fields.
56. Two conforming implementations produce the recorded restricted-JCS bytes
    and SHA-256 digests for every golden vector, including distinct composed
    and decomposed Unicode values.
57. Persistent IDs use the documented prefixed lowercase UUIDv4 form; journal
    sequence, never ID sorting, defines transition order.
58. Approval atomically advances the explicit active Gig version under the
    per-Gig writer lock; `run` never guesses a latest version.
59. Concurrent CLI, worker, and recovery writers allocate one strictly
    increasing, collision-free 12-digit committed handoff sequence from the
    committed journal head under an interprocess lock; SQLite is never the
    allocator.
60. `rehearse` and `eval` resolve Goal identity within an explicit Gig and
    approved version; a display ordinal is never accepted as stable identity.
61. `--wait` returns at terminal state or a durable operator gate with the
    documented status and exit-code distinction.
62. V1 runs on Python 3.11 macOS and Linux only, fails unsupported platforms
    before mutation, and treats mount capability as proven behavior rather than
    an operating-system assumption.
63. Every added command passes the installed-CLI scenario matrix with automated
    assertions plus durable target/workpad manifests and transcripts.

## 19. Risks and responses

### `create` becomes an expensive ceremony for trivial work

Use inspected templates and routine rigor for familiar reversible work. Spend
belongs where uncertainty and consequence justify it. `create` still produces
only a proposal: the operator sees creation spend before dispatch and may
reject the result without creating an approved Gig.

### Goal documents become a generic workflow DSL

The Goal Graph has deliberately fixed semantics: dependencies, typed outcomes,
joins, gates, recovery edges, and bounded concurrency. Versioned capabilities
own executable behavior in ordinary code. GigAI does not add arbitrary graph
expressions, dynamic node creation, or a general-purpose workflow language.

### `create` imposes the wrong verification

Treat templates as questions, inspect the actual target and available tools,
record the user's acceptance method, and require the proposed graph to explain
why each proof is valid for this Gig. The user reviews that explanation before
the proposal becomes a Gig version.

### A model or wrapper approves its own Gig Proposal

`create` and `improve` stop after writing their Gig Proposal and accept no
embedded approval, `--yes`, or same-request auto-approval. Approval records the
operator actor and validates the sealed proposal before granting version
authority. A model, CLI wrapper, or script may summarize its proposal but
cannot convert that output into an approved Gig version.

### Parallel Goals interfere with each other

Intersect declared effects, write surfaces, exclusive resources, and aggregate
budgets before launch. Serialize on uncertainty and record why concurrency was
reduced.

### Graph learning optimizes speed instead of user value

Never optimize one metric in isolation. Proposed changes cite completion proof,
user acceptance/rejection, errors, cost, duration, and safety effects. The user
chooses whether the candidate graph is actually better.

### “Self-healing” silently rewrites authority

Freeze the active graph. Permit only preapproved recovery edges during a Run.
All structural improvement creates a reviewable Gig Proposal; only approval
creates a new version.

### The private journal becomes too noisy

Commit semantic handoffs, not token streams or individual subprocess events.
Keep raw events in content-addressed objects and summarize them in the next
handoff.

### Local Git is mistaken for publication

Use a repository-local identity, configure no remote, block GigAI operation when
a remote appears, never invoke network Git commands, and state clearly that the
target repository does not track the workpad.

### Workpad and target become confused

Display both resolved roots in every live header. Record target commits and
private journal commits in distinct fields and identities.

### A missing mount loses or forks history

Fail closed on the stable locator. Never fall back to a new home directory.
Provide explicit inspection and relocation commands that preserve journal
identity.

### Local learning becomes silent personalization

Learn only from explicit feedback and outcome records, show provenance, and
require acceptance before writing a durable preference.

### “Local-only” hides provider exposure

Separate the no-GigAI-telemetry promise from user-authorized provider calls.
Show exactly what leaves the machine and record what was actually sent.

### Python or coding CLIs bypass effect policy

Use native restrictions where available, subprocess isolation, target
observation, sealed source, explicit authorization, and honest enforcement
labels. Do not claim a sandbox.

### Provider facts drift

Key evidence to endpoint, selector, resolved model, adapter/CLI/SDK version,
platform, schema, fetch time, and probe surface. Refresh explicitly and fail
closed when the required proof is stale.

### Crash recovery duplicates paid work

Persist invocations before launch, preserve raw artifacts, classify ambiguity,
and require an explicit new invocation.

### A background worker exits without reporting

Persist initial `RunDetails` and worker identity before returning from `run`.
Startup and status reconciliation classify a missing owner as interrupted,
preserve its output, finalize a truthful handoff, and never relaunch it
automatically.

### State grows indefinitely

Report mount size and largest children, warn at a configurable threshold, and
never auto-delete. Add retention only with a recoverable, audited caller.

## 20. Deferred until a real caller

- long-lived daemon, scheduler, and recurring Gigs beyond one supervised worker
  per active Run;
- TUI or hosted artifact browser;
- MCP facade;
- remote workpad synchronization, backup, or collaboration;
- Git remote creation, fetch, pull, push, or publication;
- multi-user permissions and shared approval;
- marketplace or unscoped plugin discovery;
- arbitrary model-callable dynamic tool loops;
- generic cross-Gig invocation and dependency graphs;
- automatic plan, Goal, preference, capability, or profile modification;
- dynamic Goal creation or graph rewiring inside an active Run beyond sealed
  typed recovery edges;
- automatic rollback or target Git reset;
- external-system mutation beyond separately proved adapters;
- untrusted capability execution;
- enforced cross-platform sandbox claims;
- providers beyond the five required v1 adapters;
- automatic dependency installation requested by generated content;
- exact static analysis of arbitrary Python;
- private-chain-of-thought capture;
- GigAI-hosted learning, analytics, or telemetry.

## 21. Settled decisions in this draft

- The command, package, and import name are `gigai`.
- GigAI is domain-neutral; engineering is one Gig class, not the system
  boundary.
- A Gig is a finite commissioned project; reusable behavior is a Capability or
  pack.
- An approved Gig version is represented internally as a user-specific Goal
  Graph.
- Goals are verifiable graph nodes; dependencies sequence them, independent
  ready Goals may run in parallel, and multi-parent Goals form joins.
- `create` is the critical deliberative authoring path.
- Creation discovers the user's Goals, tools, verification, dependencies,
  parallelism, joins, gates, and recovery needs rather than imposing a universal
  domain workflow.
- `create` produces an inspectable, non-executable Gig Proposal containing
  Markdown Goals and a validated graph, then stops.
- No Gig version or execution authority exists until the operator explicitly
  approves that proposal.
- Gig Proposal approval freezes an immutable Gig version but starts no Run.
- Automatic transitions and parallel execution occur only as declared in the
  approved graph; after proposal approval, in-execution operator review occurs
  only at declared gates, blocked states, and final Gig acceptance.
- Every Goal owns its executor, tools, effects, proof, evidence, typed outcomes,
  and transition policy.
- Every semantic transition creates a `.txt` handoff and private local Git
  commit.
- Each Gig owns one local-only Git repository with no remote and no GigAI Git
  network operations.
- The target repository contains only an ignored binding; the full workpad lives
  on the user-selected mount.
- `gigai open` from a target opens the active full Gig workpad in the configured
  IDE; `--with-target` exposes both roots.
- Workpad files and manifests are authoritative; SQLite is rebuildable.
- `run` is the execution instruction for one approved Gig version. It resolves
  blocking questions, revalidates current target and policy facts, commits the
  mandatory Run Brief and sealed manifest, and launches the supervised local
  background worker.
- A model, CLI wrapper, script, or API caller receives the same Run ID,
  `RunDetails`, brief, completion-audit, handoff, workpad, and target paths.
- `RunDetails` is the small shared reporting contract for humans, Codex, Claude,
  scripts, and API callers; detailed local evidence remains in documents,
  content-addressed objects, and SQLite.
- `plan` renders the human Goal Graph and clearly labels whether it is proposed
  or approved; best-effort executable observation is `preview`.
- Completed Goals are immutable; feedback revises only future authority.
- Endpoint, model target, and profile remain separate.
- OpenAI API, Anthropic API, OpenRouter API, Codex CLI, and Claude CLI are equal
  first-class v1 adapters.
- Provider discovery is metadata, not compatibility proof.
- Creation and Goal execution have separate explicit budgets.
- `improve <gig-id> ["<change request>"]` combines an explicit operator prompt
  with selected local Goal outcomes, verification, errors, cost, parallelism,
  recovery, and feedback, then produces a Gig Proposal through the ordinary
  approval lifecycle.
- Self-healing means approved recovery within a Run and provenance-backed graph
  improvement between Runs; active authority never rewrites itself.
- GigAI has no telemetry, hosted user account, cloud history, background sync,
  or knowledge of user Gigs.
- User-authorized provider calls are explicit, inspectable network exposure and
  are not confused with GigAI telemetry.
- No plan, target, provider, spend, activation, publication, or deletion action
  is silent.

## 22. References

### Binding GigAI records

- `docs/research/phase-0-spikes.md` - executable Phase 0 evidence and
  decisions
- `docs/research/runtime-contract-hardening.md` - safety, snapshot,
  lifecycle, planning, evidence, and public-contract research
- `docs/research/check-doctor-command-spike.md` - CLI validation and
  diagnostic-command research
- `docs/research/phase-0-contract-closure.md` - canonical bytes,
  identifiers, schemas, command resolution, wait behavior, and interprocess
  journal-ordering decisions
- `src/gigai/schemas/` - binding Draft 2020-12 serialized contracts and canonical
  encoding profile
- `research/phase0_spike/` - local feasibility code and fixtures
- `research/contract_spike/` - executable contract, graph, and concurrent
  journal fixtures

### Design reference

- Forge V1 Goal artifacts reviewed during revision 13 - independently
  activatable Goal, evidence, completion-audit, and stop-boundary examples;
  design input only, not a path or runtime dependency shipped with GigAI

### Companion operator reference

- `docs/reference/command-sheet.md` - revision-14 companion operator command
  contract

### Live provider contract references

These pages justify the retained adapter and usage shapes but do not replace
exact-version evidence gates:

- [OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)
- [OpenAI List models](https://developers.openai.com/api/reference/resources/models/methods/list)
- [OpenAI API-key guidance](https://developers.openai.com/api/docs/guides/production-best-practices#api-keys)
- [Anthropic Messages API](https://platform.claude.com/docs/en/api/messages/create)
- [Anthropic List Models](https://platform.claude.com/docs/en/api/models/list)
- [Claude Code model configuration](https://code.claude.com/docs/en/model-config)
- [OpenRouter usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting)
- [OpenRouter Responses API](https://openrouter.ai/docs/api/reference/responses/overview)
- [OpenRouter Models API](https://openrouter.ai/docs/api/api-reference/models/get-models)
- [Codex app-server models](https://learn.chatgpt.com/docs/app-server#models)

### Historical records

- `../2026-07-27-agent-workflow-harness-implementation-plan.md` - superseded
  harness exploration and verified facts
- `../research/STAFF-ENGINEER-SUMMARY.md` - book/research summary used to narrow
  the original runtime boundary
- `research/experiments/resume/findings.md` - native session and resume observations

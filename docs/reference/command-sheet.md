# GigAI command sheet

- **Date:** 2026-08-02
- **Status:** companion operator contract for the implementation plan
- **Plan:** `docs/architecture/v14-implementation-plan.md`, revision 14

This is the short operational view of GigAI. The implementation plan remains
authoritative where this sheet omits detail.

## Mental model

```text
GigAI         local runtime and CLI
Gig Proposal  reviewable design with no execution authority
Gig version   immutable operator-approved Gig contract and Goal Graph
Goal Graph    dependencies, parallel Goals, joins, gates, recovery, and budgets
Goal          one verifiable unit of work in the graph
Run Brief     durable preflight and audit surface for one invoked Run
Run           one invocation and execution of the complete approved Goal Graph
RunDetails    small status/result record pointing to complete local evidence
Capability    reusable executable behavior used by Goals
Workpad       private local Git repository containing one Gig and its history
```

The authority boundary is deliberately simple:

```text
create -> Gig Proposal -> operator approval -> Gig version
improve -> Gig Proposal -> operator approval -> new Gig version
run -> resolve questions -> seal Run Brief and manifest -> execute
```

`create` and `improve` cannot approve their own proposals. Once a Gig version is
approved, invoking `run` is the instruction to execute it; there is no separate
per-Run approval command.

## Where files live

The target repository receives only a small ignored binding:

```text
<target>/.gigai/
  project.toml
```

For Git repositories, `init` adds `/.gigai/` idempotently to
`.git/info/exclude`. It does not edit tracked `.gitignore` or application files.

The complete Gig lives on the configured user-controlled workpad mount:

```text
~/.gigai/workpads/                 default; configurable during setup
  projects/<project-id>/
    gigs/<gig-id>/
      .git/                        private local-only history; no remote
        gigai-writer.lock          interprocess writer lock
      gig.md
      goals/
      handoffs/
      reviews/
      decisions/
      evidence/
      manifests/
        active-gig-version.json    approved version used by default
      runs/<run-id>/
        run-brief.md               pre-execution audit surface
        run-manifest.json          sealed before external execution
        run-details.json           atomically updated during execution
        summary.txt
      objects/                     large/raw artifacts; Git-ignored
      state.sqlite                 rebuildable index; Git-ignored
```

The workpad journal is authoritative. SQLite is an index. GigAI never creates a
workpad remote or performs fetch, pull, push, or publication. If the configured
mount is unavailable, GigAI fails closed instead of creating another copy.

Persistent IDs are an entity prefix plus lowercase UUIDv4 and are never sorted
to infer order. Handoffs use a per-Gig sequence such as
`000000000004-run-started.txt`, allocated under the exclusive interprocess
writer lock. Approved Gig versions are positive integers. Without `--version`,
commands use `manifests/active-gig-version.json`; they never guess "latest".

## First use

```bash
uv tool install gigai
gigai setup

cd /path/to/any/repository
gigai init
gigai create research-gigai
```

`setup` configures the machine. `init` binds one target. `create` begins one
finite Gig and stops after producing a Gig Proposal for review.

V1 supports Python 3.11 on macOS and Linux when the selected workpad mount
passes the lock and atomic-replacement probes. Ubuntu and Debian are the
continuous Linux baselines. Windows is explicitly unsupported in v1 rather
than backed by an untested lock implementation.

## Setup and model access

```bash
gigai setup
gigai models
gigai profiles
gigai doctor
```

The setup wizard:

1. chooses the GigAI home, workpad mount, and default IDE;
2. detects Codex and Claude CLIs and authentication presence without reading or
   copying token values;
3. offers OpenAI API, Anthropic API, OpenRouter API, Codex CLI, Claude CLI, or
   any mix;
4. stores API keys in the OS credential store or records only an environment or
   secret-manager reference;
5. optionally fetches provider catalogs as an explicit metadata network action;
6. creates one or more named model targets per endpoint;
7. creates a default role profile and optional named profiles;
8. materializes immutable built-in packs and editable user capabilities;
9. shows every filesystem, credential-store, and network effect before apply.

Raw keys are entered only through hidden input. They never belong in argv,
`config.toml`, a target repository, a workpad, or Git.

The model configuration layers remain separate:

```text
Endpoint       adapter + transport + authentication reference
Model target   endpoint + model selector policy + inference settings
Profile        default target + role-to-target mappings
```

The equal first-class v1 adapters are:

```text
openai_api
anthropic_api
openrouter_api
codex_cli
claude_cli
```

Inspect configuration without generation:

```bash
gigai models
gigai models --endpoint openai-main
gigai models --endpoint openai-main --search coding
gigai models --endpoint openai-main --refresh
gigai profiles
```

Catalog state is not compatibility proof:

```text
DISCOVERED      selector appeared in provider or CLI metadata
COMPATIBLE      versioned adapter evidence supports the required capability
LIVE-VERIFIED   an explicit scoped live probe succeeded
```

Refresh is an explicit metadata request. Large catalogs are searched, filtered,
or paginated rather than dumped. No provider or model fallback is silent.

## Initialize and open a target

```bash
gigai init
gigai init --target /path/to/non-git-target

gigai open
gigai open <gig-id>
gigai open --target
gigai open --with-target
gigai workpad path [<gig-id>]
```

From an initialized target, `gigai open` opens the active Gig's complete private
workpad in the configured IDE. `--target` opens the target itself;
`--with-target` exposes both roots. GigAI does not copy or symlink the workpad
into the target.

## Create a Gig Proposal

```bash
gigai create research-gigai
gigai create security-review --spec security-review.md
```

There is no `create-gig` command. `create` is the deliberative authoring path.
It interviews the operator about:

- outcome, scope, exclusions, and uncertainty;
- target and permitted context;
- user-specific acceptance and verification;
- domain and execution expertise;
- tools, providers, network exposure, and target effects;
- dependencies, safe parallelism, joins, review gates, and recovery paths;
- creation and execution budgets.

Before creation-time model calls, searches, or paid sources, it shows the
creation team, data exposure, call limits, and budget. Routine creation may use
an inspected local template without provider spend.

The result is a committed, non-executable Gig Proposal:

```text
Gig Proposal
  ID: gp_33333333-3333-4333-8333-333333333333
  Status: proposed
  Gig: research-gigai
  Plan: <workpad>/gig.md
  Graph: <workpad>/manifests/goal-graph.json
  Review: <workpad>/reviews/creation-review.md
  Open: gigai open
  Feedback: gigai feedback gp_33333333-3333-4333-8333-333333333333
  Approve: gigai approve gp_33333333-3333-4333-8333-333333333333
```

Creation stops here. It never approves the proposal and never starts a Run.

## Review and approve a Gig Proposal

```bash
gigai proposals
gigai proposals --gig <gig-id>
gigai show <gig-id>
gigai plan <gig-id>
gigai open <gig-id>

gigai feedback <gig-proposal-id>
gigai revise <gig-proposal-id>
gigai approve <gig-proposal-id>
gigai reject <gig-proposal-id>
```

`plan` renders the readable Goal Graph and clearly labels it `proposed` or
`approved`. Feedback is preserved verbatim. Revision creates linked proposal
history instead of rewriting the earlier proposal.

Approving a Gig Proposal:

- validates the proposal and graph;
- commits an approval handoff;
- freezes the exact Markdown and graph as an immutable Gig version;
- grants that version eligibility for future Runs;
- starts no Goal and no Run.

## Improve an existing Gig

```bash
gigai improve research-gigai
gigai improve research-gigai "make source-freshness proof stricter"
```

`improve` is the only adaptation command. There is no separate `adapt`,
`improve graph`, or `learn-from` command.

With no change request, GigAI asks what should work better and which local Runs
or feedback should inform the proposal. With a quoted request, that text is the
improvement commission. GigAI shows the approved baseline, selected local
evidence, model/data exposure, and improvement budget before any provider call.

The command may propose changes to Goals, dependencies, parallelism, joins,
gates, recovery, tools, executors, budgets, or verification. Every change must
cite the operator request or relevant local evidence.

The result is an ordinary Gig Proposal. `improve` stops without mutating the
approved version or starting a Run. Use the same commands to finish it:

```bash
gigai feedback <gig-proposal-id>
gigai revise <gig-proposal-id>
gigai approve <gig-proposal-id>
gigai reject <gig-proposal-id>
```

Approval creates a new immutable Gig version. Existing versions, Runs, and
evidence remain unchanged. No background learner runs automatically.

## Inspect a Gig and its Goal Graph

```bash
gigai gigs
gigai status [<gig-id>]
gigai show [<gig-id>]
gigai history [<gig-id>]
gigai goals [<gig-id>] [--version <version>]
gigai plan [<gig-id>] [--version <version>]
```

The Goal Graph has fixed semantics: dependencies, typed outcomes, parallel-ready
Goals, joins, operator gates, typed recovery edges, and bounded concurrency. It
is not a generic workflow language, and the operator does not need to write
graph syntax.

## Check, diagnose, preview, and rehearse

```bash
gigai check [<gig-id>] [--json]

gigai doctor [--json]
gigai doctor --live --endpoint <name>
gigai doctor --live --model-target <name>

gigai preview [<gig-id>] [--version <version>] [--goal <goal-id>] [inputs...]
gigai rehearse <gig-id> --goal <goal-id> --case <name> [--version <version>]
gigai eval <gig-id> --goal <goal-id> [--suite <name>] [--version <version>]
```

| Command | Meaning | Provider/model calls by default |
|---|---|---:|
| `check` | Validate the portable Gig, graph, Goal, capability, and tool contracts | no |
| `doctor` | Diagnose this installation, credentials, mount, adapters, IDE, and journal | no |
| `doctor --live` | Run one explicitly scoped compatibility probe | yes |
| `plan` | Render the human proposed or approved Goal Graph | no |
| `preview` | Best-effort zero-effect scheduling/capability observation | no |
| `rehearse` | Execute one fixture-backed Goal case | no |
| `eval` | Evaluate fixtures; live judging only when selected explicitly | no |

`check` answers whether the authored Gig is structurally valid. `doctor` answers
whether this machine can support it. Higher-level commands perform the required
checks automatically; users do not need to memorize command chains.

`preview` cannot prove every arbitrary Python path. `rehearse` is authoritative
only for its selected fixture case. Neither grants execution authority.
Goal identity always resolves inside the selected Gig version. Labels such as
`G00` are display ordinals, not values accepted for `--goal` in automation.

## Run an approved Gig

```bash
gigai run research-gigai
gigai run research-gigai --wait
gigai run research-gigai --json
gigai run research-gigai --version 1
```

`run` is the execution instruction; it does not start another proposal or
approval workflow. It resolves the explicit active approved Gig version, or
the exact approved version named by `--version`, against current facts.
If required inputs or material policy choices are missing, interactive use asks
the operator and noninteractive use returns `needs_input` without creating a
Run.

After resolution, GigAI reserves a Run ID and durably writes these artifacts
before launching any external work:

```text
runs/<run-id>/run-brief.md
runs/<run-id>/run-manifest.json
runs/<run-id>/run-details.json
handoffs/<sequence>-run-started.txt
```

The Markdown brief is the human audit surface. Versioned front matter pins the
Run ID and hashes of the approved Gig version, graph, target observation,
resolved policy, and displayed brief. It uses an opening `---gigai-json` line,
one canonical JSON object, a closing `---` line, and UTF-8/LF text. The machine
manifest seals the same facts for execution.

The Run Brief shows:

- exact Gig version and graph hash;
- target identity, Git HEAD when applicable, and observed status;
- Goal paths, dependencies, possible parallelism, joins, gates, and recovery;
- resolved profiles, model targets, capabilities, and tools;
- filesystem, network, target, and external effects;
- model/tool calls, tokens, cost, time, and concurrency budgets;
- user-specific verification and expected evidence;
- material risks, warnings, and reasons the Run could block;
- exact invocation, resolved inputs, and facts sealed for execution.

Default output is a pointer, not a wall of logs:

```text
Run: run_77777777-7777-4777-8777-777777777777
Status: preparing
Brief: <workpad>/runs/run_77777777-7777-4777-8777-777777777777/run-brief.md
RunDetails: <workpad>/runs/run_77777777-7777-4777-8777-777777777777/run-details.json
Handoff: <workpad>/handoffs/000000000004-run-started.txt
Wait: gigai wait run_77777777-7777-4777-8777-777777777777
Open: gigai open
```

The Run starts immediately after the brief, manifest, initial `RunDetails`, and
start handoff are durable. Target, profile, model/tool, compatibility, exposure,
or budget failures stop the command before external execution. Default `run`
returns after durable worker launch; `run --wait` waits on that same Run.

`run --wait`, `wait`, and `continue --wait` return when the Run is terminal or
durably reaches `waiting_for_gate`. A healthy gate pause exits 0 and reports
`next_actions`; failure, blockage, cancellation, or interruption exits 1;
invalid usage exits 2; noninteractive `needs_input` exits 3.

The worker executes dependency-ready Goals, may run independent Goals in
parallel within effect and budget limits, waits for exact joins, and follows
only sealed gates and recovery edges. Goal completion may unlock downstream
Goals automatically; only declared gates pause healthy execution for review.

## Follow a Run

```bash
gigai run-details [<run-id>] [--json]
gigai wait <run-id> [--json]
gigai status [<gig-id>]
gigai show [<gig-id>]
gigai history [<gig-id>]
gigai open [<gig-id>]

gigai continue <run-id> [--wait] [--json]
gigai stop <run-id>
```

`RunDetails` is the shared small record for humans, Codex, Claude, scripts, and
API callers. It includes graph and per-Goal state, typed outcomes, errors,
evidence paths, usage, cost, target observations, completion-audit status,
terminal handoff, and permitted next actions.

Raw output and full evidence remain in the private workpad. A calling model can
read and summarize those artifacts; if it is itself executing inside the Gig,
it writes the same evidence paths for the surrounding operator to inspect.

`continue` resumes the same nonterminal Run after an approved operator-gate
decision. It does not create another Run. Interrupted external model calls are
never retried automatically.

## Verify, review, and close

```bash
gigai verify <run-id> [--goal <goal-id>] [--json]
gigai review <run-id> [--goal <goal-id>]
gigai accept <run-id> [--goal <goal-id>]
gigai block <run-id> [--goal <goal-id>] --reason <text>
```

`verify` executes the proof declared by this Gig or Goal. GigAI does not impose
one universal test suite across software, research, writing, automation, and
other domains.

Every completed Goal owns evidence and a completion audit. Every terminal Run
owns a Gig-level requirement-to-evidence audit. Final operator acceptance closes
the Gig; it never rewrites recorded evidence.

## Usage and cost

```bash
gigai tokens [<run-id>] [--json]
gigai costs [<run-id>] [--json]
```

GigAI retains raw provider usage plus normalized usage. Cost is labeled
provider-reported, derived with price-table provenance, or unavailable. Missing
or subscription-backed cost is never displayed as zero.

Creation, improvement, and Run execution have separate budgets. The applicable
budget and network exposure appear before each authorized spend boundary.

## Command effects at a glance

| Command | Default network | Durable effect |
|---|---:|---|
| `setup` | no | confirmed machine configuration |
| `init` | no | ignored target binding and local registry entry |
| `create` | explicit | Gig Proposal and private journal history |
| `feedback`, `revise`, `reject` | no unless review requested | proposal history |
| `approve <gig-proposal>` | no | immutable Gig version; no Run |
| `improve` | no by default | Gig Proposal; no version mutation |
| `run` | only when the approved Gig permits it | sealed brief/manifest, start handoff, and supervised Run launch |
| `continue` | only approved continuation effects | same Run resumes |
| `stop` | no new network | supervised cancellation |
| `verify` | only when declared proof requires it | verification evidence |
| `open` | no | launches configured IDE |

## Automation rules

- Use typed inputs and documented `--json` output when another program consumes
  a command.
- A program may call `create` or `improve` and present the resulting Gig
  Proposal, but it may not approve that proposal in the same request.
- `approve` applies only to a durable Gig Proposal ID and starts no worker.
- Invoking `run` starts one Run of an already-approved Gig version. `--wait`
  waits on that same Run.
- `run` without `--version` uses the explicit active-version record; an older
  approved version requires `--version <positive-int>`.
- Raw provider selectors and API keys are not ordinary Gig or Run overrides.
- Tools use structured argv and explicit environments, never free-form shell
  command strings.
- No ordinary `check`, `plan`, `preview`, `rehearse`, or default `doctor` spends
  model tokens. `run` may spend only within its approved Gig budgets.
- GigAI performs no telemetry, remote workpad synchronization, Git push, or
  hosted learning. User-authorized provider calls are the only planned network
  exposure.

## Complete primary command surface

```text
gigai setup
gigai init [--target <path>] [--workpad-root <path>]
gigai create <name> [--spec <path>] [--profile <profile>]
gigai improve <gig-id> ["<change request>"]

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
```

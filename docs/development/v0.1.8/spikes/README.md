# GigAI v0.1.8 — Research spikes

**Status:** Backlog with recorded S08/S09/S10 research and a completed bounded
S11 Phase 1 groundwork packet; a bounded Spike 6 local-model pilot is
separately authorized below.  
**Sequence:** v0.1.8 release groundwork starts with the bounded S11 Phase 1
packet under explicit authorization; later release phases still wait for the
actual v0.1.7 Scout verification. S07 remains parallel, non-blocking research;
it is not the release's first prerequisite. S08, S09 and S10 research records do not
authorize runtime changes, provider calls or implementation.  
**Requested:** 2026-09-07.

v0.1.7 is for the current operator's own use first. Finish Scout before the
remaining full spikes, apart from the explicitly authorized Spike 6 pilot and
the recorded S08/S09/S10 research. This list records
future research, not approved designs or
new v0.1.7 requirements. The included Scout package and portability work remain
part of the [existing roadmap](../../v0.1.7/roadmaps/v0.1.7-scout-roadmap.md).

Concrete delivery work is tracked in the [v0.1.8 backlog](../README.md), including
[V018-01: workpad navigation and readable artifacts](../tasks/V018-01-workpad-navigation-and-readable-artifacts.md).
That requested cleanup builds on accessibility Spike 1; it is not only research
and does not depend on the memory, hooks, or Dolt spikes.

## Ticket format for new work

Start new v0.1.8 work documents with a short Jira-style Markdown ticket:
problem, intended system-behavior change, tasks, acceptance criteria and
attached evidence. Put conversation notes and granular specifications below
that summary. Mark undefined scope and proposed experiments explicitly; the
ticket is not an execution receipt. Existing documents need not be rewritten
as part of recording this convention.

## Release foundation and parallel research

[S11](S11-behavior-based-test-organization.md) is the first v0.1.8 release
foundation: its bounded Phase 1 packet inventories and measures current tests and
installed verifiers by behavior and lane, establishes deterministic fixtures,
separates unit/integration/CLI/installed/live evidence, and performs only a
bounded initial migration. Preserve coverage and failure semantics; do not
mass-rename or block the first useful slice on repo-wide cleanup.

[S07](S07-execution-modes-and-cost-aware-orchestration.md) is a parallel,
non-blocking research thread, requested 2026-09-10. Investigate local/API/CLI
eligibility policies, automatic role and dependency assignment from the task
prompt, risk-based review, and dispatch/ETA/yield coordination with completion
events and one fallback check. Measure orchestrator overhead as well as worker
work, but do not hold the first slice for S07 implementation. Names such as
`no_$` and `quick-review` are proposals, not shipped flags. Existing Spike 1–6
identifiers remain unchanged to preserve links and history; their numbering is
not the execution order. No new v0.1.7 scope is implied.

## Current S08–S11 status

| ID | Current evidence/status | v0.1.8 release relationship |
| --- | --- | --- |
| S08 | Research recorded; frozen cases, setup parity and human adjudication method documented | Prepare a small synthetic-input pilot early; research does not prove runtime/model quality |
| S09 | Research recorded, with documented provider claims and a labelled small query addendum | Informs acquisition; freshness/indexing and durable storage rights remain unproven |
| S10 | Research recorded; local adapter boundaries are documented and caller/installed/live gaps remain | Audit relevant callers after v0.1.7 ships; adapter evidence is not caller or release proof |
| S11 | **Phase 1 groundwork implemented and independently verified:** inventory, measured receipts, explicit lanes and bounded acquisition migration recorded under `evidence/`; [focused project-local lifecycle receipt](../evidence/S11-lifecycle-uv.json) is 13/13 passed, while the missing-`questionary` result remains historical shared-interpreter baseline. Terra task `task_6795b28e61ad` / dispatch `ctx_b6f405f97b53` confirmed the bounded migration/selector and exact receipt. | First release foundation slice complete; v0.1.7 verification, Phase 2 audit/interface freeze and later gates remain required |

## Starting point from the Gig design discussion

A Gig is user-owned, clonable software, not just a prompt or workflow. Its
workspace should organize its goal graphs, instructions, local Python tools,
SQLite database, frontend, `docs/`, references, changelog, and Run history.
Documents and records should link back to their originating graph/Run and
supporting evidence. Use `docs/`, not `documents/`, for the proposed folder name.
The revised v0.1.7 amendment's layout/migration contract was accepted in its
separate re-review; this note does not relocate current data.

The operator selected simple SQLite for v0.1.7, with local CRUD/import/report
tools shipped inside each Gig. Tools can start from a common scaffold but
become that Gig's own customizable code. A clean, read-only HTML report and
local CSS provide the default view; agents use the Python tools to make data
changes. Neither HTTP CRUD services nor a shared mutable tool implementation
are required. Generic validated journal persistence remains shared locally;
domain commands and source copies remain Gig-owned. Reuse `state.sqlite` for
Scout's rebuildable tables, and separate editable `ui/` source from generated
`reports/scout/` output. Hooks and alternative database engines remain later research.

The [Scout user-owned Gig amendment](../../evidence/v0.1.7/Scout/SCOUT-00-user-owned-gig-amendment.md)
now records this workspace, SQLite/tooling, UI and username-aware `gigai init`
for all bundled defaults, accepted in the separate 2026-09-08 workspace
re-review. The earlier lifecycle review did not cover it; this spike list is not
implementation or review evidence.

## Spike 1 — User accessibility, onboarding, and interaction

**Question:** What is the simplest way for a user to set up GigAI, use a Gig
through an agent, answer questions, and approve meaningful actions without
having to understand internal IDs and lifecycle terminology?

The operator found the earlier HTMX experience cumbersome. The recent
baseline-approval exchange also exposed friction: the user wanted the agent
to proceed but had to understand and execute a separate terminal command.
Research the interaction and setup problem before choosing a UI framework.

### Investigate

- Revisit the existing HTMX/browser work, its usability findings, and the
  later CLI-first work. Identify which difficulties came from the interface,
  the underlying workflow, or the way decisions were explained.
- Compare a TUI, a small local browser UI, agent-native interaction, and
  existing plugins/integrations. Study actual onboarding and approval flows;
  do not assume any one interface is the answer.
- Follow the whole first-use path: setup, model/account selection, included
  Gig initialization, progressive preferences, first useful result, and
  returning in a fresh session.
- Explore explicit agent-mediated approval: how a user's informed approval
  can be recorded without an agent pretending it was a direct CLI action.
  Distinguish approving requirements, approving a Gig change, authorizing
  execution/spending, and accepting an outcome in plain language.
- Cover keyboard access, readable status/errors, interruption and resumption,
  and a usable non-interactive CLI path. Keep ordinary GigAI commands free of
  development-only wrappers such as RTK.

### Starting points in this repository

- [Earlier proposal-interview spike](../../evidence/phase-3/S22-01/decision-record.md)
  and [G22 implementation contract](../../goals/phase-2/G22-deliberative-create-and-proposal-interview.md).
- [Recorded proposal UI usability problems](../../tech-debt/TD-0005-proposal-interview-ui.md).
- [Browser-first setup/create work](../../goals/phase-5/S27-CREATE-browser-first-create-and-model-setup.md).
- [CLI-first seam research](../../v0.1.7/spikes/S40-cli-first-seamless-local-runtime.md).

### Expected output

A comparison grounded in existing implementations, a proposed first-use and
returning-user flow, and a decision record explaining what to keep, replace,
or defer. Use small disposable interaction prototypes where they help test
the recommendation. Record open questions and separate implementation goals.
Completing this spike does not itself change the current consent contract.

## Spike 2 — Memory that makes Gig improvements effective

**Question:** What should a Gig remember, how should it store and retrieve
that memory, and how can we show that using it produces better future work?

This is a substantial, potentially multi-day research spike. Read relevant
papers and inspect real systems; do not begin by choosing a database or
equating more saved chat with better memory. Improvement quality is the main
objective. Speed matters, but slower background analysis is acceptable if
the interactive path stays useful and the resulting improvements are real.

### Investigate

- Study primary research on agent memory, retrieval, experience reuse,
  consolidation, reflection, and evaluation. Record each approach's evidence,
  limitations, and relevance to GigAI rather than relying on paper claims alone.
- Separate user facts/preferences, source material, Run history, observed
  outcomes, feedback, reusable lessons, and proposed changes to a Gig's code,
  instructions, graph, or checks. Determine which deserve distinct records.
- Compare simple file/JSON and structured records with retrieval indexes and
  other memory representations. Evaluate selection, retrieval, summarization,
  deduplication, contradictions, freshness, forgetting, and storage growth.
- Preserve provenance back to the supporting evidence. Distinguish observed
  facts from agent conclusions; study how incorrect or adversarial material
  can become a persistent bad lesson and how a user can correct or remove it.
- Separate fast retrieval during a task from slower background consolidation
  and candidate improvement work. Compare latency, token/cost budgets,
  interruption/recovery, and user control. Background processing is a research
  option, not permission to start unattended jobs now.
- Define how to evaluate candidate changes against an unchanged baseline and
  held-out tasks. Compare no memory, simple saved context, and candidate memory
  methods; measure task quality, regressions, repeated mistakes, latency, cost,
  and storage. More records or faster retrieval alone are not proof of improvement.
- Study portability of each Gig's code, data, memory, and Run evidence;
  private-data boundaries; and review/approval of proposed new Gig versions.
  Do not assume hidden model reasoning is available or necessary.

### Starting points in this repository

- [G20 local improvement and evaluator learning](../../goals/phase-5/G20-local-improve-and-evaluator-learning.md)
  and its [completion audit](../../evidence/phase-5/G20/completion-audit.md).
- [Existing learning implementation](../../../../src/gigai/learning.py).
- Scout's saved artifacts, user corrections, and completed Run evidence, once
  available. Use only explicitly selected private material in experiments.

### Expected output

An annotated reading/source list, comparison of approaches, proposed memory
lifecycle and storage/retrieval boundaries, and an evaluation plan with
bounded experiments where useful. End with a recommendation, remaining
uncertainties, and separate implementation goals. No automatic promotion of
lessons or modification of an approved Gig follows from finishing the spike.

## Spike 3 — Per-Gig hooks and documentation drift checks

**Question:** Which explicit lifecycle hooks help a user-owned Gig keep its
code, goal graphs, documents, references, changelog, database, and reports
consistent without introducing surprising automatic work?

### Investigate

- Markdown link/backlink checks, missing or stale references, and drift
  between graph input/output contracts, tools, schema, and documentation.
  Distinguish deterministic broken-link/schema checks from semantic drift
  that needs agent review; neither should silently rewrite user documents.
- Candidate hook points such as artifact submission, Run completion, schema
  migration, report generation, and an explicit validation command. Decide
  which need hooks at all versus ordinary agent-invoked local tools.
- Per-Gig ownership and customization of hook definitions; how hooks travel
  with a cloned Gig; and how users inspect, enable, disable, or change them.
- Execution permissions and trust when cloning another person's Gig. Merely
  cloning, opening, or inspecting a Gig must not execute its supplied code.
- Ordering, bounded execution, duplicate delivery, failure visibility,
  interruption/recovery, and avoiding recursive hook loops. Separate checks
  that block an operation from optional maintenance that can run afterward.

### Expected output

A small set of justified hook use cases, a proposed execution/trust model,
and disposable examples of Markdown drift/link checks and report refresh.
Document what remains manual and how failures are reported. No hooks,
watchers, schedulers, or background processes are activated by this spike list.

## Spike 4 — Does a Gig need Dolt or other database versioning?

**Question:** Would database-native diffs, branching, merging, or cloning
improve user-owned Gigs enough to justify replacing or supplementing SQLite?

**v0.1.7 decision:** Keep simple per-Gig SQLite. Do not install Dolt or add it
as a dependency now. This spike may conclude that SQLite remains sufficient.

### Starting source

[Dolt's official repository](https://github.com/dolthub/dolt) describes a
versioned SQL database with Git-like clone, branch, diff, and merge operations.
It is MySQL-compatible, not a requirement to install a separate MySQL server:
its CLI exposes local `dolt sql` as well as an optional `dolt sql-server` mode.
Dolt itself is an additional executable/dependency. These are initial source
observations, not evidence that it fits GigAI; recheck during the spike.

### Investigate

- Start with actual needs: cloning a personal Gig, comparing improvements,
  reviewing data edits, recovering mistakes, and reconciling divergent copies.
  Distinguish copying an entire private Gig from distributing a clean template.
- Compare SQLite plus explicit records/history/snapshots against Dolt's
  table/schema versioning. Identify useful capabilities that existing Gig
  versioning and Run evidence do not already provide.
- Evaluate local CLI use before considering server mode; installation,
  Python-tool integration, offline behavior, schema migration, resource cost,
  and whether users can still move a Gig as a self-contained workspace.
- Establish ownership of application state and synchronization with Run
  artifacts. A database commit must not become a competing Gig approval or
  rewrite the meaning of a historical Run.
- Test conflicts, interrupted writes, clone/export/restore, and schema changes
  on representative disposable data. Keep private material local; cloning
  does not imply publishing to DoltHub or any remote service.
- Consider privacy/deletion implications of retaining database history and
  the work needed to migrate both into and out of an alternative engine.

### Expected output

An evidence-backed keep-SQLite / optional-Dolt / replace-engine recommendation,
with concrete use cases, costs, portability implications, and a migration
outline only if justified. Coordinate with Spike 2 where memory experiments
benefit from versioned data; database versioning alone does not prove better
memory or more effective Gig improvements.

## Spike 5 — Schema simplification and redesign

**Requested:** 2026-09-08; backlog, not started. After Scout.

**Question:** What is the smallest understandable set of contracts that supports
a user-owned Gig, its goal graphs, Runs, and evidence without today's proliferation
of schemas, duplicated definitions, and version-specific reader logic?

The operator wants a redesign, not just better formatting of the existing files.
Roughly ten clear schemas would be preferable to many overlapping ones, but ten
is an illustrative target, not a quota. Reducing file count while keeping the same
conceptual complexity inside a giant schema is not success.

The [SCOUT-02 review](../../evidence/v0.1.7/Scout/SCOUT-02-independent-review.md)
provides concrete starting cases: new versions lost nested validation rules,
and existing readers did not understand the new formats. Fix those v0.1.7 bugs
under the accepted contracts now; do not defer correctness to this redesign.

### Investigate

- Inventory schemas by purpose, producer, consumer, authority, and lifetime.
  Identify genuinely distinct domain records versus redundant wrappers,
  receipts, projections, and historical compatibility resources.
- Compare a smaller family of explicit record types with shared definitions,
  a common versioned envelope, and a single typed source that generates schemas.
  Assess indirection and tooling costs rather than assuming generation is simpler.
- Trace a real Scout example from preferences/research through a selected graph,
  Plan, Run, saved output, and tracker. Show the minimal JSON and the links a
  user or agent must follow. Explain why each retained contract exists.
- Simplify version dispatch and shared nested types so writers, readers, CLI
  diagnostics, schema documentation, and package inventories cannot easily drift.
- Preserve useful guarantees: malformed nested data is rejected, history remains
  explainable, references resolve to exact evidence, and selection is not consent.
  Challenge redundant machinery without weakening these guarantees by accident.
- Design a compatibility and migration strategy for existing private Gigs and
  sealed history. Compare read adapters with explicit migrations; do not silently
  rewrite old bytes, hashes, identities, or approval meaning.

### Expected output

A proposed smaller contract map, a keep/merge/retire table, readable before/after
Scout examples, and a disposable prototype showing validation and reader dispatch.
Measure distinct concepts, duplication, reader branches, and the effort to add a
field or understand a failed Run—not only schema count. Include a migration/test
plan and separately scoped implementation goals for operator review. Recording
this spike authorizes no schema deletion or runtime redesign in v0.1.7.

Coordinate with V018-01's readable artifacts/navigation work, but keep contract
redesign distinct from presentation cleanup and the memory/database spikes.

## Spike 6 — Local models through Ollama and agent harnesses

**Requested:** 2026-09-09; full integration research remains after Scout.
**Pilot:** Completed 2026-09-09 against the already-installed model; smoke-test
evidence only, not integration or capability acceptance.

**Scope moved into v0.1.7 on 2026-09-09:** The operator approved
[RUNTIME-01](../../v0.1.7/goals/RUNTIME-01-local-execution-and-backend-comparison.md)
after Scout implementation and before final release proof. GigAI will own the
local backend and execution-setup comparison runner; Scout supplies the first
task and eval cases. Qwen/local versus Luna/Codex is intentionally a comparison
of complete setups. That narrow delivery is no longer deferred to this spike.
The broader model/harness survey, hardware tuning, long-horizon capability and
expanded research below remain v0.1.8 work. The pilot alone is not its proof.

The operator requested a small live evaluation now. Luna task
`task_29d2fc13fa23` builds a synthetic Scout diagnostic harness; Qwen, not Luna,
produces candidate answers through the explicit loopback Ollama endpoint.
Root verified `qwen3.8:latest`, 27.3B `Q4_K_M`, digest
`22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643`
in the installed model inventory. This establishes installation, not quality.

The pilot covers grounded extraction, sponsorship uncertainty, preference
ranking, truthful tailoring, historical state, injection resistance and bounded
fixture-tool use, with predefined criteria, raw results and latency. It uses
no real private data, hosted candidate inference, model downloads, real job
applications or runtime integration changes. Supplied-source evaluation is not
proof of online job discovery; no matched Luna benchmark is claimed. Expected
report: `evidence/qwen38-scout-local-eval.md`; reproducible harness/results:
`research/local_model_eval/qwen38_scout/` at the repository root. The initial
pilot is bounded to roughly 40 minutes of evaluation work, not a release gate.

Results: [worker report](evidence/qwen38-scout-local-eval.md) and
[coordinator interpretation](evidence/qwen38-scout-coordinator-review.md).
The counted rerun reports 13/13 keyword-scored rows in 340.74 seconds with
thinking disabled. Coordinator review identifies leading prompts, weak semantic
checks and harness-validation gaps; do not interpret that score as coding
equivalence, autonomous Scout readiness or a privacy audit.

**Question:** Which Gig goals can local models execute usefully and reliably,
and what is the smallest supported path through Ollama without tying a Gig's
goal graph or private history to one model provider?

### Investigate

- Separate model inference from the agent harness that supplies tools, files,
  browsing and iteration. Compare an external harness using a local model and
  Scout's external recording protocol with direct GigAI calls for bounded goals.
  An HTTP adapter alone does not provide an agent tool loop.
- Start with the operator's [Qwen3.8 candidate](https://ollama.com/library/qwen3.8)
  and recheck availability and capabilities during the spike. Consult Ollama's
  [compatibility documentation](https://docs.ollama.com/api/openai-compatibility),
  [tool calling](https://docs.ollama.com/capabilities/tool-calling) and
  [structured outputs](https://docs.ollama.com/capabilities/structured-outputs).
  Advertised capability is a hypothesis to test, not Gig-level acceptance.
- Audit the current adapter factory, endpoint configuration, credential policy,
  health probes, streaming, cancellation, timeouts and response validation.
  Define explicit local endpoint rules; do not globally weaken HTTPS checks or
  require a real API secret for a local service that does not use one.
- Evaluate extraction, supplied-source summaries and structured handoffs before
  assuming reliable long-running research or coding. Run the same per-goal and
  changing-input workflow cases across local and existing hosted backends.
  Check output quality, evidence fidelity, graph decisions, tool behavior and
  useful completion, not just valid JSON or a successful model response.
- Pin model/version, quantization, context settings, prompts, graph and corpus
  revisions, harness and hardware. Measure memory demand, warm/cold latency,
  wall time including retries, and failure/recovery behavior. No per-token API
  bill does not establish that local inference is fast or resource-free.
- Make routing and fallback explicit. Never silently send private local inputs
  to a hosted provider. Local inference does not imply offline browsing/tools;
  document network behavior, source retention and the existing consent boundary.

### Expected output

A reproducible disposable prototype, measured task/backend comparison, and an
evidence-backed recommendation: external harness first, direct adapter, both,
or defer. Include a small evaluation pack, hardware/setup constraints, privacy
and fallback rules, and separately scoped implementation goals. Coordinate with
onboarding Spike 1 and memory Spike 2 without making either a prerequisite.
The pilot's original authorization covered local inference against the installed
model only. RUNTIME-01 separately authorizes the narrow v0.1.7 runtime delivery
at its scheduled turn; it does not authorize model downloads, paid API calls,
or real-private-input comparisons by recording this scope.

## Spike 8 — Cross-model decision evaluation methodology

**Requested:** 2026-09-20; research recorded. No runtime comparison or model
adoption follows from the record.

**Question:** How do we run the same Scout-shaped decision task fairly across
local Qwen/Ollama, hosted GPT, Luna/Codex, and Claude Sonnet, and get a
comparison that is trustworthy rather than one that favors whichever backend
resembles the grader's own style? This sits above Spike 6/RUNTIME-01's
integration mechanics: it is comparison methodology, not integration.

See [S08](S08-cross-model-decision-evaluation-methodology.md) for the full
brief: frozen case/gold-label process with mandatory human adjudication of
judgment cases, setup-parity recording, deterministic-first grading, and
existing harness patterns (DeepSeek, TypeSafe's comparison adapter) to mine.
No live cross-backend comparison or model adoption is authorized by the brief.

## Spike 9 — Local search/retrieval capability sourcing

**Requested:** 2026-09-20; research recorded. Documented provider evidence and
the labelled small query addendum inform design but do not prove freshness,
coverage or durable storage rights.

**Question:** A local-first Scout still needs to find jobs on the public web;
local inference alone does not supply that. Which search/retrieval provider
(Exa, Tavily, Brave, SerpAPI, or other) fits an installable local-first Gig,
at what real cost, and how does a local harness use it without leaking private
preferences into search queries?

See [S09](S09-local-search-retrieval-capability-sourcing.md) for the full
brief: provider comparison, the cost-zero-except-search question, harness
tool-call precedent, and the acquisition/assessment privacy boundary. No
signup, key activation, paid call, or scraping is authorized by the brief.

## Spike 10 — Ollama invocation and harness onboarding

**Requested:** 2026-09-21; research recorded. Caller, installed and live
invocation gaps remain open; no v0.1.7 release gate is added.

See [S10](S10-ollama-invocation-and-harness-onboarding.md) for the practical
invocation investigation: interactive Ollama, GigAI's existing adapter, agent
harnesses and the desktop menu's ChatGPT integration. Establish actual routing,
structured-output support and a clear first-use path, reusing Spike 6's work.
The recorded research does not authorize additional model calls, configuration
changes or implementation and adds no v0.1.7 release gate; caller/installed/live
proof remains a later, separately authorized step.

## Spike 11 — Behavior-based test organization

[S11](S11-behavior-based-test-organization.md) investigates replacing historical
goal-number organization with behavior/component-based tests, explicit unit and
integration boundaries, faster deterministic fixtures and no-model test running.
Requested 2026-09-21; the bounded Phase 1 groundwork is implemented under an
explicit operator authorization. It is the first v0.1.8 release foundation
for the later phases, while the authorization preceded v0.1.7 verification and
does not claim v0.1.7 shipped or authorize later feature/provider work. Preserve coverage,
negative cases, failure meanings and historical mapping; do not mass-rename or
block the first useful slice on repo-wide cleanup.

## Spike 12 — One Gig function as an auditable graph traversal

[S12](S12-gig-graph-traversal-and-auditable-execution.md) records the operator's
graph-centered workflow direction, Scout example, TypeSafe/Jev decision-node
connection, and proposed three-worker Orca acknowledgment evaluation.
**S15 dependency satisfied; task 1 closed 2026-09-22** — task 1 (map
`find-jobs` onto existing graph/execution capabilities) is documented in the
[Scout graph mapping](evidence/S12-scout-graph-mapping.md), revised twice
same-day after review and closed as a documentation mapping. It builds on
S15's finding that Scout's compiler emits single-goal, zero-edge graphs
today. `run.py`'s scheduler policy rejects recovery edges, parallel goals,
and operator-gated goals, but existing test coverage (read, not executed)
shows plain sequential multi-goal execution is supported; the narrower open
question is whether Scout's specific proposed stages and a custom typed
decision outcome (vs. the hard-coded `COMPLETE` observed) are supported —
this does not block the rest of S12's scoping.
**Task 2 documented 2026-09-22** in the
[decision-edge design](evidence/S12-decision-edge-design.md), which answers
that question. The schema, validator and scheduler already declare, check
and route custom typed outcomes; the routing tests were executed and passed.
But no execution path produces a label other than `COMPLETE`/`FAILED`. A
branch that isn't taken is marked `blocked`, which finishes the run
`blocked`, and OR-joins are unsupported. `find-jobs`'s discovery packet
already carries a typed `matches`/`partial`/`no_match` decision that isn't
connected to routing. Candidate changes D1–D6 are recorded; they include
alternatives and an optional item, so they are not all required. **Task 4
documented 2026-09-22** in
[proposed system-behavior changes](evidence/S12-proposed-system-behavior-changes.md)
(the D1–D6 register plus visibility changes V1–V4). **Task 3 run 2026-09-22** as the
[Orca acknowledgment check](evidence/S12-orca-ack-check.md): three ACKs and
three separate completions, none missing or duplicated, and no polling. It
didn't exercise blocking resume or the Orca-unreachable path. Its findings
complete task 4's section C. An operator-authorized
[blocking-resume check](evidence/S12-orca-blocking-resume-check.md) (N3)
then showed a blocked wait resuming within the second of completion. It
also found that heartbeat nudges can start unplanned coordinator turns
(N6). The three-worker Orca
acknowledgment check specifically remains gated behind a separate operator
go-ahead. This is not an orchestration-framework rebuild or a new release
gate.

## Spike 13 — Existing job-discovery solutions research

[S13](S13-existing-job-discovery-solutions-research.md) builds on S09's
provider/ATS survey to find concrete existing *implementations* — projects
and libraries doing posting discovery, dedup, or staleness detection — that
Scout's intended (not yet established as built) discovery tools could reuse.
Requested and researched 2026-09-22, revised twice same-day after review;
see the [research record](evidence/S13-existing-job-discovery-solutions-research.md)
for the eight candidates surveyed and a source-based adapter sketch. Finding:
two installable libraries exist — `ats-scrapers` (its live scraper classes
return a typed `list[Job]` model and cover Greenhouse/Lever/Ashby, kept
distinct from its separate hosted-dataset `search()` interface) and `JobSpy`
(LinkedIn/Indeed-class boards) — neither installed or run, so suitability is
unverified pending real evaluation; `job-finder`'s multi-pass dedup and
apply-link staleness probe are concrete techniques worth adapting; the
embedding-based dedup technique is documented but its effectiveness is
unverified. No provider change, tool-binding change, or implementation is
authorized by this ticket.

## Spike 14 — Schema inventory and consolidation audit

[S14](S14-schema-inventory-and-consolidation-audit.md) inventories the 82
schema files (85 total) in `src/gigai/schemas/`, groups them by concept
family, and identifies which schema versions are actively written by current
input validators, which are read by legacy-only paths, and which have no
evident reader/writer. Requested
2026-09-22. Audit and reporting only; no schema deletion, merge, or version
bump is authorized by this ticket. **Audit documented 2026-09-22** in the
[schema inventory audit](evidence/S14-schema-inventory-audit.md). 76 of the
82 are validated against their file by current code. None of the 12 multi-version families is
legacy-read-only: current writers still emit the older versions, chosen by
payload shape. Seven schemas are flagged for operator review, three of them with
hand-rolled code contracts that disagree with the packaged file, and one
privacy-guard gap (v1-only external-recording recognition) needs a
decision. Nothing is recommended for removal. A follow-up found that 0 of 28 real
journal handoffs conform to `handoff-frontmatter`, and that `occurrence
declare` refuses graph-set Gigs with a misleading error.

## Spike 15 — Goal and tool-unit composition research

[S15](S15-goal-and-tool-unit-composition-research.md) traces how goal graphs
are actually constructed today and finds that **Scout's bundled compiler**
does not currently compose from a reusable catalog (scoped to that one
traced path, not a codebase-wide claim): `scout_materialization.py`'s
`_compiled_snapshot` iterates a fixed six-selector tuple and emits one
single-goal, zero-edge graph per selector, with `tools` hard-coded empty and
a literal `executor` inlined each time; `graph_set.py` only validates an
already-built document. Surveys LangGraph, Temporal, Anthropic's tool-use
API, and CrewAI — CrewAI's `Task` and LangGraph's Functional-API `@task` are
the closest precedents for a position-independent "goal unit"; LangGraph
also has a genuine reusable tool object (`ToolNode`) and richer graph
composition (dynamic routing, subgraphs) than an earlier draft of this
research credited it with — and sketches a composition proposal plus a
handoff note to S14, without ranking GigAI's schema as more expressive than
the frameworks surveyed. Requested and researched 2026-09-22, revised
same-day after review, from the same Gig-composition discussion as S13; see
the [research record](evidence/S15-goal-and-tool-unit-composition-research.md).
Research only; no schema change or new authoring tool is authorized by this
ticket.

## How these spikes finish

Each spike produces a reviewable research decision, not a promise to implement
every option studied. Record sources, evidence, tradeoffs, and unanswered
questions. The operator reviews the recommendation before implementation is
scheduled. S08/S09/S10 research records are bounded inputs; S11 is the bounded
Phase 1 release foundation and its later gates remain planned; S07 remains
parallel and non-blocking. None of these records
claims v0.1.7 shipped or authorizes a provider call, schema/memory/hooks/Dolt
change, storage migration or new full Gig. The separate v0.1.7 product
additions noted above still need their own contract and acceptance updates.

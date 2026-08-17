# GigAI V15 Forward Roadmap

**Date:** 2026-08-10
**Revision:** 15  
**Status:** roadmap draft; selected implementation goals are materialized
**Predecessor:** [V14 implementation plan](v14-implementation-plan.md)  
**Research input:** [Review Loop Foundation Spike](../research/review-loop-foundation-spike.md)

V15 is the forward product roadmap after the V14 Phase 1 local spine and the
first bounded deterministic Run. It does not obsolete V14. V14 remains the
authority for the contracts already implemented and for the boundaries that
V15 must preserve.

**Current roadmap gate:** G13 through G23 are complete and independently
evidenced. G24 is the planning-only local UAT and dogfooding goal; G25 is the
later release-lane alpha-readiness candidate. Later
candidates remain planning-only until their individual goal contracts are
reviewed and approved.
G19 remains bounded by its accepted contract and does not authorize broader
target effects by inference.

This document answers a narrower question than a goal contract: what should
GigAI become useful for next, in what order, and what evidence must exist before
each capability is allowed to grow? The candidate goals below are planning
names only. Each one requires its own reviewed goal document before
implementation.

## 1. The pivot

GigAI has proven the private local substrate: setup, target binding, workpads,
journaled transitions, schema validation, offline reads, model-port wiring, and
a sealed deterministic Run. The next risk is no longer whether GigAI can store
state. It is whether a user can bring a real body of work to GigAI and receive
a result that is reviewable, reproducible, and safe to act on.

V15 therefore makes the **Review Loop Gig** the first dogfood surface. The loop
is deliberately artifact-neutral. It can review two research articles, a
repository and pull request, a spreadsheet, a resume and job description, or a
financial reference bundle without turning GigAI into five unrelated products.

The first loop is:

```text
sync-refs
  -> verify references and review contract
  -> independent review pass
  -> deterministic evidence and coverage verification
  -> feedback and adjudication
  -> address accepted findings
  -> closure verification
  -> final review and terminal decision
```

`sync-refs` is a named reference-synchronization operation, not the definition
of the entire Gig. It establishes the immutable input bundle that the review
contract and later Runs cite.

Gig creation itself needs a separate proposal-interaction stage. The initial
surface should be a short-lived, loopback-only HTMX web session launched by the
CLI, with SQLite and the Gig workpad remaining the durable authority. The model
may generate domain-specific clarification questions, but every question and
answer becomes a persisted proposal artifact before approval.

## 2. Product model

V15 keeps the V14 vocabulary and gives the next layer explicit ownership.

### Gig

A Gig is a finite, user-approved commission represented by an immutable Goal
Graph version. It states the question, intended decision, reference roles,
allowed effects, review contract, evidence requirements, and stopping rules.
A Gig is not a reusable Python workflow and does not silently become a daemon.

### Goal

A Goal is one bounded unit of work with its own executor, inputs, outputs,
verification, budget, effects, and transition policy. A review loop uses Goals
for synchronization, review, verification, feedback, addressing, closure, and
the terminal decision; it does not hide those stages in one opaque prompt.

### Review bundle

A review bundle is an immutable, content-addressed set of references with
roles, provenance, media types, acquisition metadata, and exact-byte digests.
Code is a first-class reference type, not a special case: the bundle may
contain a repository snapshot, backend or frontend source, a pull-request diff,
tests, schemas, configuration, deployment files, issue requirements, local
documents, prior accepted outputs, CSV data, articles, or other explicitly
permitted inputs. A Gig such as `implement-feature-backend` or
`implement-feature-frontend` therefore reviews and produces work against an
explicit code bundle rather than an implicit checkout.

The bundle may also carry a content-addressed capability/tool manifest for
tools required by the Gig, including tools that are proposed but not yet
implemented or installed. That manifest records the requested capability,
version or source constraints, effects, permissions, credential/network needs,
availability, and security-review state. It describes a requirement; it does
not execute or install the tool. A later Run must prove exactly which reference
bytes and which resolved tool artifacts it actually used.

### Review contract

The review contract defines the user question, criteria, severity model,
required evidence and citations, output shape, clarification questions,
closure rules, and cycle/escalation limits. It is the source from which the
system derives the questions an agent must ask when context is missing or
ambiguous.

### Proposal interaction

Proposal interaction is a bounded interview, not a fixed checklist and not a
chat transcript. A local HTMX session starts from the user's description and
selected references, asks adaptive model-generated questions, persists answers
in SQLite/workpad state, presents capability and privacy choices, and renders a
proposal preview for explicit approval. GigAI owns the question protocol,
question limits, answer provenance, and approval transition; the selected
model supplies domain-specific question wording and follow-ups. The browser
session is local-only and ephemeral; it is not a deployed server or a new
authority store.

### Finding and feedback

A finding identifies a criterion failure or unresolved question with a stable
ID, severity, source span or evidence reference, reviewer identity, trace, and
confidence/disagreement metadata. Feedback is a durable operator decision over
findings. Feedback is not itself a review and is not a revision. An address
pass consumes accepted findings and produces a new output/version linked to its
parent. Closure verification checks every accepted finding individually.

### Capability and tool

A capability is something a Goal may invoke under declared effects. A tool may
be a Python package, executable, local service, data connector, or provider
adapter. Proposal-time inspection may determine whether a capability is
available, installable, compatible, and safe under the declared policy. It must
not execute the capability or contact a provider merely to construct a
proposal. Installation and activation require an explicit user decision and a
separate security review path. The proposal's tool manifest and the Run's
resolved-tool evidence are deliberately different: the former can describe a
missing or future tool, while the latter can name only artifacts that were
actually available and authorized for execution.

## 3. Non-negotiable V15 principles

1. **Local-first authority.** Workpads, journals, reference bundles, findings,
   feedback, traces, and reports remain on the user's machine by default.
2. **Artifact neutrality.** The orchestration loop is shared; document, code,
   research, spreadsheet, resume, and finance behavior belongs in reference
   adapters, rubrics, deterministic verifiers, and tools.
3. **Evidence before confidence.** Exact bytes, schema/shape checks, citation
   existence, and deterministic calculations run before model-graded judgment.
4. **Models are replaceable actors.** OpenAI, OpenRouter, a local model,
   Codex CLI, Claude CLI, or a deterministic fixture may fill the same solver
   role. No provider becomes the authority or receives data by default.
5. **Review is not agreement.** Independent findings, disagreement, feedback,
   adjudication, and the final decision remain separately inspectable.
6. **Proposals inspect; Runs execute.** A proposal can report capability
   availability and installation options, but it cannot run a discovered tool,
   mutate a target, or make an unapproved network request.
7. **Every loop has a bound.** The first Review Loop Gig has an explicit cycle
   cap and escalation policy. No self-improvement loop runs indefinitely.
8. **Reproducibility over convenience.** A repeated Run preserves its inputs,
   evaluator versions, model/provider identity, tool versions, and evidence
   references even when timestamps or usage fields vary.
9. **Contract amendments are explicit.** If a required artifact, transition,
   schema field, authority rule, or effect is absent, stop and amend the
   contract before implementing around it.

## 4. First dogfood Gig: the Review Loop

The first useful Gig is a generic review/verify/feedback/address loop. It must
not be marketed as a code-review feature. The same loop should run over a small
fixture matrix:

| Case | Reference bundle | Seeded challenge |
|---|---|---|
| Research pair | Two research articles | Conflicting claims and missing citation |
| Climate pair | Two climate articles | Timeframe/unit ambiguity and unsupported synthesis |
| Pull request | PR diff plus repository snapshot | Regression and incomplete test evidence |
| Repository review | Full repository plus stated question | Scope discovery and high-risk omission |
| Spreadsheet review | Current plus prior CSV/sheet snapshot | Schema drift and unexplained metric change |
| Resume tailoring | Resume, job URL snapshot, and constraints | Unsupported claim or missing qualification match |
| Finance research | Filings, market data, and user question | Stale data, unit mismatch, or unsupported conclusion |

The loop does not assume that each case has the same reviewer prompt. A
domain profile supplies reference roles, rubric criteria, deterministic checks,
required citations, and output sections while the orchestration and evidence
model remain shared.

The first terminal decision must answer, in durable artifacts:

- which references were actually reviewed;
- which criteria were checked and by which evaluator;
- which findings were opened, accepted, rejected, deferred, or resolved;
- what feedback was recorded verbatim;
- what changed in the address pass;
- whether closure verification passed for every accepted finding; and
- why the final result is complete, blocked, or requires operator action.

## 5. Evaluation framework

The evaluation framework is part of the product foundation, not a later test
afterthought. It evaluates both the Gig's output and the evaluators that judge
it.

### Evaluation objects

The first framework should define these concepts before choosing a dependency:

- **Case:** a versioned input bundle, review contract, seeded defects, and
  expected evidence properties;
- **Solver:** a deterministic fixture, API model, CLI model, or other actor
  that attempts a Goal;
- **Verifier:** a deterministic or model-backed evaluator that emits findings,
  scores, evidence references, and its own version;
- **Trace:** the stable Run/Goal/invocation identity plus nested model/tool
  events, with explicit redaction boundaries;
- **Adjudication:** operator labels that preserve disagreement and decide which
  findings are authoritative for the case;
- **Report:** a machine-readable result and a human-readable review that can be
  regenerated from the committed inputs and evidence.

### Evaluator order

Use the cheapest trustworthy check first:

1. schema, shape, digest, and citation-existence checks;
2. deterministic assertions and domain calculations;
3. reference-grounded comparison;
4. calibrated model judge;
5. human adjudication for disagreement, high severity, or missing ground truth.

An aggregate score is never sufficient on its own. Every finding identifies its
criterion, evaluator version, trace, evidence, and decision state.

### Minimum eval corpus

Before live provider comparisons, create the fixed S16-EVAL corpus with exactly
8 labeled coverage cases for each critical behavior, split 4/2/2 across
Development, Calibration, and Final Held-Out Acceptance. S16-EVAL's accepted
critical-behavior matrix is the authority for the behavior list and counts.
Calibration may tune judge thresholds, while Final Held-Out Acceptance is
untouched during tuning and is the only set used to report that the fixed
evaluation bar was met. The corpus must include positive, negative, ambiguous,
incomplete-reference,
duplicate-finding, disagreement, partial-address, and cycle-limit cases.

Required assertions include:

- every seeded defect is found or explicitly marked unanswerable;
- findings cite real reference bytes rather than invented sources;
- duplicate findings merge without losing provenance;
- reviewer disagreement remains visible and is adjudicated explicitly;
- accepted feedback becomes an addressable revision requirement;
- closure verification catches a partially addressed finding;
- rejected feedback is preserved without being silently reapplied;
- missing context produces a useful blocking question;
- the cycle cap stops endless review/repair loops; and
- repeated Runs remain replay-comparable except for declared variable fields.

Mutation testing belongs in the gate. Removing a verifier rule or corrupting a
fixture must make the corresponding evaluation fail; a report that merely
exists is not evidence of coverage.

## 6. Provider, model, and handoff policy

G11 already provides the transport-neutral port and initial OpenAI API and
OpenRouter adapters. V15 uses that port rather than introducing provider-shaped
Goals.

The initial provider path is intentionally narrow:

- OpenAI API and OpenRouter API are the first external adapters;
- local deterministic fixtures remain the offline baseline;
- Codex CLI, Claude CLI, Anthropic API, and additional local models remain
  investigation targets until the G18 prerequisite spikes establish separate
  adapter evidence;
- model-to-model handoff is a declared Goal edge with explicit input/output
  artifacts, not an implicit provider fallback;
- automatic fallback, background network activity, and unbounded retries remain
  forbidden until separately contracted and tested;
- a user chooses which references may leave the machine, and redaction is
  performed before provider invocation.

The same Gig may eventually compare independent reviewers from different
providers, but agreement between models is not proof. Deterministic evidence
and human adjudication remain authoritative where ground truth is available.

## 7. Proposal-time capability and tool safety

Gigs will often need tools: a PDF extractor, a spreadsheet reader, a finance
data client, a web-search adapter, a repository analyzer, or a package already
installed on the machine. A proposal should be able to say:

- what the tool does and which Goal requests it;
- whether it is already available;
- what package, executable, credential, network, filesystem, or OS capability
  it requires;
- what alternatives exist;
- what data could leave the machine;
- what installation or activation would change; and
- which security checks must pass before use.

Those requirements belong in the review bundle's capability/tool manifest even
when the implementation is missing. This lets the user review alternatives and
security implications as part of the proposal instead of discovering an
undeclared dependency during a Run. The manifest is not a promise that the
tool exists, and it is not permission to execute it.

The proposal may present options such as “use the installed package,” “install
from this pinned source,” “use a local-only alternative,” or “continue without
this capability.” The user chooses. The proposal does not run `brew install`,
`pip install`, a provider call, or the tool itself merely to discover whether
the plan is plausible.

Installation is a separate, auditable operation with its own before/after
manifest, package/source identity, permission review, and rollback or refusal
behavior. A tool installed for one Gig must not silently become a global
default for another Gig.

## 8. Scheduling and recurring Gigs

Recurring work is a product requirement, but it is not the first execution
problem. A daily market-state Gig, weekly screener, monthly spreadsheet
analysis, or periodic resume watch should be modeled as a new Run over a new
reference bundle, not as mutable state hiding inside one Run.

The initial scheduler boundary is intentionally simple:

1. an external trigger starts an explicit GigAI Run;
2. the Run records the schedule occurrence and exact reference snapshot;
3. the same review/evaluation contract executes with a new Run ID; and
4. prior outputs are references for comparison, never silent authority.

An in-process daemon, cron installer, calendar integration, missed-occurrence
policy, retry policy, and background provider activity require later contracts.
The first recurring examples should be manually triggerable before any daemon
is proposed.

## 9. Roadmap phases and candidate goal graph

The following phases refine the unfinished V14 work without changing the
completed V14 contracts. Goal documents will be created only after each
candidate has a settled outcome, scope, acceptance evidence, and stop boundary.
The graph also names prerequisite research spikes explicitly; an `S18-*` node
is not an implementation Goal and does not advertise a supported adapter.

V14 Phase 2, deliberative `create`, remains a required product capability. V15
does not skip it or replace it with a Run. The review contract, reference bundle,
capability options, clarification questions, answers, and approval decisions
first appear as explicit proposal artifacts; the early Review Loop fixtures may
use hand-authored approved versions while those creation surfaces are being
materialized. G15 and G17 provide the substrate, S22-01 defines the interaction
protocol, and G22 owns the eventual user-facing creation implementation.

```text
G13 -> G14 -> G15 -> G16
           G15 -> G17
           G15 + G16 -> S16-EVAL
           G15 + G16 + G17 -> S22-01
           G16 + G17 -> S18-01
           G16 + G17 -> S18-02
           G16 + G17 -> S18-03
           G16 + G17 -> S18-04
           G16 + G17 -> S18-05
           S16-EVAL + S18-01 + S18-02 + S18-03 + S18-04 + S18-05 + S22-01 -> G18
           S22-01 + G18 -> G22
           G16 + S16-EVAL + G18 + G22 -> G19 -> G20 -> G21
           G17 + G19 + G20 + G22 -> G23
           G22 + G18 + S18-02 -> G26 + G27-contract
             -> S27-EVAL/S27-ROLE/S27-CREATE -> G28 -> G27 -> G24 -> G25
           G21 + G23 + G22 + G26 -> G24
           G12 + G21 + G23 + G24 + G26 -> G25
```

The graph is intentionally provisional. The arrows express planning
dependencies, not live status fields or authorization to begin.

| Node | Planning outcome | Depends on |
|---|---|---|
| G14 | Sequential Goal Graph scheduler | G13 |
| G15 | Reference bundles and evaluator substrate | G14 |
| G16 | First Review Loop Gig | G15 |
| G17 | Proposal-time capability inspection and installation review | G15 |
| S16-EVAL | Review Loop evaluation methodology spike | G15, G16 |
| S22-01 | Local HTMX proposal interview and clarification protocol spike | G15, G16, G17 |
| S18-01 | Common provider-port and evidence contract spike | G16, G17 |
| S18-02 | Codex CLI and Claude CLI adapter feasibility spike | G16, G17 |
| S18-03 | Anthropic API and local-model adapter feasibility spike | G16, G17 |
| S18-04 | Handoff, comparison, cancellation, and unavailable-provider spike | G16, G17 |
| S18-05 | Provider-input redaction, credential, and network-boundary spike | G16, G17 |
| G18 | Provider comparison and model handoff implementation | S16-EVAL, S18-01, S18-02, S18-03, S18-04, S18-05, S22-01 |
| G19 | Approved target mutation | G16, S16-EVAL, G18, G22 |
| G20 | Local `improve` and evaluator learning | G16, S16-EVAL, G18, G19, G22 |
| [G21](../development/goals/phase-5/G21-recurring-and-comparative-gigs.md) | Recurring and comparative Gigs | G13, G14, G20 |
| G22 | Deliberative `create` and user-facing proposal interview | S22-01, G18 |
| G23 | Gig self-containment and portability (capability-manifest reference and proposal-lineage resolution on the active Gig version) | G17, G19, G20, G22 |
| G24 | Local UAT and dogfooding across isolated GigAI installations | G18, G19, G20, G21, G22, G23, G26 |
| G26 | Model-facilitated Gig builder, adaptive clarification, and bounded proposal research | G18, G22, S18-02, G24 findings |
| G28 | v0.1.5 evaluation, role-registry, and browser-first create readiness | G26, G27 contract, S27-EVAL, S27-ROLE, S27-CREATE |
| G27 | Adaptive Gig discovery, bounded pre-proposal research, and model-selected direction questions | G20, G22, G26, G28, G24 findings |
| G25 | Alpha release readiness and final repository cleanup | G12, G21, G23, G24, G26 |

S16-EVAL is the hard review-loop quality gate for G18 and G19. G22 may cite
its evidence only when G22 actually invokes or evaluates the Review Loop;
S22-01 remains the authority for proposal-question quality evaluation. G23 is
independent of G21: neither depends on the other. G24 is a local UAT and
dogfooding gate, not a release declaration; it produces local-only evidence
about interaction quality, artifact shape, installation, and operator
workflows. G26 must land before G24's final UAT pass because the current create
flow does not yet perform a real model-backed proposal build. G27 makes that
builder a genuine pre-proposal discovery canvas with a five-question ceiling
and bounded research plan. G25 owns the later release-lane alpha decision.

### Phase 2 — Proposal creation and interaction

#### S22-01 — Local HTMX proposal interview and clarification protocol spike

Design, without adding runtime behavior, the interaction that turns a user's
free-form request and selected local references into an approved Gig proposal.
The spike must compare the smallest viable embedded localhost HTTP approach,
define the HTMX request/fragment protocol, and specify how a short-lived
browser session persists drafts, questions, answers, reference selections,
capability decisions, revisions, and approval in SQLite and the Gig workpad.

The question protocol must support model-generated, domain-specific questions
without requiring one fixed checklist. It must define structured question IDs,
answer types, dependencies, priorities, rationale, provenance, bounded rounds,
conditional follow-ups, and the blocking behavior for unanswered ambiguity.
The spike must also define the evaluation substrate for question quality:
coverage of material ambiguity, non-redundancy, useful domain adaptation,
privacy/tool awareness, stopping behavior, and proposal completeness. It must
prove that the browser process is loopback-only, that local files are copied or
referenced by exact bytes into the draft Bundle, and that no proposal question
session silently executes a tool, contacts a provider, or mutates a target.

Exit evidence: a checked-in decision record, a minimal disposable HTMX fixture,
the proposal/question/answer state machine, a JSON protocol example, a draft
SQLite/workpad persistence trace, and an evaluation corpus spanning at least a
repository feature, resume tailoring, reference synchronization, and one
tabular or finance Gig. S22-01 does not ship `create`, provider adapters, or a
public multi-user web service.

#### G22 — Deliberative `create` and user-facing proposal interview

Implement the approved S22-01 interaction: launch a short-lived local HTMX
session from `gigai create`, collect the user's description and references,
allow the selected model to ask bounded structured questions, persist answers
and revisions, present capability/privacy/effect choices, and seal an approved
proposal. The session remains local-first and uses SQLite/workpad authority;
remote hosting, background service behavior, and unapproved execution remain
out of scope.

G22 is complete. Its shipped path is deterministic/offline by default, binds
only to loopback with a short-lived token, requires explicit reference selection and
operator approval, and hands G19 a sealed proposal without target mutation or
Run authority. The [completion audit](../development/evidence/phase-2/G22/completion-audit.md)
and [terminal handoff](../development/evidence/phase-2/G22/terminal-handoff.md)
are the authoritative closeout records. G19 is the next authorized consumer;
it must define and separately authorize target effects rather than treating
G22's `write_workpad` choice as permission to mutate a target.

### Phase 3A — Sequential execution spine

#### G14 — Sequential Goal Graph scheduler

Consume G13's sealed Run and execute exactly one dependency path. Resolve
pending, ready, active, and complete Goal states; honor typed automatic
transitions and exact joins; persist `goal_started` and `goal_completed`; keep
the approved graph immutable. No parallelism, gates, recovery, providers,
target writes, or scheduling yet.

Exit evidence: a multi-node sequential fixture, an invalid dependency/automatic
transition fixture, ordered journal handoffs, replayable terminal RunDetails,
and proof that the scheduler cannot mutate the approved graph.

### Phase 3B — Review and evaluation foundation

#### G15 — Reference bundles and evaluator substrate

Define the first contract for content-addressed reference bundles, review
contracts, findings, evaluator versions, traces, feedback decisions, and
machine/human reports. Add deterministic verifier interfaces, case fixtures,
redaction boundaries, and mutation-tested coverage. Keep the artifacts
domain-neutral and provider-neutral.

Exit evidence: seeded cases across articles, repositories, and tabular data;
deterministic findings with real citations; duplicate/disagreement/adjudication
proof; and replayable reports from local bytes.

#### G16 — First Review Loop Gig

Implement the review → verify → feedback → address → closure → terminal
decision loop using G15's contracts and G14's sequential scheduler. Start with
the research-pair, climate-pair, pull-request, repository-review, and
spreadsheet-review fixtures. The first output may include both a review report
and an addressed artifact, but their parentage and acceptance decision must be
explicit.

Exit evidence: each seeded challenge is found or marked unanswerable, accepted
findings are individually closed, partial address fails, cycle limits stop the
loop, and the complete evidence bundle is inspectable offline.

### Phase 3C — Capability proposals, provider design, and safe tools

#### G17 — Proposal-time capability inspection and installation review

Let a proposal enumerate available, missing, installable, incompatible, and
credential-dependent capabilities with explicit user options. Add a separate
approved installation path with package/source pinning, permission review,
before/after manifests, and refusal/rollback evidence. Proposal construction
still cannot execute the capability or make an unapproved network request.

Exit evidence: installed, unavailable, incompatible, credential-missing, and
security-rejected fixtures; no proposal-time side effect; and a per-Gig tool
provenance record.

#### G18 prerequisite spike tranche

These are explicit research and contract-design spikes, not implementation
Goals and not compatibility claims. They may use disposable provider fixtures,
fake CLIs, and local recordings, but must not add runtime provider behavior to
GigAI. Each spike produces a checked-in decision record, a minimal executable
probe or fixture where useful, and a recommendation that G18 can adopt or
reject. S22-01 is part of the prerequisite set for G18 while retaining
proposal-question quality as its separate authority. G18 cannot begin until
all six records have an accepted outcome and the tranche terminal handoff is
accepted.

##### S18-01 — Common provider-port and evidence contract

Map the existing transport-neutral model port onto OpenAI API, OpenRouter API,
Codex CLI, Claude CLI, Anthropic API, and a representative local-model
runtime. Define the smallest common request/response, model identity,
streaming, finish state, error, cancellation, usage, and cost-status shape.
Identify provider-specific fields that must remain in a typed extension rather
than being discarded. Decide which artifacts a provider Goal must seal for
replay and which values are intentionally variable.

##### S18-02 — Codex CLI and Claude CLI adapter feasibility

Probe process discovery, argument construction, stdin/stdout/stderr capture,
structured-output support, exit-code mapping, timeout and cancellation
behavior, working-directory isolation, and credential inheritance. Test both
available and missing executables with fake CLIs; no real user repository may
be mutated. Decide whether these adapters can share one process boundary and
whether each deserves a separate implementation Goal after G18.

##### S18-03 — Anthropic API and local-model adapter feasibility

Probe Anthropic content blocks, tool/model identifiers, streaming and usage
fields, rate/error semantics, and cancellation. Separately probe one local
model runtime for installation/discovery, offline operation, resource limits,
model identity, and reproducible request/response capture. Do not assume that
local models are interchangeable with hosted APIs; recommend a supported
minimum or explicitly defer local-model compatibility.

##### S18-04 — Handoff, comparison, cancellation, and unavailable-provider
spike

Design the explicit Goal-edge handoff between independent reviewers/solvers.
Define input and output artifact parentage, bounded handoff count, disagreement
preservation, adjudication inputs, cancellation, provider-unavailable failure,
and normalized usage/cost evidence. Prove that no automatic fallback,
background activity, or unbounded retry is hidden in the design. Recommend
whether comparison and handoff remain one G18 implementation or split later.

##### S18-05 — Provider-input redaction, credential, and network boundary
spike

Determine the pre-invocation boundary for reference selection, secret and PII
redaction, credential lookup, network permission, and audit evidence. Test that
unselected references and redaction failures never reach a provider, that
credentials are represented by references rather than persisted values, and
that provider calls are impossible in offline fixtures. Distinguish what can
be enforced deterministically from what requires an explicit user review or a
later privacy-specific Goal.

Spike tranche exit evidence: six decision records, fixture/probe results for
each named adapter family, the S22-01 proposal-interview protocol and corpus,
a common-port compatibility matrix, an explicit G18-versus-follow-up-goal
recommendation, and a documented list of rejected assumptions. No provider is
called by the proposal path, and no adapter is advertised as supported solely
because a spike succeeded.

#### G18 — Provider comparison and model handoff

After the prerequisite spikes, run independent reviewer or solver Goals through
the existing model port using the explicitly supported adapter set. Start with
OpenAI and OpenRouter; add Codex CLI, Claude CLI, Anthropic, or a local model
only when its spike outcome and implementation evidence support it. Model-to-
model handoff is explicit, bounded, redacted, and replayable. No automatic
provider fallback or hidden background activity.

Exit evidence: one review case with independent provider passes, preserved
disagreement, normalized usage/cost status, cancellation, redaction, and a
provider-unavailable failure that remains truthful.

### Phase 4 — Controlled effects

#### G19 — Approved target mutation

Extend the Review Loop and other Gigs from workpad-only outputs to explicitly
approved target effects. Record target before/after manifests, patch identity,
user-owned commit policy, dirty-target refusal, cancellation, partial-write,
and recovery behavior. No target mutation is implied by a proposal or by a
successful read-only review.

Status: complete. Exit evidence is recorded in the [G19 completion audit](../development/evidence/phase-4/G19/completion-audit.md)
and [terminal handoff](../development/evidence/phase-4/G19/terminal-handoff.md):
one disposable document-edit Gig with a reviewed patch, exact target delta,
refusal paths, mutation evidence, installed replay, and recoverable failure.

### Phase 5 — Local improvement and recurrence

#### G20 — Local `improve` and evaluator learning

Use completed evidence, findings, feedback, and accepted outcomes to propose
changes to a Gig's review contract, rubric, or verifier. Improvement never edits
approved history automatically and never learns outside the derived local
learning root (`home_root / "learning"`). Recovery-policy and bounded-
parallelism proposals remain deferred until a later contract establishes their
evidence and authority boundaries. Accepted changes create a new proposal and
version through the existing `gig-proposal` and active-version lifecycle.

Status: complete. Exit evidence is recorded in the [G20 completion audit](../development/evidence/phase-5/G20/completion-audit.md)
and [terminal handoff](../development/evidence/phase-5/G20/terminal-handoff.md):
local provenance-tagged learning records, independently enforced evidence and
quality gates, authoring recovery, explicit G22 improve approval, stale-base
refusal, mutation evidence, and fresh-wheel replay.

#### G21 — Recurring and comparative Gigs

Add explicit schedule occurrences, repeatable reference snapshots, prior-output
comparison, and operator-visible missed-run behavior. Start with manually
triggered daily/weekly/monthly examples before adding a daemon or OS scheduler.

Status: complete. Exit evidence is recorded in the [G21 completion audit](../development/evidence/phase-5/G21/completion-audit.md)
and [terminal handoff](../development/evidence/phase-5/G21/terminal-handoff.md):
a daily market-state, weekly screener, and monthly spreadsheet fixture produce
separate Runs, preserve prior evidence, compare sealed outputs without selecting
a winner, and fail closed when snapshot evidence is unavailable.

The reviewed G21 contract is [here](../development/goals/phase-5/G21-recurring-and-comparative-gigs.md)
and its accepted additive amendment is [here](../development/evidence/phase-5/G21/occurrence-comparison-contract-amendment.md).
G21 is complete but does not authorize a daemon, OS scheduler, automatic retry,
or background provider activity.

#### G23 — Gig self-containment and portability

Add a `capability_manifest` reference to `active-gig-version.json` and a
read-only proposal-lineage resolver, so an approved Gig version can name its
own declared capability manifest and its full `create`/`improve`/`amend`
proposal chain without following the Review Bundle's `tool_requirements` or
walking `parent_proposal_id` by hand. Portability means the pinned manifest
and, where the accepted source-transport shape requires it, its pinned
source artifact travel to a second machine for local reinstallation through
G17's existing installer; installed tool bytes are never copied between
machines, and `resolved_tools` Run-time authority is unchanged.

G23 is complete after post-closeout repair under its accepted additive amendment
and does not declare an alpha or public release. It is independent of G21
and does not gate or unblock recurring Gigs.

Exit evidence: the accepted amendment settling field requiredness,
version-binding, lineage resolution, and source-transport shape; portability
and lineage fixtures including a multi-hop chain, a cycle, and a
cross-Gig/unbound-manifest refusal; a two-disposable-home reinstallation
record; mutation evidence for the semantic-verification and lineage checks;
and fresh-wheel replay, including post-closeout repair verification. The [G23 completion audit](../development/evidence/phase-5/G23/completion-audit.md)
and [terminal handoff](../development/evidence/phase-5/G23/terminal-handoff.md)
record the accepted result. See the [G23 goal contract](../development/goals/phase-5/G23-gig-self-containment-and-portability.md).
The accepted contract amendment is [here](../development/evidence/phase-5/G23/gig-self-containment-and-portability-contract-amendment.md).

#### G24 — Local UAT and dogfooding

Run GigAI as an operator would use it: install the current package in an
isolated container, enter the container, create real test Gigs step by step,
inspect the resulting proposal/workpad/journal artifacts, and record where the
interaction or data model is confusing. UAT cases may cover review-and-verify,
repository review, resume tailoring, research synthesis, and other bounded
operator workflows.

G24's evidence is deliberately local and disposable. It must not commit user
prompts, source material, credentials, model outputs, home directories,
container state, or raw transcripts to GitHub. Each session records its package
version, container image, command sequence, selected model configuration,
operator decisions, observed artifact paths, and sanitized findings in a
local UAT directory outside the repository. The default first pass should use
the deterministic/offline path; external model targets are opt-in and must be
named in the local record rather than treated as shipped provider support. After
G26, the final UAT pass must use a configured, evidenced builder target; the
deterministic/offline path remains available for fixture testing but must be
labeled as such.

G24 does not change schemas, add runtime authority, publish a package, or
declare alpha readiness. It is the dogfooding gate that tells G25 what is
actually understandable and usable before release work begins.

#### G26 — Model-facilitated Gig builder and proposal research

G26 replaces the current shallow `create` shortcut with a distinct
model-backed build phase. GigAI facilitates the local browser session and owns
question validation, persistence, model/provider boundaries, budgets,
progress, recovery, and approval. The operator-selected model asks the
domain-specific follow-up questions and performs bounded research/synthesis.

Installed `codex` or `claude` executables may be discovered as candidates, but
discovery is not support. G26 must distinguish detected, configured, verified,
and usable targets and must refuse to proceed with a real build when no usable
model is configured. It must present a reviewable draft before the operator can
approve the existing `gig-proposal` lifecycle. G26 does not grant target
mutation or Run authority and does not make a model the approval authority.

#### G27 — Adaptive Gig discovery and pre-proposal research

G27 turns the G26 loopback interview into the reusable Gig-definition canvas.
The selected model receives the initial intent, explicit references, and a
truthful capability inventory; it may propose bounded research and ask up to
five high-value direction questions that determine the Gig's Goals, outputs,
Run inputs, constraints, and success criteria. The browser displays the stable
Gig definition separately from changing Run inputs and keeps proposal,
approval, and version authority in the existing lifecycle.

G27 applies the same discovery shape to G20 improvement by supplying bounded,
provenance-tagged Run summaries and cited evidence. It does not grant arbitrary
web access, target authority, provider support, or a second improvement gate.
G27 must land before G24's final UAT pass and does not declare an alpha release.

### S27-EVAL, S27-ROLE, and S27-CREATE — v0.1.5 prerequisite spikes

These spikes define the evaluation taxonomy/behavioral-eval framework, the
namespaced role registry, and the browser-first setup/create flow. They are
research and contract-design records, not runtime Goals. G28 implements their
accepted decisions and produces the v0.1.5 candidate before G24 human UAT.

### G28 — v0.1.5 Product Readiness Foundation

G28 implements the accepted S27 prerequisite decisions: separate unit,
integration, installed-E2E, and behavioral-eval tiers; a central namespaced
role registry; and a truthful setup/model-selection path where
`gigai create <gig-name>` opens the HTMX flow after normal setup. G28 is a
release-candidate goal, not an alpha declaration. G27 runtime work follows G28
because adaptive discovery must be evaluated on a usable product foundation.

Before that UAT pass, the v0.1.5 product-readiness gate must close the
evaluation taxonomy/behavioral-eval debt, the central namespaced role registry,
and the browser-first setup/create path. G24 should evaluate the resulting
candidate, not the current implementation-plumbing release.

#### G25 — Alpha release readiness and final repository cleanup

Prepare a true alpha candidate after G24 and its G21/G23 prerequisites are
accepted, while reusing
G12's release-lane mechanics. G25 owns the final support-surface freeze,
roadmap/README/internal-changelog reconciliation, release-candidate artifact
checks, exact-tag CI, fresh-install proofs, and release evidence needed to
decide whether GigAI is ready for an alpha declaration.

G25 does not infer release authority from product-goal completion, does not
publish merely because its prerequisites are complete, and does not replace
G12's exact-tag, TestPyPI, Trusted Publisher, provenance, or clean-machine
requirements. The alpha version, publication action, and final release
approval remain explicit decisions in the release lane.

## 10. Example Gigs after the foundation

These are examples for dogfooding and evaluation, not yet product templates:

- Review and verify feedback loop over research articles or a pull request;
- tailor a resume to a job URL snapshot while preserving supported claims;
- fundamental analysis over filings and market data with explicit freshness;
- stock screener with a declared data source and reproducible filter inputs;
- daily market-state report with prior-state comparison;
- monthly sales-sheet analysis over a new CSV snapshot and the prior result;
- technical spike or repository review with evidence-backed closure.

Each example must be expressed as a Gig contract plus a domain profile and
reference bundle. None receives privileged behavior merely because it is a
named example.

## 11. Gates before each expansion

No later phase begins merely because the preceding code exists. The gate must
show:

- a completed goal audit and terminal handoff for every dependency;
- installed-artifact and supported-platform verification where applicable;
- deterministic fixture coverage for every semantic rejection class;
- exact reference, evaluator, provider, tool, and target evidence;
- failure, interruption, cancellation, and unavailable-capability behavior;
- no unauthorized network, credential, subprocess, target, or schedule effect;
- a current README/cheat sheet that does not advertise unimplemented commands;
- a decision record for every schema, transition, authority, or privacy change.

The Review Loop gate additionally requires mutation-tested evaluator coverage,
visible reviewer disagreement, explicit adjudication, individual closure of
accepted findings, and a bounded cycle count.

## 12. Decisions required before goal documents

The goal authors must settle these decisions explicitly rather than infer them
during implementation:

1. Is the first Review Loop output a report, an addressed artifact, or both?
2. Which finding states and feedback decisions are required in the first
   contract?
3. Does a clarification pause the Run immediately or collect until a review
   stage completes?
4. Is the first cycle cap one address pass, two, or user-selected within a
   declared maximum?
5. Which references may leave the machine, and what redaction is mandatory?
6. Which tool installation sources and package provenance are acceptable?
7. Are recurring triggers external until G21, or is an earlier narrow trigger
   contract justified?
8. Which fields are intentionally variable between replayed Runs, especially
   timestamps, usage, cost, and provider responses?
9. Which provider families graduate from the S18 spikes into G18, and which
   require separate adapter Goals with their own contract and evidence?

## 13. Stop boundary

Stop and amend the contract if implementation requires a missing artifact,
transition, schema field, authority rule, tool effect, provider behavior,
feedback state, or schedule policy. Do not solve an undefined boundary by
adding a command stub, silently choosing a provider, executing tools during
proposal construction, mutating a target, or introducing a daemon.

Until the G15/G16 contracts exist, the Review Loop remains a research-backed
roadmap and fixture design, not a promised CLI feature. Until G17 exists,
proposals may describe capability options but may not install or execute them.
Until G19 exists, successful Gigs remain workpad-only. Until G21 exists,
recurring examples are explicit manual Runs, not background jobs.

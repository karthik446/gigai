# G27 — Adaptive Gig Discovery and Pre-Proposal Research

- Status: Activated; contract amendment accepted; runtime implementation underway
- Type: Goal-builder and pre-proposal interaction implementation goal
- Depends on: G20 improvement evidence, G22 proposal interview, G26 model-
  facilitated builder, and G28 v0.1.5 readiness
- Incorporates: relevant G24 UAT findings when available; these are feedback
  inputs, not a prerequisite for G27 runtime activation
- Unblocks: G29 final human UAT and G25 alpha-readiness review

## Outcome

G27 turns the browser interview into the actual Gig-definition canvas. The
operator gives GigAI an initial intent and optional references. GigAI explains
the capabilities available in the current configuration, performs bounded
pre-proposal discovery/research when appropriate, and asks no more than five
high-value questions that determine the direction of the Gig.

The questions are selected by the configured model, not hardcoded by domain.
Each question is rendered through the existing typed HTMX interview and
persisted through the existing session/event lifecycle. The operator reviews a
proposal containing reusable Goals, changing Run inputs, expected outputs,
research boundaries, assumptions, and unresolved decisions before approval.

G27 also defines the corresponding `improve` path. An improvement session uses
bounded summaries of local Runs and cited evidence as additional context,
selects up to five questions about the proposed change, and sends the resulting
proposal through G20's existing evidence-sufficiency and improvement-quality
gates. G27 never lets a model or browser session become proposal, version,
approval, Run, or target-effect authority.

## Product model

The operator must be able to distinguish these layers in the browser:

```text
Gig definition
  -> reusable Goals, constraints, outputs, and question policy

Gig version
  -> the approved definition currently in force

Run input
  -> the small context that changes for one execution

Adaptive questions
  -> model-selected questions needed to define or run this Gig

Run evidence and outputs
  -> research, decisions, artifacts, unresolved questions, and review results
```

Creation establishes the first layer. A later Run supplies changing context.
An improvement proposes a new version while keeping the original definition,
original references, and prior evidence visible for comparison.

## Contract gate

Before runtime changes, an accepted contract-impact record must decide whether
the existing G26 `gig-builder-session.schema.json` and
`proposal-draft-manifest.schema.json` can represent the following without
changing their meanings by inference:

1. capability disclosures and their status (`detected`, `configured`,
   `verified`, or `usable`);
2. a bounded pre-proposal research plan and its explicit network/privacy
   boundary;
3. a model-selected question set with typed answers, dependencies, rationale,
   provenance, and a maximum of five direction questions;
4. the stable Gig definition versus changing Run-input distinction; and
5. bounded Run summaries and evidence citations supplied to G20 improve.

If an additive amendment is required, it must preserve every existing schema
resource byte and hash, keep `gig-proposal` as the sole proposal/version/
approval authority, and include installed-schema verification and canonical
vectors before implementation continues.

G27 contract-impact analysis may begin against the current G20, G22, and G26
artifacts. G27 runtime implementation may begin against G26's accepted
builder contract and G28's accepted v0.1.5 readiness evidence. The human UAT
gate is deliberately downstream: G24 records exploratory 0.1.4 findings, and
G29 will perform full post-0.1.5 acceptance. G27 must not absorb unfinished
G26 or G28 runtime work by inference.

The amendment must explicitly refuse any design in which:

- a model claims a provider, web, credential, or target capability merely
  because it suggested one;
- a research plan silently enables network access;
- a question answer mutates the target or creates a Run;
- browser state, `st.session_state`, or an in-memory model result becomes
  durable authority; or
- an improvement bypasses G20's evidence and quality gates.

## Capability disclosure and research boundary

The builder receives a truthful capability inventory derived from the actual
installed configuration. It may explain capabilities such as local reference
reading, configured model invocation, bounded research, proposal construction,
and later approved Run execution, but it must label each capability according
to its actual status.

The model may recommend research areas or external sources. GigAI must show:

- what source or capability would be used;
- whether it is local or external;
- what privacy/network boundary applies;
- what budget and time limit applies; and
- what evidence will be retained.

No network call, credential lookup, provider invocation, or target effect may
occur merely because a model placed it in a question or research plan. The
operator must explicitly approve the relevant boundary through the existing
contracted path.

## Pre-proposal question contract

The builder must ask a maximum of five direction questions per discovery round.
Five is a ceiling, not a requirement: the model should ask fewer when the
available context is sufficient. The questions should determine the highest-
leverage parts of the Gig, such as:

- the main outcome and intended user;
- the reusable Goals or stages;
- required outputs and success criteria;
- changing Run inputs versus stable references;
- research sources and privacy boundaries;
- factual or domain constraints;
- handling of missing, ambiguous, or inaccessible information; and
- what the Gig is explicitly not allowed to do.

The model chooses the question content and answer shape. GigAI validates and
renders only supported typed forms: free text, choice, multi-select,
confirmation, and exact reference selection. Every question must carry a
stable ID, answer type, dependency information, rationale, and provenance.

The builder must not repeatedly ask for facts already approved in the Gig
definition unless the operator is explicitly revising that definition.

## State and authority contract

The following invariants are normative for every G27 creation and improvement
session:

1. **Durable session state is authoritative.** The existing builder-session
   record and ordered event trace own discovery state, questions, answers,
   research progress, revisions, and recovery. Browser state, HTMX fragments,
   model memory, and any UI session cache are disposable projections.
2. **Model output is advisory.** A model may propose capabilities, research
   sources, question shapes, Goals, and wording. It cannot grant a capability,
   enable network access, resolve an approval, create a Run, mutate a target,
   or advance a Gig version.
3. **Capability status is truthful and closed.** `detected`, `configured`,
   `verified`, and `usable` are distinct statuses. A lower status cannot be
   rendered or recorded as a higher status merely because the model requested
   it.
4. **The question ceiling is authoritative.** A discovery round contains zero
   to five accepted direction questions. The model, browser, or retry path
   cannot create a sixth question by replaying or bypassing the typed event
   path.
5. **Stable definition and Run input remain separate.** Approved Gig Goals,
   constraints, and stable references belong to the Gig version. Job URLs,
   current files, operator answers, and other changing context belong to a Run
   or discovery session and cannot silently rewrite the approved definition.
6. **Research requires an explicit boundary.** A research suggestion is not a
   network permission. Every external source, provider call, credential lookup,
   and effect must pass the existing configured policy and budget checks before
   execution.
7. **Existing lifecycle authority is preserved.** `gig-proposal` remains the
   sole proposal identity, version, and approval authority. Approval is
   explicit and idempotent; G27 cannot create a parallel proposal or version
   ledger.
8. **Improve context is filtered and provenance-bearing.** Only bounded,
   selected Run summaries and cited evidence enter an improve proposal. G20's
   evidence-sufficiency and improvement-quality gates remain mandatory even
   when the model presents a confident recommendation.
9. **Recovery is fail-closed.** Interruption, stale browser events, malformed
   model output, or a repeated request may reopen or terminalize a session, but
   cannot duplicate a proposal, silently complete research, or produce partial
   approval.
10. **The UI is an adapter.** HTMX renders and submits typed protocol events;
    it does not own lifecycle transitions, authority, or durable evidence.

## Improve context contract

When the operator improves an existing Gig, the model may receive only a
bounded context package containing:

- the active Gig definition and version identity;
- operator-selected Run summaries;
- cited findings, feedback, adjudications, reports, or target-effect outcomes;
- the relevant active-version pointer snapshot; and
- explicit operator improvement intent.

The context package must preserve provenance and citations. Raw prompts,
credentials, hidden model context, unselected references, and unbounded Run
databases must not be copied into the improvement prompt. The resulting change
must remain limited to G20's review-contract, rubric, and verifier scope.

## First implementation boundary

The first implementation should extend the existing local HTMX session:

1. show the initial Gig intent and selected references;
2. show an accurate capability summary;
3. run one bounded discovery/research planning step;
4. render up to five model-selected questions;
5. persist every question and answer through the existing event trace;
6. display the stable definition, Run inputs, proposed Goals, research plan,
   assumptions, and unresolved decisions;
7. build a reviewable proposal only after discovery completes; and
8. reuse the existing revise, reject, approve, recovery, and G20 improve paths.

No second UI state machine, proposal authority, or version ledger is allowed.

## In scope

- model-authored, bounded pre-proposal questions;
- capability disclosure tied to actual configuration;
- bounded proposal research planning and evidence citations;
- stable-definition versus Run-input presentation;
- generic HTMX rendering for typed question shapes;
- G20 improve context summaries and cited evidence;
- interrupted discovery/research recovery;
- mutation tests for the five-question ceiling, capability truthfulness,
  network boundary, context filtering, and duplicate approval; and
- fresh-wheel installed replay; real human UAT is owned by downstream G29.

## Out of scope

- arbitrary autonomous web browsing;
- treating detected Codex/Claude executables as supported adapters;
- background or scheduled research;
- model-selected authority to run, write, publish, or mutate a target;
- replacing G20's improvement gates;
- raw transcript or full-Run replay into model context;
- a second Streamlit or frontend-specific authority layer; and
- alpha or public-release declaration, which remains owned by G25.

## Acceptance criteria

1. An accepted contract-impact record proves whether the existing G26 schemas
   are sufficient; any required amendment is additive, hashed, and installed-
   replayed.
2. The capability summary reports detected/configured/verified/usable status
   accurately and never advertises an unavailable provider or network path.
3. A discovery session accepts an initial Gig intent and optional exact local
   references without requiring domain-specific CLI flags.
4. The configured model selects zero to five direction questions; no code path
   can persist or render a sixth question in the same discovery round.
5. Questions are typed, dependency-aware, reasoned, provenance-tagged, and
   rendered by generic HTMX components rather than a domain-specific form.
6. The discovery path can present a bounded research plan with explicit
   source, network, privacy, budget, and evidence boundaries before execution.
7. The proposal clearly separates the reusable Gig definition, approved Goals,
   stable references, changing Run inputs, research plan, assumptions, and
   unresolved decisions.
8. Original definition/reference values remain visible during update and
   improve flows, while new answers and Run inputs are visibly distinct.
9. An improve session consumes only bounded, provenance-tagged Run summaries
   and cited evidence, then passes the resulting proposal through both G20
   gates without a parallel approval/version path.
10. Removing each of the five-question, capability-truthfulness, network,
    context-filtering, and duplicate-approval guards causes a negative fixture
    to fail; the mutation report names each guard explicitly.
11. Interrupted discovery, research, review, revision, rejection, and browser
    reopen paths preserve durable events without duplicate proposals or partial
    approval.
12. A fresh wheel can replay the contract, installed builder flow, and offline
    deterministic discovery path without contacting a provider.
13. The downstream G29 UAT goal reruns at least `tailor-resume-for-job` and one
    structurally different Gig, recording whether the operator understands
    stable definition, Run input, adaptive questions, research boundaries, and
    approval consequences.
14. Completion audit and terminal handoff identify G29 as the next consumer and
    G25 as the release-lane consumer; no alpha claim is inferred from G27 alone.

## Verification and evidence

Evidence belongs under `docs/development/evidence/phase-5/G27/` and must
include the accepted contract-impact record, schema/vector results if needed,
question-boundary fixtures, capability and network-boundary fixtures, improve
context filtering, mutation report, recovery tests, installed replay, and
sanitized G29 observations when available.

Human UAT records remain outside the repository under the operator's local G29
directory. No resume, job posting, repository content, credential, prompt,
raw model output, or private database may be committed.

## Stop boundary

Stop and amend the contract before implementation if the existing schemas
cannot represent the discovery/research/question/evidence shape without
semantic overloading. Stop if a model target, research source, or network path
cannot be classified truthfully. Stop if the five-question ceiling is bypassed,
if stable Gig definition values are confused with Run inputs, if improve context
contains unselected or raw data, or if approval can be repeated or inferred
from browser/model state.

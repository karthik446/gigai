# G26 — Model-Facilitated Gig Builder and Proposal Research

- Status: Activated; amendment accepted; implementation evidence complete
- Type: Runtime implementation goal; model-backed proposal construction
- Depends on: G18 model execution and provider boundary, G22 proposal
  interview, and S18-02 CLI feasibility evidence
- Unblocks: G27 runtime and G29 post-0.1.5 UAT against the real Gig-builder
  flow and G25 alpha release readiness

## Outcome

G26 turns `gigai create` into a real Gig-builder workflow. GigAI owns the
session, safety boundary, persistence, budgets, progress, and approval
protocol. A model selected and configured by the operator owns domain-specific
questioning, research, synthesis, and proposal drafting within those bounds.

The operator does not approve a question prompt as if it were already a
proposal. The flow has a distinct build phase:

```text
define_intent -> clarify -> build_requested -> researching
  -> proposal_draft_ready -> operator_review -> revised | rejected | approved
```

The first question establishes the Gig's main drive in operator language:

> What decision, outcome, or recurring responsibility should this Gig drive?

The model asks follow-up questions after that answer. GigAI facilitates the
conversation and validates every question, answer, reference, model call, and
transition; it does not silently invent a Gig from one sentence.

## Terminology and authority

- A **Gig** is the user-approved enduring commission.
- The **Gig builder** is the bounded model-backed creation workflow.
- The **facilitator** is GigAI's deterministic session coordinator. It owns
  state, budgets, persistence, redaction/network policy, and failure handling.
- The **builder model** is the operator-selected model target used for
  clarification and proposal research. Its output is untrusted until
  validated and reviewed.
- A **proposal draft** is a reviewable candidate. It is not an approved Gig,
  active version, Run, target mutation, or capability installation.
- The **operator** remains the only authority that can approve a proposal.

The existing `gig-proposal` and active-version lifecycle remain the sole
proposal and version authorities. G26 must not create a second draft identity,
active-version pointer, approval ledger, or model-owned authority path.

## Contract gate

Before runtime implementation, an accepted G26 contract amendment must define
the serialized shape for:

1. builder-session state, including build progress, model target identity,
   bounded call accounting, interruption state, and terminal reasons;
2. model-discovery candidates and the distinction between detected,
   configured, verified, and usable targets;
3. typed model questions and answers, including the required main-drive
   answer, optional local-context references, clarification rounds, and
   provenance;
4. proposal-draft status and its relationship to the existing `gig-proposal`
   resource without allocating parallel proposal authority;
5. research citations, assumptions, unresolved questions, and model-output
   digests without storing provider credentials or treating free text as
   authority;
6. cancellation, timeout, malformed-output, unavailable-model, and partial
   research behavior; and
7. the approval transition that converts the reviewed draft into the existing
   proposal lifecycle exactly once.

The amendment must preserve existing schema bytes and hashes unless an
explicit additive contract decision says otherwise. Any new resources require
the installed-schema verifier, canonical vectors, and fresh-wheel replay.

## Model discovery and setup boundary

`gigai setup` must make model readiness understandable before `gigai create`
starts a real build:

- detect candidate local executables such as `codex` and `claude` using
  executable resolution, without invoking them during ordinary discovery;
- show the candidate path, identity when safely available, authentication
  status when the tool exposes a non-secret diagnostic, and whether GigAI has
  an accepted adapter for it;
- allow the operator to choose a configured builder target explicitly;
- support configured API targets through credential references, never raw key
  values;
- preserve an explicit deterministic/offline mode for contract tests and
  fixture UAT, but do not silently treat it as an equivalent production
  builder; and
- fail clearly before build when no usable model is configured.

Executable discovery alone must never advertise Codex or Claude support. A
target becomes usable only after its adapter, authentication boundary, output
contract, timeout/cancellation behavior, and installed replay evidence are
accepted. A detected executable may remain `detected` or `unverified` when
existing spike evidence does not establish real compatibility.

## First implementation boundary

The first implementation supports one bounded builder target end to end, with
the target family chosen by the accepted amendment. It must use the G18 model
port and provider boundary, not a private proposal shortcut.

The browser flow exposes these distinct operator actions:

1. **Define the Gig** — answer the main-drive question and optionally provide
   local context.
2. **Answer follow-ups** — respond to model-generated questions, with GigAI
   showing why each question was asked and what context it depends on.
3. **Build proposal** — explicitly authorize the bounded research/build phase.
4. **Review proposal** — inspect goals, references, assumptions, boundaries,
   unresolved questions, model identity, and build evidence.
5. **Revise, reject, or approve** — only approval enters the existing proposal
   and active-version lifecycle.

The build phase shows progress and bounded accounting. An interruption recovers
to a resumable or explicitly terminal state; it never silently claims that a
proposal was built.

## In scope

- Model candidate discovery for installed local CLIs and configured API
  targets, with explicit detected/configured/verified/usable states.
- Setup/readiness checks for the selected builder target, credential reference,
  network policy, output limit, reasoning setting, and build budget.
- A builder session that asks adaptive typed questions through the selected
  model while GigAI validates the protocol and records provenance.
- A separate explicit build request with bounded model calls, tokens, wall
  time, cost where available, and cancellation.
- Proposal research over operator-selected local references and model-returned
  citations that can be resolved and verified.
- Draft proposal generation, progress/recovery, operator review, revision,
  rejection, and one-time approval through the existing lifecycle.
- No-model, unavailable-model, malformed-output, timeout, cancellation,
  stale-session, and approval tests.

## Out of scope

- Automatic Gig approval, active-version advancement, target mutation, target
  commits, capability installation, or Run execution during building.
- Treating a detected `codex` or `claude` executable as supported without an
  accepted adapter and compatibility evidence.
- Silent provider fallback, retries across a different model, or model-owned
  budget expansion.
- Storing API keys, credential values, browser cookies, or raw secret output.
- Background/scheduled builder sessions or a daemon.
- Arbitrary web research, public listeners, or network access outside the
  configured G18 provider boundary.
- Replacing S16-EVAL's Review Loop quality bar with proposal-completeness
  assertions. Proposal-question quality remains S22-01's concern; review
  quality remains S16-EVAL's concern.

## State and authority contract

1. `define_intent` requires a non-empty operator answer for the Gig's main
   drive. It contains no raw credential value or NUL byte.
2. `clarify` may add typed model questions, but every question has an ID,
   rationale, dependencies, requiredness, answer type, and provenance.
3. `build_requested` is an explicit operator action. A model response or a
   complete-looking answer set cannot trigger it implicitly.
4. `researching` is non-authoritative work. Every provider call uses the G18
   model port, selected-target policy, redaction/network boundary, and budget.
5. `proposal_draft_ready` is not approval. It identifies model target,
   research status, unresolved questions, citations, assumptions, and draft
   digest before review.
6. `operator_review` may produce a revision or rejection. A revised draft gets
   a new revision identity and retains its parent; it cannot overwrite history.
7. Only explicit operator approval invokes the existing proposal approval
   lifecycle. Approval is idempotent and advances the active version at most
   once.
8. Malformed, unsupported, uncited where required, over-budget, stale, or
   out-of-bound model output fails closed.
9. No model output can grant a capability, authorize a target effect, select a
   provider fallback, or change an active version.
10. Model selection is recorded as a resolved target identity and adapter
    decision, never inferred later from a display label such as “Codex” or
    “Claude.”

## Acceptance criteria

1. An accepted G26 contract amendment exists and schema/resource changes are
   additive, hashed, validated, and installed-replay verified.
2. Setup discovers candidate `codex`/`claude` executables when present without
   invoking them, reports status clearly, and does not claim support from
   discovery alone.
3. Setup configures one usable builder target with a credential reference,
   explicit network policy, output/budget limits, and no credential value in
   configuration or records.
4. `gigai create` opens the browser-first flow and requires the operator's
   main-drive answer before clarification or building can complete.
5. The selected model, not GigAI's facilitator, supplies domain-specific
   follow-up questions; GigAI validates, persists, bounds, and presents them.
6. A build cannot begin merely because questions are answered. An explicit
   **Build proposal** action produces a durable build-start event.
7. A bounded model build produces a proposal draft with model identity,
   progress/accounting, citations, assumptions, unresolved questions, and a
   content digest; fixture output is clearly labeled as deterministic.
8. The operator can review, revise, reject, or approve the draft. Approval
   creates/seals the existing `gig-proposal` exactly once; revision and
   rejection do not create an active Gig or Run.
9. No-model, unavailable-model, malformed response, timeout, cancellation,
   stale browser event, and budget exhaustion each produce distinct
   fail-closed results with recoverable or terminal guidance.
10. A model cannot see unselected references, credential values, hidden prior
    context, or data outside the configured provider boundary; mutation tests
    prove each guard is load-bearing.
11. Interrupted build sessions recover without duplicate provider calls,
    duplicate proposal identity, partial approval, or an invented success.
12. A fresh-wheel installed replay completes the selected builder path and
    proves the installed package owns the behavior.
13. G24 is re-run against the new flow with a real Gig definition, adaptive
    clarification, multi-minute build, proposal review, and approval/rejection
    decision.
14. Completion audit and terminal handoff identify G24 as the next consumer
    and state which model families are usable, detected-only, or deferred.

## Verification and evidence

Evidence belongs under `docs/development/evidence/phase-5/G26/` and includes:

- accepted contract amendment and decision record;
- model-discovery matrix with detected/configured/verified/usable outcomes;
- sanitized setup/readiness transcripts;
- question/build/review state vectors and transition tests;
- model-port invocation evidence with provider and credential-boundary checks;
- timeout, cancellation, malformed-output, budget, stale-event, and recovery
  evidence;
- citation/reference integrity and no-secret evidence;
- mutation report for every approval, fallback, boundary, and budget guard;
- fresh-wheel installed replay and schema verifier output; and
- completion audit plus terminal handoff.

No raw prompts, source references, provider output, API keys, cookies, or local
home/workpad databases may be committed.

## Stop boundary

Stop G26 if no selected model target is genuinely usable, executable discovery
is treated as adapter support, the build cannot distinguish draft from
approved proposal, model output can bypass a boundary, interruption can
duplicate calls/proposals/version advancement, or citations cannot be
resolved. G26 is not complete when a pretty interview page exists; it is
complete when a configured, evidenced model can help build a proposal through a
bounded, inspectable, recoverable workflow and the operator remains the final
approval authority.

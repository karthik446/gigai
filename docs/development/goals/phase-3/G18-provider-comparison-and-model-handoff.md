# G18 — Provider Comparison and Model Handoff

- Status: Proposed for review
- Depends on: G16 and G17; consumes accepted S18-01 through S18-05 and
  S22-01 decision records, plus G11's model port and factory
- Unblocks: G22 deliberative create, then G19 approved target effects

## Outcome

Implement the first provider-backed Review Loop path without making a provider
or model an implicit authority. A sealed Gig may run independent reviewer or
solver Goals through an explicitly supported adapter set, preserve each
provider's identity and evidence, compare their outputs, and perform a bounded
model-to-model handoff when the approved Graph requests one.

G18 is the boundary where live model effects become possible. Every such effect
must be explicit in the Run, selected by the approved proposal, redacted before
invocation, bounded by policy, and represented by replayable evidence. G18
does not turn provider availability into permission, and it does not make a
successful model response equivalent to a verified or approved result.

## Preconditions and contract gate

Before implementation, all five S18 spikes and S22-01 must have accepted
decision records. Each record must identify adopted and rejected assumptions,
the supported adapter families, the input-redaction boundary, cancellation
semantics, and the G18-versus-follow-up split. A spike probe is not itself a
support claim.

G18 must inspect the nineteen-resource baseline and determine whether the
existing Run, Trace, Finding, Feedback, Adjudication, and invocation records
can represent provider comparison and handoff. If a new artifact, field,
transition, or authority rule is required, stop for an explicit additive
contract amendment before runtime code. The amendment must preserve all prior
hashes and vectors and must update the installed-resource verifier; no schema
meaning may change by inference.

## In scope

- Use G11's transport-neutral model port and factory as the only production
  selection boundary. Domain code must not import concrete adapters or choose
  a provider by class name.
- Implement only adapter families whose S18 outcomes are accepted. OpenAI and
  OpenRouter are the initial candidates because G11 already provides their
  port conformance. Codex CLI, Claude CLI, Anthropic API, and a local-model
  runtime remain conditional on their spike evidence and may be split into
  follow-up Goals rather than being forced into G18.
- Resolve a provider target from sealed configuration and proposal metadata:
  provider family, endpoint/model identity, credential reference, capability
  requirements, budget policy, and redaction policy. Persist references and
  observed identities; never persist credential values.
- Execute two or more independent reviewer/solver Goals when the approved
  Graph requests comparison. Each invocation receives only the selected,
  redacted reference bytes and the role-specific prompt/materialized contract.
  Unselected references are not readable by the adapter boundary.
- Represent model-to-model handoff as an explicit Graph edge and durable
  artifact parentage. The receiving Goal gets a bounded handoff artifact with
  provenance, not an implicit conversation or hidden shared context.
- Normalize status, finish reason, usage, cost status, provider/model identity,
  cancellation, and provider errors through the common port while retaining
  provider-specific extensions as typed, redacted metadata.
- Preserve independent outputs and disagreements. G18 may produce comparison
  evidence or an adjudication input, but it must not erase a disagreement or
  silently choose a winner. G16's finding and adjudication rules remain the
  authority for review-loop closure.
- Enforce bounded model calls, handoffs, wall time, tokens, and tool calls from
  the sealed budget. A deterministic failure or cancellation must be committed
  before the parent reports terminal state.
- Record provider-input selection, redaction result, credential lookup status,
  network permission, invocation identity, raw-response digest, normalized
  response, and usage/cost evidence in workpad-local, content-addressed
  records. Raw provider payloads may be retained only when the accepted S18
  redaction policy makes them share-safe.
- Keep provider effects auditable and opt-in. Live proofs may run locally with
  operator credentials, but the installed verifier and CI/offline scenarios
  use fake adapters and network-denial fixtures.

## Out of scope

- Automatic provider fallback, provider racing, background activity, hidden
  retries, or an adapter selected from an unsealed environment variable.
- Any provider family whose S18 spike did not produce an accepted outcome, or
  any claim that a successful feasibility probe is production support.
- Arbitrary tool execution, package installation, capability activation, or
  credential acquisition. G17 owns capability inspection and installation;
  provider-specific tools require a later approved capability Goal.
- Target mutation, patch application, commits, deployments, or treating a
  model response as authorization to change a user repository. G19 owns those
  effects.
- The user-facing `gigai create` interview, question generation, proposal
  approval UI, or public/remote server. S22-01 defines the protocol and G22
  implements the local HTMX interaction.
- Recurring Runs, scheduled comparison, historical learning, or cross-Run
  evaluation. G20 and G21 own those behaviors.
- Universal PII detection or a claim that a model can be asked to detect PII
  without receiving the underlying bytes. G18 enforces the accepted
  deterministic redaction boundary and records uncertainty for later privacy
  work.
- Silent changes to the journal authority model, Goal transitions, budgets,
  provider defaults, or any packaged schema. Stop for an amendment when the
  accepted spike contract is insufficient.

## Acceptance criteria

1. Before runtime implementation, the evidence set contains accepted decision
   records for S18-01 through S18-05 and S22-01. It names the adopted adapter
   families, rejected assumptions, supported effects, redaction boundary,
   handoff limit, cancellation behavior, and deferred follow-up Goals.
2. The contract gate is satisfied before code changes. If G18 needs additional
   schemas or transitions, an additive amendment raises the resource count,
   preserves all prior nineteen SHA256SUMS entries and canonical vectors, and
   updates installed verifiers. If no amendment is needed, evidence proves
   the existing schemas are sufficient rather than merely omitting the check.
3. The factory and ownership tests prove that every supported adapter is
   selected through G11's model port, with no direct concrete-adapter imports
   from domain code and no provider chosen by an ambient environment value.
4. A comparison fixture runs at least two independent supported adapters (or
   two independently configured targets within an accepted family), records
   distinct invocation identities, and preserves provider/model identity,
   request/reference digests, normalized output, finish status, and usage
   evidence for each pass.
5. A handoff fixture follows an explicit Graph edge. It persists the sending
   output, receiving input artifact, parent IDs, and bounded handoff count;
   the receiver cannot observe unselected references or hidden prior context.
   Exceeding the handoff limit fails closed before another provider call.
6. Comparison preserves disagreement: conflicting outputs on the same
   criterion/evidence remain separate, carry cross-provider provenance, and
   become adjudication inputs rather than being merged into a synthetic
   consensus. Agreement is recorded distinctly from disagreement.
7. Redaction and reference-selection fixtures prove that secrets, credential
   values, and policy-blocked PII never reach an adapter. A redaction failure,
   ambiguous policy, or unselected reference is a durable blocked outcome,
   not a best-effort provider call.
8. Credential, network, and availability behavior is explicit. Missing
   credentials, unavailable providers, denied network, malformed responses,
   timeout, cancellation, and provider errors each map to deterministic
   normalized outcomes; none is reported as model success or as zero cost.
9. Usage and cost evidence distinguishes provider-reported, derived,
   unavailable, and not-applicable values. Raw provider usage and normalized
   aggregate usage remain separately attributable to invocation IDs, and
   budget exhaustion prevents a subsequent call.
10. No automatic fallback, hidden retry, background worker, or provider race
    exists. Static import/ownership checks, fake adapters, subprocess guards,
    credential canaries, and network-denial fixtures catch each forbidden
    path; mutation testing kills each guard rather than merely producing a
    report file.
11. Every provider or handoff failure commits its failure/blocked record before
    the parent reports terminal state. Re-running cancellation and unavailable
    fixtures does not duplicate terminal handoffs or rewrite prior evidence.
12. A fresh installed wheel replays the comparison, handoff, redaction,
    cancellation, unavailable-provider, and budget fixtures using fake
    adapters without a source checkout, live credentials, network, or target
    repository. Operator-only live proofs, if supported, are stored separately
    and identify exact target/provider/model versions.
13. Completion evidence includes the adopted provider matrix, contract/hash
    decision, adapter conformance matrix, handoff and comparison vectors,
    redaction/capability records, cancellation/unavailable records, usage and
    cost normalization, mutation report, sanitized manifests, installed-wheel
    replay, live-proof boundary, completion audit, and terminal handoff. The
    audit explicitly names which provider families and tool effects remain
    deferred to later Goals.

## Verification and evidence

- One decision record per prerequisite spike, with probe inputs and outputs
  committed as local fixtures or content-addressed references.
- Adapter conformance tests against fake provider servers/CLIs and the
  deterministic model port; no secrets in fixtures.
- Comparison, handoff, redaction, cancellation, unavailable-provider, budget,
  and forbidden-effect negative fixtures with deterministic finding codes.
- Mutation tests for adapter selection, reference filtering, redaction,
  handoff bound, disagreement preservation, failure ordering, and cost-status
  normalization.
- Fresh-wheel installed verifier and offline CI scenarios. Live provider proofs
  remain opt-in operator evidence and are never used to make CI green.

## Stop boundary

Stop before implementing any provider family or handoff if an S18/S22 decision
record is missing, contradictory, or insufficient to derive a schema and
transition table. Stop if the redaction boundary cannot prove which bytes were
sent, if credentials could enter durable artifacts, if provider failure cannot
be distinguished from success, or if comparison would erase disagreement.
Stop after the accepted adapter set has independent conformance, comparison,
handoff, redaction, cancellation, unavailable-provider, and budget evidence.
Defer unsupported provider families, provider-specific tools, target effects,
proposal interaction, and recurrence to their named Goals.

# S18/S22 — Provider and Proposal Prerequisite Spike Tranche

- Status: Accepted — six prerequisite spikes complete; G18 remains gated
- Type: Research and contract-design tranche; not an implementation Goal
- Depends on: G11 model port and factory; G16 and G17 substrate
- Unblocks: G18 provider comparison and model handoff after the tranche
  terminal handoff and contract review; S22-01 contributes to G22 deliberative
  `create`

## Purpose

Resolve the provider, handoff, redaction, and proposal-interaction questions
that G18 must not answer by inference. This tranche produces accepted decision
records and disposable executable evidence for S18-01 through S18-05 and
S22-01. It does not implement provider behavior, add adapter support, invoke a
provider from GigAI runtime code, or advertise compatibility.

The `S18-*` and `S22-*` identifiers are deliberate: these are research-spike
nodes, not G-numbered implementation Goals. A successful probe is feasibility
evidence only. A provider family becomes supported only through a later
approved implementation Goal and its conformance evidence.

## Contract and authority boundary

- The current G11 model port and factory are the baseline to investigate; the
  spikes must not widen or reinterpret them through implementation changes.
- Probes may use fake provider servers, fake CLIs, disposable HTTP/SQLite
  fixtures, local recordings, and synthetic credentials. They must not mutate a
  real user repository or require live credentials for acceptance.
- No packaged schema, canonical vector, Goal transition, journal authority
  rule, or installed verifier may change as an incidental result of a spike.
  If a spike identifies a required contract change, record the exact additive
  amendment and defer its application to an explicit contract review before
  G18 runtime work.
- Decision records must distinguish adopted assumptions, rejected assumptions,
  unresolved questions, and evidence limitations. They must not convert a
  successful probe into a support claim.
- Proposal interaction remains local-first and offline by default. S22-01 must
  not ship `gigai create`, provider adapters, a public server, background work,
  capability execution, credential acquisition, or target mutation.

## Spike register

| Spike | Question | Required result | Initial state |
|---|---|---|---|
| S22-01 | What is the smallest safe local HTMX proposal/question protocol? | Accepted proposal/question/answer state machine, protocol example, persistence trace, evaluation corpus, and explicit non-effects | Accepted / committed |
| S18-01 | What common request, response, identity, error, cancellation, usage, and cost contract can the candidate providers share? | Compatibility matrix, typed extension boundary, replay artifact decision, and adopted/deferred adapter families | Accepted / committed |
| S18-02 | Can Codex CLI and Claude CLI be isolated and normalized safely? | Fake-CLI process, capture, exit, timeout, cancellation, working-directory, and credential-inheritance evidence; follow-up recommendation | Accepted / committed |
| S18-03 | What is feasible for Anthropic API and one local-model runtime? | Separate API and local-runtime findings covering identity, streaming/usage, errors, cancellation, discovery, limits, and reproducibility | Accepted / committed |
| S18-04 | How should comparison and bounded model handoff work? | Explicit Goal-edge, artifact-parentage, disagreement, adjudication-input, cancellation, unavailable-provider, usage/cost, and no-fallback design | Accepted / committed |
| S18-05 | Which bytes may cross the provider boundary and how are credentials/network access audited? | Deterministic reference-selection and redaction boundary, credential-reference rules, network-denial evidence, and blocked outcomes | Accepted / committed |

## Required decision-record shape

Each spike produces one checked-in decision record under its own evidence root,
for example `docs/development/evidence/phase-3/S18-01/`. The tranche-level
terminal handoff is checked in under
`docs/development/evidence/phase-3/S18-S22/terminal-handoff.md` and links those
per-spike records. Each record must include:

1. The spike question, scope, baseline symbols/contracts, and explicitly
   rejected implementation assumptions.
2. The adopted decision, supported effect boundary, and any conditional or
   deferred provider family.
3. Probe inputs, fixture or recording identity, exact observed outputs, and
   limitations. Secrets and raw credential values are prohibited.
4. The input-selection and redaction boundary, including what is provable
   deterministically and what remains an explicit user or later privacy
   decision.
5. Cancellation, timeout, unavailable-provider, malformed-response, and
   failure-ordering semantics where applicable.
6. The recommendation for what G18 adopts, what becomes a follow-up Goal, and
   what is rejected outright.
7. Contract impact: existing schemas and transitions are sufficient, or an
   additive amendment is required. The record must name affected artifacts,
   fields, transitions, resource-count changes, preserved hashes, and verifier
   updates if an amendment is proposed.

## Spike-specific boundaries

### S22-01 — Local HTMX proposal interview and clarification protocol

Define the smallest embedded localhost interaction for turning a free-form
request and selected local references into an approved proposal. Cover bounded
question rounds, structured question IDs and answer types, dependencies,
priorities, rationale, provenance, conditional follow-ups, ambiguity blocking,
reference selection, capability/privacy/effect choices, revisions, approval,
and short-lived browser-session persistence.

The evidence must include a disposable HTMX fixture, protocol example,
proposal/question/answer state machine, draft SQLite/workpad trace, and an
evaluation corpus spanning a repository feature, resume tailoring, reference
synchronization, and one tabular or finance Gig. The fixture must prove
loopback-only behavior and no provider, tool, credential, or target effect.

### S18-01 — Common provider-port and evidence contract

Map OpenAI API, OpenRouter API, Codex CLI, Claude CLI, Anthropic API, and one
representative local-model runtime against G11's port. Define the smallest
common request/response, model identity, streaming, finish state, error,
cancellation, usage, and cost-status shape. Preserve provider-specific values
as typed redacted extensions instead of silently discarding them. Identify the
artifacts and variable fields required for replay.

### S18-02 — Codex CLI and Claude CLI feasibility

Use fake CLIs to probe process discovery, argument construction, stdin/stdout/
stderr capture, structured output, exit-code mapping, timeout, cancellation,
working-directory isolation, and credential inheritance. No probe may mutate a
real target repository. Decide whether either CLI is feasible for G18 or needs
its own implementation Goal.

### S18-03 — Anthropic API and local-model feasibility

Evaluate the Anthropic API's content blocks, tool/model identifiers, streaming,
usage, rate/error semantics, and cancellation independently from a local
runtime's discovery, installation assumptions, offline operation, resource
limits, model identity, and reproducible capture. Do not treat local models as
interchangeable with hosted APIs. Recommend a minimum supported contract or
explicit deferral for each.

### S18-04 — Handoff, comparison, cancellation, and unavailable provider

Design comparison between independent reviewer/solver Goals and a model-to-
model handoff over an explicit Graph edge. Define sending-output and
receiving-input artifacts, parentage, bounded handoff count, hidden-context
exclusion, disagreement preservation, adjudication inputs, cancellation,
unavailable-provider outcomes, and usage/cost attribution. Prove that fallback,
background activity, provider racing, and unbounded retry are not hidden in the
design. Recommend whether comparison and handoff remain one G18 or split.

### S18-05 — Provider-input redaction, credential, and network boundary

Define the pre-invocation boundary for reference selection, secret and PII
redaction, credential lookup, network permission, and audit evidence. Fixtures
must prove that unselected references, credential values, and redaction-failed
inputs never reach an adapter, and that offline scenarios deny provider calls.
Separate deterministic guarantees from explicit review and later privacy work.

## Tranche acceptance criteria

1. Six decision records are checked in, one for each spike, and each is
   accepted by review.
2. The records provide the adopted/rejected assumption set, probe evidence,
   supported effects, redaction boundary, cancellation behavior, and
   G18-versus-follow-up recommendation required by the G18 precondition.
3. A common-port compatibility matrix identifies candidate families without
   advertising any as supported solely from probe success.
4. S18-04 provides a deterministic handoff/comparison transition table that
   preserves independent outputs and disagreements.
5. S18-05 provides deterministic reference-selection, credential, redaction,
   network-denial, and blocked-outcome evidence.
6. Contract impact is explicit against the nineteen-resource baseline. Any
   required amendment is additive, preserves prior hashes and vectors, and is
   separately approved before G18 implementation.
7. All fixtures replay without live credentials, network access, a source
   checkout dependency beyond the spike package, or a real target repository.
8. The tranche terminal handoff names the accepted adapter families, deferred
   families/effects, required contract work, and the exact G18 start condition.

## Stop boundary

Stop the tranche if a decision would require silently changing a packaged
schema, authority rule, Goal transition, budget, or provider default. Stop if a
probe cannot prove which bytes were sent, if credential values enter durable
evidence, if failure cannot be distinguished from success, or if comparison
would erase disagreement. Do not begin G18 runtime implementation until all six
records are accepted and the contract impact is resolved.

# Pre-G18 contract-impact review

- Status: Complete review; additive amendment required before G18 runtime code
- Baseline: nineteen packaged schema resources and their current SHA256SUMS
- Scope: S18-01 through S18-05, S22-01, and the accepted S16-EVAL gate
- Authority: this record decides whether G18 may proceed to contract
  amendment work; it does not change a packaged schema or runtime behavior

## Verdict

The nineteen-resource baseline is sufficient for the existing Review Loop and
for the spike evidence, but it is not sufficient for the durable provider
records G18's own acceptance criteria require. G18 must stop for one separate
additive contract amendment that adds two resources:

1. `model-invocation.schema.json` for one provider-port invocation, including
   its terminal outcome, replay identity, usage/cost, and pre-invocation
   boundary attestation.
2. `model-exchange.schema.json` for an explicit Goal-edge handoff or provider
   comparison, including artifact parentage, bounded handoff state, and
   disagreement preservation.

This amendment raises the packaged resource count from 19 to 21. It must leave
all existing schema bytes, SHA256SUMS entries, canonical vectors, lifecycle
states, journal authority, and verifier behavior unchanged. The amendment is a
separate contract change; this review does not authorize implementing it.

S22-01 does not require a schema amendment before G18. Its local protocol and
SQLite trace remain spike evidence. If G22 later makes structured interview
questions and answers durable product artifacts, G22 must raise a separate
proposal-interview contract amendment rather than expanding this G18 change.

## Baseline inventory and reuse boundary

The current nineteen resources already provide useful pieces:

| Existing resource | Reusable boundary | Why it is not enough for G18's new records |
|---|---|---|
| `common.schema.json` | IDs, artifact references, effects, budgets, usage, errors | No provider terminal outcome, replay envelope, or boundary-attestation object |
| `run-manifest.schema.json` / `run-brief-frontmatter.schema.json` | Sealed model selectors, resolved identity, effects, budget | Run-level configuration is not one invocation's observed result |
| `run-details.schema.json` | Aggregate usage, model errors, terminal handoff reference | No per-invocation request/response identity or failure ordering |
| `trace.schema.json` | Ordered event digests, redaction policy, variable fields | Trace events do not define provider result semantics or selected input bytes |
| `review-bundle.schema.json` | Exact reference bytes and allowlisted redaction policy | Bundle policy is an input boundary, not proof of the invocation-time decision |
| `goal-graph.schema.json` | Goal IDs, edge IDs, dependency edges, budgets | An edge has no handoff artifact, parentage, count, or comparison result |
| `handoff-frontmatter.schema.json` | Journal handoff chronology and causal parents | It is journal authority metadata, not a model-to-model payload record |
| `finding.schema.json` / `adjudication.schema.json` | Review Findings, disagreement metadata, and human decisions | They must not be overloaded with provider exchange records; adjudication is finding-scoped |
| `review-loop.schema.json` / `report.schema.json` | Review lifecycle and projections | Neither is the authority for provider transport or invocation evidence |
| `gig-proposal.schema.json` | Opaque `creation_manifest` artifact reference | It does not define S22 question/answer semantics and is outside G18 |

The existing `invocation_id` and `artifact_ref` definitions are reused. No new
identity prefix or change to the canonical identity API is needed: the new
records are addressed by their existing invocation IDs and content-addressed
record artifacts.

## Candidate-area decisions

### 1. Common provider envelope and replay fields — amend

S18-01's common envelope cannot be represented by the current schemas without
losing the distinction between a successful result, partial output, timeout,
cancellation, unavailable provider, and a blocked pre-invocation boundary.
`trace.schema.json` can identify ordered payload digests, but it does not say
which provider/model was observed, which selected reference digests were sent,
or which terminal outcome the parent must consume. `run-details` only has
aggregate usage and coarse model errors.

`model-invocation.schema.json` must define, at minimum:

- Run, Goal, invocation, role, provider-family, configured-selector,
  endpoint/model identity, and adapter identity;
- redacted request/reference artifact digests and a nullable response artifact
  digest; raw provider payloads remain outside the record unless S18-05 marks
  them share-safe;
- terminal outcome: `succeeded`, `partial`, `failed`, `cancelled`, `timeout`,
  `unavailable`, or `blocked`;
- normalized finish/error/cancellation fields, with `error.retryable` retained
  as observation only and never interpreted as automatic retry permission;
- raw provider usage, normalized usage, and cost status using the existing
  common usage vocabulary, all attributable to the invocation ID;
- typed, namespaced, redacted provider extensions; and
- declared variable fields and a stable replay digest that excludes only those
  declared variables.

Required terminal transition rule:

```text
pending -> boundary_blocked | invoking
invoking -> succeeded | partial | failed | cancelled | timeout | unavailable
boundary_blocked -> terminal
```

No terminal record may transition back to invocation, retry, fallback, or a
different provider. The record must be durably committed before its parent
reports a terminal state.

### 2. Goal-edge handoff and comparison — amend

`goal-graph.schema.json` already names a dependency edge, and the S18-04
fixture proves the desired state model. But the edge schema does not persist
the sending output, receiving input, source parent, handoff index/cap, or
comparison result. `handoff-frontmatter.schema.json` cannot be repurposed for
this: it is the journal's causal authority record. `adjudication.schema.json`
is specifically finding-scoped and cannot become a generic model winner
record.

`model-exchange.schema.json` must define a content-addressed record for either
`handoff` or `comparison`, with:

- the Graph `edge_id`, source/receiver Goal IDs, and Run ID;
- source invocation IDs and source artifact references;
- for a handoff, the receiving input artifact, source parent artifact, bounded
  handoff index, and fixed edge cap;
- for a comparison, all independent output artifact references and their
  invocation provenance;
- status values `received`, `agreement`, `disagreement`, `cancelled`,
  `unavailable`, and `blocked`;
- `requires_human_adjudication` and an optional adjudication-input artifact;
  disagreement must carry no selected winner; and
- no field that permits hidden context, implicit provider selection, fallback,
  racing, or unbounded retry.

The record is content-addressed through an existing `artifact_ref`; this avoids
adding a new entity-ID prefix. Existing Finding/Adjudication records remain
the authority when a model comparison is converted into Review Findings.

### 3. Blocked redaction, credential, and network attestations — amend, bundled

S18-05 requires durable blocked outcomes and G18 requires evidence of selected
inputs, redaction, credential lookup, network permission, and invocation
identity. The current `review-bundle` policy says what is allowed, but it does
not attest what the invocation boundary actually checked. A generic `error`
would lose the selected-byte and policy-order evidence.

The boundary attestation belongs inside `model-invocation.schema.json`, not in
a third resource. It must include:

- selected reference IDs and exact content digests;
- redaction policy/version and result (`not_started`, `passed`, `failed`);
- credential reference metadata only and lookup result (`not_requested`,
  `reference_valid`, `available`, `missing`, `invalid`), never the value;
- network policy/result (`offline`, `denied`, `permitted`, `not_checked`);
- the adopted S18-05 check-order version; and
- a blocked outcome code when any required check fails.

The schema must prohibit raw credential values, authorization headers, and
unredacted request bytes. A blocked record is terminal and is still valid
evidence; no adapter call is implied by its existence.

### 4. S22-01 interview persistence — no amendment for G18; defer to G22

S22-01's accepted boundary is a local, short-lived protocol trace. G18 does
not implement `create`, HTMX transport, question generation, or proposal
approval. The existing `gig-proposal.schema.json` can carry a content-addressed
creation-manifest artifact without pretending that the interview protocol is a
runtime schema.

Therefore no fourth resource is added to the G18 amendment. G22 must stop for a
separate additive `proposal-interview.schema.json` if it needs durable,
schema-validated question IDs, typed answers, clarification rounds,
reference-selection decisions, or operator approval records. Until then,
S22-01's protocol example, evaluation corpus, and SQLite trace remain evidence
only, and S22-01 remains the authority for proposal-question quality.

## Amendment package and verifier obligations

The future contract-amendment change set must contain exactly:

1. `src/gigai/schemas/model-invocation.schema.json`;
2. `src/gigai/schemas/model-exchange.schema.json`;
3. the two new `SHA256SUMS` entries, with all nineteen existing entries
   byte-for-byte preserved;
4. the installed-resource verifier update from 19 to 21 resources;
5. schema registry/validator fixtures for success, blocked, timeout,
   cancellation, unavailable, handoff-cap, disagreement, and redaction-failed
   cases; and
6. canonical vectors proving existing resource hashes and vectors are
   unchanged, plus new vectors for both resources.

No runtime adapter, provider, credential resolver, journal transition, Goal
transition, budget default, or schema meaning may change in that amendment
without its own explicit contract decision.

## G18 gate after this review

G18 remains blocked. The next authorized step is to review and accept the
two-resource additive amendment package described above. Only after that
amendment is accepted and its installed verifier/canonical evidence passes may
the G18 implementation Goal begin. Its acceptance criteria must then cite this
record, S16-EVAL's fixed bar, the S18-05 boundary attestation, and S18-04's
no-fallback/disagreement rules.

### Follow-up state for G22

The interview amendment was intentionally deferred by this G18 review. That
follow-up is now resolved separately in
`S22-01/proposal-interview-contract-amendment.md`: the additive
`proposal-interview.schema.json` resource is accepted as resource 22, all
prior 21 resources and hashes are preserved, and the installed verifier reports
22 resources. This follow-up does not retroactively change the G18 amendment or
authorize G22 runtime work by itself; G22 still requires its own accepted goal
and completion evidence.

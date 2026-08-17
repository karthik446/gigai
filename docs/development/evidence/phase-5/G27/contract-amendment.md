# G27 Adaptive Gig Discovery Contract Amendment

- Status: Accepted additive amendment; runtime implementation authorized
- Type: Serialized discovery, capability-disclosure, and Gig-definition
  amendment for G27
- Depends on: G27 contract-impact record, accepted G26 builder contract,
  accepted G20 learning/improvement contract, G22 proposal-interview contract,
  and G28 v0.1.5 readiness evidence
- Unblocks: G27 runtime implementation; G29 remains the downstream human UAT
  gate
- Baseline: twenty-nine packaged schema resources and their current hashes

## Decision

Add exactly one subordinate resource:

`gig-discovery-manifest.schema.json` — the content-addressed, revisioned
manifest for one G27 discovery result. It carries the model-selected question
rounds, truthful capability inventory, pre-proposal research plan, stable Gig
definition/Run-input distinction, and bounded improve context.

The existing G26 resources remain byte-identical:

- `gig-builder-session.schema.json` remains the durable session and lifecycle
  authority;
- `proposal-draft-manifest.schema.json` remains the subordinate proposal-draft
  and completed-research manifest; and
- G22's existing typed question and answer definitions remain the shared
  question vocabulary.

The existing `gig-proposal.schema.json` remains the sole proposal identity,
approval, and active-version authority. The discovery manifest cannot allocate
a proposal ID, approve itself, advance `active-gig-version.json`, create a
Run, authorize a capability, invoke a provider, or mutate a target.

The discovery manifest is a subordinate artifact, not a second lifecycle. For
creation, the G26 `proposal-draft-manifest.proposal_artifact` reference points
to the normalized discovery-manifest bytes. The existing builder session still
points to the proposal-draft manifest through its `draft` reference, and the
ordinary proposal still uses the existing `creation_manifest` path. This
reuses existing artifact ownership without changing either G26 schema's
meaning.

For improvement, the discovery manifest records the selected G20 learning
records and bounded context projection. The resulting G20
`improvement-manifest` must copy the selected `learning_record_ids` exactly
and re-run both G20 gates. The discovery manifest never becomes the
improvement proposal or a parallel evidence authority.

This amendment defines serialized boundaries only. It adds no model adapter,
provider support claim, research executor, browser route, approval behavior,
Run behavior, target effect, or version lifecycle implementation.

## Resource: `gig-discovery-manifest.schema.json`

The resource is schema version `1.0`, rejects unknown fields, and requires:

| Field | Contract |
| --- | --- |
| `schema_version` | Exact string `1.0`. |
| `manifest_version` | Positive manifest revision. |
| `manifest_id` | Subordinate `discovery_manifest_` UUIDv4 identity; never a proposal or Gig identity. |
| `session_id` / `project_id` / `gig_id` | Exact binding to the G26 session and bound Gig. |
| `request_kind` | `create` or `improve`, matching the builder session. |
| `parent_manifest_id` | Nullable prior discovery-manifest identity for revision; prior bytes remain immutable. |
| `capabilities` | Truthful capability inventory derived from configuration and accepted checks. |
| `research_plan` | Explicit pre-execution research intent, sources, boundary, budget, and evidence obligations. |
| `question_rounds` | One or more model-selected direction rounds; each round contains zero to five typed questions. |
| `stable_definition` | Content-addressed reusable Gig definition artifact and declared stable fields. |
| `run_input_contract` | Content-addressed description of changing per-Run inputs; not a Run record. |
| `improve_context` | Nullable bounded selection of G20 learning records and summary projections; required only for `improve`. |
| `created_at` / `updated_at` | RFC 3339 timestamps. |

The exact UUID pattern, artifact-reference shape, common identities, and
question/answer definitions must reuse the existing common and
`proposal-interview` definitions. The new resource must not copy a second
incompatible identity or question vocabulary.

## Capability inventory

Each capability entry must contain a closed capability identifier, a status,
the status source, and the boundary required for use. The initial capability
identifier set is:

```text
local_reference_read
model_invocation
bounded_research
proposal_construction
approved_run_execution
target_effect
```

Each status is one of:

```text
detected | configured | verified | usable | unavailable | unsupported
```

The status meanings are:

- `detected`: a candidate or configuration signal exists;
- `configured`: non-secret configuration is present and structurally valid;
- `verified`: the accepted contract and required checks have passed;
- `usable`: the capability is eligible under the current policy and budget;
- `unavailable`: a required dependency or boundary is not usable now; and
- `unsupported`: no accepted GigAI contract covers the candidate.

The G26 `model_selection.readiness` field retains its model-target meaning.
The G27 capability inventory may refer to that readiness but cannot replace it
or promote a model from `detected` to `usable`. A model response cannot change
any capability status. In particular, discovering a CLI executable does not
claim an accepted adapter, and recommending network research does not grant
network access.

The inventory is diagnostic and proposal context. It is not an authority to
invoke a provider, read a reference, use a credential, execute a Run, or
apply a target effect.

## Pre-proposal research plan

`research_plan` is distinct from G26's completed `proposal-draft-manifest`
`research` object. It must contain:

- an explicit status, including `not_started`, `planned`, `approved`,
  `running`, `completed`, `cancelled`, `unavailable`, and `blocked`;
- a bounded list of proposed sources, each marked local or external;
- the source locator or local reference identity without copying unselected
  bytes;
- the selected network boundary, using the existing `local_only` or
  `configured_provider_only` vocabulary;
- the privacy boundary and whether redaction is required before configured
  sharing;
- the credential reference name only, or null;
- the model/tool call, token, cost, and wall-time limits; and
- the expected evidence artifact and unresolved-claim behavior.

The status is descriptive planning state. `approved` in this object does not
mean proposal approval or target-effect approval; it means only that the
operator approved the bounded research plan through the existing policy path.

The plan cannot itself cause a network call, credential lookup, provider
invocation, or target read. Execution must pass the existing configured policy,
selected references, credential-reference, cancellation, and budget checks.
The completed result remains G26's `research` object with its existing
claim/citation/verification shape.

## Dynamic question rounds

`question_rounds` is an array of revisioned direction rounds. Each round
contains:

- a positive round number and optional parent round;
- the model-selection digest that generated the questions;
- a generation provenance artifact or digest;
- `questions`, with zero to five items;
- typed `answers` bound by `question_id`; and
- a round status and timestamps.

Each question reuses G22's existing typed shape and therefore carries:

- stable `question_id`;
- `answer_type` of `text`, `choice`, `multiselect`, or `confirmation`;
- `required`;
- typed options;
- `depends_on` references;
- a bounded rationale; and
- provenance identifying how the question was produced.

The manifest validator must additionally require that:

1. every dependency resolves to a question in the same round or an explicitly
   retained prior round;
2. every answer references an existing question and matches its answer type;
3. question IDs are unique across the active round;
4. the model-selection digest is the selected session model, not model text;
   and
5. a round with sufficient context may contain zero questions.

Five is a hard maximum per direction round. The maximum applies after model
retry, response merge, revision, browser replay, and recovery reconciliation.
No path may append a sixth question after validation or render one from an
unpersisted model response.

No new top-level G26 builder-session state is introduced. G27 maps its
discovery stages to the existing states:

```text
define_intent -> clarify -> build_requested -> researching
              -> proposal_draft_ready -> operator_review -> revised
```

The existing G26 `allOf` conditionals remain authoritative for `draft`, model
readiness, and terminal reasons. G27 does not add a state that would bypass
those conditionals. A blocked, unavailable, malformed, cancelled, timed-out,
budget-exhausted, failed, or rejected path uses the existing terminal state
and recovery rules.

## Stable Gig definition and Run input

The manifest must keep these as separate content-addressed artifacts:

### `stable_definition`

This is the reusable Gig contract. It may contain or reference:

- reusable Goals and their ordering;
- stable constraints and non-negotiable boundaries;
- expected outputs and success criteria;
- stable reference roles and exact reference identities; and
- the definition's own canonical digest.

### `run_input_contract`

This describes context expected to vary for an individual Run. It may contain:

- input field identity and supported type;
- requiredness and validation rules;
- the source of a value, such as operator input or a job URL; and
- the Run-input contract's canonical digest.

The manifest may carry current draft values for a Run input, but those values
are explicitly non-authoritative until a separate Run is created through the
existing Run lifecycle. A current job URL, operator answer, or changing file
cannot silently rewrite `stable_definition`. During revision and improvement,
the original definition and reference digests remain visible and immutable.

No field in this resource creates a Run, assigns Run authority, changes the
active Gig version, or authorizes a target effect.

## Bounded G20 improve context

For `request_kind: "improve"`, `improve_context` is required and contains:

- one or more exact `learning_record_ids` from G20;
- the active Gig version expected by the discovery session;
- a bounded summary artifact containing only selected records and their
  permitted projections;
- the summary artifact's exact digest and byte limit; and
- an explicit omitted-content policy.

The context may include selected Run summaries and cited findings, feedback,
adjudications, reports, addressed artifacts, or target-effect outcomes only
through G20 learning records. It must not include raw Run databases,
credentials, hidden model context, unselected references, or an unbounded
transcript.

At G20 staging and approval, runtime must:

1. reload every cited learning record;
2. verify its exact source artifact and active-version metadata;
3. compare the G27-selected IDs with the G20 improvement manifest IDs;
4. reject missing, changed, duplicated, or unselected evidence; and
5. execute both G20 evidence-sufficiency and improvement-quality gates again.

`operator_feedback` or model confidence cannot satisfy the G20 evidence gate
alone. The G20 `improvement-manifest` remains the sole typed change and
approval input for an improvement proposal.

## Lifecycle and authority contract

1. The G26 builder-session record and ordered event trace own durable session
   state, revision, round progress, question/answer events, and recovery.
2. The G27 discovery manifest is immutable evidence for one session revision;
   a revision creates a new manifest with `parent_manifest_id`.
3. Browser fragments, in-memory model output, and model conversation state are
   disposable projections.
4. Model output may propose capability status, research sources, questions,
   Goals, definition fields, and Run-input fields. It cannot authorize any of
   them.
5. The five-question ceiling is enforced at the manifest boundary and again
   during event reconciliation.
6. Research plan approval is bounded policy consent, not proposal approval,
   Run creation, provider support, or target authority.
7. `stable_definition` and `run_input_contract` have separate digests and
   ownership. A mismatch is refused rather than repaired by inference.
8. G20 learning records and both G20 gates remain mandatory for improvement.
9. `gig-proposal` remains the sole proposal/version/approval authority; no
   discovery manifest or session state can advance the active version.
10. Repeated approval, stale browser events, malformed model output, partial
    writes, and interrupted research fail closed and cannot duplicate a
    proposal, question, evidence record, or version transition.

## Amendment invariants

1. All prior twenty-nine schema files remain byte-identical. Their hashes,
   vectors, registry entries, validators, and installed replay behavior remain
   unchanged.
2. Exactly `gig-discovery-manifest.schema.json` is added, raising the packaged
   inventory from twenty-nine to thirty.
3. `gig-builder-session.schema.json` and
   `proposal-draft-manifest.schema.json` retain their existing meanings and
   state conditionals; no G27 top-level session state is added.
4. `proposal-interview.schema.json` remains the sole shared typed question and
   answer vocabulary.
5. `gig-proposal.schema.json`, `active-gig-version.json`, the workpad journal,
   G20 learning records, and the G20 improvement manifest retain their
   authority and meanings.
6. No capability entry, research-plan status, question answer, stable
   definition, Run input, or improve-context item grants runtime authority.
7. No secret value, raw credential, hidden context, or unselected source bytes
   enter the discovery manifest or model context.
8. All new identities and artifact references are content-bound, all unknown
   fields are rejected, and all terminal/refusal paths fail closed.

## Verification obligations

The amendment package must contain:

1. `src/gigai/schemas/gig-discovery-manifest.schema.json`;
2. one new `SHA256SUMS` entry preserving all prior twenty-nine entries;
3. installed schema verifier and registry/validator inventory update from 29
   to 30;
4. schema vectors for create and improve manifests, capability statuses,
   research-plan states, local/external source boundaries, zero/one/five/six
   question cases, typed answer mismatches, dependency failures, definition vs.
   Run-input digest mismatches, and improve-context omissions;
5. semantic fixtures proving that a model cannot promote capability status,
   research cannot grant network authority, and a sixth question cannot pass;
6. state fixtures proving G27 uses the existing G26 states and conditionals,
   including terminal and recovery paths;
7. cross-resource fixtures proving the discovery manifest is subordinate to the
   G26 draft and ordinary proposal path;
8. G20 improve fixtures proving exact learning-record selection, source
   revalidation, both G20 gates, and rejection of raw/unselected Run context;
9. mutation tests for the capability, research-boundary, question-ceiling,
   definition/input, context-filtering, and G20-gate guards;
10. canonical verification that all prior twenty-nine resource bytes and hashes
    are unchanged; and
11. a fresh-wheel installed replay with sanitized evidence and no external
    network dependency.

No runtime implementation is included in this amendment. Acceptance permits
G27 implementation only after G28 readiness evidence is accepted. G29 human
UAT remains downstream and is not silently treated as complete by automated
evidence. It does not claim dynamic model support, arbitrary research,
proposal approval, Run execution, target mutation, alpha readiness, or public
release.

## Stop boundary

Stop and amend the contract again if the implementation requires any of the
following:

- a new G26 session state or changed G26 `allOf` meaning;
- a second question or answer vocabulary;
- a capability status that is not derived from installed configuration or an
  accepted runtime check;
- a research plan that grants network, credential, provider, or target access;
- a stable-definition value that can be silently replaced by Run input;
- a G27 improve path that bypasses G20 learning records or either G20 gate;
- a browser/model/in-memory object to become durable authority; or
- a new proposal, version, Run, approval, or target-effect authority.

This amendment is accepted for implementation. The schema package, vectors,
authority fixtures, installed replay, and G28 readiness dependency are the
runtime prerequisite; G29 is the later human acceptance gate.

## Evidence references

- [G27 goal contract](../../../goals/phase-5/G27-adaptive-gig-discovery-and-pre-proposal-research.md)
- [G27 contract-impact record](contract-impact.md)
- [G26 contract amendment](../G26/contract-amendment.md)
- [G26 evidence README](../G26/README.md)
- [G20 learning contract amendment](../G20/learning-contract-amendment.md)
- [G20 completion audit](../G20/completion-audit.md)
- [G20 terminal handoff](../G20/terminal-handoff.md)
- [G22 completion audit](../../phase-2/G22/completion-audit.md)
- [G22 terminal handoff](../../phase-2/G22/terminal-handoff.md)

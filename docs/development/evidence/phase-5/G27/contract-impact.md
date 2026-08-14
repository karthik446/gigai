# G27 Contract-Impact Record — Adaptive Gig Discovery

- Status: Proposed impact record; amendment not yet accepted
- Type: Schema and authority analysis for G27; no runtime implementation
- Depends on: G27 goal contract, accepted G26 builder contract, accepted G20
  learning/improvement contract, and G22 proposal-interview contract
- Unblocks: The G27 contract-amendment decision; it does not yet unblock G27
  runtime work
- Baseline: twenty-nine packaged schema resources and their current hashes

## Purpose

This record answers whether G27 can make Gig-creation questions dynamic and
model-selected using the existing G26 resources, or whether doing so would
change an existing serialized meaning by inference.

The question is not whether the current schemas can physically hold arbitrary
JSON. They reject unknown fields and therefore require an explicit contract for
every durable concept. The question is whether each G27 concept has an
existing owner with the same meaning, authority, lifecycle, and evidence
requirements.

This is an impact analysis, not an amendment. It adds no schema, route,
model call, research behavior, approval behavior, or version lifecycle.

## Evidence read

- [G27 goal contract](../../../goals/phase-5/G27-adaptive-gig-discovery-and-pre-proposal-research.md)
- [G26 contract amendment](../G26/contract-amendment.md)
- [G20 learning contract amendment](../G20/learning-contract-amendment.md)
- [G20 completion audit](../G20/completion-audit.md)
- [G22 proposal-interview schema](../../../../../src/gigai/schemas/proposal-interview.schema.json)
- [G26 builder-session schema](../../../../../src/gigai/schemas/gig-builder-session.schema.json)
- [G26 proposal-draft schema](../../../../../src/gigai/schemas/proposal-draft-manifest.schema.json)
- [G20 learning-record schema](../../../../../src/gigai/schemas/learning-record.schema.json)
- [G20 improvement-manifest schema](../../../../../src/gigai/schemas/improvement-manifest.schema.json)

The current packaged baseline was checked directly: `src/gigai/schemas/`
contains twenty-nine schema files and `SHA256SUMS` contains twenty-nine
entries. No prior resource is proposed for modification by this record.

## Decision summary

| G27 concept | Existing shape | Disposition | Reason |
| --- | --- | --- | --- |
| Capability disclosure | G26 `model_selection.readiness` | Reuse for model readiness; additive capability inventory required for broader capabilities | The existing enum describes one selected model target, not local reading, research, provider invocation, or future Run execution. |
| Pre-proposal research plan and boundary | G26 `proposal-draft-manifest.research` and `boundary` | Reuse boundary vocabulary; additive pre-execution plan required | `research` is a completed draft result and has no planned/not-started state, intended sources, or preflight evidence obligations. |
| Dynamic question round | G26 `questions` and G22 typed question shape | Additive constraint/round contract required | Questions already have the right typed fields, but the array has no maximum and does not distinguish a G27 direction round from generic builder clarification. |
| Stable Gig definition versus Run input | G26 `intent`, `references`, draft artifact | Additive typed distinction required | No current field identifies whether a value belongs to the reusable approved definition or one changing Run. |
| Bounded Run summaries into G20 improve | G20 `learning-record` and `improvement-manifest` | Reuse learning records and G20 gates; additive bounded-context selection may be required | G20 owns source identity, provenance, and improvement authority, but does not define how G27 selects a bounded set of Run summaries for an improve session. |

Overall result: an additive G27 amendment is required. The amendment should
prefer one subordinate discovery/definition resource for G27-owned concepts,
plus references to existing G20 learning records, rather than extending
unrelated resources or creating a second proposal/version authority. The exact
resource split and names remain an amendment decision.

## 1. Capability disclosure and status

### Current representation

`gig-builder-session.schema.json` contains a required `model_selection` object
with `target_name`, `endpoint_name`, `model`, `adapter`, `readiness`,
`selection_actor`, and `selection_digest`. Its readiness enum is:

```text
detected | configured | verified | usable | unavailable | unsupported
```

G26's contract gives these values model-target meanings. In particular,
`detected` means a candidate was found, `configured` means non-secret
configuration is valid, `verified` means the accepted adapter checks passed,
and `usable` means the target is eligible for this session. G26 does not
define a generic capability record.

### Impact

G27 must disclose more than the selected model. It needs to explain the
current status of capabilities such as:

- reading selected local references;
- invoking the configured model target;
- conducting bounded research;
- constructing a proposal;
- running an approved Gig later; and
- applying an approved target effect, where applicable.

Reusing `model_selection.readiness` for those capabilities would overload a
model-specific status and would make “usable model” appear to mean “usable
network,” “usable local reader,” or “usable target effect.” That would violate
G27's truthful-capability invariant.

### Disposition

Reuse the existing `model_selection` object unchanged for model readiness.
Add a separately typed, non-authoritative capability inventory in the G27
amendment. Each entry should identify the capability, its actual source of
truth, status, and the boundary needed to use it. The inventory must be a
projection of installed configuration and accepted runtime checks; model text
cannot promote its status.

The amendment must define whether the inventory belongs in a subordinate G27
discovery manifest or in an explicitly extended builder-session record. It
must not create provider, credential, Run, proposal, approval, or target
authority.

## 2. Pre-proposal research plan and boundary

### Current representation

`proposal-draft-manifest.schema.json` requires:

- `research.summary`;
- `research.citations` with `claim_id`, `source_kind`, `locator`, optional
  `source_sha256`, and `verification`;
- `research.assumptions`; and
- `research.unresolved_questions`.

It also requires `boundary.reference_ids`, `boundary.network`,
`boundary.credential_reference`, and `boundary.effects`. The existing network
enum is `local_only` or `configured_provider_only`, and the only permitted
effect is currently `write_workpad`.

These are useful vocabulary and evidence shapes. However, the manifest is a
completed proposal-draft result: `research` is a required object with a
summary, and `build.status` describes the builder execution. Nothing says
“this is a proposed research plan that has not run yet,” identifies planned
sources, or records the preflight decision before a source is contacted.

### Disposition

Reuse the existing `boundary` vocabulary and citation shape where the meaning
is genuinely the same. Add a G27-owned pre-proposal research-plan shape with
an explicit lifecycle such as `not_started`, `planned`, `approved`,
`running`, `completed`, `cancelled`, `unavailable`, or `blocked` only if the
amendment confirms those states are needed.

The plan must separately identify proposed sources, whether each is local or
external, the selected network/privacy boundary, budget/time limits, and the
evidence expected from execution. A suggested external source is not a
network grant. A plan cannot invoke a provider, resolve a credential, or read
the target until the existing configured policy and operator-authority path
allows it.

The completed `research` object must remain the evidence result. It should not
be retrofitted to mean both an unapproved plan and completed research.

## 3. Dynamic question rounds and the five-question ceiling

### Current representation

`gig-builder-session.schema.json` requires a `questions` array with
`minItems: 1`, unique items, and items from G22's typed question definition.
The referenced question shape already requires:

- stable `question_id`;
- `answer_type` of `text`, `choice`, `multiselect`, or `confirmation`;
- `required`;
- typed `options`;
- `depends_on`;
- `rationale`; and
- `provenance`.

Answers are separately typed by `question_id`, `answer_type`, `value`, and
`answered_at`. This is strong reuse for model-selected questions. The schema
does not impose `maxItems: 5`, does not define zero questions as a valid
already-sufficient round, and does not identify which questions belong to a
G27 direction round versus a generic G26 clarification sequence.

### Disposition

The existing typed question and answer definitions should be reused. An
additive G27 round contract is required to make the ceiling authoritative:

- each discovery round has zero to five accepted direction questions;
- the ceiling applies after model retries, revision, replay, and event
  reconciliation, not only to the initial response;
- question IDs remain stable and answer IDs continue to bind to them;
- dependency references must resolve within the same round or an explicitly
  cited prior durable round; and
- the model chooses content and answer shape, while GigAI validates the
  supported types and count.

Adding `maxItems: 5` directly to G26's generic `questions` array would be a
semantic decision about every G26 session. The amendment must instead decide
whether to add a G27 `discovery_round`/`direction_questions` field or to
version the existing array with an explicit round kind. It must preserve
G26's existing meaning unless that change is deliberately accepted.

The contract must allow fewer than five questions. Five is a ceiling, not a
target, and a model must be able to conclude that the available context is
sufficient without inventing questions.

## 4. Stable Gig definition versus changing Run input

### Current representation

The builder session has an operator-authored `intent`, exact local
`references`, questions, answers, and a subordinate `draft` artifact. The
proposal draft has a normalized `proposal_artifact`, research, assumptions,
and unresolved questions. These fields can carry content, but neither schema
assigns ownership to a reusable Gig definition or to one future Run.

The current shapes therefore cannot answer, in a schema-validated way:

- which Goals, constraints, outputs, and stable references are part of the
  approved Gig version;
- which values are expected to change for each Run;
- which original values must remain visible during improvement; or
- whether an answer edits the definition or only supplies this Run's input.

Treating `intent`, `references`, or `proposal_artifact` as the definition by
convention would be semantic overloading. Treating all later answers as Run
input would be equally unsafe because some answers establish reusable Gig
behavior.

### Disposition

An additive typed distinction is required. The amendment should define a
subordinate definition/input manifest or equivalent fields with explicit
ownership, for example:

```text
stable_definition -> reusable Goals, constraints, outputs, stable references
run_input         -> per-execution context and changing operator values
```

The final names and exact fields belong in the amendment. The contract must
bind both sides to the same Gig/session identity, preserve original values and
digests during revision, and refuse a proposal that silently moves a stable
definition value into Run input or vice versa.

This distinction must not create a Run, mutate a target, advance a version,
or replace `gig-proposal` as the approval authority.

## 5. Bounded Run summaries and G20 improve

### Current representation

G20's accepted `learning-record.schema.json` already provides the correct
evidence grain:

- one `learning_id` per normalized observation;
- a `run` or `goal` subject;
- `active_version` and `active_pointer_sha256` captured at observation time;
- exactly one typed source artifact with exact `source_id` and artifact
  reference; and
- one provenance value: `observed_outcome`, `evaluator_judgment`,
  `operator_feedback`, or `accepted_outcome`.

G20's `improvement-manifest.schema.json` cites one or more learning-record
IDs and carries separate evidence-sufficiency and quality-gate results. Its
quality gate contains baseline/candidate identities, corpus splits, and a
final-held-out result. G20 runtime validation requires the cited records and
recomputes the gates before approval.

### Disposition

G27 must reuse G20 learning records and must not create a second evidence
record or bypass either G20 gate. An improve session may select a bounded set
of learning-record IDs and present their exact source identities and
provenance to the model as context.

An additive G27 context-selection shape is required if the current builder
session cannot represent all of the following without free-text inference:

- selected learning-record IDs;
- the active-version identity against which each was observed;
- a bounded summary projection suitable for model context;
- exact source/artifact digests; and
- the omission of unselected Runs, raw Run databases, credentials, hidden
  context, and unbounded transcripts.

The summary is a presentation/input projection, not a replacement for the
learning record. Raw Run evidence must remain behind exact cited artifacts.
The resulting improvement still enters G20's existing `learning_record_ids`,
evidence gate, quality gate, `kind: "improve"` proposal path, and approval
lifecycle. Model confidence, operator wording, or a browser session cannot
make an improve proposal approval-ready by itself.

## Proposed amendment boundary

The impact analysis supports an additive amendment, with the following
minimum obligations:

1. Preserve all twenty-nine existing schema bytes, hashes, vectors, registry
   entries, and installed replay behavior.
2. Keep `gig-proposal` as the sole proposal identity, approval, and active
   version authority.
3. Reuse G22's typed question and answer definitions rather than inventing a
   second question vocabulary.
4. Reuse G26's model-selection readiness meanings for model targets, while
   adding a separate truthful capability inventory for non-model capabilities.
5. Add a pre-execution research-plan boundary without changing completed
   `proposal-draft-manifest.research` into a plan by inference.
6. Make the G27 direction-question ceiling mechanically enforceable as zero
   to five per round, including replay and recovery paths.
7. Add an explicit stable-definition/Run-input ownership boundary with
   immutable identity and digest checks.
8. Reuse G20 learning-record citations and gates; add only the minimum
   bounded-context selection needed to transport selected Run evidence into
   an improve session.
9. Define state ownership, interruption recovery, duplicate/replay behavior,
   and operator approval without making browser or model state authoritative.
10. Include schema vectors, semantic refusal fixtures, mutation tests,
    installed-schema verification, and canonical hash preservation before any
    runtime implementation begins.

The amendment should decide whether these concepts fit in one subordinate
`gig-discovery`/definition manifest or require two subordinate resources. It
must defend that choice against the semantic boundaries above. It must not
silently extend `gig-builder-session`, `proposal-draft-manifest`,
`learning-record`, or `improvement-manifest` merely because those resources
are structurally convenient.

## Stop boundary

Stop before G27 runtime or schema implementation if the accepted amendment
cannot demonstrate, with direct vectors and refusal fixtures, that:

- a model cannot promote a capability from detected to usable;
- a planned research source cannot grant network or credential authority;
- a sixth direction question cannot be persisted through replay or recovery;
- a stable Gig-definition value cannot silently become Run input;
- an improve session cannot inject unselected or unbounded Run evidence; and
- G20's evidence and quality gates remain mandatory at approval time.

This record is complete when the amendment decision is accepted and citable.
Until then, G27 remains a reviewed design with dynamic-question intent, not
an implementation-ready runtime contract.


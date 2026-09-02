# G43 — Adaptive Review Profiles and Sealed Run Plans

**Version:** v0.1.7
**Status:** Complete — implementation and closeout evidence accepted
**Depends on:** G40 and G41 complete and ratified; G42 complete and ratified;
existing Gig/version, capability, journal, workpad, review, and Run authorities
**Unblocks:** G44 clone/create-from, G45 reference and research workflows, and
G46 domain Gig execution

## Outcome

G43 makes review planning adaptive without making orchestration implicit. Given
an approved Gig version, declared task inputs, reference artifacts, policy, and
usable configured runtimes, GigAI classifies the work, selects one explicit
review profile, assigns typed roles to explicit model targets, and seals a
bounded Run Plan before execution.

The resulting path is:

```text
declared task and inputs
  -> typed classification
  -> explicit profile selection
  -> role and model assignment
  -> bounded Review -> Verify -> Adjudicate -> Resolve plan
  -> human override or explicit abstention where required
  -> sealed Run Plan
  -> existing Run preparation and execution authority
```

G43 owns planning and routing evidence. It does not become a second Run
authority, approve a Gig, choose an active version, install capabilities,
silently select a fallback model, or turn a model's prose into authority.

## Product boundary

G43 is consumed through the existing CLI, JSON, Markdown, and durable local
evidence surfaces. It must not depend on hidden transcript capture or an
implicit agent conversation.

The invoking agent may submit a bounded task description or proposal input
through G40's typed invocation seam. GigAI validates the input, performs the
classification and planning steps, and records the resulting evidence. The
agent cannot choose an unapproved Gig version, self-authorize a Run, alter a
sealed plan, or supply authority through hidden prompt content.

G43 is not a general-purpose scheduler or autonomous agent swarm. Every plan
has a finite participant set, fixed role assignments, fixed ceilings, explicit
stopping rules, and a terminal outcome.

## Contract gate

Before implementation, the following decisions must be reviewed and accepted
in G43 contract evidence:

1. the classification vocabulary, input precedence, confidence/ambiguity
   rules, and deterministic classification fixtures;
2. the finite profile catalog, profile versions, default profile selection,
   opt-in rules, and hard ceilings;
3. the typed participant and role vocabulary, including reviewer, verifier,
   adjudicator, and resolver behavior;
4. the model-target selection rules using G40's detected/configured/
   compatible/authenticated/verified/usable/selected states;
5. the exact Review, Verify, Adjudicate, and Resolve routing and terminal
   outcomes;
6. human override, abstention, disagreement, missing-ground-truth, and
   provider-unavailable behavior;
7. the Run-Plan artifact schema, canonicalization, digest, identity, and
   relationship to the existing Run manifest;
8. plan sealing, replay, idempotency, cancellation, and mismatch behavior;
9. usage, cost, wall-time, participant, call, pass, and loop accounting; and
10. the acceptance matrix, deterministic fixtures, provider opt-in evidence,
    portability evidence, and UAT procedure.

No implementation is accepted until every item has executable or reviewable
contract evidence.

## Inputs and authority

G43 accepts only explicitly identified inputs:

- one approved Gig ID and immutable approved version;
- the approved Gig's Goal Graph, review contract, capability requirements,
  input contract, and output contract;
- an explicit task or invocation payload bounded by G40's envelope;
- explicitly supplied or already-authorized reference and input artifacts;
- current configuration and effect policy;
- a G40 discovery snapshot and configured target records; and
- an operator-selected profile or a permitted automatic profile choice.

The active-version pointer and committed journal remain authoritative for the
approved Gig version. The workpad and existing Run preparation path remain
authoritative for lifecycle and execution. G43 reads those authorities and
records references to them; it does not copy or replace them.

No ambient repository scan, surrounding transcript, hidden prompt, provider
response, or model-generated instruction is an implicit G43 input. A missing,
changed, malformed, or unauthorized input blocks planning with a typed
diagnostic.

## Classification contract

Classification is a bounded, typed decision that selects a planning profile;
it does not approve the task or infer permission to access data.

The initial task classes are:

- `planning` — produce or inspect a bounded plan;
- `research` — gather and reconcile declared evidence;
- `fact_check` — verify claims against declared references;
- `document_review` — review a document against stated requirements;
- `code_review` — inspect code or repository artifacts against stated
  requirements; and
- `comparison` — compare explicitly named compatible artifacts or Runs.

The initial artifact classes are:

- `text`;
- `code`;
- `structured_data`;
- `mixed`; and
- `unknown`.

Gig contract declarations and explicit operator inputs take precedence over
classification heuristics. Classification may use bounded metadata and
declared artifact schemas, but it may not crawl arbitrary files or read the
ambient conversation.

The classification result records:

- classifier version;
- task and artifact class;
- confidence or explicit `ambiguous` result;
- the input identities considered;
- a bounded explanation suitable for evidence; and
- the selected profile or the typed reason that selection is blocked.

An unsupported class, conflicting declarations, low confidence, unknown
artifact type, or missing required input produces `classification_ambiguous`
or `classification_unsupported`. It does not silently choose the standard or
deep profile. An operator may resolve the condition through an explicit
override, which records the actor, selected class, reason, and inputs.

## Profile catalog

G43 defines a finite versioned profile catalog. A profile is data, not a prompt
or an unbounded instruction. Each profile specifies participant limits, role
counts, phase order, stopping rules, and budget ceilings.

The initial profile set is:

| Profile | Intended use | Participants and roles | Max model calls | Max tool calls | Max tokens | Max cost | Review passes | Verify passes | Adjudication loops | Wall time | Parallel goals |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `focused@1` | Clear, bounded task with low ambiguity | 1: reviewer + verifier, serial and non-independent | 2 | 0 | 8,000 | USD 0.50 | 1 | 1 | 0 | 5 minutes | 1 |
| `standard@1` | Normal review with independent checking | 2 reviewers + 1 verifier + 1 adjudicator | 12 | 0 | 30,000 | USD 3.00 | 2 | 1 | 1 | 10 minutes | 1 |
| `deep@1` | Higher-risk or complex task; explicit opt-in | 3 reviewers + 1 verifier + 1 adjudicator | 18 | 0 | 60,000 | USD 8.00 | 3 | 2 | 2 | 20 minutes | 1 |
| `var@1` | Bounded disagreement and verification loop; explicit opt-in | 3 reviewers + 2 verifiers + 1 adjudicator | 24 | 0 | 100,000 | USD 15.00 | 3 | 2 | 2 | 30 minutes | 1 |

The profile catalog is an implementation baseline, not permission to exceed
the ceilings. Every plan records the selected profile version and all actual
limits. A project policy may reduce these limits but may not increase the
G43 maximum envelope without a reviewed contract amendment.

The global maximum envelope is six participants, twenty-four model calls,
thirty-two tool calls, 100,000 total tokens, USD 15.00, three review passes,
two verification passes, two adjudication loops, thirty minutes of wall time,
and one parallel goal. Every profile uses `USD` for its cost ceiling. A
profile whose Gig contract requires tools must declare a tool-call ceiling no
higher than the global maximum; the initial profiles default to zero tool
calls because they operate over already-sealed inputs and references.

The profile table deliberately makes the participant math explicit. Role
overlap is allowed only when stated by the profile and is sequential: the
focused participant may review and then verify, but that verification is not
independent. The standard, deep, and VAR profiles keep reviewer identities
independent from verifier and adjudicator identities. The resolver is a
deterministic GigAI phase in every initial profile and is not an additional
provider participant. A profile may not claim independent corroboration when
its role assignments do not provide it.

The exact initial assignments are:

```text
focused@1:
  p1 = [reviewer, verifier]          # serial; one model, no independence claim

standard@1:
  p1 = [reviewer], p2 = [reviewer]   # independent reviewer groups R1 and R2
  p3 = [verifier]                    # separate verifier group V1
  p4 = [adjudicator]                 # separate adjudicator group A1

deep@1:
  p1 = [reviewer], p2 = [reviewer], p3 = [reviewer]
  p4 = [verifier]                    # separate verifier group V1
  p5 = [adjudicator]                 # separate adjudicator group A1

var@1:
  p1 = [reviewer], p2 = [reviewer], p3 = [reviewer]
  p4 = [verifier], p5 = [verifier]   # independent verifier groups V1 and V2
  p6 = [adjudicator]                 # separate adjudicator group A1
```

The resolver is GigAI-owned and deterministic in all four profiles. A
participant may have more than one role only where the profile states it;
roles execute in listed phase order and never create an independence claim.
Reviewer participants must not share an independence group with one another;
verifiers and adjudicators must not share a reviewer group. The plan records
these group IDs and rejects duplicate IDs or a profile assignment that violates
the table.

`deep` and `var` require explicit operator opt-in or an explicit policy setting
that is itself recorded before sealing. There is no hidden escalation from
`focused` to `standard`, `deep`, or `var`. Reaching a ceiling produces a
bounded terminal result such as `budget_exhausted`, `inconclusive`, or
`blocked`; it never triggers fallback participants or unbounded continuation.

## Budget accounting

G43 reuses the existing common budget algebra:

```text
max_model_calls, max_tool_calls, max_tokens, max_cost, currency,
max_wall_time_ms, max_parallel_goals
```

`max_tokens` is the total of input and output tokens. Before every model call,
GigAI reserves the configured request allowance and refuses to start the call
if the reservation would exceed the token, model-call, cost, or wall-time
ceiling. After the response, GigAI reconciles actual usage and cost. If the
actual response exceeds its reservation or a ceiling, it is retained as
evidence, the plan enters `budget_exhausted`, and no further call starts.

Provider-reported cost is preferred. A configured provider price table may
produce `cost_status: derived`; otherwise a non-null cost ceiling with an
unreported cost blocks the next provider call before it starts and records
`cost_unavailable`. A provider that reports neither token usage nor a cost
cannot be treated as free: the current bounded call may be retained as
`usage_unavailable`, but no additional call is permitted unless the sealed plan
selects the required `usage_unreported_policy`. That field is exactly one of
`block_before_call` or `reserve_remaining_budget`; it is never inferred at
runtime. `block_before_call` refuses the first unaccountable provider call.
`reserve_remaining_budget` reserves all remaining `max_tokens` and all
remaining non-null `max_cost` before that call, records
`usage_unavailable`, and permits no later provider call in that plan. It is a
bounded accounting choice, not a claim that an unreported provider cost was
measured. Tool calls reserve
`max_tool_calls` before invocation and effects are refused when the reserve is
exhausted. Wall time begins when the sealed plan is handed to Run authority;
the deadline is checked before each phase and provider/tool call.

Every phase records requested, reserved, actual, and unreconciled usage. The
terminal evidence distinguishes `budget_exhausted`, `cost_unavailable`,
`usage_unavailable`, `timed_out`, and provider failure.

## Role and model assignment

Participants have typed roles. The initial role vocabulary is:

- `reviewer` — produces an independent review finding or assessment;
- `verifier` — checks claims, findings, references, and evidence;
- `adjudicator` — handles an explicit disagreement or unresolved conflict;
- `resolver` — records the terminal resolution from the accepted evidence; and
- `operator` — a human decision actor, never a provider participant.

The Run Plan records each participant's role, stable participant ID, selected
G40 model-target ID, target configuration identity, provider identity, and
discovery/readiness evidence reference. A model target is eligible only when
GigAI's configured policy says it is usable for that role. `detected`,
`configured`, or `authenticated` alone is insufficient.

G43 must not select a model from prose, accept a provider's replacement target,
or silently switch providers. If an assigned target becomes unavailable
before execution, the plan is blocked or explicitly replanned by the operator;
it is not silently changed. Replanning creates a new plan identity and leaves
the original sealed plan intact.

Role assignment must satisfy the selected Gig's capability and review
contract. Missing capability, unavailable target, incompatible role, or
insufficient budget is a typed refusal before sealing.

## Review routing

The canonical routing is:

```text
Review -> Verify -> Adjudicate -> Resolve
```

Each phase has an explicit entry condition, participant set, input artifact
identities, output schema, stopping rule, and terminal outcomes. A phase may
be marked `not_required` only when the selected Gig contract and profile
explicitly allow it; the plan records the reason.

### Review

Reviewers work independently over the same sealed input references and
declared requirements. Their findings retain separate participant identity,
role, model target, input digests, output artifact identity, and usage. One
reviewer's output is not silently merged into another's.

### Verify

Verification checks the review findings against the declared requirements,
input/reference artifacts, and evidence rules. It records verified,
unverified, contradicted, and blocked claims. Verification cannot erase the
underlying review finding.

### Adjudicate

Adjudication is entered only for an explicit disagreement, unresolved
verification conflict, high-severity condition, or missing-ground-truth case
named by the plan. The disagreement and all contributing findings remain
inspectable. An unresolved model adjudication does not create a winner; it
produces an adjudication input or requests operator adjudication according to
the Gig contract.

### Resolve

Resolve produces the typed terminal decision permitted by the Gig contract:
for example `accepted`, `rejected`, `inconclusive`, `blocked`, or
`operator_required`. It references the findings, verification, and any
adjudication artifacts. It cannot approve a Gig, mutate a target, promote a
Run output, or rewrite prior evidence.

G43 records structured findings and evidence references, not hidden
chain-of-thought or raw private transcripts.

## CLI workflow and stable diagnostics

G43 adds a `run-plan` command group while preserving the existing `gigai plan`
command for Goal Graph projection. The public workflow is:

```text
gigai run-plan create [--target PATH] [--gig GIG_ID]
  [--class CLASS] [--artifact-class CLASS] [--profile PROFILE]
  [--input ARTIFACT] [--json]
gigai run-plan list [--target PATH] [--gig GIG_ID] [--json]
gigai run-plan show PLAN_ID [--target PATH] [--json]
gigai run --plan PLAN_ID --confirm [existing run options]
```

`run-plan create` validates the resolved approved Gig, explicit inputs,
classification, profile, role assignments, capabilities, and budgets, then
seals one plan. `--class`, `--artifact-class`, and `--profile` are explicit
operator selections; omission permits only the bounded deterministic
classification/profile rules in this contract. A model or agent cannot add
fields or change the result after validation.

There is no mutable “current plan” selection. Selecting a plan means naming its
exact `PLAN_ID` with `gigai run --plan PLAN_ID --confirm`. The handoff resolves
the plan, verifies its digest and all source identities, then obtains fresh
direct operator confirmation immediately before Run allocation. `run-plan
show` and `list` are read-only and never allocate a Run.

JSON success uses this stable shape:

```json
{
  "ok": true,
  "plan": {
    "run_plan_id": "run_plan_...",
    "content_sha256": "sha256:...",
    "state": "sealed",
    "gig_id": "gig_...",
    "gig_version": 1,
    "classification": {},
    "profile": {},
    "phases": [],
    "participants": [],
    "budget": {},
    "sealed_sources": []
  },
  "diagnostics": []
}
```

JSON failure uses the existing common error shape:

```json
{
  "ok": false,
  "error": {
    "code": "classification_ambiguous",
    "message": "...",
    "retryable": false,
    "invocation_id": null
  }
}
```

The initial stable diagnostic codes are `run_plan_not_found`,
`run_plan_invalid`, `classification_ambiguous`, `classification_unsupported`,
`profile_not_allowed`, `profile_opt_in_required`, `target_not_usable`,
`capability_missing`, `budget_invalid`, `budget_exhausted`,
`run_plan_input_mismatch`, `run_plan_digest_mismatch`,
`run_plan_consent_mismatch`, `run_plan_already_handed_off`, and
`run_plan_authority_refused`. Human output uses the same code and a concise
next action; it never exposes a traceback as the normal contract.

## Human override and abstention

The operator may explicitly override classification, choose a permitted
profile, assign an eligible target, request operator adjudication, cancel
planning, or accept a bounded refusal. Every override records the actor,
timestamp, prior decision, replacement value, reason, and affected plan
inputs.

An operator override cannot select an unconfigured or unusable target, exceed
the global maximum envelope, alter an approved Gig version, waive a required
capability, or approve its own provider output. An invalid override fails
closed.

G43 abstains when classification is ambiguous, required references are
missing, findings cannot be verified, disagreement remains unresolved, a
budget ceiling is reached, or a required provider/role is unavailable. The
terminal result identifies the next permitted operator action.

## Run-Plan artifact and sealing

G43 introduces one additive durable planning artifact:
`run-plan.schema.json`, with schema ID `urn:gigai:schema:run-plan:1` and
`schema_version: "1.0"`. It is stored at
`run-plans/<run_plan_id>/run-plan.json` in the authoritative workpad. The
artifact is evidence of preparation and is not a replacement for
`run-manifest.schema.json` or Run authority.

The schema has `additionalProperties: false`. Its required top-level fields
are `schema_version`, `run_plan_id`, `plan_version`, `state`, `gig_id`,
`gig_version`, `project_id`, `workpad_locator`, `goal_graph`,
`review_contract`, `classification`, `profile`, `phases`, `participants`,
`inputs`, `capabilities`, `effects`, `budget`, `sealed_sources`,
`created_at`, `sealed_at`, and `sealed_by`. `state` is one of `declared`,
`classified`,
`profile_selected`, `assignments_validated`, `sealed`,
`handed_to_run_authority`, `blocked`, `cancelled`, `rejected`, or
`inconclusive`. `plan_version` and `gig_version` are positive integers;
`project_id`, `gig_id`, and all artifact/authority references use the existing
GigAI identifier and artifact-reference definitions. `workpad_locator` is the
existing registry locator (`registry:project_<project-id>`), not a filesystem
path or new workpad authority. `classification` contains `task_class`,
`artifact_class`, `confidence` (`high`, `medium`, `low`, or `ambiguous`),
`classifier_version`, and the considered input references. `profile` contains
the profile ID, positive profile version, and opt-in record. `phases` is an
ordered array of objects with `phase` (`review`, `verify`, `adjudicate`, or
`resolve`), `sequence`, `required`, `participant_ids`, `input_refs`,
`output_kinds`, `stopping_rule`, and `state`; a `not_required` phase must have
an explicit reason. `participants` has unique IDs and objects with
`roles` (from the typed role vocabulary), `model_target_id`, provider
identity, independence group, and assignment reason. `budget` reuses the
common budget definition; and `sealed_sources` contains only
content-addressed artifact references.

The nested shape is also normative:

- `goal_graph` and `review_contract` are one existing `artifact_ref` each;
  `capabilities` is an object containing one capability-manifest `artifact_ref`
  and a non-empty, unique array of required capability IDs;
- `classification` is an object with required `task_class`, `artifact_class`,
  `confidence`, `classifier_version`, `considered_inputs`, and `reason`;
  `confidence` is `high`, `medium`, `low`, or `ambiguous`, and a low or
  ambiguous result cannot select a profile without an override record;
- `profile` is an object with required `profile_id`, `profile_version`,
  `selection`, `opt_in`, and `usage_unreported_policy`. Initial `profile_id`
  values are `focused`, `standard`, `deep`, and `var`; `selection` is
  `operator` or `deterministic`; `opt_in` is a nullable artifact reference and
  is required for `deep` and `var`;
- `inputs` is a non-empty array of objects with unique `input_id`, `role`,
  `record_ref`, and `snapshot_ref`. `role` is a Gig-declared identifier;
  `record_ref` and `snapshot_ref` are artifact references whose digests must
  agree with the source record. The array order is significant;
- `participants` is an array with unique IDs matching
  `participant_[a-z][a-z0-9_-]{0,63}`. Each object requires non-empty unique
  `roles`, `model_target_id`, `target_configuration_ref`, `provider_id`,
  `discovery_ref`, `independence_group`, and `assignment_reason`. Roles are
  `reviewer`, `verifier`, or `adjudicator`; resolver is the deterministic
  GigAI phase and never appears as a provider participant;
- `phases` is exactly the ordered `review`, `verify`, `adjudicate`, `resolve`
  sequence with `sequence` values 1 through 4. Every object requires `phase`,
  `sequence`, `required`, `participant_ids`, `input_refs`, `output_kinds`,
  `stopping_rule`, and `state`. `state` is `planned` when `required` is true,
  or `not_required` when false; the latter also requires `not_required_reason`.
  Provider participant IDs are forbidden in `resolve`;
- `effects` uses the existing common effect-set definition, `budget` uses the
  existing common budget definition, and `sealed_sources` is a non-empty array
  of artifact references with no duplicate `(path, content_sha256)` pair; and
- `created_at` is an RFC 3339 timestamp. `sealed_at` and `sealed_by` follow
  the null-or-required sealing rule below. No nested object accepts unknown
  fields.

For a pre-seal validation record, `sealed_at` and `sealed_by` are null. A
persisted `sealed`, `handed_to_run_authority`, or terminal plan requires both
values: `sealed_at` is an RFC 3339 timestamp and `sealed_by` is an existing
GigAI actor object. The implementation may keep pre-seal states in memory, but
any persisted state must satisfy this null-or-required rule.

The plan identifier is `run_plan_<canonical-lowercase-uuidv4>`. To derive it,
canonicalize the following identity projection with GigAI's canonical JSON
encoder, hash the bytes with SHA-256, take the first sixteen digest bytes, set
the UUID version nibble to 4 and RFC 4122 variant bits, and prepend
`run_plan_`:

```json
{
  "schema_version": "1.0",
  "gig_id": "...",
  "gig_version": 1,
  "project_id": "...",
  "workpad_locator": "registry:project_...",
  "goal_graph_sha256": "sha256:...",
  "review_contract_sha256": "sha256:...",
  "classification": {},
  "profile_id": "standard",
  "profile_version": 1,
  "phases": [],
  "participants": [],
  "input_refs": [],
  "capability_ids": [],
  "effects": [],
  "budget": {},
  "policy_sha256": "sha256:...",
  "discovery_snapshot_refs": []
}
```

Arrays in the projection are sorted only where their contract is set-like;
phase order and participant order remain significant. `created_at`,
`sealed_at`, `sealed_by`, `state`, actual usage, terminal outcome, and the
artifact's own content digest are outside the identity projection. The
idempotency key is the canonical byte sequence of this projection. Therefore
the same valid request returns the existing sealed plan, while a changed
input, target, policy, profile, assignment, or authority reference produces a
different plan ID or an explicit mismatch refusal.

The complete canonical artifact is hashed separately with SHA-256 after
sealing; its digest is the `content_sha256` in the workpad artifact reference
and is the exact digest used for consent binding. The artifact must not include
a self-referential digest field.

The canonical Run Plan contains:

- schema, plan, and profile versions;
- plan identity and canonical content digest;
- Gig ID, approved Gig version, Goal Graph identity, and review-contract
  identity;
- resolved project, workpad, and input/reference artifact identities;
- classification result and override evidence, if any;
- ordered phases and stopping rules;
- participant IDs, typed roles, model-target IDs, provider identities, and
  discovery/readiness evidence references;
- capabilities and effect policy required by the plan;
- per-profile and global budget ceilings;
- the plan's required consent scope, without pretending that fresh Run consent
  has already been granted;
- plan state, seal timestamp, sealing actor, and terminal policy; and
- parent evidence identities needed to reconstruct the plan.

Canonicalization must be deterministic. The digest covers every field that
affects routing, identity, authority, inputs, participants, budgets, or
effects. A plan cannot be sealed with unknown fields, duplicate participant
IDs, mutable ambient paths, unbound artifacts, or unresolved target identity.

The state sequence is:

```text
declared
  -> classified
  -> profile_selected
  -> assignments_validated
  -> sealed
  -> handed_to_run_authority
```

Terminal preparation outcomes are `blocked`, `cancelled`, `rejected`, and
`inconclusive`. A sealed plan is immutable. Any changed Gig version, input
digest, reference digest, target identity, profile, budget, capability,
consent scope, or policy requires a new plan and a new digest.

Run allocation may consume a sealed plan only after the existing G40 consent
and Run authority checks succeed. The Run path must include the plan artifact
reference in the Run manifest's `sealed_sources` array. Immediately before
allocation, `gigai run --plan PLAN_ID --confirm` creates fresh direct consent
whose scope contains `run_plan_id` and the plan artifact's exact
`content_sha256`, in addition to the resolved project, Gig/version, target
kind, and target observation. The consent record is redeemed before Run ID
allocation and is sealed beside the plan reference under the Run. Missing,
replayed, or mismatched plan consent refuses before allocation.

G43 does not allocate a Run merely by classifying or sealing a plan. A Run
must record the exact plan identity and digest it consumed; a mismatch refuses
before execution. This is an additive amendment to G40's consent scope and
Run-manifest `sealed_sources` handling; it does not create a second consent or
Run authority.

Repeated planning with the same canonical inputs, policy, profile, target
assignments, and source identities returns the existing plan identity. A
changed input, configuration, discovery snapshot, or policy refuses or
creates a new plan according to the explicit operator request. It never
mutates the earlier plan.

## Bridge to the existing review loop

G43 prepares the routing and identities before a `run_id` exists. The existing
review-loop contract requires a `run_id`, so G43 does not create a review loop
record during planning. At Run handoff, the existing Run authority allocates
the `run_id`, seals the Run manifest with the G43 plan reference in
`sealed_sources`, and then materializes the review-loop record under that Run.

The bridge is deterministic:

| G43 phase | Existing Run-scoped artifact | Required linkage |
|---|---|---|
| Plan inputs and classification | Review Bundle/reference artifacts | exact bundle and reference IDs/digests in the plan and Run sources |
| Review | `trace.schema.json` and one or more `finding.schema.json` artifacts | `run_id`, `gig_id`, bundle ID, contract ID, participant/model evidence |
| Verify | `verification-record.schema.json`, referenced by `review-loop.schema.json` v1.1 and the review report | source finding IDs, per-finding verification status, evidence refs, verifier participant/model, and Run ID |
| Adjudicate | `adjudication.schema.json` and adjudication input when required | all disagreeing finding IDs, actor/participant identity, Run ID, no silent winner |
| Resolve | terminal review report and optional feedback/addressed artifact | report/finding/adjudication IDs, terminal decision, and Run ID |
| Entire loop | `review-loop.schema.json` | loop ID, Run ID, Gig ID, bundle ID, contract ID, stage sequence, and artifact IDs |

The plan's phase templates become these Run-scoped artifact records only after
the Run ID and sealed input references are known. The implementation must
reuse the existing `finding`, `trace`, `report`, `adjudication`, `feedback`,
`addressed`, and `review-loop` schemas and validators. It may add plan and
participant linkage only through reviewed additive schema changes; it may not
replace the existing artifacts with a private G43 format.

Verification is not a finding mutation. G43 adds the strict run-scoped
`verification-record.schema.json` with schema ID
`urn:gigai:schema:verification-record:1`, schema version `1.0`, and path
`runs/<run_id>/review/verification/verification_<uuidv4>.json`. Its required
fields are `schema_version`, `verification_id`, `run_id`, `gig_id`, `bundle_id`,
`contract_id`, `verifier_participant_id`, `verifier_target_id`,
`source_finding_ids`, `outcomes`, and `created_at`. `verification_id` matches
`verification_<uuidv4>`; `source_finding_ids` is non-empty and unique; each
outcome requires one source `finding_id`, `status`, non-empty `evidence_refs`,
and `reason`. Every evidence reference is an existing `artifact_ref`. Status is
exactly `verified`, `unverified`, `contradicted`, or
`blocked`. A source finding is never deleted or overwritten.

G43 also adds `review-loop.schema.json` version `1.1` with optional unique
`verification_ids`, and `report.schema.json` version `1.1` with optional
unique `verification_ids`. A G43-created loop and terminal report must carry
the IDs for every verification record; older v1.0 artifacts remain readable
but cannot satisfy a G43 Run. The G43 semantic validator requires that every
verification outcome names a finding already present in the loop, and that a
report/loop reference resolves to the exact verification-record bytes. This is
an additive reviewed schema amendment, not an unvalidated trace convention.

The existing finding, report, and adjudication schemas do not all carry a
`run_id` field. Their Run relationship is established by the Run-scoped
artifact path, the trace/loop references where present, and the enclosing
`review-loop.schema.json` record. G43 must not add a parallel unvalidated
Run-link field merely to duplicate that relationship.

If the Run cannot allocate, no review-loop artifacts are created. If a phase
cannot continue, the loop preserves all artifacts already written and records
the existing `blocked` or `unanswerable` state with the G43 terminal reason.
The review loop remains Run-scoped evidence; it is not a second active-version,
approval, or plan-selection authority.

## State and authority invariants

1. The active-version pointer remains the sole authority for the approved Gig
   version selected by a plan.
2. The project registry remains the target-binding authority; a plan cannot
   establish or change a project binding.
3. The capability manifest and effect policy remain authoritative for allowed
   effects; a requested role cannot grant itself a capability.
4. A discovery snapshot is evidence of runtime facts, not proof of approval,
   usability for every Gig, or permission to execute.
5. A sealed Run Plan is immutable preparation evidence, not a Run, approval,
   active pointer, or review verdict.
6. Existing review findings, adjudication inputs, reports, journal entries,
   and Run artifacts remain individually inspectable and content-addressed.
7. Provider failure, authentication failure, timeout, disagreement, and
   budget exhaustion remain distinct terminal evidence; none triggers silent
   fallback.
8. No model output can alter its own role, target, budget, profile, consent,
   or authority references.
9. Cancellation and refusal happen before any effect not authorized by the
   sealed plan and existing Run consent.
10. Replaying a plan or Run request is idempotent and cannot allocate a second
    Run for the same sealed execution identity.

## Acceptance evidence

G43 is complete only when the following evidence is reviewed:

- deterministic classification for every initial task/artifact class;
- ambiguous, conflicting, unsupported, malformed, and missing-input refusal;
- explicit operator classification and profile override;
- each initial profile's participant, phase, call, pass, loop, wall-time, and
  usage ceilings;
- no escalation, fallback, hidden participant, or provider substitution when
  a ceiling or failure is reached;
- deterministic role/model assignment from usable G40 targets;
- detected/configured/authenticated-but-unusable and provider-unavailable
  cases remaining distinct;
- independent review findings retained through verification;
- disagreement retained through adjudication or explicit operator escalation;
- unresolved and missing-ground-truth cases abstaining rather than selecting
  a fabricated winner;
- valid and invalid human overrides;
- canonical Run-Plan schema validation, digest vectors, and reconstruction;
- changed input, target, policy, capability, consent, or Gig/version
  mismatch refusing before Run allocation;
- sealed-plan replay and idempotency;
- exact handoff into the existing Run authority with the plan identity bound;
- cancellation, timeout, provider failure, and cleanup evidence;
- CLI, JSON, Markdown, and durable evidence projections with no hidden
  transcript or credential material; and
- human UAT for a catalog Gig from setup through plan inspection and approved
  Run preparation.

Provider-backed evidence must identify the provider, model target, consent,
usage, and failure conditions. Deterministic fixtures may prove schema,
classification, refusal, identity, and budget behavior, but do not prove live
provider quality or authenticated usability.

## Out of scope

- Gig definition authoring, catalog installation, package identity, or project
  binding owned by G41/G42;
- clone/create-from and lineage owned by G44;
- repository crawling, reference synchronization, research reports, or domain
  Gig definitions owned by G45/G46;
- background workers, daemons, scheduling, retries, or unattended Runs;
- automatic credential acquisition, provider fallback, or network permission;
- changing the active Gig version, approving a proposal, installing a
  capability, mutating a target, or adjudicating a final business decision;
- hidden prompt injection, transcript import, or exposure of chain-of-thought;
- a universal model leaderboard or quality claim based on deterministic
  fixtures; and

## Stop conditions

G43 must stop and remain incomplete if:

- a classifier silently chooses a profile for ambiguous input;
- a plan can include an unknown, unusable, or provider-substituted target;
- a budget ceiling can be exceeded or silently reset;
- disagreement can be erased or resolved without recorded evidence;
- a sealed plan can be edited or replayed against changed inputs;
- Run allocation can occur without the existing consent and authority checks;
- a model or agent can change its own plan or approval state;
- a provider failure causes hidden fallback or retry; or
- the implementation creates a competing authority for Gig versions, targets,
  capabilities, Runs, or review decisions.

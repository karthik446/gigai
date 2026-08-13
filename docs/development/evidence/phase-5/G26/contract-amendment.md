# G26 Model-Facilitated Gig Builder Contract Amendment

- Status: Accepted additive amendment; runtime implementation may proceed
- Type: Additive serialized-contract and model-readiness amendment for G26
- Depends on: G18 completion audit and terminal handoff, S18-02 CLI
  feasibility decision record, G22 completion audit and terminal handoff, and
  the activated G26 goal contract
- Unblocks: G26 model-facilitated builder implementation
- Baseline: twenty-seven packaged schema resources and their current hashes

## Decision

Add two subordinate resources:

1. `gig-builder-session.schema.json` — the durable state and accounting record
   for one browser-backed Gig-builder session; and
2. `proposal-draft-manifest.schema.json` — the reviewable, content-addressed
   draft and research manifest produced by a builder session.

The existing `gig-proposal.schema.json` remains the sole proposal identity,
approval, and active-version authority. Neither new resource allocates a
`gp_` identity, approves itself, advances `active-gig-version.json`, starts a
Run, authorizes a target effect, or installs a capability.

The draft manifest is subordinate evidence. It may be referenced by the
ordinary `gig-proposal` through the existing `creation_manifest` slot when the
proposal is assembled, but it cannot become a parallel proposal record. A
draft may be revised by creating a new session revision and draft digest that
retains its parent; prior draft bytes remain immutable evidence.

This amendment settles the contract only. It adds no model adapter, executable
discovery implementation, provider call, browser route, proposal builder,
approval behavior, or active-version behavior.

## Resource 1: `gig-builder-session.schema.json`

The builder session is schema version `1.0`, rejects unknown fields, and
requires:

| Field | Contract |
| --- | --- |
| `schema_version` | Exact string `1.0`. |
| `record_version` | Positive session-record revision. |
| `session_id` | Existing `session_` UUIDv4 identity; never reused. |
| `project_id` / `gig_id` | Existing bound project and Gig identities. |
| `request_kind` | `create` or `improve`; G26 create uses `create`. |
| `state` | One exact state from the lifecycle below. |
| `revision` / `parent_revision` | Monotonic browser/session revision and optional parent. |
| `round` / `max_rounds` | Bounded clarification round and cap. |
| `intent` | One operator-authored main-drive answer, with exact byte digest and timestamp. |
| `references` | Zero or more exact local reference decisions using existing reference identity/digest shape. |
| `questions` / `answers` | Typed model-authored questions and operator answers with provenance and parentage. |
| `model_selection` | Resolved target identity, adapter, readiness state, and selection actor. |
| `policy` | Explicit network mode, credential-reference name only, output limit, call/token/wall-time budgets, and cancellation policy. |
| `accounting` | Calls, tokens where available, elapsed time, and bounded terminal usage; no secret values. |
| `draft` | Nullable subordinate `proposal-draft-manifest` artifact reference. |
| `terminal_reason` | Nullable before a terminal state; required and share-safe for terminal refusals/failures/cancellation. |
| `created_at` / `updated_at` | RFC 3339 timestamps. |

The `intent` object is the required main-drive answer. It contains the
operator's exact answer digest and bounded text metadata; the text may be
stored only in the local workpad request artifact under the existing local
evidence boundary. It is not provider authorization and cannot by itself
create a proposal.

The `model_selection` object contains:

- `target_name`, `endpoint_name`, `model`, and `adapter` from resolved G18
  configuration;
- `readiness`: exactly `detected`, `configured`, `verified`, `usable`,
  `unavailable`, or `unsupported`;
- `selection_actor`: `operator` only for an explicit model choice; and
- `selection_digest`: digest of the canonical resolved selection.

Display labels such as “Codex” or “Claude” are never model identity. A local
executable candidate may be recorded as `detected` or `unverified` metadata,
but a session may enter `build_requested` only with `usable` readiness.

The `policy` object contains no credential value. It may contain a credential
reference name and provider-bound network decision, but never an API key,
authorization header, cookie, environment value, or secret-manager result.

## Resource 2: `proposal-draft-manifest.schema.json`

The draft manifest is schema version `1.0`, rejects unknown fields, and
requires:

| Field | Contract |
| --- | --- |
| `schema_version` | Exact string `1.0`. |
| `manifest_version` | Positive manifest revision. |
| `manifest_id` | Subordinate `draft_manifest_` UUIDv4 identity; not a proposal identity. |
| `session_id` / `project_id` / `gig_id` | Exact builder-session and Gig binding. |
| `parent_manifest_id` | Nullable prior draft identity for a revision; never a second approval chain. |
| `model_selection` | Exact digest-bound resolved target used for the build. |
| `build` | Explicit build status, accounting, started/completed timestamps, and deterministic/remote mode. |
| `proposal_artifact` | Content-addressed normalized draft artifact; it is not an approved `gig-proposal`. |
| `research` | Typed citations, assumptions, unresolved questions, and claim/evidence relationships. |
| `boundary` | Exact local-reference, network, credential, and effect boundary used during the build. |
| `created_at` / `updated_at` | RFC 3339 timestamps. |

The normalized `proposal_artifact` may contain candidate Gig name, commission,
Goal Graph draft, review contract draft, reference roles, stopping rules,
assumptions, and expected evidence. It must not contain approval state,
`active_version`, target-effect authority, capability installation authority,
Run identity, credential values, or hidden model context.

Each `research` citation contains a claim ID, source kind, exact source
locator, source digest when the source is local or materialized, and a
verification result. A model's unsupported assertion is an unresolved claim,
not evidence. Free-text explanation cannot replace a citation or authorize a
proposal field.

The draft manifest's `build.status` values are `not_started`, `running`,
`completed`, `cancelled`, `timed_out`, `unavailable`, `malformed`,
`budget_exhausted`, and `failed`. Only `completed` can be presented for
operator review. A completed deterministic fixture must identify its mode as
`deterministic_fixture`; it cannot be reported as production model evidence.

## Model readiness and discovery contract

G26 may inspect `PATH` for candidate local executables without invoking them.
The discovery result is diagnostic metadata, not support evidence. For each
candidate, the readiness matrix is:

| Readiness | Meaning | May build? |
| --- | --- | --- |
| `detected` | Executable path was found; no adapter/auth/output proof yet. | No |
| `configured` | Operator selected a target and its non-secret configuration is valid. | No, until verified |
| `verified` | Adapter, authentication boundary, output contract, timeout/cancellation, and replay checks passed. | Only after usable promotion |
| `usable` | Verified target is eligible for this session under its current policy/budget. | Yes |
| `unavailable` | Required executable/endpoint/credential reference cannot be used now. | No |
| `unsupported` | Candidate exists but no accepted GigAI adapter contract covers it. | No |

This amendment does not advertise Codex CLI or Claude CLI as supported merely
because their executables are installed. S18-02 remains feasibility evidence;
the first G26 implementation target must be selected from a G18 adapter that
has accepted compatibility evidence. Codex/Claude candidates may become a
later usable target after a separate adapter decision and replay evidence.

## Lifecycle and authority contract

The exact session states are:

- `define_intent`: session exists and awaits the required main-drive answer;
- `clarify`: the selected usable model may ask bounded typed follow-ups;
- `build_requested`: the operator explicitly authorized proposal research/build;
- `researching`: bounded model calls and evidence processing are in progress;
- `proposal_draft_ready`: a completed draft manifest is durable and reviewable;
- `operator_review`: the draft is presented for explicit operator decision;
- `revised`: a new draft/session revision exists and retains its parent;
- `approved`: terminal session state after existing proposal approval succeeds;
- `rejected`: terminal operator decision with no active-version advancement;
- `cancelled`: terminal operator/system cancellation before approval;
- `timed_out`: terminal budget/time expiry;
- `unavailable`: terminal inability to use the selected model; and
- `blocked`: terminal unresolved boundary, recovery, or contract failure.

Permitted transitions are:

```text
define_intent      -> clarify | blocked
clarify            -> clarify | build_requested | cancelled | unavailable | blocked
build_requested    -> researching | cancelled | blocked
researching        -> proposal_draft_ready | cancelled | timed_out | unavailable | malformed | budget_exhausted | failed
proposal_draft_ready -> operator_review | failed
operator_review    -> revised | rejected | approved | cancelled
revised            -> clarify | build_requested | operator_review | blocked
```

`approved`, `rejected`, `cancelled`, `timed_out`, `unavailable`, `malformed`,
`budget_exhausted`, `failed`, and `blocked` are terminal. No terminal state
has an implicit retry, fallback, alternate provider, alternate proposal, or
active-version transition. A retry requires a new explicitly identified
session or revision under the accepted recovery rules; it cannot mutate the
old record.

`proposal_draft_ready` does not mean a proposal exists. The ordinary
`gig-proposal` is assembled only after operator review and explicit approval;
the approval handler may then create the proposal identity, attach the draft
manifest, validate it, and invoke the existing approval lifecycle exactly once.

## Amendment invariants

1. All prior twenty-seven schema files remain byte-identical. Their hashes,
   vectors, validators, and installed replay behavior remain unchanged.
2. Exactly the two named G26 resources are added, raising the packaged
   inventory from twenty-seven to twenty-nine.
3. `gig-proposal.schema.json`, `active-gig-version.schema.json`, the workpad
   journal, and G22's existing interview resource retain their meanings. No
   second proposal/version/approval authority is introduced.
4. A model target is usable only after accepted adapter/readiness evidence;
   executable discovery cannot advertise support.
5. The required operator main-drive answer and the explicit `build_requested`
   transition cannot be bypassed by model output or inferred completeness.
6. Every model call is subject to the G18 provider boundary, selected
   references, credential-reference-only policy, cancellation, and fixed
   budgets. No secret value enters a session, draft, citation, or proposal.
7. A draft manifest cannot authorize target effects, capabilities, Runs,
   provider fallback, approval, or active-version advancement.
8. A completed draft must identify citations, assumptions, unresolved claims,
   model identity, mode, accounting, and exact boundary before review.
9. Revisions preserve parent identity and prior bytes. Approval is idempotent
   and advances the existing active version at most once.
10. All terminal and malformed paths fail closed and have no implicit retry or
    fallback edge.

## Verification obligations

The amendment package must contain exactly:

1. `src/gigai/schemas/gig-builder-session.schema.json`;
2. `src/gigai/schemas/proposal-draft-manifest.schema.json`;
3. two new `SHA256SUMS` entries preserving all prior twenty-seven entries;
4. installed schema verifier and registry/validator inventory update from 27
   to 29;
5. schema vectors for each lifecycle state, readiness state, revision, draft
   binding, citation shape, boundary, and terminal rule;
6. model discovery fixtures proving candidate detection does not invoke a
   process and does not claim support;
7. fail-closed fixtures for no model, unsupported/detected-only model,
   credential-shaped fields, unselected references, malformed output, stale
   browser event, budget exhaustion, cancellation, and implicit approval;
8. canonical vectors proving the existing twenty-seven resources are
   unchanged; and
9. no runtime provider, browser, proposal, approval, active-version, target,
   or Run behavior in the amendment-only change.

Acceptance authorizes G26 implementation against this boundary; it does not
claim a model adapter is supported, create a proposal, or mark G26 complete.

## Evidence references

- [G26 goal contract](../../../goals/phase-5/G26-model-facilitated-gig-builder.md)
- [G18 completion audit](../../phase-3/G18/completion-audit.md)
- [G18 terminal handoff](../../phase-3/G18/terminal-handoff.md)
- [S18-02 CLI feasibility decision](../../phase-3/S18-02/decision-record.md)
- [G22 completion audit](../../phase-2/G22/completion-audit.md)
- [G22 terminal handoff](../../phase-2/G22/terminal-handoff.md)
- [G24 UAT goal](../../../goals/phase-5/G24-human-uat-and-dogfooding.md)

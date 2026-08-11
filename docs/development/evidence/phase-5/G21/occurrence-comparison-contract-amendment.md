# G21 Occurrence and Comparison Contract Amendment

- Status: Accepted additive amendment for G21; no runtime implementation
- Type: Additive serialized-contract amendment for G21
- Depends on: G13 and G14 completion audits and terminal handoffs, G15 and
  G16 review-substrate evidence, and the G20 completion audit and terminal
  handoff
- Unblocks: G21 manual occurrence and comparison implementation
- Baseline: twenty-five packaged schema resources and their current hashes

## Decision

Add exactly two dedicated packaged resources:

1. `gig-occurrence.schema.json` — the identity and lifecycle record for one
   operator-triggered occurrence of one approved Gig version; and
2. `gig-comparison.schema.json` — derived evidence comparing two explicitly
   named occurrences and their sealed Run outputs.

The resources are dedicated because an occurrence binds an external slot to a
fresh Run, while a comparison is derived evidence between two Runs. Neither
resource allocates a proposal identity, approves a Gig, advances
`active-gig-version.json`, authorizes a target effect, or creates a scheduler.
The packaged inventory becomes exactly twenty-seven resources. All prior
twenty-five schema files, hashes, canonical vectors, and meanings remain
unchanged.

The existing G13/G14 Run path remains the only Run authority. G21 supplies an
explicit occurrence context and verifies the sealed Review Bundle snapshot
before calling that path; it does not copy or reinterpret Run preparation.
The active-version pointer and workpad journal remain the only authority for
the approved Gig version.

## Occurrence resource

`gig-occurrence.schema.json` uses schema version `1.0`, rejects unknown
fields, and requires these fields:

| Field | Contract |
| --- | --- |
| `schema_version` | Exact string `1.0`. |
| `occurrence_version` | Positive serialized-record revision. |
| `occurrence_id` | Unique `occurrence_` plus lowercase UUIDv4 identity. |
| `project_id` / `gig_id` | Existing project and Gig identities. |
| `gig_version` | The explicitly selected approved Gig version. |
| `cadence` | Exactly `daily`, `weekly`, or `monthly`; descriptive only. |
| `occurrence_key` | Canonical lowercase external slot key, unique within `(gig_id, cadence)`. |
| `trigger_actor` | Existing actor shape for the declaration/requesting actor; v1 accepts only an explicit operator or GigAI reconciliation actor. |
| `outcome_actor` | Nullable until a refusal/outcome terminal is recorded; then the explicit operator or GigAI actor responsible for that terminal decision. |
| `scheduled_for` | Optional RFC 3339 timestamp supplied by the caller; G21 does not calculate it. |
| `snapshot` | Object containing the exact Review Bundle `bundle_id`, `bundle_version`, artifact reference, and reference-set digest. |
| `prior_occurrence_id` | Nullable explicit prior occurrence identity; never inferred from chronology. |
| `run_id` | Nullable fresh Run identity, populated only after Run preparation succeeds. |
| `state` | One of the exact states in the lifecycle contract below. |
| `outcome` | Nullable explicit Run/occurrence outcome; required when the occurrence is terminal. |
| `comparison` | Nullable artifact reference to the derived comparison record. |
| `reason` | Nullable bounded share-safe reason; required for refusal and missed-state outcomes. |
| `created_at` / `updated_at` | RFC 3339 publication timestamps. |

The occurrence identity is the tuple `(gig_id, cadence, occurrence_key)`.
The same tuple cannot create a second occurrence or allocate a second Run.
The selected `gig_version`, snapshot artifact, and prior occurrence are
immutable after the first durable declaration. A changed replay request is a
deterministic refusal, not a new identity.

The `snapshot` object is not a copy of private reference bytes. It contains
the Review Bundle identity and an artifact reference to the canonical Bundle
manifest, plus a digest of the ordered reference identity set. The Bundle
validator rechecks the manifest, every named object, path safety, symlink
policy, exact imported bytes, and redaction policy before Run preparation.

## Comparison resource

`gig-comparison.schema.json` uses schema version `1.0`, rejects unknown
fields, and requires:

| Field | Contract |
| --- | --- |
| `schema_version` | Exact string `1.0`. |
| `comparison_version` | Positive serialized-record revision. |
| `comparison_id` | Unique `comparison_` plus lowercase UUIDv4 identity. |
| `project_id` / `gig_id` | Existing bound identities. |
| `current_occurrence_id` / `prior_occurrence_id` | The two explicit occurrence identities. |
| `current_run_id` / `prior_run_id` | The two distinct sealed Run identities. |
| `current_gig_version` / `prior_gig_version` | The versions recorded by those Runs. |
| `current_snapshot` / `prior_snapshot` | Exact snapshot artifact references copied from the occurrences. |
| `current_output` / `prior_output` | Exact nullable output artifact references; missing output prevents a changed/unchanged result. |
| `current_goal_graph` / `prior_goal_graph` | Exact Goal Graph artifact identities from both Run manifests. |
| `current_review_contracts` / `prior_review_contracts` | Complete ordered Review Contract identity sets from both sealed Run inputs. |
| `method_id` / `method_version` | Deterministic comparison method identity and version. |
| `result` | Exactly `changed`, `unchanged`, `incomparable`, or `blocked`. |
| `reason` | Nullable bounded reason; required for `incomparable` and `blocked`. |
| `evidence` | One or more exact artifact references supporting the derived result. |
| `selected_winner` | Required and permanently `null`; comparison never selects a winner. |
| `created_at` / `updated_at` | RFC 3339 publication timestamps. |

The comparison is valid only when both occurrence records, both Run
manifests, both snapshots, both Goal Graph identities, both Review Contract
identities, and the required output artifacts independently revalidate. A
digest-only record without the corresponding occurrence and Run identities is
insufficient. The comparison is immutable derived evidence and cannot rewrite
either occurrence, Run, output, active Gig version, or G20 learning record.

## Lifecycle and terminal semantics

The exact occurrence states are:

- `declared`: the unique slot, selected version, actor, and snapshot are
  durably recorded;
- `triggered`: the operator explicitly requested preparation;
- `snapshot_verified`: the Review Bundle and exact reference set revalidated;
- `run_prepared`: the fresh Run was sealed through the existing G13 path;
- `run_terminal`: the linked Run has an explicit terminal result;
- `compared`: a requested valid comparison was published; and
- `closed`: the occurrence is terminally closed.

The refusal/outcome states are `blocked`, `skipped`, `cancelled`,
`unavailable`, `failed`, and `missed`. They are terminal and have no outgoing
transition. `closed` is also terminal. The only nonterminal transitions are:

```text
declared          -> triggered | skipped | cancelled | unavailable | failed | missed
triggered         -> snapshot_verified | blocked | skipped | cancelled | unavailable | failed | missed
snapshot_verified -> run_prepared | blocked | cancelled | unavailable | failed
run_prepared      -> run_terminal | blocked | failed | cancelled
run_terminal      -> compared | closed | blocked
compared          -> closed
```

There is no transition to retry, fallback, catch-up, another Run, another
provider, another Gig version, or a background scheduler. An explicit replay
of a terminal occurrence returns its existing record. A comparison is
requested separately and only after both inputs pass the compatibility gate.

## Amendment invariants

1. All prior twenty-five schema files remain byte-identical. Their
   `SHA256SUMS` entries, canonical vectors, validators, and installed replay
   behavior remain unchanged.
2. Exactly `gig-occurrence.schema.json` and `gig-comparison.schema.json` are
   added, raising the packaged inventory from 25 to 27.
3. Existing Run, Review Bundle, Review Contract, active-version, proposal,
   G20 learning, G19 target-effect, and G23 portability meanings do not
   change by inference.
4. Occurrence identity is unique within a Gig/cadence/slot tuple and replay
   cannot allocate a second occurrence or Run.
5. Every occurrence snapshot is a digest-bound canonical Review Bundle whose
   exact references are revalidated before Run preparation.
6. Every successful occurrence links exactly one fresh Run and preserves the
selected approved version and sealed Goal Graph; it never advances the
active pointer.

### Corrective contract clarification (accepted 2026-08-11)

The implementation review identified three invariants that must be enforced
both in the runtime and in the serialized contract:

- refusal/outcome states (`blocked`, `skipped`, `cancelled`, `unavailable`,
  `failed`, and `missed`) require a share-safe `reason`, matching `outcome`,
  and non-null `outcome_actor`;
- a prepared occurrence cannot be manually terminalized while its linked Run
  is still in flight or has completed without reconciliation; and
- `compared` requires a non-null comparison artifact reference.

The declaration-time `trigger_actor` remains immutable; `outcome_actor` records
who or what made the later terminal decision. These are additive constraints
within the accepted `gig-occurrence` resource and do not create a new authority
or schema resource. The `mark_occurrence` API and CLI require this actor from
the caller; they do not synthesize a human identity when it is omitted.
7. A comparison binds two explicit occurrences, distinct Runs, versions,
   snapshots, Goal Graphs, Review Contracts, outputs, and method identity.
8. `selected_winner` is always `null`; comparisons are not adjudication,
   consensus, improvement approval, or target authority.
9. Missed, skipped, cancelled, unavailable, failed, incomparable, and blocked
   outcomes require explicit share-safe reasons where specified and never
   silently become successful Runs.
10. G21 has no daemon, timer, scheduler, network, provider, credential,
    target-effect, retry, catch-up, or concurrency behavior.

## Verification obligations

The amendment package must contain exactly:

1. `src/gigai/schemas/gig-occurrence.schema.json`;
2. `src/gigai/schemas/gig-comparison.schema.json`;
3. two new `SHA256SUMS` entries while preserving all prior twenty-five lines;
4. registry/validator inventory updates from 25 to 27;
5. schema vectors for all lifecycle states, all cadence values, nullable
   fields, terminal reasons, duplicate identities, selected-winner rejection,
   and unknown fields;
6. semantic fixtures for snapshot digest/path/symlink/reference-set checks,
   occurrence idempotency, Run linkage, comparison input binding, and
   incomparability;
7. mutation fixtures for each named load-bearing guard; and
8. no daemon, scheduler, provider, network, credential, target mutation, or
   active-version behavior.

This amendment does not itself implement occurrence creation, Run linkage,
comparison, reconciliation, journal transitions, or scheduler behavior.

## Evidence references

- [G21 goal contract](../../../goals/phase-5/G21-recurring-and-comparative-gigs.md)
- [G13 completion audit](../../phase-3/G13/completion-audit.md)
- [G13 terminal handoff](../../phase-3/G13/terminal-handoff.md)
- [G14 completion audit](../../phase-3/G14/completion-audit.md)
- [G14 terminal handoff](../../phase-3/G14/terminal-handoff.md)
- [G15 completion audit](../../phase-3/G15/completion-audit.md)
- [G15 terminal handoff](../../phase-3/G15/terminal-handoff.md)
- [G16 completion audit](../../phase-3/G16/completion-audit.md)
- [G16 terminal handoff](../../phase-3/G16/terminal-handoff.md)
- [G20 completion audit](../../phase-5/G20/completion-audit.md)
- [G20 terminal handoff](../../phase-5/G20/terminal-handoff.md)

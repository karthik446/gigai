# SCOUT-06 persistence integration decisions

Date: 2026-09-10. Coordinator implementation refinements under the user's
continued Scout delivery authorization. These govern new candidate code and
disposable fixtures; they do not approve, migrate, or activate a real Gig.
Read with [the integration plan](SCOUT-06-persistence-integration-plan.md).

## Packet and approved contract

Use one composite output kind `research`, retaining the candidate domain kind
`role_research` and all findings, sources, uncertainties, questions and checks.
The next candidate source/Graph Set contract describes this explicitly; old
four-output contracts stay immutable and cannot be silently reinterpreted.

Introduce a closed version-2 `run_output_contract` artifact with the existing
identity/fields and a `domains` mapping keyed by declared output kind. Each
entry pins a known domain schema ID, an exact approved schema artifact ref,
and fixed validator identity/source ref. Domain requirements are sealed by
the Plan's existing output-contract ref, not caller assertions. Unknown IDs,
unrecognized validators, mismatched source/schema identities or an omitted
required domain packet refuse. Existing version-1 field-list contracts retain
their exact behavior. No arbitrary callbacks, dynamic imports from a Gig,
subprocesses, network schema resolution, or general plugin framework.

## Versioned external evidence channel

Preserve the four-field generic sidecar. Add an explicit typed domain sidecar
and supporting-artifact channel beside it, never JSON hidden in prose or a
lossy replacement for the domain packet. In-memory supporting content is bytes;
JSON transport uses canonical base64, with explicit media type and logical
artifact ID. Strict IDs, media types, decoded and encoded size limits, unique
IDs and complete reference coverage are mandatory. Refuse unsupported binary
encodings and unused/unmapped supporting content.

Use version-2 resources of the existing external invocation/checkpoint/receipt
families for the new wire and persisted shapes. Preserve v1 resources and
readers. Version dispatch must be deliberate and fail closed on unsupported
versions; do not widen an existing schema ID with new unknown nested shapes.
Plan/Run formats need change only when a concrete new field/input variant
requires it. Central registration, hashes, goldens and installed inventory
remain coordinator-owned and are integrated as soon as worker schema files
settle. No new top-level schema family or workpad root.

Persist Markdown, generic envelope, domain sidecar and supporting bytes in one
checkpoint transition under the existing writer lock, at the Run paths in the
plan. Persisted output tuples carry exact domain schema identity and artifact
refs, including a deterministic logical-ID-to-supporting-ref map. Submit
re-reads and validates the complete committed tuple under the lock; it never
republishes checkpoint artifacts or succeeds from agent-declared checks alone.
The fixed domain validator deterministically validates shape, byte bindings and
relationships, not semantic research truth. Source verification remains supplied
evidence with its recorded actor/method.

Checkpoint and submit enforce the Plan-pinned domain requirement even if a
caller tries to submit a legacy envelope without the domain fields. Preserve
existing exact-replay and parent-CAS contracts: a completed identical retry is
not invalidated merely by later workspace changes. Changed intent must conflict.

## Later research inputs

### Role-only request refinement

The [mapping audit](SCOUT-06-input-mapping-design.md) correctly identifies that
the candidate renderer's synthetic `kind: role` record/revision projection
has no real admitted input counterpart. Its proposed new native record kind
is not adopted. A general research request should not require creating a
profile, posting, or artificial native record first.

Use a closed explicit Plan input instead:
`{family: role_request, role_title: <nonempty string, max 300>, role_context:
<null or nonempty string, max 4096>}`. Exactly one is required for research.
The Plan invocation records the actual caller actor/origin; this is not an
extra human-confirmation claim. Sealing preserves the exact role text and all
optional original input envelopes. The renderer must repeat the role title
exactly and bind every selected input, not a lossy record-ID projection.

This requires deliberate new external Plan/invocation input versions, not
edits to old strict schemas or native CRUD kinds. The unshipped candidate
research domain moves to `urn:gigai:scout:research-packet:2`, payload version
`scout-research-sidecar:2`, fixed validator `scout-role-research:2`. The bridge
receives authenticated Plan inputs plus exact selected graph ID, selector and
graph version (not an inferred Gig version). No executable import comes from
Gig/caller paths. Terra owns the fixed packaged bridge and candidate packet
adjustment; root owns Plan-input and source/compiler integration.

### Reusing completed research

Add a distinct research-output input variant when implementing consumer reuse.
Require an exact succeeded terminal receipt, Plan/Run/checkpoint identity and
output tuple, not just a checkpoint that may still be partial or unvalidated.
Authenticate all byte refs, supporting maps and historical source/schema
identity from committed records. A correctly pinned older research revision
remains selectable; age or a newer source version alone is not corruption.
Never select latest implicitly, relabel it as imported G45 content, or copy it
into a new record merely to evade the input-family contract.

## Implementation sequence and limits

1. Generic external channel and version dispatch, with strict compatibility
   and omission/downgrade tests; unknown domain validators refuse.
2. Fixed research bridge and malformed-input corrections, with exact trusted
   packaged validator/source identity. No execution of editable Gig Python by
   the generic recorder.
3. Candidate composite contracts/source inventory, exact historical research
   input resolution, installed caller and fresh-session integration proof.

These may overlap only with explicit disjoint file ownership. No unfinished
default promotion. The user performs code review and UAT on the release
candidate before final release acceptance; tag/publish/merge are not authorized.

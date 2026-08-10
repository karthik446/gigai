# G19 Target-Effect Contract Amendment

- Status: Proposed for review; blocking prerequisite for G19 runtime code
- Type: Additive serialized-contract amendment; no runtime implementation
- Depends on: G16 completion audit and terminal handoff, accepted S16-EVAL
  methodology, G18 completion audit and terminal handoff, and G22 completion
  audit and terminal handoff
- Unblocks: G19 approved target-mutation implementation
- Baseline: twenty-two packaged schema resources and their current hashes

## Decision

Add one dedicated `target-effect.schema.json` as the twenty-third packaged
resource. It is the durable authority for one explicitly authorized target
effect and its recoverable mutation lifecycle.

The resource is dedicated rather than an extension of an existing resource:

- `gig-proposal.schema.json` describes a proposal and cannot become an
  operator authorization by inference;
- `active-gig-version.schema.json` identifies the active proposal/version but
  does not authorize an effect;
- `run-details.schema.json` and target observations describe Run state, not a
  target-write command;
- `trace.schema.json` and journal records provide chronology and replay
  evidence, but cannot be the authority for a target mutation; and
- `common.schema.json` provides reusable definitions, not a complete effect
  authorization or mutation state machine.

Bundling this record into one dedicated resource keeps authorization,
action-time binding, transition state, and before/after evidence addressable as
one content-bearing contract. It does not grant permission merely because the
record exists: G19 must still validate the active proposal, Review Loop
prerequisite, target binding, and action-time target manifest before exposure.

This amendment adds no target-mutation implementation, Git operation, journal
transition, or permission behavior. It settles the serialized boundary that
runtime code must later implement.

## Required resource shape

`target-effect.schema.json` uses schema version `1.0`, rejects unknown fields,
and requires the following top-level fields:

| Field | Contract |
|---|---|
| `schema_version` | Exact string `1.0`. |
| `effect_id` | New opaque `effect_` plus lowercase UUIDv4 identity; never reused. |
| `effect_version` | Positive integer version of this effect record. |
| `state` | One of the exact lifecycle states in the state contract below. |
| `project_id` / `gig_id` / `gig_proposal_id` | The bound project, Gig, and active proposal identities, reusing the existing common/GigAI identity definitions. |
| `target` | Git target identity and action-time binding, including `kind: git`, the bound project/target binding digest, and the expected `HEAD`. |
| `operator` | Existing actor shape with `kind: operator`; raw credentials or session tokens are not allowed. |
| `effect_kind` | Existing effect vocabulary value `write_target`. |
| `operation` | Exact v1 value `replace_file`; no multi-file or arbitrary patch operation is admitted. |
| `relative_target_path` | Existing relative-path definition; one regular, non-symlink file only. |
| `source_artifact` | Existing artifact reference for the reviewed workpad artifact whose exact bytes are the proposed replacement. |
| `expected_before_sha256` | Exact imported-byte digest required before exposure. |
| `expected_after_sha256` | Exact imported-byte digest required after exposure. |
| `expected_file_mode` | File mode captured and preserved by the v1 replacement. |
| `authorization` | Immutable operator authorization object containing `authorized_at`, the bound proposal/target/path/artifact identities, and the authorization digest. |
| `cancellation_policy` | Exact v1 value `before_exposure_only`; cancellation after exposure resolves through verification, restoration, or blocking and never creates an automatic second attempt. |
| `commit_policy` | Exact v1 value `leave_uncommitted`; G19 never creates a Git commit. |
| `patch_identity` | Digest-bearing descriptor of the one-file replacement, including source artifact digest, before digest, after digest, path, and mode. |
| `target_before_manifest` / `target_after_manifest` | Nullable content-addressed manifest references. The before manifest is required before preparation; the after manifest is required before `applied`. |
| `created_at` / `updated_at` | RFC 3339 timestamps for record creation and the latest accepted transition. |
| `terminal_reason` | Nullable before a terminal state; a non-empty share-safe reason is required for `refused`, `failed`, `cancelled`, `rolled_back`, and `blocked`. |

The `authorization` object is immutable after the first persisted
`effect_authorized` record. Its operator actor, active proposal, target
identity, relative path, source artifact, expected before/after digests,
cancellation policy, and `leave_uncommitted` policy are all part of the
authorization digest. A later record may advance state or add evidence, but
may not silently change any authorized value.

The target identity must be sufficient to reject a changed binding or Git
repository at action time. The target manifest must include, at minimum, the
repository identity, `HEAD`, index/worktree status digest, relative path, file
mode, size, and exact content digest. Manifest fields are evidence of the
observed target; they do not expand the authorized read or write set.

## Lifecycle state and transitions

`proposal_approved` remains a prerequisite state owned by G22, not a state of
the new target-effect resource. The target-effect record begins only after a
separate operator decision creates `effect_authorized`.

The exact serialized states are:

- `effect_authorized`: the immutable authorization exists; no target-visible
  change has occurred;
- `prepared`: source bytes, target policy, before manifest, staging location,
  and expected after digest have been validated; no target-visible change has
  occurred;
- `exposed`: the atomic replacement has occurred, but final verification and
  terminal journaling are not complete;
- `verified`: the exact expected after state and target-after manifest have
  been verified; the terminal `applied` transition is still required;
- `applied`: terminal success, with the authorized file changed and left
  uncommitted;
- `refused`: terminal rejection before exposure;
- `failed`: terminal failure before exposure or during preparation;
- `cancelled`: terminal cancellation before exposure;
- `rolled_back`: terminal recovery after exposure restored the exact before
  state; and
- `blocked`: terminal unresolved or ambiguous recovery requiring operator
  inspection.

The only permitted transitions are:

```text
effect_authorized -> prepared | refused | failed | cancelled
prepared          -> exposed  | refused | failed | cancelled
exposed           -> verified | rolled_back | blocked
verified          -> applied
```

All listed terminal states have no outgoing transition. There is no
transition to retry, fallback, another provider, another target, another
proposal, or another patch. A cancellation request after `exposed` does not
create `cancelled`; recovery must produce `verified`/`applied`,
`rolled_back`, or `blocked` according to observed bytes and manifests.

The record is append-only at the journal/evidence layer. A transition is
accepted only once, is causally linked to the prior state, and names the
authorization/patch identity it continues. A repeated read of an already
`applied` record against the same after state is an idempotent replay, not a
new transition or write.

## Amendment invariants

1. The prior twenty-two schema files remain byte-identical. Their
   `SHA256SUMS` entries, canonical vectors, validators, and installed replay
   behavior remain unchanged.
2. `target-effect.schema.json` is the only new packaged resource. The
   installed resource verifier increases from 22 to 23, and no existing
   resource count or meaning changes by inference.
3. The new resource is additive and versioned at schema `1.0`. It does not
   redefine `gig-proposal`, `active-gig-version`, journal, Goal Graph, Run,
   invocation, exchange, capability, or G22 interview semantics.
4. `operator.kind` is constrained to `operator`; the record has no field for
   a credential value, authorization header, session token, model approval,
   hidden context, or provider response.
5. The only v1 effect is `write_target` with `replace_file`, one relative
   regular non-symlink document path, and `leave_uncommitted`. The schema does
   not admit multi-file effects, automatic commits, pushes, branch changes,
   shell commands, subprocesses, tools, providers, credentials, or network
   operations.
6. A target-effect record cannot be validly authorized without the active
   proposal, operator actor, exact target/path/artifact binding, expected
   before/after digests, cancellation policy, and commit policy.
7. A terminal state cannot carry an implicit retry, fallback, winner, or
   alternate target. Disagreement or review success remains input evidence;
   neither becomes target authority.
8. A blocked or rolled-back record is valid evidence of a terminal attempt;
   it is not silently rewritten as success. The target is unchanged after
   `rolled_back`, and `blocked` requires operator inspection.

## Verification obligations for the amendment

The amendment change set must contain exactly:

1. `src/gigai/schemas/target-effect.schema.json`;
2. one new `SHA256SUMS` entry while preserving all twenty-two existing lines
   byte-for-byte;
3. the installed-resource verifier update from 22 to 23;
4. registry/validator fixtures for valid `effect_authorized`, `prepared`,
   `exposed`, `verified`, `applied`, `refused`, `failed`, `cancelled`,
   `rolled_back`, and `blocked` records;
5. fail-closed fixtures for missing authorization fields, non-operator actor,
   unsupported operation/effect, path escape, symlink/path policy, invalid
   digests, changed target identity, unknown fields, terminal transitions,
   credential-shaped fields, automatic commit, and retry/fallback fields; and
6. canonical vectors proving the prior twenty-two resources and their vectors
   are unchanged, plus vectors for the new resource.

The amendment must not contain target mutation code, atomic replacement code,
Git subprocess calls, journal implementation, credential resolution, provider
calls, or runtime authorization logic. Those belong to the subsequent G19
implementation and remain blocked until this amendment is accepted.

## Acceptance decision

This amendment is accepted only when a review confirms the dedicated-resource
decision, required field set, exact state machine, and all eight invariants;
the amendment package then passes source and installed schema verification.
Acceptance authorizes G19 to begin implementation against this contract. It
does not itself authorize a target write, create a Git commit, or mark G19
complete.

## Evidence references

- [G19 goal](../../../goals/phase-4/G19-approved-target-mutation.md)
- [G16 completion audit](../../phase-3/G16/completion-audit.md)
- [S16-EVAL completion audit](../../phase-3/S16-EVAL/completion-audit.md)
- [G18 completion audit](../../phase-3/G18/completion-audit.md)
- [G22 completion audit](../../phase-2/G22/completion-audit.md)
- [G22 terminal handoff](../../phase-2/G22/terminal-handoff.md)

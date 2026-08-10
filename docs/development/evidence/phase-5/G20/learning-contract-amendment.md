# G20 Learning and Improvement Contract Amendment

- Status: Proposed additive amendment; no runtime implementation
- Type: Additive serialized-contract amendment for G20
- Depends on: G20 approved goal contract, G16 completion audit and terminal
  handoff, accepted S16-EVAL methodology, G18 completion audit and terminal
  handoff, G19 completion audit and terminal handoff, and G22 completion audit
  and terminal handoff
- Unblocks: G20 runtime implementation
- Baseline: twenty-three packaged schema resources and their current hashes

## Decision

Add exactly two subordinate resources:

1. `learning-record.schema.json` — one immutable, provenance-tagged
   observation of exactly one existing source artifact; and
2. `improvement-manifest.schema.json` — one typed, evidence-backed change
   manifest subordinate to an ordinary `gig-proposal`.

The existing `gig-proposal.schema.json` remains the sole proposal identity and
approval/version authority. G20 uses its existing `kind: "improve"`,
`base_gig_version`, `parent_proposal_id`, and `creation_manifest` fields. The
new manifest cannot allocate a proposal ID, approve itself, or advance
`active-gig-version.json`.

The learning root is derived as `home_root / "learning"`; no configuration
field or second version ledger is added. A learning journal under that root
publishes learning records only. The workpad journal and
`active-gig-version.json` remain the only authority for proposal approval and
active-version advancement.

This amendment defines the serialized boundary only. It adds no learning
engine, evaluator, proposal route, approval behavior, model call, target
effect, or version lifecycle implementation.

## Learning-record resource

`learning-record.schema.json` is schema version `1.0`, rejects unknown fields,
and requires these fields:

| Field | Contract |
| --- | --- |
| `schema_version` | Exact string `1.0`. |
| `record_version` | Positive record revision. |
| `learning_id` | Opaque `learning_` plus lowercase UUIDv4 identity; never reused. |
| `project_id` / `gig_id` | Existing common project and Gig identities. |
| `subject` | Exactly one `run` or `goal` subject using the existing Run/Goal ID definitions. |
| `active_version` | Positive active Gig version read from `active-gig-version.json` at observation time. |
| `active_pointer_sha256` | Exact digest of the active-version pointer bytes read at observation time. |
| `source` | Exactly one typed source artifact and its exact identity. |
| `provenance` | Exactly one of `observed_outcome`, `evaluator_judgment`, `operator_feedback`, or `accepted_outcome`. |
| `observed_at` | RFC 3339 observation timestamp. |
| `explanation` | Optional bounded descriptive text; never evidence authority. |
| `created_at` / `updated_at` | RFC 3339 publication timestamps. |

The `source` object requires:

- `kind`: exactly one of `finding`, `feedback`, `adjudication`, `report`,
  `addressed_artifact`, or `target_effect`;
- `artifact`: an existing common artifact reference containing path, exact
  content digest, media type, and byte size; and
- `source_id`: the source record identity appropriate to the selected kind.

The artifact reference is the authoritative citation. A free-text explanation
may explain why the observation matters but cannot substitute for the exact
source bytes, add a second source, or authorize an improvement.

One learning record represents one normalized observation of one source
artifact. The same source artifact cannot be published twice for the same
Gig, active version, and observation subject. Distinct source artifacts remain
separately attributable and may be composed into one manifest.

`operator_feedback` and `accepted_outcome` are valid provenance values for
history and context, but neither is sufficient by itself for the evidence
gate. At least one cited record with `observed_outcome` or
`evaluator_judgment` must support an improvement proposal.

## Improvement-manifest resource

`improvement-manifest.schema.json` is schema version `1.0`, rejects unknown
fields, and requires:

| Field | Contract |
| --- | --- |
| `schema_version` | Exact string `1.0`. |
| `manifest_version` | Positive manifest revision. |
| `manifest_id` | Opaque `improve_manifest_` plus lowercase UUIDv4 identity; subordinate only. |
| `project_id` / `gig_id` | Existing bound identities. |
| `base_gig_version` | Positive version that the candidate was built from. |
| `parent_proposal_id` | Existing proposal identity being improved. |
| `learning_record_ids` | One or more cited `learning_` identities. |
| `change_request` | Optional descriptive operator text; never authorization. |
| `changes` | One or more typed changes restricted to the allowlist below. |
| `evidence_gate` | Deterministic pass report with exact supporting citations. |
| `quality_gate` | Deterministic pass report over the S16-EVAL extension corpus. |
| `created_at` / `updated_at` | RFC 3339 manifest timestamps. |

Each change requires a typed `target` in exactly one of these namespaces:
`review_contract`, `rubric`, or `verifier`. It also requires a dotted field
`path` rooted in that namespace, a `change_kind`, and exact before/after
artifact identities:

- `before`: an artifact reference and its exact digest;
- `after`: an artifact reference and its exact digest; and
- `operation`: one of `replace`, `add`, or `remove`.

The schema path pattern admits only lowercase identifiers rooted at the three
allowed namespaces. The following paths are forbidden by both the schema
shape and the semantic validator, even if placed inside `change_request` or a
descriptive field: `allowed_effects`, redaction policy, network policy,
credentials, provider selection, budgets, cycle caps, recovery policy,
parallelism, and target-effect authority. Free text never widens this set.

The `evidence_gate` requires a passing deterministic result, an exact report
artifact, and cited learning IDs. Runtime validation must additionally prove
that all cited records are valid and that at least one has provenance
`observed_outcome` or `evaluator_judgment`.

The `quality_gate` requires a passing deterministic result, baseline and
candidate identities, evaluator version, corpus identity, development,
calibration, and final-held-out results, `final_holdout_pass: true`, and
`no_regression: true`. Runtime validation must reject a candidate that fails
the fixed S16-EVAL bar or regresses any normative metric, regardless of
operator feedback or model confidence.

## G22 improve and existing-version authority

The G22 loopback session must carry an explicit request mode of `improve`; a
create session cannot be relabeled as improve by inference. The resulting
ordinary proposal must contain:

- `kind: "improve"`;
- the current `base_gig_version`;
- the `parent_proposal_id`;
- a `creation_manifest` reference to the improvement manifest; and
- the proposal's exact learning and quality citations through that manifest.

At approval time, runtime must reread and validate the active pointer, base
version, proposal identity, manifest digest, and cited source bytes. A stale
base or changed citation fails closed. A valid approval advances the existing
active version exactly once through the existing lifecycle path. Repeated
approval or replay returns the already-published version and cannot create a
second active-version advancement.

## Learning-root authoring and recovery

The learning root is always `home_root / "learning"` after the configured home
root is canonicalized. No caller-supplied alternate root is admitted. Learning
records and the learning journal must remain below that root; path traversal,
symlink escape, outside-root writes, malformed roots, and unexpected file types
fail closed.

The authoring state contract is:

```text
drafting -> written
    |          |
    v          v
 discarded  reconciled
```

Publication is write-temp-then-rename followed by an append-only journal
publication. A record is `written` only when its exact bytes and journal entry
both validate. Interruption fixtures must cover before temporary write, after
temporary write, after rename, and before/after journal publication.

Reconciliation discards temporary files, orphaned artifacts, journal entries
whose artifact is missing, malformed records, and duplicate observations. It
never silently completes a partial record or treats an orphan as accepted
learning. The learning journal does not replace the workpad journal and cannot
publish an active-version change.

## Amendment invariants

1. All prior twenty-three schema files remain byte-identical. Their
   `SHA256SUMS` entries, canonical vectors, validators, and installed replay
   behavior remain unchanged.
2. Exactly `learning-record.schema.json` and
   `improvement-manifest.schema.json` are added, raising the packaged
   inventory from 23 to 25.
3. `gig-proposal.schema.json`, `active-gig-version.schema.json`, the workpad
   journal, and G22's proposal-interview resource retain their existing
   meanings. No parallel proposal or version authority is introduced.
4. A learning record cannot cite more than one source artifact, and an
   improvement manifest cannot pass the evidence gate with only
   `operator_feedback` or `accepted_outcome` provenance.
5. The improvement manifest admits only review-contract, rubric, and verifier
   paths. It cannot authorize effect, security, provider, budget, recovery,
   parallelism, or target-authority changes.
6. A quality-gate pass is not a model-confidence claim: it requires the fixed
   S16-EVAL bar and no regression on the final held-out acceptance set.
7. Learning-root records are local, append-only evidence. They cannot approve
   a proposal, advance an active version, mutate a target, execute a provider,
   or create a Git commit.
8. All schema objects reject unknown fields, all identities are content-bound
   where stated, and all terminal/refusal behavior fails closed.

## Verification obligations

The amendment package must contain exactly:

1. `src/gigai/schemas/learning-record.schema.json`;
2. `src/gigai/schemas/improvement-manifest.schema.json`;
3. two new `SHA256SUMS` entries preserving all prior twenty-three lines;
4. the installed verifier and registry/validator inventory update from 23 to
   25;
5. schema vectors covering every provenance value, one-source grain,
   duplicate policy, valid/invalid allowed paths, every forbidden namespace,
   both gate shapes, stale citations, and unknown fields;
6. semantic fixtures proving the evidence gate and quality gate are separate;
7. learning-root atomic-write, orphan-reconciliation, symlink, traversal, and
   duplicate fixtures; and
8. no runtime provider, target, Git, approval, or version-advance behavior.

The amendment is accepted only after the source and installed schema
verification passes, prior hashes are independently unchanged, and the
contract review confirms the two-resource decision, authority boundaries,
closed path allowlist, separate gates, and recovery state machine. Acceptance
unblocks G20 implementation; it does not mark G20 complete.

## Evidence references

- [G20 goal contract](../../../goals/phase-5/G20-local-improve-and-evaluator-learning.md)
- [G16 completion audit](../../phase-3/G16/completion-audit.md)
- [S16-EVAL completion audit](../../phase-3/S16-EVAL/completion-audit.md)
- [G18 completion audit](../../phase-3/G18/completion-audit.md)
- [G19 completion audit](../../phase-4/G19/completion-audit.md)
- [G22 completion audit](../../phase-2/G22/completion-audit.md)
- [G22 terminal handoff](../../phase-2/G22/terminal-handoff.md)

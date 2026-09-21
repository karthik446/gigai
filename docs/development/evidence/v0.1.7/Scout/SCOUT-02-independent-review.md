# SCOUT-02 — Independent review (Claude)

**Date:** 2026-09-08
**Reviewer:** Claude (independent review agent; not an implementer)
**Worktree:** `/Users/kar/orca/workspaces/gigai/gigai-v0.1.7`, branch
`karthik446/gigai-v0.1.7`, HEAD `fda4857` (all reviewed source is uncommitted
working-tree change).
**Scope reviewed:** SCOUT-02 / G43.2 multi-graph foundation against accepted
[A02](SCOUT-00-contract-amendments.md#8-caller-ownership-and-acceptance-matrix),
[SCOUT-00 §1](SCOUT-00-contract-amendments.md#1-authority-and-compatibility),
the [user-owned Gig amendment](SCOUT-00-user-owned-gig-amendment.md), the
[G43.2 goal](../../../v0.1.7/goals/G43.2-multi-graph-gig-versions-and-graph-selected-run-plans.md),
the [implementation plan](SCOUT-02-implementation-plan.md), the
[implementation record](SCOUT-02-implementation.md), and the
[caller audit](SCOUT-02-caller-audit.md). Reviewed actual source and the two
focused test files, not the success summary.

**Boundary honoured:** No source, test, schema, private Gig, provider, commit,
or external write was made. Only this file was written. Existing dirty G43.1
provider-dogfood work in `provider_review.py`, `adapters/claude_cli.py`, and the
provider-review lifecycle helpers inside `run.py`/`journal.py`/`cli.py` was
treated as unrelated concurrent work and preserved. Disposable offline test runs
only; the full suite is the independent verifier's responsibility.

---

## Verdict: **changes-requested**

The core additive multi-graph authority chain — v2 proposal staging, v2 approval,
immutable Graph Set revalidation from the tagged commit, selector/alias
normalisation, selection-record binding, sealed v2 Run Plan, historical-version
redemption, and a directly-confirmed offline Run — is implemented soundly and the
16 focused tests plus the v1 regression slices I ran offline pass. The v1
byte-identity and v1 identity-projection guarantees hold under my checks.

The verdict is **changes-requested**, not accepted, because two A02 obligations
are not met by the current source:

1. **Strict nested schema identity is lost in the new v2 Plan and v2 Run
   manifest schemas** (Finding 1). A02 requires "strict nested/additive schema
   identity"; `run-plan-v2` and `run-manifest-v2` degrade every nested object to
   `{"type":"object"}` with no `additionalProperties:false`, no `required`, and
   no enums, so a v2 Plan/manifest that carries unknown or malformed nested
   fields still passes strict schema validation. This is a real deviation from
   the "additive with old guarantees preserved" contract, even though the
   happy-path constructor and `_validate_plan_semantics` recover most semantics.

2. **Downstream listing / portability / comparison / occurrence callers are not
   version-aware** (Finding 2). A02 item 6 and the caller audit require legacy
   index/comparison/portability callers to "remain valid or return deliberate
   version-aware diagnostics." For a legitimately approved v2 Gig, `gigai gigs`
   renders it as `N/A Gig / N/A / N/A` (indistinguishable from corruption),
   `verify_active_version_portability` raises `refused_unsealed_pointer` (a
   mislabelled refusal of a sealed pointer), and `compare_occurrences` raises a
   generic `Run manifest failed schema validation`. These are fail-closed (no
   crash, no silent wrong result) but they are not the deliberate
   `unsupported_schema_version`/version-aware diagnostics A02 asks for, and no
   test exercises v2 through any of these readers.

Neither finding requires SCOUT-03/04/05 domain features. Finding 1 is a schema
tightening in files SCOUT-02 already owns. Finding 2 needs either version-aware
dispatch or explicit typed `unsupported_schema_version` diagnostics in the four
named readers plus focused tests; the implementation record's "Deliberate
limits" section should also stop implying these readers are unaffected.

---

## What was verified as correct (evidence for the accepted parts)

- **v1 byte-identity and v1 identity projection.** `approve_offline` /
  `_recover_approved_publication` keep the v1 `active-gig-version.json` payload
  (`goal_graph` retained, `graph_set` only added under `is_v2`);
  `_identity_projection` adds `graph_set_sha256`/`selected_graph_id`/
  `selection_sha256` only when `plan.get("plan_version") == 2`.
  `tests/test_scout02_independent.py::test_legacy_plan_identity_has_no_new_empty_graph_fields`
  and offline runs of `tests/test_g43_run_plan.py`, `tests/test_g13_run.py`,
  `tests/test_canonical.py`, `tests/test_canonical_ownership.py` (72 passed),
  and `tests/test_g23_portability.py` / `tests/test_g21_comparison.py` /
  `tests/test_g21_occurrence.py` / `tests/test_index_projection.py` /
  `tests/test_bug_001_gigs_listing.py` / `tests/test_journal_locking_recovery.py`
  (74 passed) all pass — v1 readers are unchanged in behaviour.
- **Nested vs additive schema dispatch.** `_resolve_authority`,
  `_validate_authority`, `validate_run_plan`, `read_run_details`, `approve_offline`,
  and `_recover_approved_publication` all dispatch by schema validity / presence
  of `graph_set` / `plan_version == 2`, never by silently coercing v2 into a v1
  reader. The 7 new schemas are additive files with distinct `$id` URNs
  (`:gig-graph-set:1`, `:graph-selection-record:1`, `:graph-selection-record-v2:1`,
  `:gig-proposal-v2:1`, `:active-gig-version-v2:1`, `:run-plan-v2:1`,
  `:run-manifest-v2:1`); v1 schema bytes are unchanged
  (`test_pre_scout_strict_schema_bytes_are_unchanged` + captured digests match).
- **v1 Plan IDs/bytes unchanged.** v1 `create_run_plan` still writes
  `manifests/goal-graph.json` as the sealed graph ref, still omits the graph-set
  projection keys, and still derives the same `run_plan_id`. Confirmed by the
  legacy-identity test (re-read digest equals original bytes) and by
  `test_g43_run_plan.py` passing unchanged.
- **Atomic journal-approved proposal staging.** `propose_graph_set_offline`
  reads the local definition as untrusted transport (`is_symlink`/`is_file`
  guard, per-ref safe-path + symlink-component + size + digest check in
  `safe_source`), rejects a non-approved/pending Gig, rewrites every Goal Graph
  contract and descriptor attachment under `manifests/graph-sets/<proposal_id>/`,
  validates the assembled Graph Set in a throwaway temp root via
  `validate_graph_set(..., root=...)`, and journals every staged artifact plus
  the pending `gig-proposal.json` in one `record_transition` under transition
  `gig_graph_set_proposed` (added to the closed `TRANSITIONS` set) inside the
  single writer lock. It never approves, never allocates a Run, and does not
  mutate a private Gig outside its resolved workpad. `journal._record_chain`
  now runs `_preflight_artifact_destinations` (symlink/dir-collision refusal,
  and immutable-exists refusal when `allow_artifact_replacement=False`) before
  creating recoverable transaction state. Ordinary `gigai approve <proposal>`
  then produces version N+1. The flow test approves v2 then v3 and asserts the
  version counter and journal advance.
- **Exact graph/contract refs and selected input binding.**
  `resolve_selected_graph_authority` re-reads `goal_graph` + all six attachment
  refs + every goal `contract` ref from `authority["commit"]` via
  `git show <commit>:<path>`, checking digest **and** `size_bytes`, plus
  absolute/backslash/`..` path rejection, and revalidates the selected Goal
  Graph (`_validate_authority`), its `aggregate_budget` vs the descriptor budget
  (`_budget_within`), its goal effects/executor capabilities vs the descriptor
  ceilings, and each goal contract digest. `_validate_plan_handoff` for a v2
  Plan additionally requires `plan["graph_set"] == authority["graph_set_ref"]`,
  `plan["selected_graph_id"] == descriptor["graph_id"]`,
  `plan["selected_graph"] == descriptor["goal_graph"]`, a sealed
  `selection_record` source whose bytes pass
  `validate_selection_record(...)`, and `selected_descriptor.goal_graph.content_sha256
  == plan.goal_graph.content_sha256`. `validate_selection_record` pins the
  Graph Set by `digest_imported_bytes(canonical_json_bytes(graph_set))`, the
  `gig_id`/`gig_version`, the descriptor match, and
  `selected_graph == descriptor.goal_graph`. Alias normalisation:
  `graph_set_descriptor` resolves alias→canonical `graph_id`; the persisted
  selection stores the canonical `selected_graph_id`
  (`test_persisted_selection_uses_canonical_selector_not_alias`,
  `test_two_graph_propose_approve_plan_history_and_run` selects via alias `role`
  and asserts `selected_graph_id == "career"`).
- **Semantic selector vs Goal Graph UUID kept distinct.** Descriptor `graph_id`
  / alias use `^[a-z][a-z0-9-]{0,63}$`; the attached Goal Graph keeps its
  `graph_<uuidv4>` entity ID validated by the existing Goal Graph schema. The
  selection record carries both the slug and the Graph Set / Goal-Graph
  artifact refs. New `EntityPrefix.GRAPH_SET` / `GRAPH_SELECTION` were added to
  the central `canonical.ENTITY_ID` regex and enum rather than a feature-local
  UUID derivation; `derive_deterministic_id("graph_selection", …)` /
  `("graph_set", …)` reuse the central helper. v1 canonical tests pass.
- **Current vs historical authority.** When redeeming a sealed Plan,
  `launch_run` sets `version = selected_plan.plan["gig_version"]` if none is
  passed, so `_resolve_authority` resolves the historical tag `gig-vNNNNNN`,
  verifies tag→commit agreement, and reads the v2 proposal + Graph Set + every
  reference **from that commit**. The current mutable `manifests/` tree and the
  current active pointer are never consulted for a pinned Plan. The flow test
  approves v3, then redeems the v2 Plan and the Run succeeds against v2; the v2
  Plan bytes on disk are asserted byte-identical after v3 approval.
- **Selection actor / invocation provenance vs operator consent.**
  `create_run_plan` only ever mints `only_member_default` (deterministic
  `gigai_deterministic` selector, actor `{"kind":"gigai","id":"graph-selector"}`)
  or `operator_explicit`; it never mints `agent_explicit`. `agent_explicit` is
  only *accepted* from a pre-journaled `--selection-record` and requires (in
  `validate_selection_record`) `selector.kind == "agent"`, an agent actor, and a
  Mapping `invocation_ref`, with empty routing evidence. Selection is never
  treated as Run consent: `launch_run` still raises
  `run_plan_consent_mismatch` unless `operator_consent` with
  `source: direct_cli_confirm` is supplied, for v1 and v2 alike; the redeemed
  consent scope records `provider_review_requested` separately.
  `test_agent_selection_schema_accepts_an_agent_not_a_forged_operator` confirms
  an agent record validates and an added `operator_consent` key fails.
  `test_only_member_selection_requires_deterministic_gigai_actor` confirms a
  null or operator actor is rejected for `only_member_default`.
- **Graph/Plan provider and budget ceilings.** `validate_graph_set` rejects
  descriptor `effect_policy`, `capability_requirements`, `provider_eligibility`,
  or `budget` that is not a subset / within the `shared_policy` ceiling
  (`graph_policy_widened`), rejects duplicate selectors/aliases, duplicate Goal
  Graph digests, and (with `root`) the actual Goal Graph exceeding its
  descriptor. `_budget_within` compares `max_cost` numerically via `Decimal`
  (not lexically) and treats `None` as unbounded only when the parent is also
  `None`, with `currency` equality required.
  `test_graph_cost_cannot_widen_shared_ceiling` (incl. `None`) and
  `test_graph_cost_comparison_is_numeric_not_lexical` pass. `create_run_plan`
  additionally enforces `_budget_within(plan_profile_budget, descriptor.budget)`
  and `{"write_workpad"} ⊆ descriptor.effect_policy`, and does **not** copy the
  current active `capability_manifest` into a v2 Plan (`if descriptor is None`
  guard).
- **Tamper / symlink / traversal / recovery.** `graph_set._ref_data`,
  `resolve_selected_graph_authority`, `lifecycle.safe_source`,
  `_validate_plan_handoff`, `read_run_details`, and the provider helpers all
  reject symlinked path components, absolute paths, `..`, and byte/size drift.
  `read_run_details` now re-reads Run details from `HEAD:<relative>` and raises
  `run_details_reconciliation_required` if the working file differs from the
  committed bytes (never adopting an interrupted writer's uncommitted terminal
  state). `_preflight_artifact_destinations` refuses immutable collisions before
  transaction state is created. `test_journal_locking_recovery.py` passes.
- **No implicit cross-graph prior-Run leakage in the reviewed path.** A v2 Plan
  seals its inputs as explicit record/snapshot refs; there is no code path that
  turns a `prior_occurrence_id` or another graph's output into a v2 Plan input.
  (The comparison reader's cross-graph behaviour is Finding 2, not a leakage
  bug — it refuses rather than feeds anything through.)
- **Schema inventory wiring (partial).** All 7 new schemas are in
  `validators.SCHEMA_NAMES`, `schemas/SHA256SUMS`, and
  `tools/verify_installed_schemas.py::EXPECTED_SHA256`; counts agree at 44/44/44
  and `verify_installed_schemas.py` passes. The README is the gap — see
  Finding 3.

---

## Findings (prioritised, actionable)

### Finding 1 — [P1] `run-plan-v2` / `run-manifest-v2` drop strict nested-object identity

**A02 clause:** "strict nested/additive schema identity"; SCOUT-00 §1 "Old
serializers/readers, approval tags, journal commits, Plan identity projections,
and Run evidence remain valid unchanged … Use additive schema versions … for
changed strict contracts."

**Where:** `src/gigai/schemas/run-plan-v2.schema.json`,
`src/gigai/schemas/run-manifest-v2.schema.json`.

**Detail:** v1 `run-plan.schema.json` defines strict `$defs` for
`classification`, `override`, `profile`, `phase`, `participant`, `input`, and
`capabilities`, each with `additionalProperties: false`, `required`, and enums.
`run-plan-v2.schema.json` replaces every one of these with a bare
`{"type":"object"}` (and `phases`/`participants`/`inputs` items with
`{"type":"object"}`), keeping only top-level `additionalProperties: false`.
`run-manifest-v2.schema.json` does the same to `goal_contracts` items
(v1 uses a strict `$defs/goal_contract`). Consequences:

- A v2 Plan whose `classification`, `profile`, `capabilities`, `inputs[]`, or
  `participants[]` carries unknown/extra keys, a missing `record_ref` /
  `snapshot_ref`, an out-of-range `profile_id`, or a bad `state` enum still
  passes `validate_serialized_contract("run-plan-v2.schema.json", …)`.
- `_identity_projection` reads only a fixed set of nested keys, so such extra
  keys do not change the derived `run_plan_id` and are not caught by
  `run_plan_digest_mismatch`.
- `_validate_plan_semantics` recovers phase ordering, participant uniqueness,
  role/profile match, budget-equals-catalog, opt-in, and sealed-time
  consistency — but it does **not** enforce `additionalProperties: false` nor a
  strict per-`inputs[]` shape outside typed-closure Plans.

**Realistic impact:** happy-path Plans/manifests are still built strictly by
`create_run_plan` / `_prepare_records`, and `_validate_plan_handoff` re-derives
the graph/selection/graph-set identities from approved authority, so this is not
a demonstrated Run-authority bypass. It is a genuine loss of the "additive with
old strict guarantees preserved" property for hand-crafted or tampered v2
Plan/manifest bytes, and it is the kind of gap A02 explicitly names.

**Reproduction:** take the v2 plan produced by
`tests/test_scout02_graph_set_flow.py::test_two_graph_propose_approve_plan_history_and_run`,
add `"classification": {..., "forged_field": 1}` (or drop `snapshot_ref` from an
`inputs[]` entry) and re-hash `run_plan_id` over the unchanged projection;
`validate_run_plan(plan_bytes)` returns valid. The v1 equivalent fails on
`additionalProperties`.

**Suggested fix:** give `run-plan-v2` / `run-manifest-v2` the same strict nested
`$defs` as v1 (referencing v1's `$defs` by URN where possible), adding only the
new v2 top-level fields. Add a focused vector per nested object asserting
unknown-key and malformed-nested rejection for both schemas.

### Finding 2 — [P1] Legacy listing / portability / comparison / occurrence readers are not v2-aware

**A02 clause:** item 6 — "Legacy approval/Plan/Run/index/comparison/portability
callers remain valid or return deliberate version-aware diagnostics";
caller audit "Report `unsupported_schema_version`/deliberate diagnostics rather
than treating v2 as malformed v1."

**Where (unmodified by SCOUT-02):** `src/gigai/listing.py:257-297`,
`src/gigai/portability.py:67,82,170`, `src/gigai/comparison.py:158`,
`src/gigai/occurrence.py` (pins Gig/version then calls `launch_run`).

**Detail:** none of these files reference `graph_set` / `plan_version` / a v2
schema (`grep` = 0 hits). For a v2-approved Gig:

- `listing._read_entry` calls
  `_valid_manifest(proposal_bytes, "gig-proposal.schema.json")` and
  `_valid_manifest(active_bytes, "active-gig-version.schema.json")`; both return
  `None` for the v2 proposal/pointer (they lack `goal_graph`, carry
  `graph_set`), so `proposal_invalid` / `active_invalid` are set and the Gig
  lists as `title="N/A Gig"`, `status="N/A"`, `version="N/A"` — the same output
  as a genuinely corrupt Gig, with diagnostic code
  `proposal_metadata_invalid` / `active_version_metadata_invalid`.
- `portability.verify_active_version_portability` calls
  `_require_schema("active-gig-version.schema.json", live_bytes,
  "refused_unsealed_pointer")` → raises `PortabilityError("refused_unsealed_pointer")`
  for a pointer that *is* sealed and valid, just v2.
- `comparison._read_run` validates against `run-manifest.schema.json`; a v2
  graph-selected Run's `run-manifest-v2` manifest fails → raises the generic
  `ComparisonError("Run manifest failed schema validation")`. (Occurrence-driven
  Runs still work because `launch_run` is v2-aware; only `occurrence compare`
  inherits the comparison gap.)

**Realistic impact:** fail-closed (no crash, no silent wrong comparison or
false-portable claim), but a legitimately approved multi-graph Gig is
mis-described as broken across `gigs`/portability/compare, and an operator
cannot distinguish "unsupported schema version" from "corrupted record." No test
covers any of this.

**Reproduction:** after
`test_two_graph_propose_approve_plan_history_and_run` reaches version 2, call
`gigai gigs` (or `listing.read_gig_listing`) for that project and observe the
`N/A Gig / N/A` row; call `portability.verify_active_version_portability(workpad)`
and observe `refused_unsealed_pointer`.

**Suggested fix:** in each of the four readers, detect the v2 proposal/pointer/
manifest by schema and either (a) project the v2 record's Graph Set digest /
selector / selected Goal Graph UUID for read-only display and inspection-only
portability, or (b) emit an explicit typed `unsupported_schema_version`
diagnostic distinct from `*_metadata_invalid` / `refused_unsealed_pointer`. Add
one focused test per reader asserting the deliberate v2 outcome. Update the
implementation record's "Deliberate limits" paragraph, which currently implies
"v1 readers keep their original single-graph path" without noting these four
now mislabel a valid v2 Gig.

### Finding 3 — [P2] README schema catalogue not updated for the 7 new SCOUT-02 schemas

**Caller-audit clause:** "Add every new schema to resource dispatch, `$id`
registry, README, `SHA256SUMS`, installed verifier, package/wheel checks."

**Where:** `src/gigai/schemas/README.md` — both the amendment-narrative block
(lines ~17-48) and the `## Files` bullet list (lines ~69-123). `grep` for
`gig-graph-set` / `graph-selection-record` / `gig-proposal-v2` /
`active-gig-version-v2` / `run-plan-v2` / `run-manifest-v2` = 0 hits. The G43.1
`provider-review-closeout-receipt.schema.json` (dirty concurrent work) *was*
added, so the omission is specific to SCOUT-02.

**Impact:** documentation/inventory only; `SHA256SUMS`, `SCHEMA_NAMES`, and the
installed verifier are all consistent (44/44/44) and pass. Low risk, but it is
an explicit A02/caller-audit inventory requirement and a reviewer/operator
reading the catalogue would not know the v2 family exists.

**Suggested fix:** add a SCOUT-02 amendment paragraph and one `## Files` bullet
per new schema, matching the existing style.

### Finding 4 — [P2] `agent_explicit` `invocation_ref` is a bare artifact ref with no resolution

**SCOUT-00 §1 / caller audit:** "add `agent_explicit` with an agent actor and
**exact invocation reference**"; audit: "The accepted amendment requires exact
invocation provenance, not merely a free-text agent claim."

**Where:** `graph-selection-record-v2.schema.json` (`invocation_ref` =
nullable `artifact_ref`), `graph_set.validate_selection_record` (only checks
`invocation_ref` is a Mapping and evidence is empty).

**Detail:** for `agent_explicit`, the validator accepts any well-formed
`{path,content_sha256,size_bytes,media_type}` as the invocation reference; there
is no check that it resolves to a journaled invocation artifact, that the
actor's `id` matches that invocation, or that the path lives under an
invocation namespace. SCOUT-02 does not itself *produce* `agent_explicit`
records (that is G44), so this cannot be exercised end-to-end yet, and requiring
full invocation-artifact resolution here arguably reaches into G44 territory.

**Impact:** none within SCOUT-02's own flows; a latent weakening of the
"exact invocation provenance" contract that G44 will inherit if not tightened.

**Suggested fix:** either (a) note explicitly in the implementation record that
`agent_explicit` invocation-ref *resolution and actor binding* is deferred to
G44 and that SCOUT-02 only provides the schema slot, or (b) have
`validate_selection_record` require the `invocation_ref` path to be under a
recognised invocation prefix and (when a workpad root is available) resolve it
to a journaled `parse_invocation`-valid artifact whose actor equals the
selector actor.

### Finding 5 — [P3] Provider-review recovery helper hard-codes the v1 run-manifest schema

**Where:** `run.py::_provider_review_active` validates the Run manifest with
`validate_serialized_contract("run-manifest.schema.json", manifest_bytes)`.

**Detail:** for a v2 graph-selected Run that also opted into provider review,
the manifest is `run-manifest-v2` and this check fails, so
`_provider_review_active` returns `False` and
`_recover_abandoned_provider_review` / the `read_run_details` reconciliation
branch would treat an abandoned provider review as an ordinary detached-worker
Run (marking Graph Goals failed) instead of interrupting it truthfully. This
sits on the seam between the dirty G43.1 provider-review work and SCOUT-02's
v2 manifest; the SCOUT-02 tests only exercise a deterministic worker Run on v2,
not provider review + graph selection together, so it is untested in both
directions.

**Impact:** provider-review + multi-graph is out of SCOUT-02's stated scope, so
this is not A02-blocking, but it is a concrete latent inconsistency the next
integrator should close (accept both `run-manifest.schema.json` and
`run-manifest-v2.schema.json` in `_provider_review_active`, or gate provider
review off for v2 Plans explicitly).

---

## Evidence limitations

- **G43.1 / SCOUT-02 entanglement.** SCOUT-02 and the in-flight G43.1
  provider-dogfood work share the same uncommitted working tree and the same
  files (`run.py`, `journal.py`, `cli.py`, `validators.py`, `schemas/SHA256SUMS`,
  `schemas/README.md`). There is no isolated SCOUT-02 diff. I attributed the
  provider-review lifecycle helpers, the `provider-review-closeout-receipt`
  schema, the `provider_review_no_fix_required` transition, and the
  `review-input-record` / `requirements-baseline-approval` inventory additions
  to G43.1 and did not review them for correctness beyond Finding 5's seam.
- **Full suite not run.** I ran only disposable offline slices:
  `test_scout02_independent.py` + `test_scout02_graph_set_flow.py` (16 passed),
  `test_g43_run_plan.py` + `test_g13_run.py` + `test_canonical*.py` (72 passed),
  and `test_g43_run_plan.py` + `test_journal_locking_recovery.py` +
  `test_g23_portability.py` + `test_g21_comparison.py` + `test_g21_occurrence.py`
  + `test_index_projection.py` + `test_bug_001_gigs_listing.py` (74 passed), plus
  `compileall` and `tools/verify_installed_schemas.py` (44 schemas, ok). The
  independent verifier owns the complete regression + installed-wheel run.
- **No adversarial execution.** I did not build the forged-selector,
  altered-member-digest, foreign-Graph-Set, descriptor-policy-widening, or
  interrupted-approval fixtures myself (source-edit / fixture-authoring
  boundary). My confidence in the tamper-refusal path is from source reading
  plus the existing focused tests; Astra's isolated adversarial suite remains a
  required gate.
- **v2 downstream readers.** Finding 2's outcomes were derived by reading the
  unmodified reader source against the v2 schema shapes, not by executing
  `gigs` / portability / compare against a live v2 workpad. The direction of the
  failure (fail-closed, mislabelled) is high-confidence from the code; a
  verifier should still reproduce it.
- **No provider / private-Gig / commit / external activity**, per task
  boundary. Structural fixtures in the tests are not evidence of any functioning
  domain workflow, and this review makes no such claim.

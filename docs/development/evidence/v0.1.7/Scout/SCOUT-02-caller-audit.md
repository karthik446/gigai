# SCOUT-02 — Independent caller and compatibility audit

**Date:** 2026-09-08  
**Scope:** Read-only audit for SCOUT-02/G43.2 against the current worktree, the
accepted SCOUT-00 lifecycle contract (including section 1 and A02), and the
accepted user-owned workspace amendment.  This report is independent baseline
evidence, not a review of Terra's in-flight implementation and not proof that
the feature works.

**Boundary:** Only this report is written. No source, test, schema, private Gig,
provider, commit, or publication was changed or invoked. Existing dirty G43.1
source/tests/docs were observed and treated as unrelated concurrent work.

## Executive result

The current callers implement one approved v1 Goal Graph: approval publishes a
v1 active pointer containing one `goal_graph`; Run Plan identity contains one
Goal Graph digest; Run launch re-resolves that one graph before allocation; and
SQLite is rebuilt from the private Git journal. There is no current
`graph_set`, selection-record, semantic selector, or graph-aware Run/Plan
caller in `src/` yet.

SCOUT-02 should therefore be additive and dispatch by exact schema/version. The
highest-risk compatibility mistake is to make a semantic descriptor selector
look like the existing UUID `goal-graph.json.graph_id`, or to let an index/CLI
projection stand in for the approved Graph Set. The implementation must also
make every new input and comparison bind to the selected graph explicitly;
same-Gig or prior-Run identity alone is insufficient.

## Contract anchors used

- G43.2 defines `Gig version -> immutable Graph Set -> one selected graph ->
  sealed Run Plan -> directly confirmed Run`, with structural fixtures only
  ([G43.2](../../../v0.1.7/goals/G43.2-multi-graph-gig-versions-and-graph-selected-run-plans.md)).
- Accepted SCOUT-00 section 1 preserves legacy bytes/readers, distinguishes
  descriptor `graph_id` from Goal Graph UUID, requires alias normalization
  before sealing, and adds `agent_explicit` selection without operator consent
  ([contract amendments](SCOUT-00-contract-amendments.md#1-authority-and-compatibility)).
- Accepted A02 requires two selectable graphs, aliases, invalid/missing
  selection refusal before Run allocation, changed-version behavior, unchanged
  v1 readers, and tamper refusal ([A02 matrix](SCOUT-00-contract-amendments.md#8-caller-ownership-and-acceptance-matrix)).
- The workspace amendment keeps the private Git journal and typed revisions as
  authority, reuses `state.sqlite` as a rebuildable projection, and requires
  explicit full Gig/version/source/tool identity at local-tool dispatch
  ([workspace authority](SCOUT-00-user-owned-gig-amendment.md#journal-authority-and-a-single-rebuildable-scout-view-finding-1),
  [tool boundary](SCOUT-00-user-owned-gig-amendment.md#gig-owned-domain-code-shared-validated-persistence-finding-3)).

## Current caller map and required integration

| Area | Current authority/caller | Required SCOUT-02 integration | Exact hazard to avoid |
|---|---|---|---|
| Approval | `lifecycle.approve_offline` (`lifecycle.py:1444-1550`) validates a v1 proposal, journals `gig_proposal_approved`, then publishes a v1 `active-gig-version.json` with one `goal_graph`. `_recover_approved_publication` (`lifecycle.py:1553-1667`) reconstructs that same v1 pointer. | Add a version-aware proposal/approval branch that seals and journals the Graph Set reference/digest and validates every descriptor/member before publication. Keep the v1 branch byte-identical; recovery must identify which approved schema family it is recovering. | Adding optional Graph Set fields to v1 schemas/pointers, rewriting old approval bytes, or publishing a pointer whose Graph Set is not present at the tagged approval commit. |
| Proposal validation/build | `validate_proposal_workpad` (`validators.py:655-728`) requires `manifests/gig-proposal.json`, `manifests/goal-graph.json`, and v1 artifact refs. `_build_proposal_artifacts` (`lifecycle.py:1901+`) creates one UUID graph. Create/revise/improve paths call this family (`lifecycle.py:676`, `863`, `1271`, `1403`). | Add strict Graph Set/member validation and a proposal artifact bridge. Validate descriptor policy cannot widen approved Gig policy; validate descriptor selector, aliases, Goal Graph UUID/ref, and exact bytes before proposal publication. Existing custom v1 create/improve readers remain unchanged. | Treating a semantic selector as `goal-graph.schema.json`'s UUID `graph_id`; mapping authoring/catalog material directly to executable authority; allowing changed descriptor content to reuse an old approval/version. |
| Active-version resolution | `run._resolve_authority` (`run.py:577-625`) reads the approved tag/commit, then only `manifests/gig-proposal.json` and `manifests/goal-graph.json`; `_validate_authority` (`run.py:628-657`) validates the v1 graph/proposal and their digest binding. | Resolve requested/current version from its immutable tag and commit, then dispatch v1 or Graph Set authority. For v2, load the approved Graph Set from that commit and validate the selected descriptor/Goal Graph chain. Keep historical version selection independent of the current pointer. | Reading the working-tree Graph Set or current projection for an old version; using the current active pointer's graph for a requested historical version; accepting a Graph Set or Goal Graph from another Gig/version. |
| Run Plan create/read/identity | `create_run_plan` (`run_plan.py:865-1073`) calls `_resolve_authority`, creates v1 review participants/inputs, and derives identity in `_identity_projection` (`run_plan.py:673-710`) from one `goal_graph_sha256`. `read_run_plan` (`run_plan.py:832-846`) validates v1 schema and sealed refs. | Add an explicit graph-aware plan schema/path or additive v2 dispatcher. Plan identity must include schema/mode, approved commit/version, Graph Set digest, semantic selector, selected descriptor/Goal Graph refs/digests, selection-record digest, and exact selected input refs. Accept/reuse a valid sealed selection; never infer from request prose or ambient conversation. | Reusing v1 identity for a graph-aware plan; placing a selector in `goal_graph`; accepting an unsealed/foreign/altered selection record; allowing an input path/digest to omit the graph that owns it. |
| Managed Run handoff | `launch_run` (`run.py:83-371`) reads a plan before allocation, then `_validate_plan_handoff` (`run.py:659-780`) checks v1 plan/Goal Graph/source/target identities and requires direct operator consent when a plan is used. CLI `run` (`cli.py:2464-2589`) constructs `direct_cli_confirm`. | Dispatch v1 managed plans to the current provider-review/worker behavior. Dispatch a graph-aware plan through a graph-aware manifest builder that carries Graph Set, selector, Goal Graph UUID/ref, selection evidence, and exact inputs; validate all before `_allocate_run_id`. Keep managed provider Run consent separate from graph selection. | Letting `agent_explicit` become `operator_explicit` or `direct_cli_confirm`; calling graph selection approval; allocating a Run before selected graph, foreign refs, policy widening, or source tamper checks complete. |
| Agent invocation | `invocation.parse_invocation` (`invocation.py:97-140`) accepts bounded agent envelopes, rejects agent-provided Run consent, and currently permits only `setup/models/doctor/create/run`; `cli.py:407-435` is validation-only. | If SCOUT-02 records agent selection, bind `agent_explicit` to a strict invocation reference/actor without claiming user approval. Extend only the new selection input boundary; preserve recursive forbidden-key checks and current v1 `run` consent behavior. | Treating an agent envelope as operator consent, or silently using an invocation actor in an operator-only selection record. The accepted amendment requires exact invocation provenance, not merely a free-text agent claim. |
| Journal writer/index | `journal.record_transition` (`journal.py:155-181`) is the lock/commit boundary and closed `TRANSITIONS` set (`journal.py:36-100`); `index.read_index` rebuilds `state.sqlite` from committed journal history (`index.py:49-145`). The index currently projects only proposal/active pointer/entries. | Add only typed additive transitions and projection fields needed for Graph Set/selection/Run references. Rebuild from committed artifacts; preserve `interview_events` during SQLite replacement (`index.py:163-243`). New Run authority must read committed Git/tag bytes, not a mutable SQLite row. | A shaped Graph Set or selection file becoming authority without a handoff; SQLite becoming a second editable source; dropping existing G22 trace during rebuild; accepting stale projection state for new planning. |
| Occurrences/prior Runs | `occurrence.declare_occurrence`/`trigger_occurrence` (`occurrence.py:86-212`) pin Gig/version and call `launch_run`; `compare_occurrences` (`comparison.py:30-146`) reads two same-Gig Runs and compares versions, bundle, Goal Graph, and contracts. | Keep v1 occurrence/comparison callers valid. For graph-aware records, carry Graph Set/selector/Goal Graph identity and require an explicit same-graph or declared cross-graph comparison policy. Prior Run artifacts must be explicit digest-pinned inputs, never inferred from `prior_occurrence_id`. | Same `gig_id` plus same version does not prove same selected graph; current comparison can read two Runs and only label differing Goal Graphs `incomparable`, while a new handoff/input caller could accidentally consume the prior output. |
| Comparison/report readers | `comparison.py` validates v1 `gig-comparison.schema.json` and emits only Goal Graph refs; CLI exposes `occurrence compare` (`cli.py:2843-2874`). General projections use `_read_projection`, `status`, `show`, `plan`, `check`, `history`, and `gigs` (`cli.py:2877-3250`). | Add version-aware read/report projections that display Graph Set digest, selector/alias-normalized selector, Goal Graph UUID, selection provenance, and input refs. Preserve old comparison/report bytes and mark legacy v1 Graph Set as inspection-only. | Rendering a semantic selector as a Goal Graph UUID; claiming a projection or report proves approval; hiding `graph_selection_required`, foreign graph, stale projection, or unsupported schema diagnostics. |
| Portability/listing | `portability.verify_active_version_portability` (`portability.py:51-93`) and `_publication_children` (`portability.py:127-206`) validate the v1 active pointer/publication; `resolve_proposal_lineage` (`portability.py:96-124`) follows v1 proposal ancestry. `listing._read_entry` (`listing.py:202-309`) validates only v1 proposal/pointer schemas. | Version-dispatch portability and listing at the tagged commit. Verify Graph Set and all referenced member bytes, preserve detached historical versions, and keep imported authority inspection-only on transfer. Report `unsupported_schema_version`/deliberate diagnostics rather than treating v2 as malformed v1. | Declaring a v2 Graph Set portable because the v1 pointer is valid; validating only the live pointer and not its Graph Set members; allowing imported approval/selection bytes to become new local Run authority. |
| Strict schema dispatch/inventory | `validators.SCHEMA_NAMES` (`validators.py:28-66`) is a static 37-resource registry. `tools/verify_installed_schemas.py` pins the exact resource set/digests and package data is configured in `pyproject.toml:39-45`; tests also assert the inventory count. | Add every new schema to resource dispatch, `$id` registry, README, `SHA256SUMS`, installed verifier, package/wheel checks, and focused schema vectors. Use exact schema/version dispatch and semantic validators for cross-document identity/policy rules. | Adding files without dispatch/inventory or changing a v1 schema/hash; accepting unknown fields/versions by dropping them; passing valid JSON directly to a caller without semantic authority validation. |

## Identity and authority rules that callers must share

1. **Semantic selector versus Goal Graph UUID.** The Graph Set descriptor's
   semantic selector/alias is a slug (`research-role`, etc.). The attached
   `goal-graph.json.graph_id` remains the canonical `graph_<uuidv4>` entity ID
   validated by the existing Goal Graph/common schemas. A sealed Plan and Run
   must carry both, plus Graph Set digest and approved Gig version. Never feed a
   descriptor selector to `validate_entity_id` as a Goal Graph ID.

2. **Approved source versus projection.** Approval authority is the journaled
   tagged Git commit and its exact artifact bytes. `state.sqlite` and CLI
   status/show/plan/list output are projections. A virtual one-member Graph Set
   for a v1 record may be constructed in memory for inspection only; it must not
   be written into the old approval, pointer, Plan, Run, or journal.

3. **User versus agent selection.** `operator_explicit`, `g44_routed`,
   `only_member_default`, and accepted `agent_explicit` are distinct provenance.
   `agent_explicit` must include a strict agent actor and exact invocation ref,
   but is not operator approval, direct Run consent, or provider authorization.
   G44 routing evidence is selection evidence only; it cannot become Run
   authority by itself.

4. **Legacy v1 bytes.** Existing v1 proposal, active pointer, Plan, Run,
   journal, occurrence, comparison, and portability records remain readable and
   byte-identical. New schemas/resources must be additive. Managed v1 provider
   callers must refuse the external/graph-aware family when its mode/schema is
   not supported, rather than coercing it into a v1 provider invocation.

5. **No cross-graph prior-Run leakage.** A prior occurrence, output, or Run ID is
   not an implicit input. New Plan inputs must name exact records/revisions/blob
   or artifact refs and digests, with the selected graph contract permitting the
   relationship. If a comparison is permitted across graphs, it must be an
   explicit derived comparison with both identities; it must never silently feed
   the prior graph's output into the current graph.

## Executable acceptance cases for Terra/Astra

These are acceptance cases to execute after source stabilizes; this audit did
not run them.

### Authority and selection

- Build one approved fixture with two structural Graph Set members (Career and
  Stock or equivalent). Select each through the real local Plan interface and
  assert the normalized alias, semantic selector, Goal Graph UUID, Graph Set
  digest, Gig version, and selection-record digest all agree.
- Omit selection for the multi-member set; use an unknown selector, an
  ambiguous alias, and an invalid alias. Each returns a stable typed diagnostic
  (`graph_selection_required` for omission where applicable) and creates no Run
  directory or `run_started` handoff.
- Build an only-member set and assert `only_member_default` has deterministic
  GigAI provenance. Assert agent-origin selection is `agent_explicit` with an
  exact invocation ref and cannot satisfy a missing operator confirmation.
- Forge a selector/UUID mismatch, foreign Graph Set, altered member digest,
  descriptor policy widening, and selected Goal Graph mismatch. Each refuses
  before Run allocation and leaves journal/old records unchanged.

### Version and legacy compatibility

- Approve v1 fixture, capture exact proposal/pointer/Plan/Run/journal bytes,
  then read them after Graph Set code is installed. Assert bytes and v1 IDs are
  unchanged; a virtual one-member view is inspection-only.
- Approve Graph Set version N, create a Plan/Run, approve changed member or
  descriptor as version N+1, and assert N still resolves the original Graph Set,
  selector, Goal Graph, and Run history while N+1 resolves only its new bytes.
- Read/list/show/check/plan/Run-details/occurrence/comparison/portability for
  both v1 and Graph Set fixtures. Assert intentional version-aware diagnostics
  for unsupported or malformed resources, with no fallback to the current
  active version.

### Input, comparison, and provenance boundaries

- Attempt to submit a selected graph's Plan with another graph's output, prior
  Run, record, revision, or digest. Assert `external`/Plan validation refuses
  unless an explicit digest-pinned input relationship is present in the selected
  contract.
- Compare two same-Gig Runs from different selected graphs. Assert the result
  is an explicit, inspectable incomparable/refused outcome according to the new
  contract; it must not produce a reusable input binding.
- Replay identical selection/Plan operation key and payload and assert the
  original selection/invocation evidence is returned. Change the payload under
  the same key and assert conflict; use a new key only with explicit selection
  reuse or a new `agent_explicit` selection/Plan.
- Verify direct CLI `--confirm` remains required for managed Run/provider
  execution. Verify no graph-selection API can emit `operator_run_consent` or
  call a provider.

### Journal/index/inventory

- Tamper with the projection and rebuild; assert committed Graph Set/selection
  artifacts remain authoritative and the projection repairs. Run legacy-first
  and Graph Set-first rebuilds with a recognized G22 `interview_events` table;
  assert trace preservation and stale-projection refusal.
- Interrupt Graph Set approval/Plan publication at artifact replacement,
  handoff, and commit boundaries; reconcile through the existing journal path
  and assert no duplicate IDs, approval, or selection authority.
- Run source schema tests, `SCHEMA_NAMES` dispatch, `SHA256SUMS`, installed
  wheel verifier, and package inventory checks. Assert all new schemas reject
  unknown fields and unsupported versions and all old inventory/hash vectors
  remain valid.

## Audit disposition

No unresolved contract ambiguity was added by this caller audit. The remaining
work is implementation/evidence: add the version-aware dispatcher and callers
above, preserve v1 behavior, and execute A02 plus the adversarial cases after
Terra's source stabilizes. This report intentionally does not assess in-flight
implementation quality or claim any provider/live/domain workflow evidence.

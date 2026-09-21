# SCOUT-02 — Corrections independent re-review (Claude)

**Date:** 2026-09-08
**Reviewer:** Claude (independent re-review agent; not an implementer)
**Worktree:** `/Users/kar/orca/workspaces/gigai/gigai-v0.1.7`, branch
`karthik446/gigai-v0.1.7`, committed HEAD `fda4857`. All reviewed source is
uncommitted working-tree change (SCOUT-02 + in-flight G43.1 entangled, no
isolated diff).
**Scope reviewed:** the five required corrections in
[SCOUT-02-review-corrections.md](SCOUT-02-review-corrections.md), the
[corrections implementation record](SCOUT-02-review-corrections-implementation.md),
and my five original [independent-review](SCOUT-02-independent-review.md)
findings, against accepted
[A02](SCOUT-00-contract-amendments.md#8-caller-ownership-and-acceptance-matrix),
[SCOUT-00 §1](SCOUT-00-contract-amendments.md#1-authority-and-compatibility),
the [user-owned Gig amendment](SCOUT-00-user-owned-gig-amendment.md), and the
[caller audit](SCOUT-02-caller-audit.md). Read actual completed source and the
three SCOUT-02 test files; ran bounded offline disposable regressions only.

**Boundary honoured:** No source, test, schema, private Gig, provider, commit,
tag, publication, or cleanup. Only this file was written. Existing dirty G43.1
provider-dogfood work in `provider_review.py`, `adapters/claude_cli.py`, and the
provider-review lifecycle helpers was treated as unrelated concurrent work and
not re-reviewed beyond the v2 manifest seam. Source was treated as frozen for
review. The full suite and the isolated built wheel are the independent
verifier's (Luna's) responsibility, not re-run here.

---

## Verdict: **accepted** (for A02 / the five corrections)

All five required corrections are implemented at the actual ingress boundaries,
with focused regression coverage, and every one of my five original findings is
resolved or reduced to a correctly-scoped G44 deferral. The additive multi-graph
authority chain that the first review accepted as sound is unchanged. v1 schema
bytes, v1 Plan identity projection, and v1 reader behaviour are preserved under
my offline checks.

Remaining items are **non-blocking**: two are latent tightness gaps the
implementation record itself scopes to G44, one is a stale sentence in the
*earlier* (pre-corrections) implementation record. None of them require reopening
A02. Concrete findings and priorities are in the Findings section.

This verdict covers only A02 and the correction brief. It does **not** declare
the whole Scout roadmap, G43.1, or the v0.1.7 release complete, and it does not
substitute for Luna's full-suite + clean-room-wheel verification.

---

## Evidence: what I executed vs. what is source-derived

### Executed offline repros (this re-review, disposable `.venv`, `GIGAI_G30_UAT=0`)

| Command | Result |
| --- | --- |
| `pytest tests/test_scout02_review_corrections.py tests/test_scout02_graph_set_flow.py tests/test_scout02_independent.py` | `18 passed in 14.76s` |
| `pytest tests/test_scout02_independent.py -v` (coordinator-owned, isolated) | `15 passed in 2.43s` |
| `pytest tests/test_g23_portability.py test_g21_comparison.py test_g21_occurrence.py test_g21_contract.py test_bug_001_gigs_listing.py test_index_projection.py test_g43_run_plan.py test_g13_run.py test_canonical.py test_canonical_ownership.py test_journal_locking_recovery.py` | `153 passed in 96.29s` |
| `pytest research/contract_spike/tests/test_schemas.py` | `6 passed, 87 subtests passed in 0.25s` |
| `pytest tests/test_cli_and_scenario_harness.py test_jsl_closeout_regressions.py test_g15_review_substrate.py test_g16_review_loop.py test_g17_capabilities.py test_g19_target_effect_contract.py test_g20_learning_contract.py test_g22_proposal_interview_contract.py test_g26_builder_contract.py test_g27_discovery_contract.py` | `106 passed in 119.03s` |
| `pytest tests/test_g43_provider_review.py test_g43_provider_run_status.py test_g43_response_framing.py test_g07_contract_validators.py test_g08_offline_create_lifecycle.py test_g22_lifecycle.py` | `68 passed in 80.59s` |
| `python tools/verify_installed_schemas.py` | `verified 44 installed GigAI schemas`, exit 0 |
| `python -m compileall -q src/gigai` | exit 0 |
| `ruff check` on `graph_set/listing/portability/comparison/run/run_plan/validators` | `All checks passed!` |
| Local hash sweep of `src/gigai/schemas/*.schema.json` vs `SHA256SUMS` | 44 files / 44 entries / 0 mismatch / 0 missing / 0 extra |
| `git diff HEAD --name-only -- 'src/gigai/schemas/*.schema.json'` | empty (no tracked v1 schema modified; 9 new files untracked) |
| Direct probe of v2 `$defs` strictness markers | every nested `$def` in `run-plan-v2` / `run-manifest-v2` has `additionalProperties:false` + `required` |

Total executed here: **≈434 offline test cases + subtests**, all passing. No
provider call, no private Gig, no network, no `GIGAI_G30_UAT=1` case.

### Source-derived claims (read, not executed end-to-end here)

- The `agent_explicit` journaled-invocation authentication path
  (`graph_set._agent_invocation_findings` / `_journaled_agent_invocation`) is
  reviewed from source plus `tests/test_scout02_review_corrections.py`; I did not
  hand-build an adversarial forged-journal fixture. SCOUT-02 does not itself mint
  `agent_explicit` records, so it cannot be exercised through a real
  propose→approve→plan→run flow yet.
- Finding 2's reader outcomes for a live v2 Gig
  (`listing` / `portability` / `comparison` / `occurrence compare`) are confirmed
  by `tests/test_scout02_graph_set_flow.py::test_v2_nested_contracts_and_version_aware_readers`,
  which drives `list_gigs`, `verify_active_version_portability`, `_read_run`, and
  `_provider_review_active` against a real approved two-graph fixture. I did not
  additionally drive the `gigai gigs` / `occurrence compare` CLI surface.
- v1 byte-identity is checked via the regression slices above and the
  `git diff` schema-name check, not a byte-capture-then-replay across a fresh
  install.

---

## Correction-by-correction assessment

### Correction 1 — strict nested v2 Plan / Run-manifest validation → **met**

`src/gigai/schemas/run-plan-v2.schema.json` now carries the v1 nested `$defs`
verbatim: `classification`, `override`, `profile`, `phase`, `participant`,
`input`, `capabilities` — each with `additionalProperties:false`, `required`,
and the v1 enums. `phases`/`participants`/`inputs` items reference those strict
`$defs` (not `{"type":"object"}`). `run-manifest-v2.schema.json` restores the
strict `$defs/goal_contract` (`additionalProperties:false`, `required`,
artifact-ref-shaped `contract`).

- **Serialized-schema path:** `validators.validate_serialized_contract("run-plan-v2.schema.json", …)`
  dispatches to the strict schema.
- **Public read path:** `run_plan.validate_run_plan` selects
  `run-plan-v2.schema.json` when `plan_version == 2`, then re-derives
  `run_plan_id` from `_identity_projection`; `read_run_plan` calls it. The Run
  ingress (`launch_run` → `read_run_plan` → `_validate_plan_handoff`) therefore
  applies the strict nested schema before a Run ID is allocated.
- **Manifest read paths:** `comparison._read_run` and `run._provider_review_active`
  both accept `run-manifest.schema.json` **or** `run-manifest-v2.schema.json`.
- **Checksums / verifier / inventory:** `SHA256SUMS` (44), `SCHEMA_NAMES` (44),
  `tools/verify_installed_schemas.py::EXPECTED_SHA256` (44) are consistent and
  pass; my independent hash sweep matched. No v1 schema hash changed.
- **Vectors:** `test_v2_nested_contracts_and_version_aware_readers` mutates
  `classification` (unknown key), `profile` (bad enum), `phases[0].state` (bad
  enum), `participants[0].roles` (bad enum), `inputs[0]` (missing `snapshot_ref`),
  `capabilities` (unknown key) for the Plan, and `goal_contracts` (unknown key /
  missing `contract` / malformed extra member) for the manifest — each rejected.
  I re-confirmed the strictness markers by direct schema probe.

Caveat, not a defect: v2's `run-plan` `state` enum drops the pre-seal v1 states
(`declared`…`assignments_validated`) and the v1 `allOf` `sealed_at`/`sealed_by`
null-vs-present conditional, making those fields required-non-null
unconditionally. This is a deliberate narrowing (v2 Plans are only ever created
sealed), stricter than v1, and consistent with the additive-with-old-guarantees
intent — but it is not literally the v1 state machine. Worth a one-line note in
the record; no change requested.

### Correction 2 — v2 listing / portability / comparison / occurrence → **met**

- **Listing** (`listing._read_entry`): `_valid_manifest_versioned` now tries
  `gig-proposal.schema.json` then `gig-proposal-v2.schema.json` (and the
  active-pointer pair), returning the matched schema name. A valid sealed v2
  proposal/pointer renders its real `name` / `status` / `active_version`;
  `_valid_active_pointer` resolves the historical proposal with the matching v2
  schema. `test_v2_nested_contracts_and_version_aware_readers` asserts
  `title == "two-graph-readers"`, `status == "Approved"`, `version == "v2"`, and
  **no** `proposal_metadata_invalid` / `active_version_metadata_invalid`
  diagnostic — i.e. a valid v2 Gig is no longer indistinguishable from
  corruption. Field names align (`name`, `status`, `active_version`,
  `approved_proposal_id` present in both v2 schemas).
- **Portability** (`portability.verify_active_version_portability`): a live
  pointer that validates as `active-gig-version-v2` now checks
  `journal_commit` + `journal_tag` are present and that the tag resolves to that
  commit (`_require_approval_tag`), then raises a **typed**
  `PortabilityError(code="unsupported_schema_version")` with an
  "inspection-only" message — no longer the mislabelled `refused_unsealed_pointer`
  on a sealed valid pointer. Test asserts `portability.value.code ==
  "unsupported_schema_version"`.
- **Comparison** (`comparison._read_run` + `_selected_graph_identity`):
  `_read_run` accepts v1 or v2 manifests. `_selected_graph_identity` returns
  `(None, None, None)` for a v1 Run and the
  `(graph_set.content_sha256, selected_graph_id, selected_graph.content_sha256)`
  tuple for a v2 Run, and `compare_occurrences` marks the pair `incomparable`
  ("selected Graph identities differ") when those tuples differ — so a v1/v2
  pair, or two different selected graphs, are **deliberately incomparable**, not
  implicitly treated as one graph. Test drives `_read_run(workpad, run_id)` on a
  real v2 Run and asserts `selected_graph_id == "career"`.
- **Occurrence**: `occurrence.py` is unchanged and reads no manifest directly;
  `occurrence trigger` still routes through the v2-aware `launch_run`, and
  `occurrence compare` inherits the fixed `comparison` path. No mislabelling of a
  valid v2 sealed pointer in any of the four readers.

### Correction 3 — README schema catalogue → **met**

`src/gigai/schemas/README.md` now covers all seven new resources in **both**
places: a SCOUT-02 amendment paragraph (lines 49–54, with version relationship
narrative) and one `## Files` bullet per schema (lines 97–108, purpose +
v1-relationship). `SHA256SUMS`, `SCHEMA_NAMES`, the installed verifier, and the
contract-spike `EXPECTED_SCHEMA_NAMES` are all consistent at 44 and no new schema
family was invented. (The verifier's `EXPECTED_SHA256` also gained the three
G43.1 receipt/review-input entries that were previously in `SHA256SUMS` but not
the verifier — a pre-existing inventory gap this correction also closed; benign.)

### Correction 4 — agent-explicit provenance at the selection acceptance boundary → **met**, with a scoped G44 residual

`graph_set.validate_selection_record` now, when a workpad `root` is supplied and
`selection_kind == "agent_explicit"`, calls `_agent_invocation_findings`, which
binds:

1. path shape exactly `agent-invocations/<name>.json` — two components, no
   absolute path, no backslash, no `..`, `.json` suffix, and no symlinked
   component (`_reject_symlink_components`);
2. exact bytes: `len(payload) == size_bytes` and
   `digest_imported_bytes(payload) == content_sha256`;
3. a valid bounded agent envelope via `invocation.load_invocation_bytes`
   (`parse_invocation`: protocol version, `inv_` id, explicit trigger, agent
   actor with `id` + `session_id`, allowed command, forbidden-key rejection);
4. filename equals `<invocation.invocation_id>.json`;
5. selection actor `id` equals the invocation actor `id` (and `session_id` when
   the selection actor supplies one) — else `agent_invocation_actor_mismatch`;
6. `_journaled_agent_invocation`: `git log` of the reference path, and for a
   commit whose committed bytes equal `payload` exactly, an
   `handoffs/*.txt` in that same commit whose JSON front-matter `actor` is
   `{kind: "agent", id: <same id>}` — else `agent_invocation_unjournaled`.

Typed findings exist for each failure mode: `agent_invocation_invalid`,
`agent_invocation_missing`, `agent_invocation_mismatch`,
`agent_invocation_actor_mismatch`, `agent_invocation_unjournaled`. A
syntactically valid artifact reference or `agent-invocations/` prefix alone is no
longer sufficient — bytes, envelope validity, filename↔id agreement, actor
binding, and a committed journal handoff are all required. Selection stays
separate from consent: `launch_run` still raises `run_plan_consent_mismatch`
unless `operator_consent` with `source: direct_cli_confirm` is supplied, for v1
and v2 alike.

**This authentication is reached at the real ingress**, not only in a schema-only
validator:

- `run._validate_plan_handoff` (Run acceptance, before `_allocate_run_id`) calls
  `validate_selection_record(selection_bytes, …, root=resolved.path)` on the
  sealed selection record carried by a v2 Plan.
- `run_plan.create_run_plan` calls it with `root=resolved.path` for an
  operator-supplied `--selection-record`.
- The GigAI-constructed `only_member_default` / `operator_explicit` selection at
  `run_plan.py:903` is called **without** `root`; that is correct — those kinds
  never reach the `agent_explicit` branch (kind is fixed from `graph_selector`),
  so no authentication is skipped.

**Residual (non-blocking, G44-scoped):**

- The journal binding in `_journaled_agent_invocation` authenticates
  "*this* agent committed *these* invocation bytes and *some* agent handoff with
  its own actor `id` in one journal transaction." It does **not** require the
  handoff to reference the invocation (no `body_sha256` / invocation-id linkage
  between the handoff and the invocation payload), and the binding key is the
  short agent `id` slug. An agent with journal-write authority and a known `id`
  could therefore construct a passing `agent_explicit` record. This does not
  create operator consent, is not minted by SCOUT-02 (G44 produces
  `agent_explicit`), and the Run still needs `direct_cli_confirm`. It is exactly
  the "exact invocation provenance" tightness the first review's Finding 4 asked
  for, now substantially delivered; the last increment (handoff↔invocation
  content binding) belongs to G44's producer work. See Finding 2 below.
- `graph-selection-record-v2.schema.json` makes the agent-actor `session_id`
  optional, so a selection record may omit it and step 5 then binds on `id`
  only. Minor; note in the record.

### Correction 5 — provider-review recovery at the v2 seam → **met**

`run._provider_review_active` now accepts a manifest that validates as
`run-manifest.schema.json` **or** `run-manifest-v2.schema.json`, while keeping
every existing G43.1 guard: sealed `operator-consent.json` referenced from
`sealed_sources` with a matching digest, `provider_review_requested is True`,
and scope `gig_id` / `project_id` agreement. An abandoned v2 graph-selected
provider review is therefore left to its own terminalizer rather than being
treated as an ordinary detached-worker Run (which would mark Graph Goals
failed). `test_v2_nested_contracts_and_version_aware_readers` builds a real v2
Run, attaches an `operator-consent.json` + sealed-source ref, and asserts
`_provider_review_active(...)` returns `True`. `run.py:1049–1058,1069–1070` seal
a `run-manifest-v2` manifest whenever `graph_authority`/`selected_descriptor` is
present, so the two sides agree. No provider was invoked; this is the
offline/fake-adapter seam only.

### Inventory / fixture / CLI-expectation corrections → **met**

- The nine `len(SCHEMA_NAMES) == 44` assertions (previously 37) are updated
  across `test_g15/g16/g17/g19/g20/g21/g22/g26/g27` and the contract-spike
  `test_all_schema_documents_are_valid_draft_2020_12` / `_one_golden_instance_…`
  (`len == 44`).
- The contract-spike `EXPECTED_SCHEMA_NAMES` gains all seven new names, and
  `valid_instances()` supplies **real golden instances** for every new boundary
  (`gig-graph-set`, `graph-selection-record`, `graph-selection-record-v2`,
  `gig-proposal-v2`, `active-gig-version-v2`, `run-plan-v2`, `run-manifest-v2`),
  each passing draft-2020-12 validation + canonical serialization.
  `test_one_golden_instance_for_every_serialized_boundary` asserts
  `set(instances) == set(schemas) - {common}` — i.e. no boundary is left without
  a valid example. This is a genuine example set, not just a changed count.
- The bare-command / socket-permission diagnostics in
  `test_cli_and_scenario_harness.py` pass in my offline run (`106 passed` in the
  harness+regression slice). Five loopback-bind cases that Luna saw fail under a
  restricted sandbox are environment limits, not deterministic corrections; I did
  not reproduce a permission-enabled rerun (out of my boundary).

---

## Findings (prioritised, actionable)

### Finding 1 — [P3] stale "Deliberate limits" sentence in the *earlier* implementation record

**Where:** `docs/development/evidence/v0.1.7/Scout/SCOUT-02-implementation.md:103–109`
still says "v1 readers keep their original single-graph path."

**Detail:** after the corrections, `listing`, `portability`, and `comparison`
readers are now explicitly v2-aware (real v2 display / typed
`unsupported_schema_version` / explicit incomparability). The
*corrections* record (`SCOUT-02-review-corrections-implementation.md` item 2)
documents this accurately, but the older record's wording now under-describes
reader behaviour. Documentation only; no code impact.

**Suggested fix:** add one line to `SCOUT-02-implementation.md` "Deliberate
limits" pointing at the corrections record, or amend the sentence to
"v1 readers keep their single-graph path; four readers additionally emit typed
v2 diagnostics (see SCOUT-02-review-corrections-implementation.md)."

### Finding 2 — [P2] `agent_explicit` journal binding is actor-id-scoped, not invocation-content-bound (G44 residual)

**Where:** `src/gigai/graph_set.py::_journaled_agent_invocation`.

**Detail:** the check confirms a commit contains both the exact invocation bytes
and *an* `handoffs/*.txt` whose front-matter actor is
`{kind:"agent", id:<selector actor id>}`. There is no linkage between that
handoff and the invocation (no `body_sha256`/invocation-id match), and the key is
the short `id` slug. A journal-writing agent with a known `id` could assemble a
record that passes. It is not operator consent, SCOUT-02 does not mint
`agent_explicit`, and Run still requires `direct_cli_confirm`, so this is **not
A02-blocking** — but G44, which will *produce* `agent_explicit`, should close it.

**Suggested fix (G44):** require the authenticating handoff to bind the
invocation — e.g. front-matter carrying the invocation id / `content_sha256`, or
a dedicated `agent-invocation` transition whose recorded artifact set is exactly
`{that invocation, that handoff}` — and bind on the full actor identity
(`id` + `session_id`) rather than `id` alone. Record explicitly in the SCOUT-02
implementation record that SCOUT-02 provides the *acceptance-side* authentication
and G44 owns the *producer-side* binding.

### Finding 3 — [P3] v2 `run-plan` state model is a narrowing of v1, not noted

**Where:** `src/gigai/schemas/run-plan-v2.schema.json` `state` enum + absence of
the v1 `allOf` `sealed_at`/`sealed_by` conditional.

**Detail:** v2 drops the v1 pre-seal states and makes `sealed_at`/`sealed_by`
unconditionally required non-null. This is deliberate and *stricter*, and
matches "additive with old guarantees preserved" in spirit, but it is a
semantic change from v1's lifecycle, so a reader porting v1 expectations onto v2
should be told. Not a defect.

**Suggested fix:** one line in `SCOUT-02-review-corrections-implementation.md`
item 1 stating v2 Plans are sealed-only by construction and the v1 draft-state
machine does not apply.

### Finding 4 — [P3] `session_id` optional in `graph-selection-record-v2` agent actor

**Where:** `graph-selection-record-v2.schema.json` `$defs/agent_actor`
(`required: ["kind","id"]`).

**Detail:** the selection record can omit `session_id`; the acceptance check
then binds on `id` only (the invocation envelope still requires its own
`session_id`). Combined with Finding 2, the practical binding key is the agent
`id` slug. Minor.

**Suggested fix (G44):** make `session_id` required in the v2 agent actor and
assert selection `session_id == invocation.actor.session_id` in
`_agent_invocation_findings`.

---

## Evidence limitations

- **G43.1 / SCOUT-02 entanglement.** Shared uncommitted working tree and shared
  files (`run.py`, `journal.py`, `cli.py`, `validators.py`,
  `schemas/SHA256SUMS`, `schemas/README.md`). No isolated SCOUT-02 diff exists.
  I attributed the provider-review lifecycle helpers, the
  `provider-review-closeout-receipt` schema, and the review-input inventory
  additions to G43.1 and did not re-review them beyond the v2 manifest seam in
  `_provider_review_active`.
- **Full suite not run.** I executed the focused + related regression slices
  above (~434 cases, all green). I did not run
  `GIGAI_G30_UAT=0 .venv/bin/pytest` in full, and I did not build or install a
  wheel — those are Luna's gates. The corrections record's own
  `747 passed, 5 failed (loopback-bind env), 1 skipped (live-provider gate)` and
  its isolated-wheel result are Terra's self-report, not verified here.
- **No adversarial fixture authoring.** The forged-journal, foreign-Graph-Set,
  altered-member-digest, and policy-widening adversarial cases were not
  hand-built by me (fixture-authoring boundary). My confidence in the
  agent-invocation authentication and tamper-refusal paths is from source
  reading plus `tests/test_scout02_review_corrections.py` (missing / tampered /
  actor-mismatch / accepted). Astra's / Luna's adversarial suite remains a
  required gate.
- **Reader outcomes** for Finding 2 are confirmed through the flow test's
  library calls (`list_gigs`, `verify_active_version_portability`, `_read_run`,
  `_provider_review_active`) against a real approved two-graph fixture, not
  through the `gigai gigs` / `occurrence compare` CLI surface.
- **No provider / private-Gig / commit / external activity**, per task boundary.
  Structural fixtures are not evidence of any functioning domain workflow, and
  this re-review makes no such claim. This document accepts only the five
  corrections against A02; it does not declare Scout, G43.1, or the v0.1.7
  release complete.

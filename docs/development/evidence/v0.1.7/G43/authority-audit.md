# G43 Authority Audit (read-only)

**Scope:** map the existing authority / consent / Run schema paths that the G43
implementation MUST preserve, and flag security + lifecycle pitfalls in plan
sealing, digest binding, reruns, profile participant assignment, budget
enforcement, and verification artifacts.

**Method:** read of the active G43 contract
(`docs/development/v0.1.7/goals/G43-adaptive-review-profiles-and-sealed-run-plans.md`)
against the current `src/gigai/*` implementation. No files were edited, no
destructive commands run, no commit made.

---

## 1. Existing authority / consent / Run schema paths G43 must preserve

### 1.1 Active-version authority (the sole approved-version authority)

- `run.py:286` `_resolve_authority()` — resolves the approved Gig version from
  the committed **index projection** `active_version` (`run.py:289-291`), then
  **re-validates** it against `active-gig-version.schema.json`
  (`run.py:295-299`) and cross-checks the immutable git tag
  `gig-v%06d` == `journal_commit` (`run.py:307-323`). Divergence between the
  pointer and its tag is a hard `RunError` (`run.py:322-323`).
- `run.py:337` `_validate_authority()` — re-validates goal-graph + proposal
  schema, runs `validate_goal_graph` semantic pass, requires
  `proposal.status == "approved"`, `proposal.gig_id` match, and that the
  proposal pins the graph digest (`run.py:349-359`).
- `occurrence.py:479` `_active_pointer()` / `occurrence.py:496`
  `_selected_version()` — the G21 precedent: an occurrence may only select the
  **current** approved version (`occurrence.py:500-502`).

  **G43 must:** read this same projection + tag cross-check and record only a
  *reference* (gig_id, gig_version, `goal_graph_sha256`,
  `review_contract_sha256`, `journal_commit`) into the plan. It must not copy
  proposal/graph bytes as a private authority, and it must re-resolve +
  re-verify at `gigai run --plan` handoff (contract §"Run-Plan artifact and
  sealing", §"State and authority invariants" #1).

### 1.2 Run consent path (direct-operator-only)

- CLI: `cli.py:2296` `run_command` — `--confirm` is mandatory
  (`cli.py:2378-2379`); an `--invocation` envelope additionally requires
  `--confirm` (`cli.py:2340-2343`) and must match home/project/gig/version/wait
  exactly (`cli.py:2350-2377`). The consent object is built **in the CLI**
  with `source: "direct_cli_confirm"` and `actor {kind: operator, id:
  local-user}` (`cli.py:2380-2387`).
- Enforcement: `run.py:105-119` — `launch_run` rejects any consent whose
  `source != "direct_cli_confirm"` (`run.py:106-107`), then **synthesizes the
  authoritative scope server-side**: `project_id`, `gig_id`, `gig_version`,
  `target_kind`, `target_observation_sha256`, plus a fresh
  `confirmation_id` and `redeemed_before_allocation: true`
  (`run.py:108-119`). Scope is *not* taken from the caller.
- `invocation.py:126-129` — an agent invocation with `command == "run"` and
  any `consent` array is rejected outright; `invocation.py:275-292`
  (`_consent`) only ever accepts `actor {kind: operator, id: local-user}`.
- The redeemed consent is persisted as a sealed source:
  `run.py:401-412` writes `runs/<run_id>/operator-consent.json` and
  `run.py:476` appends `consent_ref` into the manifest `sealed_sources`
  array. There is **no** `operator-consent.schema.json` — the consent object
  is schema-free today (see pitfall 5.4).

  **G43 must (contract §"Run-Plan artifact and sealing"):** at
  `gigai run --plan PLAN_ID --confirm`, extend this exact scope with
  `run_plan_id` and the plan artifact's `content_sha256`, redeem **before**
  Run-ID allocation, seal beside the plan ref under the Run, and refuse on
  missing / replayed / mismatched plan consent
  (`run_plan_consent_mismatch`, `run_plan_already_handed_off`). The synthesis
  must stay server-side in `run.py` — do not let `run-plan` pre-compute the
  consent scope.

### 1.3 Run allocation + sealing path

- `run.py:120-122` — `_allocate_run_id()` then `run_path.mkdir(mode=0o700)`.
  ID via `generate_entity_id(EntityPrefix.RUN, is_persisted=<dir exists>)`
  (`run.py:1158-1164`).
- `run.py:124-134` `_prepare_records()` builds run-brief, **run-manifest**,
  run-details, goal-graph copy, target-before, sealed offline-capability, goal
  contracts (fetched from `authority_commit` via `git show`,
  `run.py:462-468`).
- `run.py:141-160` — `record_transition(transition="run_started",
  outcome="SEALED")` commits all sealed artifacts atomically to the journal.
- `run.py:229-234` — failed preparation removes the run dir **iff** no
  `*-run-started.txt` handoff exists yet (partial-seal cleanup).
- Manifest schema: `run-manifest.schema.json` — `additionalProperties:false`,
  `authority` const `run_invocation`, `status` const `sealed`,
  `sealed_sources` `minItems:1` array of `common#/$defs/artifact_ref`,
  `aggregate_budget` = `common#/$defs/budget`.

  **G43 must (contract §"Bridge to the existing review loop"):** put the plan
  `artifact_ref` into the Run manifest `sealed_sources`, and only after
  `record_transition("run_started")` succeeds may it materialize review-loop
  records under that `run_id`.

### 1.4 Digest / canonicalization primitives (identity substrate)

- `canonical.py:170` `canonical_json_bytes` — restricted RFC 8785 (sorted
  keys, no floats, ASCII member names, `-2^53..2^53` ints). This is the ONLY
  canonical encoder; the G43 identity projection + artifact digest must use
  it.
- `canonical.py:244` `digest_imported_bytes` (SHA-256 over exact bytes) vs
  `canonical.py:238` `digest_owned_text` (normalizes CRLF/'\n'). Contract
  §"Run-Plan artifact and sealing" says the plan digest is
  `digest_imported_bytes` over the canonical artifact bytes — **the plan JSON
  is GigAI-owned but must be hashed as imported bytes of the canonical
  encoding**, matching how `run.py:138` digests the manifest.
- `canonical.py:33-37` `ENTITY_ID` regex + `canonical.py:100-117`
  `EntityPrefix` enum — **the prefix set is closed and hard-coded**. There is
  **no `run_plan` prefix** and no `EntityPrefix.RUN_PLAN`. `common.schema.json`
  likewise has `run_id`, `handoff_id`, … but no `run_plan_id` def.
- `canonical.py:332` `generate_entity_id` requires a UUID **v4** from the
  factory and only accepts prefixes in the enum. G43's ID derivation is
  *deterministic from a digest* (contract lines 496-501: SHA-256 of identity
  projection → set version nibble to 4 → set RFC 4122 variant), which is the
  pattern already used by `review_loop.py:61` `_stable_id()`. G43 cannot use
  `generate_entity_id` for this and must not; but `validate_entity_id`
  (`canonical.py:307`) will **reject** `run_plan_...` because the regex has no
  such prefix. See pitfall 5.1.

### 1.5 Model-target readiness states (G40/G27 vocabulary)

- `model_discovery.py:102` `ModelReadiness` with `readiness` +
  `states` tuple. `resolve_target_readiness` (`model_discovery.py` ~line 250)
  returns `usable` only for `deterministic` adapters; every networked adapter
  returns `configured` with `states=("configured",)` and reason "explicit
  readiness probe required".
- `probe_target_readiness` (opt-in, may spend provider cost) can return
  `usable` with `states=("configured","compatible","authenticated","verified",
  "usable")`, or `configured` on `ModelAuthenticationRequired`.
- `discovery.py:86-89` collapses readiness into `{detected, configured,
  verified, usable, unavailable, unsupported}` for the discovery manifest.

  **G43 must (contract §"Role and model assignment"):** treat a target as
  eligible for a role **only** when policy says `usable` for that role;
  `detected` / `configured` / `authenticated` alone are insufficient
  (contract lines 267-270). The discovery snapshot is evidence, not approval
  (invariant #4). Persist the `discovery_ref` + `target_configuration_ref` +
  `provider_id` per participant.

### 1.6 Review-loop / verification artifact substrate

- `review_loop.py:180` `run_review_loop` — requires an existing **succeeded**
  sealed Run (`review_loop.py:194-207`), is deterministic/workpad-only,
  reuses `review.py` validators (`validate_finding`, `validate_trace`,
  `validate_report_artifact`, `validate_adjudication`, `validate_review_loop`,
  `validate_review_loop_artifacts`).
- `review-loop.schema.json` — `state` enum currently `{reviewing, verifying,
  feedback_pending, addressing, closing, complete, blocked, unanswerable}`;
  **no `verification_ids` field**, `additionalProperties:false`. Same for
  `report.schema.json`.
- `review.py:679` `validate_review_loop_artifacts` — the replay validator
  that re-checks every referenced artifact resolves to exact bytes.
- `validators.py:28-61` `SCHEMA_NAMES` — the closed tuple of packaged
  schemas; `validators.py:107-117` `_schema_registry()` loads exactly those;
  `validators.py:120-126` `validate_serialized_contract` returns
  `unknown_schema` for anything not in the tuple.
- `journal.py:53-94` `TRANSITIONS` — closed set. **No `run_plan_*`
  transitions.** `_validate_transition` (`journal.py:444-446`) rejects any
  unknown transition string.
- `handoff-frontmatter.schema.json` — `run_id` is `run_id | null` (nullable),
  `transition` enum is closed and has no plan transitions; `goal_id`,
  `gig_version` nullable. So a *pre-`run_id`* plan handoff can satisfy the
  nullable fields but **cannot use a new transition name**.

  **G43 must (contract §"Bridge…"):** add `verification-record.schema.json`
  (`urn:gigai:schema:verification-record:1`), bump `review-loop` to v1.1 and
  `report` to v1.1 with **optional** `verification_ids`, and register all
  three in `SCHEMA_NAMES`. Older v1.0 artifacts stay readable but cannot
  satisfy a G43 Run. It must NOT invent a private review format.

### 1.7 Budget algebra

- `common.schema.json#/$defs/budget` — `{max_model_calls, max_tool_calls,
  max_tokens, max_cost (decimal-string|null), currency (`^[A-Z]{3}$`|null),
  max_wall_time_ms, max_parallel_goals (1..1024)}`.
- `model_execution.py:77-99` `InvocationBudget.reserve()` — fail-closed
  pre-call reservation: refuse if `model_calls >= max_model_calls` or
  `tokens + requested_output > max_tokens`. Reserved **before** the adapter
  call at `model_execution.py:224-226`.
- `common.schema.json#/$defs/usage.cost_status` enum = `{provider_reported,
  derived, unavailable, not_applicable}`.
- `model_call.py:invoke_bounded` — hard caller-side wall-time boundary;
  distinct `BoundedCallError` reasons `{budget_exhausted, cancelled,
  timed_out, failed}`; a late/timed-out result is discarded, never written.

  **G43 must (contract §"Budget accounting"):** reuse this budget object and
  the reserve-before / reconcile-after discipline, add the sealed
  `usage_unreported_policy` field (`block_before_call` |
  `reserve_remaining_budget`, never inferred), and keep terminal evidence
  distinguishing `budget_exhausted`, `cost_unavailable`, `usage_unavailable`,
  `timed_out`, provider failure.

### 1.8 Agent invocation seam (G40)

- `invocation.py` — `parse_invocation` / `load_invocation_bytes`.
  `ALLOWED_COMMANDS` = `{setup, models, doctor, create, run}` — **no
  `run-plan` / plan command**. `_FORBIDDEN_KEYS` blocks `conversation`,
  `transcript`, `messages`, `raw_prompt`, `hidden_prompt`, `secret*`,
  `token*`, `credential*` recursively (`invocation.py:305-316`).
  `MAX_ENVELOPE_BYTES = 256 KiB`.
- `_INPUT_FIELDS["run"] = {gig_id, version, wait}` only.

  **G43 must (contract §"Product boundary"):** if `run-plan create` is
  reachable through the invocation seam, extend `ALLOWED_COMMANDS` +
  `_INPUT_FIELDS` with a bounded typed input (`class`, `artifact_class`,
  `profile`, `input` IDs) and keep the recursive forbidden-key + size guard.
  The agent still cannot provide plan consent, choose an unapproved version,
  or alter a sealed plan.

### 1.9 Workpad locator authority

- `project_binding.py:63` — `workpad_locator = f"registry:{project_id}"`
  (i.e. `registry:project_<uuid>`), validated equal at
  `project_binding.py:119-120`. `listing.py:326` cross-checks the registry
  record. This is the registry locator, **not** a filesystem path.

  **G43 must:** store `workpad_locator` as this exact registry string in the
  plan (contract line 439-440), never a resolved path.

---

## 2. Pitfalls: plan sealing

- **2.1 Identity-projection drift vs. artifact digest.** The contract defines
  *two* hashes: the idempotency key (canonical bytes of the identity
  projection, lines 496-532) and the artifact `content_sha256`
  (`digest_imported_bytes` of the whole canonical artifact, lines 534-537).
  Pitfall: computing the plan ID from the full artifact (which includes
  `created_at`, `sealed_at`, `state`) makes every seal non-idempotent.
  **Test:** two `run-plan create` calls with identical inputs/policy/profile/
  assignments must return the same `run_plan_id` and the same
  `content_sha256`; changing only `created_at` must not change the ID.
- **2.2 Projection completeness.** If any routing/authority-bearing field is
  omitted from the identity projection (e.g. `usage_unreported_policy`,
  `opt_in` ref, `independence_group`, per-participant `target_configuration_ref`),
  two materially different plans collide on one ID — a consent-binding
  bypass. Contract line 557-560: "digest covers every field that affects
  routing, identity, authority, inputs, participants, budgets, or effects."
  **Test:** perturb each such field individually and assert a new ID or an
  explicit mismatch refusal.
- **2.3 Array ordering.** Contract line 524-526: phase order and participant
  order are significant; only set-like arrays are sorted. Pitfall: passing
  participants/phases through `canonical_json_bytes` (which sorts *object
  keys* but preserves list order) is fine, but any pre-sort of the
  participant list erases the reviewer/verifier identity ordering the
  independence groups depend on. **Test:** swap p1/p2 reviewer entries →
  different plan ID.
- **2.4 `additionalProperties:false` everywhere.** Contract line 431 + "No
  nested object accepts unknown fields." A model/agent adding a field after
  validation must be rejected at re-validation on handoff, not just at
  create. **Test:** hand-edit the sealed artifact to add a key, run
  `gigai run --plan` → `run_plan_digest_mismatch` / `run_plan_invalid`.
- **2.5 Null-or-required sealing rule.** Contract line 490-494: pre-seal
  validation record has `sealed_at=null, sealed_by=null`; any *persisted*
  `sealed`/`handed_to_run_authority`/terminal plan requires both. Pitfall:
  persisting a `sealed` state with null `sealed_by`. **Test:** attempt to
  persist each state with mismatched null-ness → refusal.
- **2.6 Sealed-sources duplicate / traversal.** Contract line 487-488: no
  duplicate `(path, content_sha256)`; `common#/$defs/relative_path` already
  forbids `..` and backslashes. Reuse that def; occurrence's `_safe_path`
  (`occurrence.py:624-634`) is the local precedent for path containment.
  **Test:** duplicate ref, `../` ref, symlinked ref all refused.
- **2.7 Partial-seal cleanup.** Mirror `run.py:229-234`: if journal commit of
  the plan fails after the `run-plans/<id>/` dir is created, the dir must be
  removed so no half-sealed plan is addressable. **Test:** fault-inject the
  journal write; assert no `run-plan.json` remains and `run-plan show`
  reports `run_plan_not_found`.

## 3. Pitfalls: digest binding & reruns / idempotency

- **3.1 Handoff must re-verify, not trust the stored digest.** At
  `gigai run --plan`, recompute the artifact digest from disk bytes and
  compare to the workpad artifact-ref `content_sha256` **and** re-resolve
  every `sealed_sources` entry + the active-version authority + each
  `discovery_ref` (contract line 352-354, invariant #1/#4). Precedent:
  `run.py:558-567` re-digests the sealed graph + manifest before scheduling;
  `occurrence.py:175-184` re-verifies the reference snapshot identity after
  declaration. **Test:** mutate a referenced input's bytes after seal →
  `run_plan_input_mismatch` before allocation; mutate the plan bytes →
  `run_plan_digest_mismatch`.
- **3.2 Replay must not allocate a second Run.** Invariant #10 + contract
  line 594-598. The plan's consumed identity must be recorded on the Run and
  a second `gigai run --plan SAME_ID` after successful handoff must refuse
  `run_plan_already_handed_off`. Pitfall: keying idempotency on the plan
  file's existence rather than a redeemed-consent / handed-off marker.
  **Test:** run the same `--plan` twice; second call refuses and no new
  `runs/<id>` dir appears.
- **3.3 Changed active version between seal and handoff.** If the operator
  approves a new Gig version after sealing, the plan pinned the old
  `gig_version` + `journal_commit`. `run.py:_resolve_authority` with an
  explicit `version` requires the `gig-v%06d` tag to still resolve to that
  commit. G43 must pass the pinned version explicitly and fail closed if the
  tag/commit no longer matches (contract line 575-576, "Any changed Gig
  version … requires a new plan"). **Test:** seal, approve v2, attempt
  handoff of the v1 plan → refusal, original plan left intact.
- **3.4 Consent scope must include BOTH `run_plan_id` and
  `content_sha256`.** A plan-ID-only binding lets a re-sealed plan with the
  same deterministic ID but different terminal/annotation bytes ride an old
  consent. Contract line 537: "the exact digest used for consent binding."
  **Test:** consent carrying a stale `content_sha256` →
  `run_plan_consent_mismatch`.
- **3.5 Deterministic ID collision surface.** Because the ID is 16 digest
  bytes with the version/variant nibbles overwritten (contract line 498-500),
  ~6 bits are fixed — do not additionally truncate. Reuse the exact
  `review_loop.py:_stable_id` transform. **Test:** golden vector — a fixed
  identity projection JSON → fixed `run_plan_<uuid>` string, checked in as a
  fixture.

## 4. Pitfalls: profile / participant / role assignment

- **4.1 Independence-group enforcement is semantic, not schema.** Contract
  lines 203-209: reviewers must not share an independence group with each
  other; verifiers/adjudicators must not share a reviewer group; duplicate
  group IDs rejected; assignments must match the profile table exactly. JSON
  Schema (`uniqueItems` on participant IDs) cannot express this — it needs a
  dedicated semantic validator like `validators.py`'s
  `validate_goal_graph` / `review.py` cross-checks. **Test matrix:** for
  each of `focused@1/standard@1/deep@1/var@1`: correct assignment passes;
  two reviewers sharing a group → refusal; verifier sharing a reviewer group
  → refusal; duplicate participant ID → refusal; wrong role count vs. table
  → refusal.
- **4.2 `resolver` must never be a provider participant.** Contract lines
  203-205, 476: resolver is the deterministic GigAI phase; `participants[].roles`
  is `{reviewer, verifier, adjudicator}` only; `phases` `resolve` (sequence 4)
  forbids provider participant IDs (line 483). **Test:** a participant with
  `roles:["resolver"]` → schema/semantic refusal; a `resolve` phase listing
  any `participant_id` → refusal.
- **4.3 Role overlap only where the profile states it, and sequential.**
  `focused@1` p1 = `[reviewer, verifier]` with **no independence claim**;
  all others keep identities separate. Pitfall: emitting an independence
  assertion for the focused profile. **Test:** focused plan records
  non-independent verification; standard/deep/var record independent groups.
- **4.4 No hidden escalation / fallback.** Contract lines 211-215, stop
  conditions. Reaching a ceiling → `budget_exhausted` / `inconclusive` /
  `blocked`; never spawn a fallback participant, never auto-upgrade
  `focused`→`standard`. `deep`/`var` require a recorded opt-in artifact
  **before** sealing (`opt_in` non-null). **Test:** omit `--profile` with
  ambiguous classification → `classification_ambiguous` (not a silent
  `standard`); request `deep` without opt-in → `profile_opt_in_required`;
  drive a profile to its call ceiling → terminal `budget_exhausted`, no 5th
  participant.
- **4.5 Target eligibility gate.** Per 1.5: a participant's `model_target_id`
  must be `usable` for its role per policy; `configured`/`authenticated` is
  refused pre-seal (`target_not_usable`). An assigned target that becomes
  unavailable before handoff → `blocked` or explicit operator replan (new
  plan identity), never a silent swap (contract lines 272-276, invariant #7).
  **Test:** assign a `configured`-only networked target → `target_not_usable`;
  seal with a `usable` target, then make it unavailable, attempt handoff →
  `blocked`, original plan intact; replan → new `run_plan_id`.
- **4.6 Capability / review-contract satisfaction.** Contract lines 277-279:
  role assignment must satisfy the selected Gig's capability manifest +
  review contract; missing capability → `capability_missing` before sealing.
  Reuse the capability-manifest authority (`capabilities.py`,
  `capability-manifest.schema.json`), do not let the plan grant a capability
  (invariant #3). **Test:** Gig requiring a capability the manifest lacks →
  `capability_missing`.
- **4.7 Global maximum envelope is a hard clamp.** Contract lines 164-170:
  6 participants, 24 model calls, 32 tool calls, 100k tokens, USD 15.00, 3
  review passes, 2 verify passes, 2 adjudication loops, 30 min, 1 parallel
  goal. A project policy may only *reduce*. An operator override may not
  exceed it (`profile_not_allowed` / `budget_invalid`). **Test:** policy
  raising a ceiling → rejected; override beyond global max → fails closed.

## 5. Pitfalls: budget enforcement

- **5.1 `run_plan_` prefix is unregistered.** `canonical.py` `ENTITY_ID`
  regex + `EntityPrefix` enum + `common.schema.json` have **no `run_plan`**.
  Any call to `validate_entity_id("run_plan_…")` throws
  `InvalidIdentifierError`. **Action for impl:** add `RUN_PLAN = "run_plan"`
  to `EntityPrefix`, extend the `ENTITY_ID` alternation, add
  `run_plan_id` to `common.schema.json#/$defs`, and add
  `verification_id` pattern. **Test:** `validate_entity_id` round-trips a
  canonical `run_plan_<uuidv4>` and rejects a non-v4 / uppercase variant.
- **5.2 `currency` must be `USD` for every G43 profile.** Contract line 167:
  "Every profile uses `USD`." `common#/$defs/budget.currency` only enforces
  `^[A-Z]{3}$`. G43's plan/profile validator must additionally require
  `currency == "USD"` and `max_cost` non-null for the four catalog profiles.
  **Test:** a profile with `currency:"EUR"` or null cost → `budget_invalid`.
- **5.3 Reserve-before / reconcile-after for every accounted resource.**
  `model_execution.py` only reserves model-calls + tokens. G43 needs the
  same for cost, wall-time, tool-calls, passes, adjudication loops. Contract
  lines 226-249: reserve request allowance before each call; refuse if it
  would exceed token/model-call/cost/wall-time ceiling; reconcile actuals
  after; an over-reservation response is retained as evidence and the plan
  enters `budget_exhausted` with no further call. **Test:** a response
  exceeding its reservation → retained + `budget_exhausted`; wall-time
  deadline checked before each phase and each provider/tool call (line
  248-249).
- **5.4 `usage_unreported_policy` never inferred.** Contract lines 238-246.
  It is exactly `block_before_call` or `reserve_remaining_budget`, sealed
  into the plan `profile` object. Pitfall: defaulting it at runtime when a
  provider reports neither tokens nor cost. **Test:** provider with no
  usage/cost + `block_before_call` → first unaccountable call refused;
  + `reserve_remaining_budget` → remaining tokens + non-null cost reserved,
  `usage_unavailable` recorded, no later provider call permitted.
- **5.5 `cost_status: derived` requires a configured price table.** Contract
  lines 234-237: provider-reported cost preferred; a configured price table
  yields `derived`; otherwise a non-null `max_cost` with unreported cost
  blocks the next call (`cost_unavailable`). **Test:** the three branches
  (`provider_reported`, `derived`, `cost_unavailable`) produce distinct
  terminal evidence.
- **5.6 Consent object has no schema today.** `run.py` builds and persists
  `operator-consent.json` with no `operator-consent.schema.json` in
  `SCHEMA_NAMES`. G43 adds `run_plan_id` + `content_sha256` to this scope —
  strongly consider adding a consent schema now so the extended scope is
  validated on redemption, not just constructed. **Test:** malformed /
  extra-field consent → refusal before allocation.
- **5.7 Wall-time clock start.** Contract line 248: "Wall time begins when
  the sealed plan is handed to Run authority." Pitfall: starting it at
  `run-plan create`. **Test:** time between create and handoff does not
  consume `max_wall_time_ms`.

## 6. Pitfalls: verification artifacts & the review-loop bridge

- **6.1 New schemas must be registered + versioned additively.**
  `verification-record.schema.json`
  (`urn:gigai:schema:verification-record:1`, path
  `runs/<run_id>/review/verification/verification_<uuidv4>.json`),
  `review-loop.schema.json` v1.1 (+ optional unique `verification_ids`),
  `report.schema.json` v1.1 (+ optional unique `verification_ids`). All three
  must be added to `validators.py:SCHEMA_NAMES` (`unknown_schema` otherwise)
  and to `schemas/SHA256SUMS` / `README.md`. `additionalProperties:false` on
  the new schema. **Test:** v1.0 loop/report still validate; a G43 loop
  *without* `verification_ids` for every verification record → semantic
  refusal; a `verification_ids` ref that does not resolve to exact bytes →
  refusal (contract lines 640-646).
- **6.2 Verification never mutates a finding.** Contract lines 305-307,
  632-637: `outcomes[].status` ∈ `{verified, unverified, contradicted,
  blocked}`; each outcome names one existing `finding_id` already in the
  loop; `source_finding_ids` non-empty + unique; every `evidence_refs` entry
  is an existing `artifact_ref`; the source finding is never deleted or
  overwritten. Precedent: `review_loop.py` writes finding versions as new
  files `findings/<id>/vN-<status>.json`, never in place. **Test:** a
  verification outcome referencing a finding not in the loop → refusal;
  attempt to rewrite `v1-open.json` → refusal.
- **6.3 No `run_id` back-link field added to finding/report/adjudication.**
  Contract lines 648-652: their Run relationship is the Run-scoped path + the
  trace/loop references + the enclosing `review-loop` record. Do not add a
  parallel unvalidated Run-link field. **Test:** schema diff review — no new
  `run_id` property on `finding`/`report`/`adjudication`.
- **6.4 No review-loop record before `run_id` exists.** Contract lines
  600-607: the existing review-loop contract requires a `run_id`; planning
  must not create a loop record. Loop materialization happens only *after*
  `run_started` seals the manifest with the plan ref. **Test:**
  `run-plan create` writes only `run-plans/<id>/run-plan.json` + a journal
  entry — no `manifests/review-loop.json`, no `findings/`, no `traces/`.
- **6.5 Journal transitions for the plan lifecycle.** `journal.py:TRANSITIONS`
  is closed and has no `run_plan_*`. G43's `declared → classified →
  profile_selected → assignments_validated → sealed → handed_to_run_authority`
  plus terminals `{blocked, cancelled, rejected, inconclusive}` need either
  new transition names added to `TRANSITIONS` **and**
  `handoff-frontmatter.schema.json`'s `transition` enum, or a decision to
  keep pre-seal states in memory (contract line 492-494 permits this) and
  only journal the `sealed` + handoff events. Pick one explicitly. **Test:**
  every persisted plan state has a valid journal entry; `_validate_transition`
  accepts each new name.
- **6.6 Abstention is a first-class terminal, not an error.** Contract
  §"Human override and abstention": ambiguous classification, missing
  references, unverifiable findings, unresolved disagreement, ceiling
  reached, unavailable provider/role → a terminal that names the next
  permitted operator action; `inconclusive` / `blocked` / `operator_required`
  are outcomes, not exceptions. Adjudication with no resolution must **not**
  fabricate a winner (contract lines 309-316, 699-701). **Test:** unresolved
  model adjudication → `operator_required` (or adjudication input), all
  contributing findings still inspectable, no synthetic "winner" finding.
- **6.7 `not_required` phase needs a recorded reason.** Contract lines
  289-292, 481-482: a phase may be `not_required` only when Gig contract +
  profile allow it, and must carry `not_required_reason`; `state` is
  `planned` iff `required`, else `not_required`. **Test:** `not_required`
  phase without reason → refusal; `required:true` with `state:"not_required"`
  → refusal.

---

## 7. Recommended test matrix (consolidated)

### Deterministic (no provider) — prove schema, identity, refusal, budget shape

| Area | Case | Expected |
|---|---|---|
| Classification | each of `planning/research/fact_check/document_review/code_review/comparison` × `text/code/structured_data/mixed/unknown` fixture | deterministic `task_class`+`artifact_class`, stable `classifier_version` |
| Classification | conflicting Gig-contract vs operator input | Gig contract + operator input win over heuristic; recorded precedence |
| Classification | ambiguous / low-confidence / unknown artifact / missing required input | `classification_ambiguous` or `classification_unsupported`; **never** silent `standard`/`deep` |
| Classification | explicit `--class` / `--artifact-class` override | recorded actor + reason + inputs |
| Profile | select each `focused@1/standard@1/deep@1/var@1` | participant/role/phase/ceiling match the table exactly |
| Profile | `deep`/`var` without opt-in | `profile_opt_in_required`; with recorded opt-in artifact → sealed |
| Profile | project policy reducing a ceiling | accepted; policy raising a ceiling → rejected |
| Profile | override beyond global max envelope | fails closed (`profile_not_allowed`/`budget_invalid`) |
| Participants | reviewers sharing an independence group | refusal |
| Participants | verifier/adjudicator sharing a reviewer group | refusal |
| Participants | duplicate participant ID / duplicate independence group ID | refusal |
| Participants | `roles:["resolver"]` on a participant / `participant_id` in `resolve` phase | refusal |
| Participants | `focused@1` p1=[reviewer,verifier] | no independence claim recorded |
| Assignment | `configured`-only networked target for a role | `target_not_usable` |
| Assignment | `detected`/`authenticated`-but-not-`usable` | distinct refusal reasons, not merged |
| Assignment | missing required capability | `capability_missing` |
| Sealing | identical inputs/policy/profile/assignments twice | same `run_plan_id` + same `content_sha256` (idempotent) |
| Sealing | perturb each identity-projection field individually | new `run_plan_id` or explicit mismatch |
| Sealing | change only `created_at` | unchanged `run_plan_id` and `content_sha256` |
| Sealing | swap participant/phase order | new `run_plan_id` |
| Sealing | unknown field in artifact / nested object | rejected at create and at handoff re-validation |
| Sealing | persisted `sealed` state with null `sealed_by` | refusal (null-or-required rule) |
| Sealing | duplicate / `..` / symlinked `sealed_sources` entry | refusal |
| Sealing | journal write fault after dir creation | dir removed, `run_plan_not_found` |
| Identity | golden vector: fixed identity-projection JSON → fixed `run_plan_<uuid>` | byte-exact fixture |
| Identity | canonical artifact digest reconstruction | `content_sha256` reproducible from disk bytes |
| Budget | `currency:"EUR"` or null `max_cost` on a catalog profile | `budget_invalid` |
| Budget | `usage_unreported_policy` omitted | `budget_invalid` (never inferred) |
| Budget | wall-time between create and handoff | not counted against `max_wall_time_ms` |
| Schema | v1.0 review-loop/report still validate; G43 loop missing a `verification_ids` entry | v1.0 ok; G43 loop refused |
| Verification | outcome names a finding not in the loop | refusal |
| Verification | attempt in-place finding rewrite | refusal; new version file required |
| Verification | `verification_ids` ref not resolving to exact bytes | refusal |
| Bridge | `run-plan create` output | only `run-plans/<id>/run-plan.json` + journal entry; no loop/finding/trace artifacts |
| CLI | `run-plan show` / `list` | read-only; never allocate a Run |
| Diagnostics | each stable code (`run_plan_not_found`, `run_plan_invalid`, `classification_ambiguous`, `classification_unsupported`, `profile_not_allowed`, `profile_opt_in_required`, `target_not_usable`, `capability_missing`, `budget_invalid`, `budget_exhausted`, `run_plan_input_mismatch`, `run_plan_digest_mismatch`, `run_plan_consent_mismatch`, `run_plan_already_handed_off`, `run_plan_authority_refused`) | exact code in JSON + concise human next-action, no traceback |

### Handoff / consent / rerun (uses `gigai run --plan`, still deterministic Gig)

| Case | Expected |
|---|---|
| `gigai run --plan PLAN_ID --confirm` happy path | fresh consent scope = existing Run scope **+** `run_plan_id` + plan `content_sha256`; redeemed before Run-ID allocation; sealed under the Run; plan ref in manifest `sealed_sources` |
| Handoff without `--confirm` | refused (`run requires direct --confirm operator consent`) |
| Agent-supplied plan consent via `--invocation` | rejected (`invocation.py:126-129` semantics extended to plan) |
| Mutate a referenced input's bytes after seal | `run_plan_input_mismatch` before allocation |
| Mutate the plan artifact bytes after seal | `run_plan_digest_mismatch` |
| Consent carrying a stale `content_sha256` | `run_plan_consent_mismatch` |
| Replay `gigai run --plan SAME_ID` after successful handoff | `run_plan_already_handed_off`; no second `runs/<id>` |
| Seal, approve Gig v2, attempt handoff of v1 plan | refusal; original sealed plan intact |
| Assigned target unavailable at handoff | `blocked` (or explicit operator replan → new `run_plan_id`); never silent swap |
| Cancel planning mid-flow | terminal `cancelled`; no Run, no effect |
| `run_started` commit fails after Run dir mkdir | Run dir removed; plan not marked handed-off; retry-safe |

### Provider-backed (opt-in evidence; identify provider/model/consent/usage/failure)

| Case | Expected |
|---|---|
| Deterministic target assigned to every role | plan seals; review/verify/adjudicate/resolve run workpad-only |
| Networked target, `probe_target_readiness` → `usable`, within budget | independent reviewer findings retained through verify; adjudication retains all contributing findings |
| Networked target reaches model-call / token / cost / wall-time ceiling | terminal `budget_exhausted` (resp. `cost_unavailable` / `usage_unavailable` / `timed_out`) — distinct evidence, no fallback participant |
| Provider reports no tokens and no cost, `block_before_call` | first unaccountable call refused |
| Provider reports no tokens and no cost, `reserve_remaining_budget` | remaining tokens + non-null cost reserved, `usage_unavailable`, no later provider call |
| Provider auth failure mid-plan | distinct terminal (auth failure ≠ timeout ≠ disagreement ≠ budget); no hidden retry |
| Unresolved model adjudication / missing ground truth | `operator_required` (or adjudication input); no fabricated winner; findings inspectable |
| Human UAT: catalog Gig from setup → `run-plan create` → `run-plan show` → `gigai run --plan --confirm` | plan inspectable in CLI/JSON/Markdown; no transcript / credential material in any projection |

---

## 8. Non-negotiable invariants for implementers (from contract §"State and authority invariants" + stop conditions)

1. Active-version pointer + committed journal remain the ONLY approved-version
   authority; G43 records references, never copies (`run.py:_resolve_authority`).
2. Project registry remains the target-binding authority; a plan cannot
   establish/change a binding (`project_binding.py`).
3. Capability manifest + effect policy remain authoritative; a role cannot
   grant itself a capability.
4. A discovery snapshot is runtime evidence, not approval/usability/permission.
5. A sealed Run Plan is immutable preparation evidence — not a Run, approval,
   active pointer, or review verdict.
6. Existing findings/adjudications/reports/journal/Run artifacts stay
   individually inspectable and content-addressed.
7. Provider failure, auth failure, timeout, disagreement, budget exhaustion
   are distinct terminal evidence; none triggers silent fallback.
8. No model output can alter its own role, target, budget, profile, consent,
   or authority references (`invocation.py` forbidden keys; server-side
   consent-scope synthesis in `run.py:108-119`).
9. Cancellation/refusal happen before any effect not authorized by the sealed
   plan + existing Run consent.
10. Replaying a plan or Run request is idempotent and cannot allocate a
    second Run for the same sealed execution identity.

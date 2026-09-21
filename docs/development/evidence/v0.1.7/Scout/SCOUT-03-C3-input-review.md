# SCOUT-03 C3 — fresh independent native-input-integration review

**Date:** 2026-09-09
**Reviewer:** Fresh independent Claude session, dispatched worker. Separate from
the coordinator, the C3 implementer, and the accepted native / external lane
reviewers.
**Scope:** Read-only source review of the delivered SCOUT-03 C3 native input
integration, plus four small disposable adversarial probes run against a
throwaway offline Gig under a scratchpad `TMPDIR` and then deleted. No real
`.gigai`, no providers, no source / test / schema / contract edits, no commits.
The coordinator separately runs the fresh CLI M1 test, the central schema
suite, and resource digests; this review did **not** run the delivered focused
suite or the whole integration suite.
**Sole writable artifact:** this file.

**Inputs read in full:** `SCOUT-03-C3-input-integration.md`,
`SCOUT-03-C3-integration-notes.md`, `parallel-delivery-plan.md` "Native input
task-context disposition" (§364-385) and the surrounding C3 sequencing entries,
`SCOUT-03-native-corrections-rereview.md` (accepted native lane, findings F1-F8
+ N1/N2), `SCOUT-04-R1-R2-final-review.md` (accepted external lane, R1/R2 +
O1/O2), `SCOUT-00-contract-amendments.md` §B1 / §B2 / §M1.

**Source inspected (current working tree, all untracked/uncommitted):**
`src/gigai/scout_inputs.py` (whole), `src/gigai/external_recording.py`
(`_snapshot`, `_input_ref`, `_revalidate_input`, `plan`, `_read_plan`, `start`,
`checkpoint` / `_progress`, `submit`, `_plan_for_run`, `inspect`), the two
changed reader guards in `src/gigai/run.py` (`read_run_details` external-family
refusal, `_validate_plan_handoff` private-prefix refusal) and
`src/gigai/run_plan.py` (`_read_plan_path` external-family refusal),
`src/gigai/provider_review.py` (`execute_provider_review` /
`close_provider_review_no_fix_required` plan-read path),
`src/gigai/schemas/external-recording-invocation.schema.json` and
`external-recording-plan.schema.json` (both whole, pretty-printed), the three
unchanged `external-recording-{checkpoint,run,receipt}` schemas' digests,
`src/gigai/native_records.py` (`_chain`, `_sidecar`, `_KIND`, `_scope`,
`create_task_override`), `src/gigai/validators.py:68-72`,
`tools/verify_installed_schemas.py`, `src/gigai/schemas/SHA256SUMS`,
`tests/test_scout03_c3_inputs.py`, `tests/test_scout04_input_integration.py`,
and the `_fixture` / `_envelope` helpers in
`tests/test_scout04_external_recording.py`.

**Method.** Every claim below is tagged `[source]` (I followed the code path,
not executed) or `[probe]` (a disposable script ran against a throwaway offline
Gig and I observed the result). Probe scripts lived only in the session
scratchpad and were deleted; `git status` shows no probe artifact and no source
change.

---

## Verdict: **bounded accept** the SCOUT-03 C3 native input integration at its
stated scope.

Every property the charge lists is enforced in the delivered source and, where
probed, behaves as specified. The admitted native surface is exactly
`profile_preferences` and `experience_qa`; every other native `jsl_blob` kind
(`imported_reference`, `supplied_source`, `selected_conversation`) is refused
with a deliberate typed error before Plan publication. The closed external
schemas keep full nested `additionalProperties:false` strictness, the sealed
identity carries the resolved native scope including `base`, sealed inputs are
re-resolved from the locked committed snapshot at seal / start / checkpoint /
submit, and the managed Plan / Run / provider readers each refuse the external
recording family before allocation or provider execution. No integration
regression against the already-accepted external R1/R2 receipt gates or the
native lane's locked-snapshot / CAS / privacy guarantees was found.

Two **non-blocking observations** (C1, C2) are carried for coordinator
visibility. Neither reproduces a defect; each has a one-line optional remedy.
The pre-existing native N1 (schema `task_context_id` regex looser than the code
check) does **not** recur here — the external schemas pin the strict UUIDv4
shape (see §"Schema strictness"). C3 tool authorization, package/export guard,
and bundled initialization are explicitly not claimed and were not inspected as
acceptance evidence.

---

## Charge items — explicit status

| Charge item | Status | Evidence |
|---|---|---|
| Committed-snapshot exact native revision / blob / base authentication | **Met** | `[source]` `scout_inputs._native_revision` walks `native_records._chain` for the *exact* `revision_id` (no head/latest), then `_sidecar` binds the blob by digest; `_native_scope` re-authenticates the override `base` as a real historical revision via `_native_revision`. `[probe]` correct override selection seals `scope.base.{record_id,revision_id}` equal to the committed base. |
| Explicit scope and single-override-context | **Met** | `[source]` `_supplied_native_scope` requires exactly `{mode, task_context_id}`; missing scope raises (`raw_input_ref` 3-key branch is G45-only). `assert_single_override_context` counts distinct `run_override` `task_context_id`s and refuses `>1`; saved-defaults alongside one override context pass. `[probe]` two override contexts → `external_input_mismatch`; base + one override → sealed. |
| Old revisions readable after update/archive; replay before current-source revalidation | **Met** | `[source]` `_chain` returns every revision including archived tails (native F3 writes a full `jsl_blob` copy, original revision retained); `_record_revision` matches the sealed `revision_id` regardless of a newer tail. `[source]+[test]` `test_external_plan_pins_native_revisions_and_keeps_old_history` updates + archives, then continues the old checkpoint at sequence 2 with the old pinned revisions. |
| G45 legacy compatibility | **Met** | `[source]` `resolve_external_input` handles bare `{family: g45_*, id}`, the unscoped `{family: scout_record, record_id, revision_id}` wrapper over a G45 family (re-resolves and byte-compares the wrapped content), and refuses a G45 wrapper that carries a native `scope`. `raw_input_ref` `oneOf` admits all four shapes. `[probe]` native-only Plan (no G45 anchor) still seals — no hidden G45 requirement. |
| Closed schemas preserve nested strictness | **Met** | `[source]` every `$def` in both changed schemas is `additionalProperties:false`; `resolved_input` = `oneOf[g45_reference, g45_run_input, scout_record]`, `scout_record` = `oneOf[scout_g45_record, scout_native_record]`, `scout_native_record.native_kind` enum = `{profile_preferences, experience_qa}`, `scope` → `native_scope` (with `base`), `content` → `native_blob` (`family` const `jsl_blob`). `[test]` `test_native_scope_is_closed_...` proves both a popped `scope` and an unknown `scope.latest` member fail plan-schema validation. |
| Revalidation at seal / start / checkpoint / submit | **Met** | `[source]` `plan` resolves each raw ref through `_input_ref` before sealing (line 561); `start` (881), `checkpoint`/`_progress` (1236), `submit` (1420) each loop `_revalidate_input` over `plan["inputs"]` against a fresh locked snapshot; `_plan_for_run` re-reads the sealed Plan bytes by digest, not a cache. |
| No ambient defaults / private leakage / provider grant | **Met** | `[source]` no `latest` / `chain[-1]` / head-revision selection anywhere in `scout_inputs.py`; the `external_successor_required` `next_action` exposes `selected_inputs = plan["inputs"]` = record/revision IDs + kind + scope + blob digest/path only (no blob bytes, prompt, answer, or fact values) and `missing_inputs` = the agent-supplied question `id`/`prompt`/`state`. `run.py:_validate_plan_handoff` refuses any sealed source under `references/`, `run-inputs/`, `records/`, `docs/` with `private_provider_disclosure_refused` before Run allocation. The invocation schema `disclosure` `$defs` still pins `execution:const unobserved` / `actor_report:const declared`. |
| Deliberate managed-family refusal | **Met** | `[source]` `run_plan.py:_read_plan_path` raises `external_plan_family_refused` when a `run-plans/<id>/external-plan.json` sibling exists; `run.py:read_run_details` raises `external_run_family_refused` when `runs/<id>/external-run.json` exists; `provider_review.execute_provider_review` / `close_provider_review_no_fix_required` both enter through `read_run_plan`, so the provider route is transitively gated. `[test]` `test_managed_readers_and_provider_route_refuse_external_native_inputs` exercises `read_run_plan`, `launch_run`, `read_run_details`, and a private-blob `create_run_plan` ingress. |
| Fresh-process M1 inputs / successor relationship | **Met** | `[source]+[test]` `test_fresh_cli_question_answer_and_successor_external_plan`: a fresh `gigai record native context` process reads the outstanding question, a second fresh `gigai record native update` process records a user-reported answer, `external_recording.inspect(run_id=…)` reports the old Run `waiting_input`, and a fresh `gigai external plan` + `gigai external start` process seals a successor pinned to the old waiting checkpoint (`predecessor.kind == "checkpoint"`) while the old sealed revisions stay pinned. No dependence on prior-process ambient memory. |
| Bounded admitted native kinds (`profile_preferences`, `experience_qa`); other families fail deliberately | **Met** | `[source]` `scout_inputs._NATIVE_KINDS` = those two; `resolve_external_input` raises `"native record kind is not admitted to external recording"` when `revision.kind` (cross-checked against `sidecar.kind`) is anything else. `native_records._KIND` really permits 5 kinds, so the refusal is a live defense, not vacuous. `[probe]` intent confirmed by source path; a full `imported_reference` fixture build was skipped (constructor plumbing), so this row is `[source]`-strong + partial-`[probe]`. |
| C3 tool authorization / package / init not claimed | **Confirmed** | Delivered `SCOUT-03-C3-input-integration.md` §"Schemas and remaining gates" lists these as still outside the slice; no tool-dispatch, `package.py`, or `template_instances` code was inspected or is touched by the delivered diff. |

---

## Schema strictness and digest consistency

`[source]` Both changed schemas (`external-recording-invocation.schema.json`,
`external-recording-plan.schema.json`) are fully closed at every level. The
native additions:

- `native_scope` `oneOf`: `saved_default` requires exactly
  `{mode, task_context_id:null, base:null}`; `run_override` requires exactly
  `{mode, task_context_id:<UUIDv4>, base:{record_id, revision_id}}` with both id
  patterns pinned to the strict RFC-4122 v4 shape
  `^…-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-…$`.
- The invocation-side `native_selection_scope` (`raw_input_ref` 4-key branch)
  is the caller shape `{mode, task_context_id}` — no `base`; `base` is derived
  from the committed sidecar by `_native_scope`, never accepted from the caller.
- `scout_native_record` requires `native_kind` (2-value enum), `scope`,
  `content` (`native_blob`, `family` const `jsl_blob`).

The native lane's **N1** note (native-record-content schema `task_context_id`
regex `[0-9a-f-]{36}` is looser than the code's UUIDv4 check) does **not**
recur in the external schemas: the patterns here are the strict v4 form and
match `scout_inputs._TASK_CONTEXT`.

`[source]` All five `external-recording-*` schema digests are internally
consistent across the on-disk files, `src/gigai/schemas/SHA256SUMS`, and
`tools/verify_installed_schemas.py`; the invocation
(`87074566…d27a8a`) and plan (`2961b6ed…96c2bd`) digests match the delivered
`SCOUT-03-C3-input-integration.md` handoff. Central schema-inventory / hash
registration is explicitly out of C3 scope and coordinator-owned; not assessed.

`[source]` `validators.SCHEMA_NAMES:68-72` registers all five families, so the
in-code `_schema` / `_recorded` validations load real resources.

---

## Disposable adversarial probes (deleted after the run)

Run as `PYTHONPATH=<repo> .venv/bin/pytest -q -s probe_c3.py` against a
throwaway offline Gig; module deleted afterward, no `src/` or `tests/` change.

| Probe | Construction | Result |
|---|---|---|
| override-record-as-`saved_default` | `create_task_override` record selected with `{mode:saved_default, task_context_id:null}` | `ScoutInputError` (scope mismatch vs committed sidecar) — **refused** |
| override-record-correct-selection | same record selected `{mode:run_override, task_context_id:<real>}` | sealed; `scope.base.record_id` == the committed base record — **base authenticated** |
| `saved_default`-record-as-fabricated-`run_override` | plain `saved_default` record selected with an invented `run_override` `task_context_id` | `ScoutInputError` matching `scope` — **refused** |
| native-only Plan (no G45 anchor) | `plan` with a single `scout_record` native input | sealed, `inputs[0].native_kind == "profile_preferences"` — resolver does not silently require a G45 anchor |
| tampered committed blob at `start` | overwrite `records/<id>/blobs/<rev>.json` in the working tree, then `external_recording.start` | refused — the journal snapshot's own `working evidence differs from committed bytes` guard fires *before* `_revalidate_input`; defense-in-depth, still fail-closed |

3 of the 4 native probes (`imported_reference` fixture skipped for constructor
plumbing) plus the sanity probe passed as intended; the tamper probe is refused
one layer earlier than the resolver, which is a stronger outcome than the
charge asked for.

---

## Non-blocking observations (source-distinct; optional narrow remedies)

### C1 — `[source]` — `revalidate_external_input` reconstructs the override raw ref without `base`; correctness rests on `resolve_external_input` re-deriving it

`scout_inputs.revalidate_external_input` (`:226-234`) rebuilds the raw
selection for a native input as `{family, record_id, revision_id, scope:{mode,
task_context_id}}` — it never forwards the sealed `scope.base`. Re-resolution
then re-derives `base` from the committed sidecar via `_native_scope`, and the
final `current != value` comparison (`:238`) catches any drift, so the
behavior is fail-closed and correct today. The gap is only that a future edit
weakening `_native_scope`'s base re-authentication would not be caught by this
revalidation path, because `base` is reconstructed rather than round-tripped.
**Narrow remedy (optional):** carry the sealed `scope.base` into the rebuilt
`raw` and assert equality explicitly, mirroring how the G45-wrapper branch
already round-trips its full `content`. Source-only; not reproduced as a wrong
success.

### C2 — `[source]` — `private_provider_disclosure_refused` is prefix-based and would also refuse a legitimate future managed private-input Plan

`run.py:_validate_plan_handoff` refuses *any* sealed source whose first path
component is `references`, `run-inputs`, `records`, or `docs`. For the C3 slice
this is exactly right (managed Runs must not carry private selected inputs).
`SCOUT-03-C3-integration-notes.md` §"Managed Plan input seams" contemplates a
later managed Plan that pins private identities "only with fail-closed provider
ingress"; if that lands, this blanket prefix refusal will need to become a
provider-ingress check rather than a planning-time hard stop, and legitimate
inert `docs/` template references (called out in the notes' package guard
section) are currently swept in too. **Narrow remedy (optional):** when that
work is scheduled, replace the prefix set with a private-provenance
classification and move the hard refusal to the provider boundary. No action
for C3 acceptance — the current behavior is the safe direction.

---

## Explicitly not assessed (out of C3 scope per the charge and the delivered note)

- C3 supported tool authorization / dispatch seam.
- `package.py` private-family inspection / `export_package` guard.
- Bundled / `template_instances` initialization (B3).
- Central schema inventory / hash-registry updates (coordinator-owned).
- The whole SCOUT-04 external integration suite and the delivered C3 focused
  suite (coordinator-run; the R1/R2 receipt gates were checked only for
  *integration regression*, and none was found — the `check_artifact_input`
  `result` gate, the output-tuple binding, and the `unobserved`/`declared`
  disclosure `$defs` are unchanged by this slice).
- Native lane N2 (multiple `saved_default` records of one kind) — the C3
  *selected-default resolver* is separately open per the delivery plan; the
  external recording lane admits an explicitly-named revision only, so the
  ambiguity never reaches it here.

---

## Source-inference vs. probe — summary

- **Probe-verified** (throwaway offline Gig, scratchpad `TMPDIR`, deleted
  scripts): override-as-`saved_default` refusal; correct override sealing the
  authenticated `base`; `saved_default`-as-`run_override` refusal; native-only
  Plan sealing; tampered-committed-blob refusal at `start`.
- **Source-inference only** (followed the path, not executed): the four
  revalidation call sites; `_chain` archived-tail retention; the managed
  Plan/Run/provider family refusals (the delivered test covers them, I did not
  re-run it); schema `additionalProperties` closure and digest consistency;
  the `imported_reference` / `supplied_source` / `selected_conversation`
  admission refusal (path confirmed; full fixture build skipped);
  `next_action` metadata-only exposure; C1 and C2.
- **Not inspected:** C3 tool/package/init surfaces; central schema registry;
  any real `.gigai` or provider; hidden reasoning of any prior worker.

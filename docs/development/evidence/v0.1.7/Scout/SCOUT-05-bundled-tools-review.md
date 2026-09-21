# SCOUT-05 bundled tools — independent read-only review

**Reviewer dispatch:** `task_01769ab72731` / `ctx_ae24895d9876`.
**Date:** 2026-09-10.
**Mode:** Read-only source review in the current worktree
(`karthik446/gigai-v0.1.7`). No test suites were rerun. Small disposable
probes only: `py_compile`, `ruff check`, and one in-process manifest
validation probe (positive + three negatives). No `.gigai/` private data,
providers, network, real init, or approval was exercised. No source or test
files were edited. Sole owned artifact: this file.

**Subject:** `SCOUT-05-bundled-tools-implementation.md` and the bundled
inert-preparation surface it hands off:
`src/gigai/scout_bundled_tools.py`, `scout_materialization.py`,
`scout_template.py`, `default_init.py` (narrow retry / current-authority
changes), `scout_tools.py`, `scout_tool_adapter.py`, `capabilities.py`
(`_validate_tool_binding`), `src/gigai/data/scout/gig.py` and
`tools/cap_00000000-0000-4000-8000-000000000071/{record_tool.py,operation.schema.json}`,
`capability-manifest.schema.json` and `scout-operation-receipt.schema.json`,
and `tests/test_scout05_bundled_tools.py`.

**Context read:** accepted SCOUT-00 lifecycle contract amendments
(`SCOUT-00-contract-amendments.md` §7, §9/B1), accepted user-owned Gig
workspace amendment (`SCOUT-00-user-owned-gig-amendment.md` §2, §4 finding 3,
§7 scenario 11), and `parallel-delivery-plan.md` dispositions through
"Existing-binding derived-manifest recovery scope" (the two dispatches that
authorized `task_edc6827b09cd` literal-root-wrapper scope and the narrow
`default_init.py` derived-manifest recovery caller).

---

## Verdict

**The delivered inert-preparation scope meets the accepted contract for this
slice.** The bundle prepares a schema-valid but deliberately unapproved
per-Gig CRUD capability manifest, copies real immutable source (root `gig.py`
plus the exact `tools/cap_<uuid>/` subtree) into the journaled software
inventory, compiles the five real Scout graph goal IDs into the manifest,
and stages a pending first Graph Set proposal — all without executing copied
Python, marking a security review passed, attaching an *approved* manifest to
an active version, promoting Scout to a release-eligible default, or claiming
an external agent's `python gig.py` execution is sandboxed. Every
contract-mandated boundary I audited holds.

**The remaining caller is real and correctly deferred, not a false
completion.** There is genuinely no source path that transitions
`security_review.status` from `pending` to `passed` or `availability_state`
to `available` for the Scout CRUD manifest (verified by exhaustive grep of
`src/gigai/*.py`; the only `"passed"` literals are the G17 install-record
audit strings and the read-side gates that *require* `passed` as a
precondition). A full copied-`gig.py` CRUD/replay success path therefore
remains blocked on a dedicated public capability-review / effect-consent
caller that does not yet exist. This matches
`SCOUT-05-bundled-tools-implementation.md` §"Explicit remaining caller and
release gates" and the roadmap's SCOUT-05 "Done when" — full template
usefulness is explicitly re-verified only after the domain goals. **No
artificial blocker is raised for the explicitly deferred end-to-end
consent.**

**Test-only vs shipped code:** the shipped executable assets are real —
`src/gigai/data/scout/gig.py` (337 lines) and `record_tool.py` (37 lines)
are the actual bundled files, listed by `scout_source_files()`, copied
byte-for-byte by `materialize_scout_candidate`, and pinned in the journaled
inventory. This is a genuine advance over the prior wave where "executable
entry source/manifest are still synthetic fixtures"
(`parallel-delivery-plan.md`, "First-version receipts integrated"). What is
**not** shipped / proven: any reviewed-and-`available` manifest, an
installed-wheel run, a real user `gigai init`, provider execution, default
activation, or a committed repository change. `tests/test_scout05_bundled_tools.py`
proves preparation and refusal semantics with disposable synthetic workpads;
it deliberately never sets `security_review: passed` and asserts the wrapper
stays refused (`tool_authority_unavailable` before Graph approval,
`tool_capability_refused` after Graph approval without capability review).

---

## Audited boundaries — findings by requirement

### 1. Literal root-wrapper binding consistency and mandatory behavior — PASS

- `capability-manifest.schema.json` `$defs/tool_binding` admits `gig.py` only
  as the exact string `{"const": "gig.py"}` (via `tool_inventory_item`'s
  `oneOf`); every other member must match the
  `^tools/cap_<uuidv4>/...` pattern. `entry_path` is pattern-restricted to
  the `tools/cap_<uuid>/` form, so the entry can never *be* the root
  wrapper. `allOf`/`if`-`then`: an inventory that `contains` a `gig.py` path
  **requires** `wrapper_ref` (`capability-manifest.schema.json` lines ~150-166;
  identical block in `scout-operation-receipt.schema.json`).
- `capabilities.py:198-204` (`_validate_tool_binding`) binds it further: at
  most one `gig.py` inventory member, and `dict(wrapper_ref) ==
  dict(root_member)` exactly; a `wrapper_ref` with no `gig.py` member is
  rejected (`tool_wrapper_not_in_inventory`).
- `scout_tools.py:192-205` re-checks at dispatch: any `gig.py` inventory
  member requires `dict(wrapper_ref) == dict(item)`, and a `wrapper_ref`
  present with no `gig.py` member in the resolved inventory is refused
  (`tool_wrapper_invalid`).
- `scout_bundled_tools.py:44,49,109` builds `wrapper_ref` as
  `next(item ... == _ROOT_WRAPPER)` from the already-sorted inventory, so the
  prepared manifest's `wrapper_ref` is byte-identical to its inventory
  member. Probe confirmed `wrapper_ref.path == "gig.py"` and a
  `+1` `size_bytes` tamper is rejected.
- Mandatory-when-present: the strict branch in `scout_tools.py:259-268`
  fires whenever `binding.get("wrapper_ref")` is set; a request whose
  `wrapper_ref` differs (or is absent) is refused. `gig.py:269-288`
  recomputes the wrapper digest from the on-disk file on every
  create/update/archive and passes it through, so a redirected or edited
  wrapper cannot satisfy the bound reference.

### 2. Active version and capability identity — PASS

- `scout_tools.py:226-236`: the active pointer is schema-validated against
  `active-gig-version-v2.schema.json`; `pointer.gig_id`, `request.gig_id`,
  and `resolved.gig_id` must all agree, and `request.gig_version ==
  pointer.active_version`, else `tool_scope_refused`. The capability
  manifest is fetched by the committed `capability_manifest` artifact ref
  (`_artifact_ref_matches`, digest + size + media-type checked against the
  locked journal snapshot), re-validated, and its `gig_id` must equal
  `resolved.gig_id`.
- `scout_bundled_tools.py:16-19,54,65`: `SCOUT_CRUD_CAPABILITY_ID` /
  `SCOUT_CRUD_MANIFEST_ID` are fixed canonical v4 UUIDs; `entry_path` and
  `schema_path` are derived from the capability ID, so identity cannot
  drift between the manifest, the inventory, and the on-disk tool subtree.
- `source_constraints.required_digest == entry.content_sha256` and
  `required_identity == "record_tool.py" == Path(entry_path).name` are
  enforced in both `capabilities.py:205-212` and `scout_tools.py:273-279`.

### 3. Source closed inventory and lock revalidation — PASS

- `scout_tools.py:178-222` (`_inventory`): every member must be
  `{path, content_sha256, media_type, size_bytes}` exactly; non-root members
  must be under `tools/<capability_id>/`; each file is read via
  `_regular_file` (component-wise symlink rejection, `S_ISREG`, no exec
  bits) and its digest/size must match the claimed values; the inventory
  must be canonical-sorted; and `rglob` over the real tool root must yield
  exactly `seen - {gig.py}` (no extra or missing files), rejecting an
  unapproved sibling or a stray `__pycache__`.
- Dispatch-time + writer-lock revalidation: `dispatch_native_record_tool`
  calls `_binding` once (`scout_tools.py:364`), then `native_records._publish`
  (line 319-335) calls `revalidate_tool_binding_at_publication` **inside**
  the journal-writer snapshot and refuses if `verified_binding !=
  dict(tool_binding)` (`tool_binding_invalid`). `invoke_approved_tool_entry`
  additionally re-reads and digest-checks the entry file immediately before
  `exec_module` (`scout_tools.py:453-457`) and runs with
  `sys.dont_write_bytecode = True` so an import cache cannot appear as an
  unapproved inventory member on the immediate re-check.
- The receipt records the resolved binding (`native_records.py:361-362`) and
  is schema-validated against `scout-operation-receipt.schema.json` before
  journaling.

### 4. Same-account non-sandbox boundary — PASS (honest)

- Module docstrings state it plainly: `scout_bundled_tools.py:3-6`
  ("never executes Python, marks a review passed, installs a capability, or
  publishes a receipt"); `scout_materialization.py:4-7`; `scout_tools.py:1-8`
  ("does *not* import or execute the tool entry"); `gig.py:2-10`.
- `invoke_approved_tool_entry` is the one place that `exec_module`s the
  entry, and its own docstring (`scout_tools.py:433-439`) says "not
  automatic execution, a scheduler, or a sandbox." An external agent's
  direct `python gig.py` runs under its own OS permissions; the validated
  service still gates every mutation and cannot manufacture direct-CLI
  consent — matches user-owned-Gig amendment §4 finding 3
  ("Editable Python under the same OS account is **not a sandbox**").
- `materialize_scout_candidate` copies source with `_atomic_write` and
  `_safe_root_path` (absolute / `..` / backslash / non-allowed-root /
  component-symlink rejection) and **never** imports it
  (`scout_materialization.py:1-7, 234-250, 471-482`).

### 5. Strict nested fields — PASS

- `capability-manifest.schema.json` `tool_binding`: `additionalProperties:
  false`, `required: [entry_path, inventory, inventory_sha256, operations,
  effects]`, `operations` enum ⊆ {record_create, record_update,
  record_archive} with `uniqueItems`, `effects` items `{"const":
  "write_workpad"}` with `uniqueItems`, `inventory` `maxItems: 16`,
  `size_bytes` 1..262144, `media_type` enum {text/x-python,
  application/schema+json}. `tool_inventory_item` is also
  `additionalProperties: false`.
- `capabilities.py:186-224` adds the cross-field checks the closed schema
  cannot express: `inventory_sha256 == canonical_json_digest(inventory)`,
  canonical-sorted unique paths, entry ∈ inventory, `operations ==
  sorted(set(operations))`, `effects == ["write_workpad"] ==
  declared_effects`.
- `scout_tools.py:_request` (lines 80-136): the request field set is an
  exact match against an operation-specific `expected` set (unknown field →
  `tool_invocation_invalid`); `wrapper_ref`, when present, must be exactly
  `{path, content_sha256, media_type, size_bytes}` with `path == "gig.py"`,
  `media_type == "text/x-python"`, `size_bytes >= 1`.
- `scout-operation-receipt.schema.json` `tool_binding`: `required` includes
  `manifest_ref, capability_id, gig_version, entry_path, inventory,
  inventory_sha256, operation, effects, actor`; `actor` is a closed
  `{kind: "agent", id}` object; `gig_version` bounded integer.
- In-process probe: the prepared manifest validates clean; duplicate
  `operations`, a duplicated `effects` entry, and a tampered `wrapper_ref`
  size are each rejected.

### 6. Historical tools-only compatibility — PASS

- `tool_binding` is a **new optional** property on the capability object
  (`capability-manifest.schema.json` diff: added alongside existing
  `options`). Manifests without it are unaffected: `_validate_tool_binding`
  returns immediately when `binding is None` (`capabilities.py:175-177`).
- `scout_tools.py:259-266`: an approved binding with `wrapper_ref is None`
  is explicitly kept usable ("A historical tools-only binding remains
  usable"); its receipt does not gain wrapper provenance because a newer
  copied wrapper passed local bytes. New bindings that advertise a
  `wrapper_ref` take the strict branch.
- `scout-operation-receipt.schema.json`: `tool_binding` and its inner
  `wrapper_ref` are optional (the `if inventory contains gig.py then require
  wrapper_ref` conditional only bites when a root member is present); the
  existing `reference_add` / `run_input_add` receipt shape is untouched
  (`operation` enum still carries all five values; `tool_binding` is not in
  the top-level `required`).
- The central golden `test_scout_tool_receipt_accepts_first_version_and_retains_strict_binding`
  (`research/contract_spike/tests/test_schemas.py:1391-1429`) covers a
  tools-only (`wrapper_ref`-absent) receipt binding plus first/later
  version acceptance; `test_tool_bindings_require_explicit_literal_root_wrapper_reference`
  (lines 1431-1483) covers the literal-root-wrapper conditional for both
  schemas.

### 7. Inert source copying / pending review vs actual approval — PASS

- `prepared_scout_crud_manifest` hard-codes `availability_state: "missing"`,
  `security_review.status: "pending"` with empty `checks`, `compatibility.status:
  "unknown"`, and the single option `decision: "pending"`
  (`scout_bundled_tools.py:83-102`).
- `_validate_manifest_semantics` refuses any option whose `decision !=
  "pending"` before approval (`capabilities.py:151-152`,
  `proposal_not_pending`), so a pre-decided manifest cannot even validate.
- `materialize_scout_candidate` journals the sealed source snapshot and the
  derived manifest to `manifests/capabilities/<id>.json` but **does not**
  attach it to an active version (no `active-gig-version*.json` write in
  `scout_materialization.py`). `propose_first_graph_set_offline` stages a
  *pending* proposal only.
- Attaching the manifest to an active version happens only via an explicit
  operator `approve_offline(capability_manifest_id=...)`
  (`lifecycle.py:2064-2091`). That path validates the manifest is
  schema/semantics-valid and Gig-scoped but does **not** require
  `security_review == passed`; the runtime gate `scout_tools.py:247`
  (`availability_state != "available" or security_review.status != "passed"
  → tool_capability_refused`) is what keeps a Graph-approved-but-unreviewed
  manifest non-executable. `tests/test_scout05_bundled_tools.py:195-228`
  is exactly this scenario and asserts the refusal plus an unchanged active
  pointer.

### 8. Derived-manifest deletion / recovery vs authoritative committed artifacts — PASS

- `repair_prepared_scout_capability_manifest` (`scout_materialization.py:288-364`):
  - the `inventory_ref` must be `manifests/software/.../source-inventory.json`,
    `application/json`, with `str` digest + `int` size;
  - the committed inventory bytes are re-read from the journal
    (`read_committed_artifact`), digest/size checked, on-disk copy compared
    byte-for-byte and symlink-rejected;
  - the inventory payload must declare `kind ==
    "scout_software_inventory"`, `compiler_version == _COMPILER_VERSION`,
    and `source_digest == <pinned>`, else "not eligible for derived
    manifest repair";
  - the sealed manifest is read from
    `manifests/software/<version>/compiled/prepared-capability-manifest.json`
    (journal-committed, on-disk byte-compared, symlink-rejected), its
    `manifest_id` must equal `SCOUT_CRUD_MANIFEST_ID`, and it is restored
    via `materialize_capability_manifest`.
  - `materialize_capability_manifest` (`capabilities.py:277-293`) writes
    only when the target is **absent** or byte-identical; a divergent
    existing manifest is refused ("refusing to overwrite divergent
    capability manifest"), and the restored bytes are re-validated on
    replay.
  - Docstring and behavior confirm it never compiles current package
    source, copies editable files, re-stages a Graph Set, allocates IDs, or
    infers review approval.
- `default_init.py:741-759` only invokes repair when `is_scout_candidate and
  not update_available and not active_version_exists`, and only after
  confirming the committed binding carries a `software_inventory` object.
  Any Gig with a committed active pointer skips repair and reports
  `capability_review_required` (line 764-766, 774-780). This is the
  `parallel-delivery-plan.md` "Existing-binding derived-manifest recovery
  scope" authorization implemented faithfully:
  "authenticate existing Scout v2 binding, preserve exact pinned
  source/package/proposal identity, restore missing derived unapproved
  capability material from committed software snapshots. Do not adopt an
  available upstream update, overwrite conflicting/reviewed/approved
  material, or restage an approved proposal."
- `test_scout05_bundled_tools.py:243-262` proves customized-entry + deleted
  manifest → resumed init recovers the manifest, preserves the customized
  bytes, and reuses the same `proposal_id`.

### 9. Stale / upgraded / customized source preservation — PASS

- Working-copy loop (`scout_materialization.py:470-482`): an existing
  destination that differs from the bundled bytes is appended to
  `customized_paths`, **not overwritten**; only truly missing files are
  written. Non-regular / symlinked destinations are refused.
- Upstream change: `is_scout_candidate` compares `catalog_id`,
  `definition_version`, `package_id`, and `entry_content_digest`
  (`scout_materialization.py:55-64`); a changed `definition_version` makes
  the entry no longer the pinned candidate, so `default_init.py:727-730`
  computes `update_available` and returns status `update_available` with
  "existing source and proposal remain unchanged" — repair is skipped
  (line 741 guard). `test_scout05_bundled_tools.py:265-283` confirms the
  on-disk tool bytes and the committed `source-inventory.json` are
  unchanged.
- Existing snapshot path (`scout_materialization.py:440-469`): a committed
  inventory whose `source_digest` / `compiler_version` / `definition_version`
  disagrees with the recomputed candidate raises
  "Scout source snapshot conflicts with the pinned candidate inventory"
  rather than silently re-copying.

### 10. Missing actual public consent / review caller — CONFIRMED DEFERRED (not a blocker)

- Exhaustive grep of `src/gigai/*.py` for `security_review` / `"passed"`:
  the only writers of `status: "passed"` are the three static G17
  install-record audit strings (`capabilities.py:410-412`); every other
  reference is a **read gate** that *requires* `passed`
  (`capabilities.py:450,505`; `scout_tools.py:247`). Nothing flips the
  Scout CRUD manifest's `pending` → `passed` or `missing` → `available`.
- `inspect_capability_manifest` can only reconcile toward `available` when
  `security["status"] == "passed"` is already true
  (`capabilities.py:448-475`); otherwise it stamps `security_rejected`.
- Therefore the `SCOUT-05-bundled-tools-implementation.md` statement — "no
  existing public capability-review/effect-consent transition ... can
  safely transform this candidate's pending manifest into a reviewed,
  `available` one" — is accurate. This is the deliberate remaining caller,
  aligned with SCOUT-00 §7 ("Invocation requires an explicitly registered
  local capability and a separately authorized effects path") and the
  roadmap SCOUT-05 gate. **Per this task's instruction, no blocker is
  raised for the deferred end-to-end consent.**

---

## Prioritized actionable findings

### F1 — LOW (Root golden coverage, not a source defect)

`research/contract_spike/tests/test_schemas.py` `valid_instances()` still
maps `urn:gigai:schema:capability-manifest:1` to the plain
`capability_manifest()` fixture (lines 205-238, 1188), which carries **no**
`tool_binding`. The new `tool_binding` shape is covered only by the two
dedicated tests (`test_scout_tool_receipt_accepts_first_version...` at line
1391 and `test_tool_bindings_require_explicit_literal_root_wrapper_reference`
at line 1431, the latter exercising the `#/$defs/tool_binding` subschema
directly). Root's central positive/negative golden pass should add a
`tool_binding`-bearing `capability-manifest:1` instance (and its negative
mutations) to the top-level `valid_instances` map so the full-manifest
round-trip — not just the isolated `$defs` fragment — is a golden.
**Owner:** Root (central goldens). **Not a merge blocker for the source
slice.**

### F2 — LOW (imprecise messaging, safe)

`default_init.py:764-766` infers `capability_review_required` purely from
"a committed active pointer exists for a Scout candidate", without checking
that the active pointer's `capability_manifest` ref actually resolves to the
still-pending `SCOUT_CRUD_MANIFEST_ID`. Today this is always true and the
message is conservative (it never overstates approval). Once the deferred
review/consent caller lands, a Scout instance whose manifest *is* reviewed
would still be told "explicit capability review ... remain pending". Add a
manifest-state check (or a TODO anchored to the review-caller task) so the
rerun status stays truthful after that caller exists. **Owner:** whoever
lands the capability-review caller.

### F3 — INFO (doc precision)

`SCOUT-05-bundled-tools-implementation.md` line 26 says materialization
"does not attach the manifest to an active version" — correct — but the
next section and the Verification table describe "Graph approval without
capability review refusing mutation". A one-line note that *approval*
(`approve_offline(capability_manifest_id=...)`) is what binds the **pending**
manifest to the active pointer, and that the runtime tool gate
(`availability_state`/`security_review`) is the actual execution barrier,
would remove any reading that the manifest is never referenced by an
approved version. No code change.

### F4 — INFO (defense-in-depth observation, already safe)

`repair_prepared_scout_capability_manifest` restores the committed pending
manifest bytes without re-checking that the current on-disk
`tools/cap_<uuid>/record_tool.py` still matches the manifest's
`required_digest`. This is intentional (the manifest is inert authority; the
runtime `_inventory` catches drift with `tool_inventory_changed`) and
`test_scout05_bundled_tools.py:243-262` relies on it. Recorded only so a
future reader does not mistake it for a gap: the recovered manifest may
legitimately reference bytes that differ from a customized working copy
until a real capability review re-pins them.

---

## What was NOT reviewed / out of scope

- Full test suites (instructed not to rerun; relied on the coordinator's
  recorded combined checkpoints in `parallel-delivery-plan.md`).
- Installed-wheel behavior, real `gigai init`, provider execution, default
  eligibility promotion, repository commit — all explicitly deferred by
  SCOUT-05 and this task.
- The broader untracked SCOUT-02/03/04 modules on this branch
  (`graph_set.py`, `native_records*.py`, `external_recording*.py`, etc.)
  except where `scout_tools.py` / `native_records.py` are the direct
  publication path for the bundled tool.
- `default_init.py` init/registry/owner/migration logic outside the narrow
  retry / current-authority / derived-manifest recovery changes.
- Central schema hash integration and positive/negative goldens beyond
  confirming the two subject schemas' on-disk SHA-256 match `SHA256SUMS`,
  `tools/verify_installed_schemas.py`, and the implementation handoff block
  (`capability-manifest.schema.json` =
  `2d36b8e0552c810f1ec17e50d4edbc68be39cc1572c0f535073bf7f59731b5a7`,
  `scout-operation-receipt.schema.json` =
  `a387d8d258dda7f86612e52b4698990062de8ae5ee846f4d3f154a91427e1bcd`) —
  those are Root's in-progress ownership.

## Probe log

| Probe | Result |
| --- | --- |
| `.venv/bin/python -m py_compile` over the 9 reviewed Python files (incl. both `data/scout` assets) | passed |
| `uv run ruff check` over the 7 reviewed `src/gigai` modules | `All checks passed!` |
| `git diff --check` | clean |
| On-disk SHA-256 of `capability-manifest.schema.json` / `scout-operation-receipt.schema.json` vs `SHA256SUMS` + `verify_installed_schemas.py` + impl-doc handoff block | all three match |
| In-process: `prepared_scout_crud_manifest(...)` → `validate_capability_manifest` | `valid: True`, `availability_state=missing`, `security_review=pending`, option `decision=pending`, inventory canonical-sorted `[gig.py, .../operation.schema.json, .../record_tool.py]` |
| In-process negatives: `wrapper_ref.size_bytes += 1`; `operations=[dup]`; `effects=[dup]` | each rejected |

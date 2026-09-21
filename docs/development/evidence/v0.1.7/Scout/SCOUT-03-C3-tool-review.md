# SCOUT-03 C3 tool bridge — independent read-only review

**Reviewer:** dispatched worker (fresh, independent).
**Scope reviewed:** `src/gigai/scout_tools.py`, `src/gigai/native_records.py`
(tool publisher: `_publish`, `_create_native_record`,
`create_native_record_from_tool`), `src/gigai/lifecycle.py` manifest
publication (`approve_offline`, `_recover_approved_publication`,
`_existing_capability_manifest_ref`), `src/gigai/capabilities.py`
(`validate_capability_manifest`, `_validate_manifest_semantics`,
`capability_manifest_artifact_ref`), `src/gigai/schemas/capability-manifest.schema.json`
(diff), `src/gigai/schemas/scout-operation-receipt.schema.json`,
`src/gigai/validators.py` registration, `tests/test_scout03_c3_tools.py`.
Read for context: SCOUT-03-C3-tool-implementation.md,
SCOUT-03-C3-integration-notes.md, SCOUT-00-user-owned-gig-amendment.md §4
(frozen), SCOUT-03-correction-plan.md, scout roadmap SCOUT-03 row.

**Method:** source review plus three narrow disposable probes (deleted):
a mid-flight source-change probe against the publication revalidator, and two
symlink-rejection probes. Ran `tests/test_scout03_c3_tools.py` once to confirm
worker evidence (8 passed, ~28s). No source or committed-test edits. No
private `.gigai/` data, providers, package files, commits, or other worker
edits touched. Central schema hashes / SHA256SUMS / verifier fixtures are the
coordinator's parallel work and were not evaluated for closure.

---

## Verdict

**The delivered slice is a correct, fail-closed partial service seam. It does
NOT yet satisfy the contracted "real Gig-owned fixture entry" acceptance, and
whole SCOUT-03 C3 is not complete.**

What is genuinely delivered and verified by source review + probes:

- Authority is the **committed** active-version pointer plus the **committed**
  capability-manifest journal artifact, not working-tree bytes or install
  state. `scout_tools._artifact_ref_matches` reads `snapshot.artifacts[path]`
  (journal snapshot), and `approve_offline` publishes the selected manifest as
  an authenticated `manifests/capabilities/<id>.json` artifact in the same
  `gig_accepted` transition as the pointer (lifecycle.py:1729-1742); the
  recovery path does the same (lifecycle.py:1895-1905). A later version that
  inherits the manifest carries only the immutable ref
  (`_existing_capability_manifest_ref`), and that path fail-closes if the
  working manifest drifts from the sealed ref.
- Separate effect authorization: `_binding` requires
  `declared_effects == ["write_workpad"]`, `permissions ==
  {"filesystem":"write_isolated","network":"none","credentials":"none"}`,
  `availability_state == "available"`, `security_review.status == "passed"`,
  and `tool_binding.{entry_path, operations:[record_create], effects:[write_workpad]}`.
  A broadened effect in the request (`["write_workpad","network"]`) is
  refused (`_request` → `tool_effect_refused`; covered by a parametrized test).
- Exact source inventory + publication-time revalidation: `_inventory` reads
  every listed file under `tools/<capability_id>/` from disk, rejects
  intermediate and member symlinks (`_regular_file` per-component `lstat` +
  `rglob` `lstat`), rejects executable bits, rejects extra/foreign members
  (`actual != seen`), recomputes `inventory_sha256` over canonical JSON and
  requires it to equal both the approved binding value and the caller-supplied
  value, and requires the entry digest to equal
  `source_constraints.required_digest`. A caller-invented digest is therefore
  inert (covered by a parametrized test). **Probe confirmed** a member symlink
  and a symlinked subdirectory are both refused (`tool_source_unsafe`).
- Publication re-check under the journal writer lock: `_publish` checks an
  identical existing receipt first (idempotent replay returns the original
  receipt/IDs), then runs `tool_revalidator(snapshot)` which re-reads
  `read_index().active_version` fresh and re-runs the full `_binding` against
  the publication snapshot, and refuses if the recomputed binding differs.
  **Probe confirmed**: corrupting an approved tool byte *after* the
  dispatch-time `_binding` but *before* the publication revalidator is refused
  (`approved tool source changed`) with **no receipt committed**. Because
  `run_with_journal_writer` serializes writers, a concurrent approval cannot
  interleave with the publication transaction; it is seen by the revalidator or
  blocked behind it. `payload_sha256` includes `tool_binding`, actor and
  origin, so a different binding or actor with the same content is not treated
  as a replay.
- Direct built-ins stay distinct: `create_native_record` takes no
  binding/revalidator; its receipts carry no `tool_binding`. `_publish`
  enforces `(tool_binding is None) == (tool_revalidator is None)`.
- `record_update` / `record_archive` deliberately refuse
  (`tool_operation_refused`); a manifest binding declaring them is
  un-dispatchable (`_binding` requires `operations == ["record_create"]`).
- No Python source is imported or executed anywhere in the path; the request
  is a typed `record_create` dict. This is consistent with the task's stated
  deferred sandbox/executor scope.

Why it is **not** the contracted acceptance and **not** "whole C3 complete":

- **No real Gig-owned entry / adapter / `gig.py` / CLI exists.**
  `dispatch_native_record_tool` has zero callers outside the test
  (`grep` across `src/gigai/`). The integration notes require the fixture to
  "Exercise the real supported entry point" and "Let the tool construct a
  typed operation"; the test hand-builds the `invocation` dict and the
  inventoried `record_tool.py` (`def build_native_record_request(): return
  None`) is never imported or called. The doc's own "remaining C3 work" list
  concedes `gig.py`, the tool adapter, package/tool initialization, consent UX
  and execution authorization are all still open. Roadmap SCOUT-03 "Done when"
  (fresh session recovers the right work; unselected/private records cannot
  leak through package/public paths) is out of scope of this slice and is not
  demonstrated here.
- Integration-notes step 4 ("Change the tool bytes **between dispatch and
  publication**: refuse without a record commit") has **no committed negative
  test.** The only delivered mutation test
  (`test_tool_refuses_changed_schema_or_extra_executable_before_publication`)
  mutates bytes *before* `dispatch_native_record_tool` is called, so the
  dispatch-time `_binding` catches it and the `tool_revalidator` path
  (native_records.py:321-324) and the publication-time `_binding` re-run are
  **never exercised by worker evidence.** The mechanism is correct (my probe
  proves it), but the contract explicitly requires that negative coverage and
  the task explicitly requires "actual negative coverage for changes between
  dispatch and publication, not only edits before dispatch."

---

## Findings

### F1 — Missing negative coverage: change between dispatch and publication
**Severity:** Important (blocks C3 acceptance; not a correctness defect).
**Affected requirement:** SCOUT-03-C3-integration-notes.md "Required tool-path
acceptance story" step 4; correction-plan C3 bullet 1; dispatch task
requirement "actual negative coverage for changes between dispatch and
publication."
**Evidence:** `tests/test_scout03_c3_tools.py:182-194` mutates
`operation.schema.json` and adds an executable member *before* calling
`dispatch_native_record_tool`. Dispatch order is
`_binding` (scout_tools.py:248) → `create_native_record_from_tool` →
`_publish` → transaction: `_existing_receipt` → `tool_revalidator`
(native_records.py:321). A pre-dispatch change is caught at step `_binding`;
`tool_revalidator` / publication-time `_binding` get no failing case. Reviewer
probe (deleted): wrapping `native_records._existing_receipt` to corrupt an
approved byte on first call, then dispatching, yields
`ScoutToolError('approved tool source changed')` and zero
`records/operations/record_create-*` receipts — the mechanism works but is
untested.
**Remedy (narrow):** add one negative case that lets dispatch-time `_binding`
pass, then perturbs an approved tool byte (or the manifest bytes, or approves a
new version) via a seam reached inside the publication transaction, and asserts
`ScoutToolError` plus no committed receipt and no new journal commit. A
`tool_revalidator`/`_existing_receipt` hook or an injectable pre-publication
callback is sufficient; no redesign needed.

### F2 — Acceptance fixture does not exercise a real supported entry
**Severity:** Important (scoping/closure claim), partly deferred to SCOUT-05.
**Affected requirement:** integration-notes "Exercise the real supported entry
point, not a helper fed a caller-invented digest"; "The final C3 handoff must
identify the supported command/library paths and tests, not merely list new
helper functions or schema files."
**Evidence:** `dispatch_native_record_tool` has no caller in `src/gigai/`
(cli.py, native_records_cli.py, scout_checks.py). The test constructs the
`invocation` mapping directly and the inventoried `record_tool.py` is inert and
never loaded. `SCOUT-03-C3-tool-implementation.md` names
`gigai.scout_tools.dispatch_native_record_tool` as "the one supported entry"
but there is no adapter, `gig.py`, or CLI, and the doc defers all of that to
SCOUT-05.
**Remedy (narrow, no new consent UX):** either (a) explicitly restate in the
C3 handoff that the delivered artifact is a library seam only and that the
"real Gig-owned fixture entry" acceptance row remains **incomplete** pending
SCOUT-05 scaffolding, or (b) add a minimal in-repo adapter that imports the
inventoried entry and builds the typed request, and drive the acceptance test
through it. Do not claim the acceptance story is satisfied without one of
these.

### F3 — `tool_binding` internal consistency is unvalidated at manifest approval
**Severity:** Minor.
**Affected requirement:** amendment §4 "declarative inventoried Gig source,
validated before a new version is approved"; correction-plan C3 "checked before
dispatch and publication" (met at dispatch/publish, not at approval).
**Evidence:** `capabilities._validate_manifest_semantics`
(capabilities.py:118-161) never inspects `tool_binding`. A manifest can be
approved with `tool_binding.inventory_sha256` not equal to the digest of its
own declared `inventory`, or `entry_path` absent from `inventory`, or
`tool_binding.inventory` inconsistent with `source_constraints.required_digest`.
Such a manifest is simply un-dispatchable later (all mismatches are caught in
`scout_tools._binding`), so there is no security exposure, but an
internally-broken tool binding can reach an approved Gig version silently.
**Remedy:** in `_validate_manifest_semantics`, for `kind == "tool"` capabilities
carrying a `tool_binding`, assert `inventory_sha256 ==
digest(canonical_json(inventory))`, `entry_path` is one of the inventory
paths, inventory paths are unique and sorted, and the entry item's
`content_sha256 == source_constraints.required_digest`.

### F4 — `source_constraints.required_identity` unchecked for tool capabilities
**Severity:** Minor / informational.
**Evidence:** `scout_tools._binding` validates `required_digest` (line 209) but
never reads `required_identity`; `capabilities.py` checks `required_identity`
only for installed capabilities (capabilities.py:449-452). The test sets it to
`"record_tool.py"` and nothing enforces it.
**Remedy:** either drop the field's relevance for `kind == "tool"` in the
handoff, or check `entry_path` basename against `required_identity` in
`_binding`. The entry path + digest already pin the source, so this is
belt-and-suspenders.

### F5 — Additive schema change breaks strict old readers pinned to `:1`
**Severity:** Minor (coordinator owns the central inventory / SHASUMS / `$id`).
**Affected requirement:** "strict nested/additive validation and old-reader
compatibility."
**Evidence:** `capability-manifest.schema.json` and
`scout-operation-receipt.schema.json` both use `additionalProperties: false` at
the object level where `tool_binding` was added, and `tool_binding` is optional
(absent from `required`). New-reader/old-data is fine (optional field). But
old-reader/new-data fails: a reader holding the pre-C3 schema bytes rejects any
manifest or receipt that carries `tool_binding`, because
`additionalProperties: false` forbids the unknown key. Both `$id`s remain
`...:1`. Direct built-in receipts omit `tool_binding`, so only tool-bridge
artifacts are affected.
**Remedy:** confirm with the coordinator that all in-tree readers use the
bundled (updated) schema (they do — single `SCHEMA_NAMES` registry), and note
in the handoff that any externally-pinned `:1` copy must be refreshed. If
external pinning is a real constraint, bump `$id` to `:2`. No code change
needed for the in-repo path.

---

## Areas checked and found sound (no finding)

- **Approval semantics / committed vs editable authority:** dispatch validates
  the pointer against `active-gig-version-v2.schema.json`, requires
  `pointer.gig_id == resolved.gig_id == request.gig_id` and
  `request.gig_version == pointer.active_version`, and reads the manifest from
  the journal snapshot. Working-tree manifest tampering post-approval does not
  affect dispatch. `approve_offline` re-hashes the manifest file against the
  computed ref before journaling and raises if it changed during approval
  (lifecycle.py:1737-1741, mirrored in recovery).
- **Source snapshots vs only digests:** the tool bytes themselves are re-read
  from disk at both dispatch and publication and compared by digest and by
  full-directory membership; the receipt records the full inventory (paths +
  digests + sizes + media), the manifest ref, gig_version, entry_path,
  operation, effects and agent identity (schema-required, 9 fields).
- **Caller-invented tool bindings/revalidators bypassing the service:**
  `create_native_record_from_tool` is only reachable with a binding + a
  revalidator (both or neither), the revalidator independently recomputes the
  binding from committed state under the lock and must match, and the CLI
  `record create` path uses `create_native_record` (no binding) only. A caller
  invoking `create_native_record_from_tool` directly must still supply a
  revalidator that reproduces a valid committed binding, so a fabricated
  binding fails at `_publish` (`tool_binding_invalid`).
- **Replay / CAS / actor provenance:** operation-key + normalized-payload
  (incl. actor, origin, tool_binding) identity inside the writer critical
  section; identical retry returns the original receipt/IDs
  (`test_..._dispatches_only_through_native_journal_service` asserts
  `not replay.created`); same key + different payload conflicts
  (`_existing_receipt` → `native_operation_conflict`).
- **Path / link / type / race safety:** `_safe_tool_path` rejects
  non-`tools/` prefixes, `\`, empty/`.`/`..` parts, and post-join escape;
  `_regular_file` rejects per-component symlinks and non-regular / executable
  final targets; `_inventory` rglob rejects member symlinks and executable
  members and any file not in the approved set; a FIFO/socket in the tool root
  is never opened (not `S_ISREG`) and triggers `actual != seen`. TOCTOU
  between the lstat walk and `read_bytes` is closed by the digest comparison
  and the publication-time re-read under the writer lock.
- **Foreign Gig / version / capability:** parametrized negative cases cover
  wrong active version, foreign gig_id, foreign capability_id, false caller
  digest, broadened effects, non-agent actor — all `ScoutToolError`.
- **Agent origin required:** `_actor` requires `kind == "agent"` with a
  1..255-char id; `_request` requires `origin == "agent_supplied"` and
  `operation == "record_create"`; the CLI cannot mint a tool-bound
  `agent_supplied` record (it hardcodes an operator actor and no binding).
- **Receipt `gig_version` minimum:2** encodes that a tool-bearing version is
  never the first approved version; the happy-path fixture approves version 3,
  so this is satisfied. Consider stating this invariant explicitly in the
  handoff.

## Not evaluated (out of scope per dispatch)

Central schema inventory closure, `SHA256SUMS`, `tools/verify_installed_schemas.py`
and golden fixtures (coordinator's parallel work); full/focused suite reruns;
package/export private-record guarding (`package_privacy.py`,
SCOUT-03-C3-package-*); run-plan / run / provider private-provenance ingress
(SCOUT-03-C3-input-*); SCOUT-04 external executor; SCOUT-05 scaffolding;
private `.gigai/` data.

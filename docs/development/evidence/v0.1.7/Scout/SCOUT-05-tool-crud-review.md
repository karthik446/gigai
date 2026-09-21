# SCOUT-05 approved per-Gig full CRUD — independent read-only review

**Reviewer:** dispatched worker (fresh, independent). Read-only.
**Method:** source inspection of the delivered CRUD seam plus recorded tests and
fixtures. One recorded probe was attempted and could not run for an
environment reason unrelated to this lane (see *Probe status*). No source, no
test, no fixture, no private `.gigai/` state was edited. No provider calls, no
commit, no suite rerun.

**Verdict:** **Accept (scoped).** The CRUD-specific security properties in the
dispatch checklist all hold in source. `record_update` / `record_archive` reach
publication only through the same committed owner-Gig / approved-version /
inventoried-source / agent-origin authority the C3 `record_create` seam already
established, and are additionally CAS-bound, replay-idempotent, tombstone-only,
and operation-shape-strict. No CRUD-specific defect rises to blocker or major.
Two low-severity observations and several already-conceded whole-SCOUT-05 gates
are listed below; none blocks accepting this slice.

**Scope reviewed (source):**
`src/gigai/scout_tool_adapter.py`,
`src/gigai/scout_tools.py`,
`src/gigai/native_records.py` (`_publish`, `_create_native_record`,
`_update_native_record`, `_archive_native_record`, `*_from_tool`, `_chain`,
`_existing_receipt`, `_from_receipt`, `_native_rows`, `native_context`),
`src/gigai/capabilities.py` (`_validate_manifest_semantics`,
`_validate_tool_binding`, `validate_capability_manifest`,
`materialize_capability_manifest`),
`src/gigai/data/scout/gig.py`,
`src/gigai/schemas/scout-operation-receipt.schema.json`,
`src/gigai/schemas/capability-manifest.schema.json` + `SHA256SUMS`,
`src/gigai/workpad.py::resolve_workpad`,
`tests/test_scout05_tool_crud.py`, and the affected fixtures
`tests/test_scout03_c3_tools.py::_manifest` / `_approved_tool`,
`tests/test_scout05_tool_scaffold.py::test_update_and_archive_require_an_explicit_approved_crud_binding`.
Read for context: `SCOUT-00-user-owned-gig-amendment.md` §4 and the journal-row
`records/` table, `SCOUT-00-contract-amendments.md`,
`SCOUT-03-C3-tool-corrections-review.md`,
`SCOUT-05-tool-scaffold-correction.md`,
`SCOUT-05-tool-crud-implementation.md`.

---

## Checklist findings

### 1. Exact owner Gig vs active selection — HOLDS

Three independent layers pin the owner Gig by identity, never by active
selection:

* `gig.py::_owning_workpad` (`data/scout/gig.py:129-176`) authenticates the
  wrapper's own on-disk location: `_wrapper_parent` rejects a non-`gig.py`
  name, a symlinked/irregular script, and any `resolve(strict=True)` that
  differs from the un-resolved path (redirected source). It then requires
  **exactly one** registered workpad row whose `workpad_locator` equals that
  parent path (`:143-152`), passes that row's `record.gig_id` **explicitly**
  to `resolve_workpad`, and asserts `resolved.path == wrapper_parent` plus
  `os.path.samefile` (`:167-176`). `active_gig_id` is never read; the comment
  at `:154-155` is accurate.
* `resolve_workpad` (`workpad.py:254-297`): when `gig_id is not None`
  (always true on this path — `gig.py` passes `record.gig_id`, and
  `scout_tools` threads `resolved.gig_id` into every service call), `selected`
  is set from the supplied id and **both** active-selection branches
  (`:267-292`) are skipped. No `NoActiveGigError` fallthrough, no
  `select_active_workpad` side effect.
* `scout_tools`: `dispatch_native_record_tool` (`:316-317`) refuses when
  `request["gig_id"] != resolved.gig_id`; `_binding` (`:198`) refuses unless
  `pointer.gig_id == resolved.gig_id == request["gig_id"]` **and**
  `request["gig_version"] == pointer.active_version`; `_binding` (`:205-206`)
  refuses when the committed manifest's `gig_id` is another Gig.

Recorded coverage:
`test_wrapper_crud_is_bound_to_its_own_gig_not_active_or_foreign_gig`
(`test_scout05_tool_crud.py:259-286`) drives wrapper B in a project where Gig A
just wrote a record, and asserts B's foreign `archive` fails
(`tool_authority_unavailable` / `tool_capability_refused`) with B's operation
set left empty. The scaffold lane
(`test_scout05_tool_scaffold.py:112-204`) additionally proves A-active-B and
no-active-Gig isolation for the read/create paths. **No leak.**

### 2. Committed version / manifest / effects / operation authority — HOLDS

`_binding` (`scout_tools.py:195-250`) rebuilds the whole binding from
committed bytes only:

* active-version pointer validated against `active-gig-version-v2.schema.json`
  (`:196`); `gig_version` must equal `pointer.active_version` (`:198`).
* `capability_manifest` artifact ref matched byte-for-byte against the locked
  snapshot (`_artifact_ref_matches`, `:56-67` — path prefix, exact
  `content_sha256`, exact `size_bytes`, `application/json` media) and then
  re-validated through `validate_capability_manifest` (`:202`), which runs the
  full `_validate_tool_binding` semantic layer.
* effects pinned to exactly `["write_workpad"]` on the request (`:104`), the
  capability `declared_effects` (`:215`), and the binding `effects` (`:226`);
  `permissions` must be exactly
  `{"filesystem":"write_isolated","network":"none","credentials":"none"}`
  (`:215`); `availability_state == "available"` and
  `security_review.status == "passed"` (`:217`).
* operation must be a member of the approved `binding.operations`, which must
  itself be sorted, de-duplicated and a subset of the three admitted labels
  (`:219-228`).
* `origin` forced to `"agent_supplied"`, operation forced into
  `_ALLOWED_OPERATIONS` (`:102`).

`capabilities._validate_tool_binding` (`capabilities.py:165-217`) enforces the
same cross-field consistency at manifest **approval** (invoked per capability
from `_validate_manifest_semantics:133`, merged into
`validate_capability_manifest:229`, raised on by
`materialize_capability_manifest`): canonical sorted unique inventory,
`inventory_sha256 == canonical_json_digest(inventory)`, entry is an
inventoried member, `entry.content_sha256 == source_constraints.required_digest`,
`Path(entry_path).name == source_constraints.required_identity`,
`operations` non-empty / sorted / de-duplicated / admitted subset
(`:206-214`), `effects == ["write_workpad"] == declared_effects` (`:215-217`).
A `tool_binding` on a non-tool capability is rejected (`:178-180`).

### 3. Source-inventory re-check under the writer lock, owned by the publisher — HOLDS

`_publish`'s `transaction` (`native_records.py:314-374`) runs inside
`run_with_journal_writer` (`:377`). After `writer.snapshot(...)` and the
replay short-circuit, **for a tool binding only**, it does
`from .scout_tools import revalidate_tool_binding_at_publication` and calls it
with the locked snapshot (`:327-333`). That revalidator
(`scout_tools.py:275-287`) independently re-reads
`read_index(...).active_version` and re-runs the full `_binding`, whose
`_inventory` (`:156-192`) re-reads every inventoried tool byte from disk,
re-digests it, re-checks size, and walks the entire
`tools/<capability_id>/` root rejecting any symlink, any executable-bit
member, and any file not in the approved inventory (`actual != seen ->
tool_inventory_changed`). `_publish` then requires
`verified_binding == dict(tool_binding)` (`:334-335`) and separately
`tool_binding.operation == operation_name` and
`tool_binding.actor == dict(actor)` (`:322`).

The only caller-reachable `Callable` on the tool path is
`_before_publication` / `_before_tool_revalidation` — a **no-arg, no-return**
test hook (`scout_tools.py:308,329`; `native_records.py:304,320-321`). It is
passed no reference to `tool_binding`, cannot supply/mutate/observe it, and
fires *before* revalidation so any state it perturbs is caught. A direct
caller of `*_from_tool` supplying a fabricated binding is refused because the
publisher's independent revalidation cannot reproduce it from committed
authority (C3 `test_exposed_tool_publisher_cannot_mint_a_fabricated_binding`
still applies; the CRUD publishers add no new binding parameter).

Recorded coverage:
`test_tool_crud_refuses_stale_mutated_and_malformed_operations_before_publication`
(`test_scout05_tool_crud.py:237-256`) mutates `operation.schema.json` from
inside `_before_publication` on a `record_update` and asserts
`ScoutToolError("approved tool source changed")`, unchanged git HEAD, and an
unchanged `records/operations/` set.

### 4. CAS — HOLDS

`_update_native_record` / `_archive_native_record`
(`native_records.py:496-531`) take a pre-check snapshot, require `parent`
(the named `parent_revision`) to be a committed revision of the chain
(`stale_parent` otherwise), then delegate to `_publish` with that
`parent_revision`. Inside the writer lock, `_publish` (`:352-355`)
re-derives `_chain(...)[-1]` and refuses unless its `revision_id` equals the
supplied `parent_revision` — so the authoritative compare-and-swap is under
the lock, not at the earlier lock-free read. `run_with_journal_writer`
serialises writers, so a racing commit either advances the tip (then the CAS
fails on retry) or does not (then the append is correct); there is no
interleave window. Update also refuses a kind/scope change (`:505-506`).

Recorded coverage: `...:213-222` (stale `--parent-revision` →
`stale_parent`, HEAD and operation set unchanged);
`test_fresh_wrapper_executes_approved_create_update_archive_with_cas_and_replay:159-198`
(a real create→update→archive chain, each naming the prior tip).

### 5. Replay of update/archive before the terminal checks, without leaking cross-actor / cross-origin authorization — HOLDS

`_publish`'s `_existing_receipt` short-circuit (`native_records.py:316-318`)
runs before the tool-binding revalidation and before the archived/CAS check.
It returns the original receipt **only** when
`payload_sha256 == digest(canonical_json_bytes(intent))` matches an existing
receipt at `records/operations/<op>-<hash(operation_key)>.json`. The `intent`
for update/archive (`:508`, `:528`) includes `record_id`, `parent_revision`,
`content_sha256`, `origin`, `actor`, and (tool path) the full `tool_binding`.
Consequences:

* A different actor or origin → different `payload_sha256` → `_existing_receipt`
  raises `native_operation_conflict` ("operation key was already used with
  different payload", `:234-235`). A replay cannot be re-attributed.
* `_receipt_path` is keyed on `operation` + `operation_key` only, but receipts
  live in the per-Gig journal, so cross-Gig replay is structurally impossible.
* A genuine same-intent, same-actor replay returns `created: False` and the
  original `revision_id` (`_from_receipt`, `:290-301`) and mutates nothing —
  this is the amendment's "matching-key retry returns the original receipt".
* Replaying an `operation_key` whose record has since been archived still
  returns the original committed revision (idempotent), and asserting a *new*
  append against a stale/archived tip is separately refused (see #4 and #6).
  This is correct: replay reports history, it does not revive.

Recorded coverage: `...:171-175` (update replay → `created is False`, same
`revision_id`); `...:186-187` (archive replay after archive →
`created is False`).

### 6. Tombstones preserve history — HOLDS

Archive (`_archive_native_record:518-531`) reuses the parent's committed
sidecar verbatim (`native, _ = _sidecar(resolved, snapshot, parent)`) — no
caller content — and `_publish` appends a **new** revision
`records/<id>/revisions/<new>.json` with `parent_revision` set to the prior
tip and `state: "archived"`, plus a fresh sidecar blob. Prior revisions and
blobs are never rewritten (append-only journal; transition type
`private_record_archived`, `:369`). `_native_rows` hides `archived` tips
unless `include_archived=True` (`:560-561`); `read_native_record` with an
explicit `revision_id` still returns the pre-archive revision. After an
archived tip, a further update/archive naming that tip is refused with
`native_record_archived` (`:356-357`) and one naming an older revision is
refused with `stale_parent` — no post-archive revival.

Recorded coverage: `...:189-198` asserts `current["state"] == "archived"`,
`old["state"] == "active"` (pre-archive revision still readable), and that
both `record_update-` and `record_archive-` operation receipts persist.
Matches `SCOUT-00-user-owned-gig-amendment.md` §4 ("delete appends an
archive/tombstone", "Hard deletion / privacy erasure is not promised").

### 7. Operation-specific payload strictness — HOLDS

`scout_tools._request` (`:79-120`) computes an **exact** expected key set per
operation and refuses any deviation (`set(value) != expected -> tool_invocation_invalid`):
`record_create` = base + `content`; `record_update` = base + `record_id` +
`parent_revision` + `content`; `record_archive` = base + `record_id` +
`parent_revision` (**no `content`**). Types are then re-checked
(`content` must be a Mapping for create/update; `record_id` /
`parent_revision` must be non-empty strings for update/archive, `:112-116`).
`scout_tool_adapter` mirrors this: `native_record_archive_operation`
(`scout_tool_adapter.py:54-62`) returns only
`{operation_key, record_id, parent_revision}`;
`native_record_update_operation` requires `content`.
`invoke_approved_tool_entry` (`scout_tools.py:440-446`) re-validates the
builder's return against the same per-operation shape before dispatch.

Recorded coverage: `...:224-234` (`operation: "record_delete"` with a
`content` field → `ScoutToolError`, HEAD unchanged).

### 8. Wrapper cannot manufacture operator / application-event consent — HOLDS

`gig.py` for create/update/archive always constructs
`actor={"kind": "agent", "id": args.actor_id}` and hardcodes
`"origin": "agent_supplied"` in the response envelope
(`data/scout/gig.py:250-284`). `scout_tools._actor` (`:70-76`) **rejects any
non-`agent` actor kind** (`tool_invocation_invalid`, "tool operations require
an agent origin"), and `_request` forces `origin == "agent_supplied"`
(`:102`). `_publish` cross-checks `tool_binding.actor == dict(actor)`
(`:322`). The operator-facing built-ins
(`create_native_record` / `update_native_record` / `archive_native_record`,
which permit an `operator` actor) are **not imported or reachable** from
`gig.py` — it only calls `list_native_records`, `read_native_record`,
`native_context`, and `invoke_approved_tool_entry`. The same-account Python
loader in `invoke_approved_tool_entry` (`:410-426`) sets
`sys.dont_write_bytecode`, does not insert into `sys.modules`, and is
explicitly not a sandbox — but it cannot cross the actor/origin constraint,
and a published record's binding is still authenticated under the lock. The
implementation doc's claim ("neither creates operator consent nor represents
any agent/tool declaration as an application-submitted event") is accurate in
source.

### 9. Create-only capabilities refuse update / archive — HOLDS

With `binding.operations == ["record_create"]`, `_binding` (`:219-228`)
refuses any request whose `operation` is not a member
(`tool_binding_invalid`, "tool request does not match the approved binding").
Widening the set requires a **new approved manifest version**: the CRUD test
fixture materialises `operations = ["record_archive","record_create","record_update"]`
and runs it through `propose_graph_set_offline` + `approve_offline`
(`test_scout05_tool_crud.py:91-110`); there is no path that adds an operation
to an already-approved create-only binding without re-approval
(`_validate_tool_binding` + `validate_capability_manifest` gate approval;
`_binding` re-validates the committed manifest at dispatch).

Recorded coverage:
`test_scout05_tool_scaffold.py::test_update_and_archive_require_an_explicit_approved_crud_binding`
(`:339-361`) — the C3 create-only fixture; `update` and `archive` both return
`tool_binding_invalid` with `next_action.kind == "proposal_required"`.
The C3 approval-rejection parametrization
(`test_scout03_c3_tools.py:271`) additionally rejects an **unsorted**
`["record_update","record_create"]` operations list at materialization.

---

## Observations (low severity, non-blocking)

* **L1 — `record_archive` tombstone origin is always `agent_supplied` on the
  tool path, regardless of the parent revision's origin.**
  `_archive_native_record` (`native_records.py:527`) does
  `actual_origin = str(parent["origin"]) if origin is None else origin`; the
  tool path passes `origin="agent_supplied"` (never `None`), so a tombstone
  over a `user_reported` / `imported` record records `origin: agent_supplied`.
  The built-in operator `archive_native_record` passes `origin=None` and
  inherits. This is a defensible, deliberate distinction (the tombstone
  attributes *who archived*, and the parent revision keeps its own origin
  intact and readable), and `agent_supplied` is the least-privileged origin,
  so there is no authorization escalation. Flagging only so the coordinator
  can confirm the projection / tracker rendering does not misattribute the
  *record* as agent-origin because its current tip is. No code change
  required for the CRUD contract; worth a one-line note in the SCOUT-05
  tracker/UI slice.

* **L2 — `_publication_request` uses placeholder `record_id` /
  `parent_revision` for the revalidation request
  (`scout_tools.py:270-271`).** This is safe today because
  `_binding` never inspects the record chain (chain/CAS resolution lives in
  `_update_native_record` / `_archive_native_record` / `_publish`, not in
  `_binding`), so the placeholders are inert. If a future change moves any
  record-scoped check into `_binding`, the revalidation path would validate
  against fabricated IDs. Recommend a short comment at `:253-272` stating that
  `_binding` is authority-only by contract and must not gain record-scoped
  checks, or that `revalidate_tool_binding_at_publication` must be handed the
  real target IDs if it does.

## Probe status

`.venv/bin/pytest -q tests/test_scout05_tool_crud.py::...` could **not**
collect: `src/gigai/target_binding.py:434` currently has an
`IndentationError` inside `_initialize_non_git_target` (file is `M` in the
working tree, part of the init/registry/target lane Terra is concurrently
correcting). This blocks import of `gigai.workpad` → `gigai.journal` →
the whole Scout test module. Per this dispatch's exclusions I did not touch or
probe that lane. This is **not** a SCOUT-05 CRUD defect; it is a transient
state in a sibling lane and should clear when Terra's correction lands. The
recorded evidence in `SCOUT-05-tool-crud-implementation.md` (all five focused
runs green, executed 2026-09-09) plus this source review are the basis for the
verdict.

## Schema / hash

`scout-operation-receipt.schema.json` recomputes to
`3506617a28ca0211748fdf2937cbbb6eb70201a681472ef9316d8a5bdea0d8a6` and
`capability-manifest.schema.json` to
`b2227c05feee824c39e4cea12aa3eb10fad0b50ebe8c865dca354ddf80682493`; **both
match their `SHA256SUMS` entries** — the stale-hash note from the C3
re-review has already been refreshed by the coordinator. The receipt schema's
`tool_binding.operation` enum admits the three CRUD labels, `gig_version`
`minimum: 2`, `effects` items `const "write_workpad"` with `uniqueItems`,
`actor.kind` `const "agent"` — consistent with the code's runtime checks.
Central-registry / verifier / golden-fixture closure remains coordinator-owned
and was not evaluated here.

## Out of scope for this verdict (explicit open product gates, not CRUD defects)

1. **Root tool-source approval binding** and **actual source / proposal /
   default shipping integration** — `gig.py` is not yet in
   `scout_template.py::scout_source_files`, not copied into each Gig's
   editable root, and has no package/hash verification; the root wrapper is
   not pinned execution authority until that lands. Called out by
   `SCOUT-05-tool-scaffold-correction.md` and the implementation doc's
   *Limits* section. Terra's init/registry/workpad/target lane.
2. **Copied-source installation proof** and remaining Scout domain workflows
   (import-run, report generation, tracker UI).
3. **Same-account loader is not a sandbox** — stated, not overclaimed;
   arbitrary same-account Python bypass writes invalidate supported evidence
   or are repaired as disposable SQL divergence, they are not certified.
4. Consent UX / execution authorization / scheduler behaviour — deferred per
   amendment §4.

## Evidence-accuracy statement

Source-inspection review plus one attempted (blocked) recorded probe. No
files modified. No suite rerun. No provider calls. No commit. Fixtures, user
`.gigai/` state, other evidence reports, and other workers' worktrees left
untouched.

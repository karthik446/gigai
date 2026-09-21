# SCOUT-03 C3 tool bridge corrections — independent read-only re-review

**Reviewer:** dispatched worker (fresh, independent). Read-only.
**Verdict:** **Accepted (scoped).** The five review findings (F1–F5) and the
coordinator forged-callback concern are resolved *for the delivered library
seam*. Whole-C3 and SCOUT-05 requirements that remain open are listed below and
were already conceded by the corrections doc; they are not blockers to
accepting this correction.

**Scope reviewed (source only):**
`src/gigai/scout_tools.py`, `src/gigai/scout_tool_adapter.py`,
`src/gigai/native_records.py` (publisher `_publish` / `_create_native_record` /
`create_native_record_from_tool`), `src/gigai/capabilities.py`
(`_validate_manifest_semantics`, new `_validate_tool_binding`),
`src/gigai/schemas/capability-manifest.schema.json` (diff),
`src/gigai/schemas/scout-operation-receipt.schema.json` (new),
`src/gigai/schemas/SHA256SUMS` (diff), `tests/test_scout03_c3_tools.py`.
Read for context: `SCOUT-03-C3-tool-corrections.md`,
`SCOUT-03-C3-tool-review.md`, `SCOUT-03-C3-integration-notes.md`,
`SCOUT-00-user-owned-gig-amendment.md` §4 (frozen),
`SCOUT-03-C3-tool-implementation.md`.

**Method:** source review only. **No probe was run.** No pytest / suite
reruns (coordinator verifies the new race / forgery / fresh-process cases per
dispatch). No source, fixtures, private `.gigai/`, other reports, or other
workers' edits were touched. No provider calls, no commits.

---

## Finding-by-finding disposition

### F1 — negative coverage for a change *between dispatch and publication* — RESOLVED
A committed regression now exists:
`test_tool_change_after_dispatch_validation_refuses_before_record_publication`
(`tests/test_scout03_c3_tools.py:241-259`). It lets dispatch-time `_binding`
pass, then via the `_before_publication` hook (fired **inside** the journal
writer lock at `native_records.py:320-321`, after the transaction snapshot,
before revalidation) rewrites the approved `operation.schema.json` bytes, and
asserts `ScoutToolError("approved tool source changed")`, unchanged Git HEAD,
and an unchanged `records/operations/` set.

Source path confirms the mechanism: `_publish`'s `transaction`
(`native_records.py:314-336`) takes `writer.snapshot(...)` under the lock,
short-circuits on an identical existing receipt, then — for a tool binding —
calls `revalidate_tool_binding_at_publication` (imported *inside* the
transaction from `scout_tools`). That revalidator re-reads
`read_index(...).active_version` fresh, then `_binding` re-reads the manifest
from the locked snapshot and re-runs `_inventory`, which re-reads every tool
byte from disk and re-digests it. A post-dispatch mutation is caught here with
no `writer.record(...)` call, so nothing commits. `ScoutToolError`
(a `PrivateRecordError`) is not a `JournalConflictError`, so it propagates out
of `run_with_journal_writer` cleanly.

Race note (source reasoning, not probed): `run_with_journal_writer` serializes
all writers, so a concurrent `approve_offline` cannot commit inside the
publication transaction. The revalidator sees committed state as of lock
acquisition — either the new version (then the request's `gig_version` no
longer matches `pointer.active_version` → refuse) or the old version (publish
proceeds correctly under the old binding). No interleave window.

### F2 — acceptance fixture must exercise a real supported entry — SUBSTANTIALLY RESOLVED, command-path deferred
Genuinely delivered now:
- `scout_tool_adapter.native_record_create_operation` — a tiny, authority-free
  typed constructor (`{operation_key, content}`), no I/O, no journal work.
- The fixture `record_tool.py` (`tests/test_scout03_c3_tools.py:38-45`) really
  `import`s `gigai.scout_tool_adapter` and exposes the closed
  `build_native_record_operation(context)` entry — it is no longer inert.
- `scout_tools.invoke_approved_tool_entry` resolves committed authority
  (`_authority_snapshot` + `_entry_candidate` → `_binding`), re-reads and
  re-digests the entry file against the inventoried digest, `exec_module`s it,
  calls its builder, checks the returned shape is exactly
  `{operation_key, content}`, then re-enters `dispatch_native_record_tool`
  (full `_request` + `_resolved` + `_authority_snapshot` + `_binding` again) →
  `create_native_record_from_tool` → `_publish` (revalidation under lock).
  Bytes are therefore revalidated at least three times.
- `test_fresh_process_executes_approved_fixture_entry_and_adapter`
  (`tests/test_scout03_c3_tools.py:318-346`) drives this in a **separate Python
  process** and asserts a created record and a persisted `tool_binding`.

Still open (correctly deferred): `invoke_approved_tool_entry` and
`dispatch_native_record_tool` have **zero callers in `src/`** (grep confirmed).
No `gig.py`, no CLI subcommand, no `cli.py` wiring. The corrections doc is
honest: "`gig.py` distribution and general initialization remain SCOUT-05
work", and amendment §4 assigns scaffolding/default distribution to a common
scaffold owned by SCOUT-05. This is a library-seam acceptance with a real
executed entry, not the shipped `python gig.py …` command. Accept as scoped;
see "Remaining requirements" below.

### F3 — `tool_binding` internal consistency unvalidated at manifest approval — RESOLVED
`capabilities._validate_tool_binding` (new, `capabilities.py:165-207`) is
invoked from `_validate_manifest_semantics` (`capabilities.py:133`) for every
capability, and `validate_capability_manifest` merges its report
(`capabilities.py:222`); `materialize_capability_manifest` calls
`validate_capability_manifest` and raises on any finding
(`capabilities.py:263-286`). For `kind == "tool"` with a `tool_binding` it
asserts: inventory paths unique and sorted; `inventory_sha256 ==
canonical_json_digest(inventory)`; `entry_path` is an inventoried member;
entry `content_sha256 == source_constraints.required_digest`; `operations ==
["record_create"]`; `effects == ["write_workpad"] == declared_effects`. A
`tool_binding` on a non-tool capability is rejected
(`tool_binding_not_tool`). Coverage:
`test_tool_binding_is_rejected_before_manifest_materialization` parametrizes
all eight mismatches and asserts both `validate_capability_manifest(...).valid
is False` and a `CapabilityManifestError` from materialization.

Minor (non-blocking): a few malformed-shape branches in `_validate_tool_binding`
`return` silently with no finding (e.g. `inventory` not a list, `entry_path`
not a string). Those shapes are already rejected by the JSON schema
(`tool_binding` `$ref`, `additionalProperties:false`, `required`, `path`
patterns), so the semantic layer legitimately only handles cross-field
consistency. No gap in practice.

### F4 — `source_constraints.required_identity` unchecked for tool capabilities — RESOLVED
Now enforced in **both** places:
- Manifest approval: `_validate_tool_binding` asserts `Path(entry_path).name
  == required_identity` (`tool_entry_identity_mismatch`).
- Dispatch/publication: `scout_tools._binding` (`scout_tools.py:210-216`)
  requires `Path(binding["entry_path"]).name == constraints.get(
  "required_identity")` alongside the existing `required_digest` check.
Covered by the `required_identity → "other.py"` case in the
manifest-rejection parametrization.

### F5 — additive schema change breaks strict old readers pinned to `:1` — HONESTLY DOCUMENTED, coordinator-owned
`capability-manifest.schema.json` adds `tool_binding` / `tool_inventory_item`
`$defs` and an optional `capabilities[].tool_binding` property;
`scout-operation-receipt.schema.json` (new file) carries an optional
`tool_binding`. Both keep `additionalProperties:false` and `$id … :1`. So:
new-reader/old-data is fine (optional field); old-reader/new-data fails for
tool-bearing artifacts only. The corrections doc's "Reader compatibility"
section states exactly this, makes no compatibility claim for externally
pinned `:1` copies, and assigns the registry / `SHA256SUMS` /
`verify_installed_schemas.py` / golden fixtures to the coordinator. It also
correctly states no new schema version or family is introduced. Honest and
in-scope for a scoped accept; any external-pinning decision is the
coordinator's.

Schema-byte / hash consistency (verified by recompute):
`capability-manifest.schema.json` →
`b2227c05…682493`, `scout-operation-receipt.schema.json` →
`3c6414d0…d49e75`, `native-record-content.schema.json` →
`cfde09a9…cb1dfe` — all three match their `SHA256SUMS` entries in the diff.
Central hashes are refreshed and consistent with current bytes; no schema
bytes changed as part of this correction beyond the C3 `tool_binding`
additions that F5 already evaluated. (The many other `SHA256SUMS` additions —
`active-gig-version-v2`, `gig-proposal-v2`, `external-recording-*`, etc. — are
the broader v0.1.7 Scout parallel work, not this correction.)

### Coordinator forged-callback concern — RESOLVED
`create_native_record_from_tool` no longer accepts a caller revalidator. The
only `Callable` params on the tool path are `before_tool_revalidation` /
`_before_publication` — a **no-argument, no-return** test hook that cannot
supply, mutate, or observe a binding (it is passed no reference to
`tool_binding`). The shared publisher itself performs the authentication:
`_publish`'s transaction does `from .scout_tools import
revalidate_tool_binding_at_publication` and calls it with the locked snapshot;
`revalidate_tool_binding_at_publication` independently re-reads
`read_index(...).active_version` and re-runs the full `_binding`
(`scout_tools.py:249-261`). `_publish` then requires `verified_binding ==
dict(tool_binding)` (`native_records.py:334-335`) and separately
`tool_binding["operation"] == operation_name` and `tool_binding["actor"] ==
dict(actor)` (`native_records.py:322`). A direct caller of
`create_native_record_from_tool` supplying a fabricated binding fails: the
independent revalidation cannot reproduce it from committed authority.
Covered by `test_exposed_tool_publisher_cannot_mint_a_fabricated_binding`
(asserts refusal + unchanged HEAD + unchanged operation set). Even if a
malicious `_before_publication` closed over and mutated `tool_binding`, the
`verified_binding == dict(tool_binding)` equality is computed against the
mutated dict and revalidation yields the true committed binding — a mutation
away from authority is refused, a mutation *to* authority is simply the
correct binding.

The persisted receipt `tool_binding` (`scout-operation-receipt.schema.json`
`$defs.tool_binding`) requires `manifest_ref`, `capability_id`, `gig_version`
(minimum 2 — encodes "never the first approved version"), `entry_path`,
`inventory`, `inventory_sha256`, `operation` (const `record_create`),
`effects` (const `write_workpad`), `actor` (`kind` const `agent`). The full
binding incl. actor / agent-origin / operation / version is independently
authenticated under the lock and recorded.

---

## New loader — no implicit execution; path/race limits (source review only)

- `exec_module` appears **only** in `invoke_approved_tool_entry`. `grep`
  confirms no `scout_tools` import and no `importlib` use in `lifecycle.py`,
  `capabilities.py`, `index.py`, `package.py`, `run.py`, `run_plan.py`. Init,
  clone, inspect, approval, materialization, and scheduling never load or run
  a tool entry. `native_records.py` imports `scout_tools` lazily, inside the
  publication transaction, only when `tool_binding is not None`.
- Path safety before load: `_safe_tool_path` rejects non-`tools/` prefixes,
  `\`, empty/`.`/`..` parts, and post-join escape; `_regular_file` walks the
  path per component with `lstat` and rejects any symlink, then requires a
  non-executable regular file. The entry bytes are digest-checked against the
  inventoried `content_sha256` immediately before `spec_from_file_location`.
- `sys.dont_write_bytecode` is set `True` around `exec_module` and restored in
  `finally`, so no `__pycache__` is written into the closed tool root (which
  would itself fail the immediate re-inventory). The module is **not**
  inserted into `sys.modules` (no namespace pollution).
- Closure honesty: `exec_module` runs with full interpreter access; there is
  **no sandbox and none is claimed**. The corrections doc explicitly states
  "the same-account loader is not represented as a sandbox or consent
  mechanism" and amendment §4 says editable same-account Python "is **not** a
  sandbox". A TOCTOU swap between the pre-load digest check and `exec_module`'s
  own read could run different bytes — but (a) that is same-account arbitrary
  Python, out of scope by design, and (b) the subsequent `_binding` /
  `_inventory` re-read and the publication-time revalidation prevent a
  *published record* whose binding does not match committed bytes. This limit
  is stated, not overclaimed.
- `read_index` inside the revalidator acquires `database_lock`, not the
  journal writer lock — no journal-lock reentrancy. This is the same
  `run_with_journal_writer(operation=lambda writer: read_index(...))` pattern
  already used by `_authority_snapshot` in production. It may trigger a
  disposable projection repair write; that is pre-existing `read_index`
  behavior, not new risk from this correction.

## Real fixture entry now vs SCOUT-05 default `gig.py` distribution

The fixture tool lives at `tools/<capability_id>/record_tool.py` — a
**capability-scoped `tools/` entry**, inventoried in the approved manifest and
pinned by the `tool_binding`. It is a genuine Gig-owned, approved, executed
entry (fresh-process test). It is **not** the future user-facing root
`gig.py`, and it is not shipped by any scaffold. Default `gig.py` / tool
distribution, package/tool initialization, root-wrapper shipping, and any
consent/execution-authorization UX remain SCOUT-05. The corrections doc draws
this line explicitly and correctly.

## Approval consistency / identity / rejection-path coverage (source review)

- Dispatch rejection cases (`test_tool_refuses_unapproved_identity_effects_
  and_caller_digest`, parametrized): wrong `gig_version`, foreign `gig_id`,
  foreign `capability_id`, caller-invented `inventory_sha256`, broadened
  `effects`, non-agent actor — all `ScoutToolError`.
- Pre-dispatch source mutation
  (`test_tool_refuses_changed_schema_or_extra_executable_before_publication`):
  changed schema bytes and an added executable member — both refused.
- Post-dispatch/pre-publication mutation: F1 test above.
- Fabricated publisher binding: forged-callback test above.
- Manifest-approval rejection: eight-case parametrization above.
- Idempotent replay + direct-built-in distinctness:
  `test_approved_gig_tool_dispatches_only_through_native_journal_service`
  asserts `replay.created is False` and that `create_native_record` (no
  binding, operator actor) still works and its receipt carries no
  `tool_binding`.
The rejection surface has relevant, targeted coverage. (Suite execution is the
coordinator's to verify per dispatch; not rerun here.)

---

## Blockers

**None** for accepting this correction as a scoped library seam. F1–F5 and the
forged-callback concern are addressed in source and have committed coverage.

## Remaining whole-C3 / SCOUT-05 requirements (not blockers to this accept)

1. **Supported command path.** `invoke_approved_tool_entry` /
   `dispatch_native_record_tool` have no `src/` caller. The
   integration-notes requirement to "identify the supported command/library
   paths" is met only at the library level; the shipped `python gig.py …`
   entry, its CLI wiring, and default tool/`gig.py` distribution are SCOUT-05.
2. **Package/export private-record guard** (`package_privacy.py`,
   `SCOUT-03-C3-package-*`) — separate lane, not in this review's scope.
3. **Run-plan / run / provider private-provenance ingress**
   (`SCOUT-03-C3-input-*`) — separate lane.
4. **Central schema inventory closure** — `SCHEMA_NAMES` registry,
   `SHA256SUMS`, `tools/verify_installed_schemas.py`, golden fixtures:
   coordinator-owned; the hash recompute above shows the three C3-relevant
   schema files are internally consistent, but full registry/verifier closure
   was not evaluated.
5. **Roadmap SCOUT-03 "Done when"** (fresh session recovers the right work;
   unselected/private records cannot leak through package/public paths) spans
   lanes beyond this seam.
6. Consent UX, execution authorization, scheduler behavior — explicitly
   deferred / separately authorized per amendment §4 and the corrections doc.

## Not reopened

The optional redesign directions floated earlier (a general capability
executor, an injectable pre-publication callback API, `$id` bump to `:2`) are
**not** reopened here. The `:2` bump is a coordinator call tied to whether
external `:1` pinning is a real constraint; the in-repo single-registry path
needs no code change.

## Evidence-accuracy statement

This re-review was **source-inspection only**. **No disposable probe, script,
or test run was executed.** No files were modified. No provider calls or
commits were made. Fixtures, user `.gigai/` data, other evidence reports, and
other workers' worktrees were left untouched.

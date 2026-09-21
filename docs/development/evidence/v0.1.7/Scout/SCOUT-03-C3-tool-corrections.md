# SCOUT-03 C3 tool bridge corrections

## Findings resolved

**F1 — publication-time change is now a committed regression.**
`test_tool_change_after_dispatch_validation_refuses_before_record_publication`
lets the initial source closure pass, changes the approved operation-schema
inside the native publisher's writer-locked pre-revalidation seam, and proves
that the shared publication revalidation raises `ScoutToolError`.  It compares
the Git HEAD and `records/operations/` artifact set before and after, so no
native receipt, record artifact, or operation commit is published.  The
existing same-key replay and pre-dispatch source-change tests remain present.

**F2 — the fixture has a real explicit Gig-owned entry.**  The approved
inventoried `tools/<capability_id>/record_tool.py` now imports
`gigai.scout_tool_adapter.native_record_create_operation` and exposes the
closed `build_native_record_operation(context)` entry.  A fresh Python process
calls `gigai.scout_tools.invoke_approved_tool_entry`, which first resolves the
approved source closure, explicitly loads only its selected entry, lets the
entry construct the typed `{operation_key, content}` result through the shared
adapter, and sends that result back through normal dispatch and C1 persistence.
The fixture tool path is deliberately a capability-scoped `tools/` entry, not
the future user-facing root `gig.py`; SCOUT-05 still owns template/default
distribution and root-wrapper shipping.  This is explicit invocation only:
init, inspect, approval, import, and scheduling do not execute a tool, and the
same-account loader is not represented as a sandbox or consent mechanism.

**F3/F4 — binding consistency is rejected before approval.**
`capabilities._validate_manifest_semantics` now requires tool inventories to
be sorted and unique, requires their declared canonical digest, requires the
entry to be a member, binds the entry digest to `required_digest`, and requires
the narrow C3 operation/effect pair to agree with the capability declaration.
For tools, `source_constraints.required_identity` has the precise established
mapping `Path(entry_path).name`; both manifest validation and dispatch enforce
it.  Negative tests cover every mismatch before `materialize_capability_manifest`
can produce an approval candidate.

**Forged publisher input is no longer trusted.**
`create_native_record_from_tool` no longer takes a caller revalidator.
Before any tool-bearing record publication, the shared native publisher
independently calls `scout_tools.revalidate_tool_binding_at_publication` under
the journal writer lock, re-reading the committed active pointer, capability
manifest, actual inventory and binding.  The exposed publisher regression
passes a fabricated binding without monkeypatching and proves no receipt or
new commit results.  This protects the supported publication path; it does not
claim an arbitrary Python process with filesystem access is sandboxed.

## Reader compatibility and coordinator handoff

In-tree readers use the bundled `validators.SCHEMA_NAMES` registry and call
`validate_serialized_contract`: `capabilities` validates the manifest and
`native_records` validates operation receipts on both write and read.  Therefore
the updated bundled readers accept old manifests/receipts with no optional
`tool_binding` (new-reader/old-data), while pre-C3 strict copies with
`additionalProperties: false` reject new tool-bound manifests/receipts
(old-reader/new-data).  No claim is made that those old copies are compatible;
they must refresh the bundled schema.  The coordinator owns the registry,
SHA256SUMS, verifier and golden-fixture updates, and this correction does not
introduce a new schema version or schema family.

## Focused verification

- `.venv/bin/pytest -q tests/test_scout03_c3_tools.py` — 19 passed in 39.89s.
- `ruff check src/gigai/scout_tools.py src/gigai/scout_tool_adapter.py
  src/gigai/native_records.py src/gigai/capabilities.py
  tests/test_scout03_c3_tools.py` — passed.
- `python -m compileall -q src/gigai/scout_tools.py
  src/gigai/scout_tool_adapter.py src/gigai/native_records.py
  src/gigai/capabilities.py` — passed.

No full suite, wheel build, provider execution, real private workpad, commit,
or default template/init work was performed.  Remaining SCOUT-05 scope is
shipping the default `gig.py`/tool distribution and any separately authorized
consent, execution, package, or scheduler behavior.

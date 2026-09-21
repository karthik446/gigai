# SCOUT-05 approved tool CRUD implementation

## Delivered boundary

Approved Gig-owned entries can now request `record_create`, `record_update`,
and `record_archive` through the existing C3/C1 authority path.  The bridge
resolves the exact wrapper-owned Gig, active approved version, committed
capability manifest, capability entry, inventoried source bytes, required
identity/digest, allowed operation, `write_workpad` effect, agent actor, and
`agent_supplied` origin before dispatch; the native publisher independently
resolves the same binding again while holding the journal writer lock.

`record_update` and `record_archive` carry an exact record ID and expected
parent revision.  The publisher looks up the named committed parent to form a
stable intent, finds an identical receipt before its terminal/CAS check, then
requires that parent to still be current before appending.  Thus same-key
replay returns the original receipt, a different stale key refuses without a
new revision, and archive appends an `archived` tombstone while retaining every
prior revision and sidecar.  Direct built-in native operations remain separate;
the copied wrapper does not call them directly.

The copied `gig.py` now exposes explicit `update` and `archive` command forms.
They still require a capability whose approved binding admits that specific
operation; a create-only capability returns a typed tool-binding refusal.
Wrapper path authentication continues to select its exact registered Gig,
never active selection or caller-supplied Gig identity.

## Files

* `src/gigai/scout_tool_adapter.py` — typed create/update/archive operation
  constructors only; no I/O or authority work.
* `src/gigai/scout_tools.py` — operation-specific request shapes, approved
  entry execution, sealed binding, and dispatch to the tool-only publishers.
* `src/gigai/native_records.py` — tool-only update/archive publishers and
  replay-before-CAS append behavior; archive remains non-destructive.
* `src/gigai/capabilities.py` — canonical nonempty subset validation for the
  three admitted native CRUD operations.
* `src/gigai/data/scout/gig.py` — per-Gig authenticated CLI commands.
* `src/gigai/schemas/scout-operation-receipt.schema.json` — tool binding now
  permits the three exact operation labels; no object is opened.
* `tests/test_scout05_tool_crud.py` plus narrow intentional updates to the
  existing C3/scaffold fixtures.

## Focused evidence

Executed 2026-09-09 with disposable synthetic workpads only:

* `tests/test_scout05_tool_crud.py::test_fresh_wrapper_executes_approved_create_update_archive_with_cas_and_replay`
  — 1 passed in 21.56s.
* `tests/test_scout05_tool_crud.py::test_tool_crud_refuses_stale_mutated_and_malformed_operations_before_publication`
  — 1 passed in 8.88s.
* `tests/test_scout05_tool_crud.py::test_wrapper_crud_is_bound_to_its_own_gig_not_active_or_foreign_gig`
  — 1 passed in 5.00s.
* Affected C3 manifest/replay set — 9 passed in 7.26s; affected scaffold
  create-only CRUD-gate test — 1 passed in 3.59s.
* Scoped Ruff passed for all modified bridge, wrapper, and focused test files.
  Scoped `py_compile` passed for those Python files.

Negative coverage proves stale parent rejection, malformed operation refusal,
source/schema mutation between dispatch and publication with unchanged HEAD
and operation artifacts, cross-Gig wrapper isolation, exact same-key update
and archive replay, and preserved pre-archive revision visibility.

## Schema handoff

`capability-manifest.schema.json` was not changed in this slice; its existing
operation enum already admitted the three labels.  The changed
`scout-operation-receipt.schema.json` SHA-256 is
`3506617a28ca0211748fdf2937cbbb6eb70201a681472ef9316d8a5bdea0d8a6`;
the unchanged capability manifest digest is
`b2227c05feee824c39e4cea12aa3eb10fad0b50ebe8c865dca354ddf80682493`.
Coordinator must refresh only the central hash inventory/verifier and any
golden fixture that embeds the strict receipt binding; no schema registry name
or new schema family is required.

## Limits

This does not claim whole SCOUT-05 completion.  Default/proposal integration,
root source inventory approval binding, copied-source installation proof, and
the remaining Scout domain workflows still need their own evidence.  The
same-account Python loader is not a sandbox; this implementation neither
creates operator consent nor represents any agent/tool declaration as an
application-submitted event.

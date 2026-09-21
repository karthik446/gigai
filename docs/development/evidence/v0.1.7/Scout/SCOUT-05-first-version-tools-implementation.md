# SCOUT-05 first-version tool receipt compatibility

**Status:** bounded implementation evidence, 2026-09-09. This closes the
first-version receipt schema mismatch only; it does not make Scout a
release-eligible default or ship a generally approved executable tool bundle.

## Change

`scout-operation-receipt:1` now permits `tool_binding.gig_version: 1` while
retaining the positive-integer bound (`minimum: 1`). A fresh v2-layout Gig's
first explicitly approved Graph Set is correctly numeric version 1, so a
receipt for a separately approved, exact-version capability is now serializable
without weakening the active-version, manifest, inventory, effect, actor,
operation-key, CAS, or publication-lock checks.

Final schema digest for coordinator-owned registration and inventory refresh:

```text
2c45e5d2954abaf625728cb59013f969acdfe31511d0c29b49c80b9026126872  scout-operation-receipt.schema.json
```

## Public path exercised

`tests/test_scout05_first_version_tools.py` provisions a disposable workspace,
calls `initialize_defaults(inventory=scout_candidate_inventory())`, and uses
the actual copied `<Gig>/gig.py`. It materializes a closed, synthetic
inventory-only CRUD entry under that Gig, records it through the existing
capability-manifest service, then calls the separate
`approve_offline(..., gig_id=<reserved-gig>)` service for the pending first
Graph Set. That explicit approval returns version 1 and leaves the project
active selection unset; the copied wrapper then performs `create`, `update`,
matching-key `update` replay, and `archive` through the shared validated
publisher.

The synthetic entry is deliberately a test-only, approved inventory fixture;
the copied `gig.py` is the real bundled candidate wrapper. This proves the
first-version receipt bridge and wrapper-to-authority path, but does not claim
that the candidate package has an approved shipped executable CRUD entry.

## Refusals covered

- Before separate approval, wrapper mutation returns
  `tool_authority_unavailable` and creates no operation receipt.
- After approval, an unknown capability returns `tool_capability_refused` and
  creates no receipt.
- A direct foreign-Gig request is refused before publication, and a changed
  approved tool schema returns `tool_inventory_changed`, both with the receipt
  set unchanged.
- Actual version-1 create/update/archive receipts validate against the schema;
  otherwise identical receipts with version `0` or `-1` do not validate.

## Verification

| Command | Result |
| --- | --- |
| `.venv/bin/pytest -q tests/test_scout05_first_version_tools.py` | `5 passed in 23.98s` |
| `.venv/bin/pytest -q tests/test_scout05_tool_crud.py::test_fresh_wrapper_executes_approved_create_update_archive_with_cas_and_replay` | `1 passed in 22.39s` |
| `uv run ruff check tests/test_scout05_first_version_tools.py src/gigai/scout_tools.py src/gigai/scout_tool_adapter.py src/gigai/native_records.py` | passed |
| `.venv/bin/python -m py_compile tests/test_scout05_first_version_tools.py src/gigai/scout_tools.py src/gigai/scout_tool_adapter.py src/gigai/native_records.py` | passed |

No provider/network call, private user workpad, real user initialization or
approval, active-selection change, Git commit, or release promotion occurred.

## Remaining gates and coordinator handoff

Coordinator must register the changed schema digest in the central schema
inventory, SHA256SUMS, installed-schema verifier, and golden fixtures; this
lane intentionally did not edit those files. Remaining SCOUT-05 work includes
an approved executable source inventory in the distributed candidate, installed
package/CLI proof, source/proposal adoption integration, and the Scout domain
workflows needed before release-eligible default promotion.

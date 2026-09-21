# SCOUT-05 init identity corrections

**Date:** 2026-09-09  
**Scope:** bounded recovery and upstream-template corrections to default init.

## Delivered behavior

- Default-instance identity is now recovered from each authenticated committed
  `template-instance-binding.json` reachable through the project’s validated
  workpad locators.  `template_instances` remains only a cache: after its rows
  are lost, init rebuilds the exact cache row from the binding and retains the
  original Gig ID, binding commit, package provenance, journal history, and
  workpad contents.
- Recovery scans all project workpad locators rather than treating a missing
  template cache row as permission to mint an ID.  A missing committed binding,
  a cached row without authority, a reserved ID that differs from a pending
  binding, a malformed binding, or two bindings for one template returns typed
  `template_reconciliation_required`; no replacement workpad or receipt is
  published.
- Bound intents are validated for exact schema, owner pin, project, inventory
  digest, unique template keys, and unique canonical reserved Gig IDs before
  reuse or replacement by a later completed batch.  A pending batch still
  requires the exact pinned inventory and reserved identity.
- Historical cache validation now compares cache columns to the committed
  binding’s original package ID/digest, not the newly supplied catalog entry.
  When an incoming template has changed definition/source bytes, init returns
  the same instance as `update_available` with a truthful non-adoption action;
  it does not overwrite source, data, history, active selection, approval, or
  package provenance.  Returning to the original inventory is an ordinary
  completed init, not a pending-batch conflict, and a newly added default gets
  its own new instance.

## Focused evidence

| Command | Result |
| --- | --- |
| `.venv/bin/pytest -q tests/test_scout05_init.py tests/test_scout05_init_corrections.py tests/test_scout05_init_recovery.py` | 18 passed in 22.44s. |
| `uv run ruff check src/gigai/default_init.py tests/test_scout05_init.py tests/test_scout05_init_corrections.py tests/test_scout05_init_recovery.py` | Passed. |
| `.venv/bin/python -m py_compile` for the default-init module and three focused test modules | Passed. |

`tests/test_scout05_init_recovery.py` covers completed cache loss, interrupted
batch cache loss, duplicate committed bindings, corrupt cache refusal, source
upgrade/retry, return to the original inventory, and extension with a new
default.  Fixtures are synthetic disposable Git workpads; no real private
home, provider, tool execution, approval, activation, or commit was used.

## Limits and handoff

This change uses existing validated registry workpad locators to find committed
bindings; it does not scan arbitrary filesystem directories or reconstruct a
missing workpad/source from IDs.  It reports available upstream template
updates only—source copying, a prepared proposal, explicit update adoption,
root tool source approval binding, central schema/resource inventory work, and
the remaining SCOUT-05 through SCOUT-10 release gates remain separate work.

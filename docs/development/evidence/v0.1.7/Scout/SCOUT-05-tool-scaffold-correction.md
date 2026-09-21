# SCOUT-05 copied-source identity-boundary correction

## Finding and correction

The first scaffold passed `home_root` and `requested_target` without a Gig
identity. The shared resolver consequently fell back to `active_gig_id`, so a
copied wrapper from Gig A could operate on active Gig B in the same project;
this was an ownership violation.

`src/gigai/data/scout/gig.py` now authenticates its own location before any
native/tool service call:

1. The script must be a regular, non-symlink file named `gig.py`, and its
   parent must resolve without a symlink or relocation.
2. The exact parent path must match exactly one strict registered workpad row;
   no directory basename, display username, caller JSON, or active selection
   is used to derive the identity.
3. The registered row's `gig_id` is passed explicitly to
   `resolve_workpad`, which validates the target's registered project/Gig
   binding without selecting or changing an active Gig. The resolved workpad
   must equal the wrapper parent both canonically and by filesystem identity.
4. The authenticated `gig_id` is passed to every list/read/context and
   approved-create service call. Foreign home/project/target, unregistered or
   relocated wrappers, symlinked wrappers, and ambiguous registry ownership
   fail closed with a typed `wrapper_ownership_refused` diagnostic.

This preserves the existing shared native/C3 authority and does not edit
registry, service, template, package, schema, or initialization code. Help,
module import, and the explicit update/archive pending-CRUD refusals return
before ownership resolution and cannot mutate state; approved create remains
an explicit `agent_supplied` operation and cannot masquerade as operator
consent.

## Regression coverage

`tests/test_scout05_tool_scaffold.py` now uses fresh Python processes and
disposable fixtures to prove:

- two registered Gigs in one project remain independent while B is active;
  A's list/create paths address A, B's list/read paths address B, A and B
  receipts carry their exact respective Gig IDs, and active binding remains B;
- two registered Gigs in one project work from A with no active Gig selected;
  A cannot see B's native record, B reads its own record, and no active
  binding is created as a side effect;
- copied wrappers relocated outside the registered workpad and wrappers
  reached through symlinks fail closed with exit status 2 and the typed
  ownership refusal.

The prior positive inert-copy, no-mutation help/import/read-only, refusal,
approved create/list/read/context, and update/archive regression coverage is
retained. No actual private records, user `.gigai` state, provider, HTTP, or
release state is used.

## Verification and remaining gates

The final focused results are:

```text
.venv/bin/pytest -q tests/test_scout05_tool_scaffold.py
# 8 passed in 32.48s

ruff check src/gigai/data/scout/gig.py tests/test_scout05_tool_scaffold.py
# All checks passed!
```

Ruff and all eight scaffold regressions pass after the coordinator-owned
`src/gigai/workpad.py` syntax issue was repaired; that unrelated file remained
outside this lane and was preserved. No full repository suite, wheel build,
provider, Qwen, commit, or release action was run.

The coordinator/Terra still owns the separate source-inventory/init gate:
add `gig.py` to `scout_template.py::scout_source_files`, copy it into each
Gig's editable root, and perform ordinary package/hash verification. The root
wrapper is not pinned execution authority until that integration is accepted,
and update/archive remain refused until the full-CRUD agent-tool bridge is
explicitly integrated; this correction does not bypass either gate.

# SCOUT-05 copied-source tool scaffold implementation

## Delivered lane

`src/gigai/data/scout/gig.py` is a small, private-Gig-owned command wrapper
intended to be copied into each newly provisioned Scout Gig. It contains no
Scout domain implementation, SQL, journal/receipt writes, HTTP API, provider
call, or hardcoded project/Gig identity. The wrapper lazy-imports the shared
validated service only for an operation, so source inspection, module import,
and `--help` do not load or execute a domain tool or mutate state.

The read-only commands are:

- `list`: shared `list_native_records`
- `read RECORD_ID`: shared `read_native_record`
- `context`: shared `native_context`

The explicit mutation command is:

- `create --capability-id ... --actor-id ... --operation-key ... --input-json ...`:
  shared `invoke_approved_tool_entry`, which derives the selected Gig context
  from the bound target and active selection, requires `{"kind":"agent"}`
  actor semantics, resolves the committed capability/inventory, and sends the
  typed result through the C3/C1 publication path. The wrapper does not accept
  a caller-supplied Gig ID or digest as authority, and never represents agent
  invocation as direct operator consent.

Unapproved or unavailable capability execution emits a typed refusal and a
`proposal_required` next action. `update` and `archive` are deliberately
explicit typed refusals with an SCOUT-05 integration-gate next action: the
current accepted bridge only admits the bounded `record_create` tool effect.
Full CRUD remains a release requirement; this lane does not claim that
create/list/read/context is full CRUD and does not bypass the bridge through
built-in operator APIs.

## Fresh-process evidence

`tests/test_scout05_tool_scaffold.py` copies the source into disposable
workpads and invokes it in separate Python processes. It proves:

- two copied wrappers diverge independently after one copy is edited;
- import/help/list/context preserve Git HEAD and working-tree status;
- an unapproved create returns `tool_capability_refused` plus a typed proposal
  next action and publishes no operation;
- an approved create, followed by fresh-process `list`, `read`, and `context`,
  returns the created record through the shared service with
  `origin=agent_supplied`, without HTTP;
- update/archive remain visibly refused at the current bridge boundary.

All fixtures are disposable and synthetic. No actual private records,
resumes, preferences, secrets, user `.gigai` state, providers, or hosted
execution are used.

## Required source-inventory integration gate

This lane intentionally does not edit `scout_template.py`, catalog/package
code, central schema/hash inventories, or initialization. The coordinator/Terra
must make the separately reviewed integration patch before default Scout
provisioning can ship this source:

1. Add `gig.py` to the path tuple in
   `src/gigai/scout_template.py::scout_source_files`, preserving its
   private-data-free deterministic inventory and generated
   `definition/scout-source.json` entry.
2. If future copied tool modules are added, add only their exact portable
   `tools/<name>/...` paths to that same source inventory; this lane adds none.
3. Regenerate/verify the ordinary package inventory and central schema/hash
   evidence through the coordinator-owned checks, without treating the root
   wrapper as pinned execution authority.
4. At initialization, copy the source bytes into each Gig's editable root and
   register any executable capability through the normal separate
   proposal/approval path. The wrapper's `create` command is not a self-
   approval or an authority claim; changed copied source must require a new
   approved inventory/version before supported mutation.

The root wrapper is therefore a discoverable scaffold, not a release-ready
default until that inventory/init gate is complete. Its current approved
create path requires an existing capability-scoped tool entry and an active
approved manifest; absent those, the refusal/next-action output is the
correct behavior.

## Focused verification

```text
.venv/bin/pytest -q tests/test_scout05_tool_scaffold.py
# 5 passed in 19.67s

ruff check src/gigai/data/scout/gig.py tests/test_scout05_tool_scaffold.py
# All checks passed!
```

Exact focused results: the five SCOUT-05 tests passed in 19.67 seconds and
the scoped Ruff check passed. No full repository suite, wheel build, commit,
provider, Qwen pilot, or release action is part of this implementation.

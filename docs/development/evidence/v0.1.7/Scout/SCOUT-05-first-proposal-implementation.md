# SCOUT-05 first Graph Set proposal implementation

Date: 2026-09-09. This is a bounded implementation handoff, not evidence that
all SCOUT-05 defaults are release-ready.

## Delivered surface

`lifecycle.propose_first_graph_set_offline` stages the first pending
multi-graph proposal for an explicitly selected, provisioned v2 workpad. It
requires a closed `schema_version: "1.0"` source definition containing the
resolved `gig_id`, name, commission, canonical Markdown Gig document, strict
creation metadata, at least one complete Graph Set descriptor, and shared
policy. Each declared local member is regular-file, non-redirected and
digest/size checked; the copied Goal Graphs, goal contracts and attached
contracts are then validated with the existing Graph Set validator before any
write.

The published `gig-proposal-v2` has `kind: "create"`, null
`base_gig_version` and null `parent_proposal_id`. Its source copies live below
`manifests/graph-sets/<proposal-id>/` and a journal-authenticated
`first-proposal-inputs.json` seals the definition digest and all source member
identities. Publication and pending-proposal lookup occur under the existing
journal writer lock: the source is re-read in that lock, exact retry recovers
the original proposal ID, changed inputs refuse, and an interrupted return
after publication cannot create a second proposal.

`gigai graph-set propose --first --gig GIG --definition PATH` exposes this
route without approving or selecting a Gig. `approve_offline` and `gigai
approve PROPOSAL --gig GIG` now accept an optional exact Gig selector; this
permits direct confirmation on a no-active or different-active target while
leaving the active selection unchanged. The existing
`propose_graph_set_offline` amendment path remains its approved-predecessor
path.

## Focused verification

Executed in a disposable fixture:

```
.venv/bin/pytest -q tests/test_scout05_first_proposal.py \
  tests/test_scout02_graph_set_flow.py::test_two_graph_propose_approve_plan_history_and_run
```

Result: `5 passed in 10.26s`.

The new cases prove:

- a v2 default-bound Gig with no active selection stages an authenticated
  pending create proposal and explicitly approves it as version 1;
- a different active Gig does not redirect explicit approval and the unrelated
  Gig stays untouched;
- a post-publication interruption followed by identical retry returns the same
  proposal and retains one journal publisher, while changed source intent
  refuses; and
- the public first-proposal and explicit-approval CLI paths work, while the
  existing approved-Gig amendment regression still passes.

Scoped lint passed for the owned lifecycle logic and new test:

```
ruff check src/gigai/lifecycle.py tests/test_scout05_first_proposal.py
```

`python -m py_compile src/gigai/lifecycle.py src/gigai/cli.py
tests/test_scout05_first_proposal.py` also passed. A whole-file `cli.py` Ruff
run remains blocked by nine pre-existing errors in independently owned private
record CLI code (an unused import and semicolon-style E702 sites); this slice
does not suppress or modify those unrelated lines.

## Schema and compatibility disposition

No schema resource changed. The existing strict
`gig-proposal-v2.schema.json` already represents a `kind:create` proposal with
null predecessor/base references, and
`active-gig-version-v2.schema.json` permits the resulting first approved
numeric version `1`. The existing `scout-operation-receipt.schema.json`
requires `gig_version >= 2`; that remains a coordinator integration obligation
for later tool-receipt work, not a reason to forge first-Gig history or loosen
the receipt schema here.

## Remaining gates

This slice does not materialize editable root software, wire init batches to
the service, create an actual Scout Graph Set source body, bind an approved
tool manifest, or promote any default to release eligibility. It does not
execute copied Python, call a provider, grant a capability, approve during
initialization, or change active selection.

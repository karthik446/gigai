# SCOUT-02 implementation record

**Status:** implemented locally and awaiting independent coordinator review.
This record is fixture/offline proof, not provider dogfood or an activation
claim. The existing G43.1 managed-review path remains v1 and is not
reclassified by this work.

## Delivered foundation

The additive multi-graph authority path is:

```text
approved v2 proposal -> immutable Graph Set at its approval tag
  -> descriptor selector/alias -> sealed selection record -> v2 Run Plan
  -> fresh direct-confirmed Run -> v2 Run manifest
```

`gigai run-plan create --graph SELECTOR` accepts an approved descriptor's
semantic selector or alias.  A one-member Graph Set creates a deterministic
`only_member_default` record; a multi-member Set without `--graph` returns
`graph_selection_required`.  `--selection-record ID` accepts a previously
journaled, digest-pinned G44 selection under `graph-selections/ID.json`; the
validator does not route or infer intent.

The Graph Set is a strict `gig-graph-set:1` resource.  It records one or more
descriptors, their exact Goal Graph and contract references, and shared policy
ceilings.  Validation rejects collisions, duplicate Goal-Graph bytes, unsafe
or changed references, invalid Goal Graphs, and descriptor effects,
capabilities, provider allowlists, or budgets that widen the Gig ceiling.
Before a selected Run can allocate its ID, the runtime rereads every Graph Set
reference from the approval tag, validates the selected Goal Graph, rereads all
sealed Plan sources without symlinks or traversal, validates the selection
record against the exact approved version/digest, and retains G43 direct
`--confirm` Run consent.

## Source and schemas

| Area | Files |
| --- | --- |
| Authority and selection semantics | `src/gigai/graph_set.py`, `src/gigai/run.py` |
| v2 proposal approval | `src/gigai/lifecycle.py`, `src/gigai/journal.py` |
| Plan sealing and CLI | `src/gigai/run_plan.py`, `src/gigai/cli.py` |
| Central ID namespace | `src/gigai/canonical.py` |
| Additive resources | `gig-graph-set`, `graph-selection-record`, `graph-selection-record-v2`, `gig-proposal-v2`, `active-gig-version-v2`, `run-plan-v2`, and `run-manifest-v2` schemas |
| Packaged inventory | `src/gigai/validators.py`, `src/gigai/schemas/SHA256SUMS`, `tools/verify_installed_schemas.py` |

`graph-selection-record-v2` is intentionally separate from v1.  It adds the
`agent_explicit` provenance form, requiring a bounded agent actor and an exact
invocation artifact.  It is selection evidence only: it is neither operator
approval nor managed-provider Run consent.

## Verification recorded by the implementation owner

```text
.venv/bin/python -m compileall -q src/gigai
.venv/bin/pytest -q tests/test_g43_run_plan.py -x
.venv/bin/python tools/verify_installed_schemas.py
```

This earlier focused baseline was superseded by the complete fixture flow and
focused regression command recorded below. Independent coordinator review and
the separately scoped G43.1 provider suite remain external verification gates.

## Completed SCOUT-02 acceptance behavior

`gigai graph-set propose --definition FILE --gig GIG` is the narrow public
staging surface. It accepts an existing approved Gig only, reads a local
definition as untrusted transport input, rejects symlinks/traversal/digest
changes/foreign Gig members/unsupported contract forms, rewrites every Goal
Graph contract and descriptor attachment below the workpad, and journals all
of them plus the pending v2 proposal under the existing writer lock in one
`gig_graph_set_proposed` transition. It does not approve, mutate a private
Gig outside its resolved workpad, or create a Run; ordinary explicit
`gigai approve PROPOSAL` creates the next Gig version.

The Graph Set validator now checks actual selected Goal-Graph effects,
capabilities, and budgets against descriptor ceilings; every attachment is
checked as either the strict G15 review contract or one supported typed A02
contract envelope. V2 Plans normalize aliases to canonical descriptor IDs,
seal selection provenance, and snapshot the selected Goal Graph, Graph Set,
and all descriptor attachments under the Plan. A later approved version does
not alter those Plan bytes or sources; redeeming a Plan without `--version`
uses its sealed version rather than the current active graph. Selection and
all complete Plan/Run handoff checks occur before Run allocation, with direct
operator `--confirm` still separate from selection evidence.

`tests/test_scout02_graph_set_flow.py` exercises two independently selectable
structural graphs, missing-selection refusal, alias normalization, v2 then v3
approval, byte-preserved v2 Plan readback, and a confirmed offline Run against
v2 after v3 is active. It is structural fixture proof only: no provider,
network, research, or domain workflow execution was claimed.

## Verification recorded by the implementation owner

```text
.venv/bin/python -m compileall -q src/gigai
.venv/bin/pytest -q tests/test_scout02_independent.py tests/test_scout02_graph_set_flow.py tests/test_g43_run_plan.py tests/test_g13_run.py -x
# 29 passed
ruff check src/gigai/graph_set.py src/gigai/lifecycle.py src/gigai/run.py src/gigai/run_plan.py src/gigai/cli.py src/gigai/workpad.py tests/test_scout02_graph_set_flow.py
tools/verify_installed_schemas.py
```

## Deliberate limits

This slice does not implement G44 routing production, external recording,
research/broker phases, Scout CRUD/UI/tooling, providers, private operator
Gigs, or 0.1.8 migration.  It does not rewrite v1 proposals, active pointers,
Plans, Runs, journal history, or virtual v1 Graph Sets; v1 readers keep their
original single-graph path.

Post-review update (2026-09-08): listing, portability, and comparison now also
recognize v2 records (occurrence comparison delegates to comparison). See the
[correction handoff](SCOUT-02-review-corrections-implementation.md) for real v2
listing, typed portability refusal, and selected-graph comparison behavior.

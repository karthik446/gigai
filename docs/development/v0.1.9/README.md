# GigAI v0.1.9 — Backlog index

**Status:** Not decided. This directory holds research/spike documents that
inform a future v0.1.9 roadmap; it is not a plan, a schedule, or an authorized
scope. v0.1.8's own roadmap and release work are unaffected by anything here.

## Purpose

v0.1.8 shipped Scout as a Gig living under `src/gigai/scout/`, with core
(`src/gigai/*` outside `scout/`) still importing `gigai.scout.*` directly for
CLI wiring, graph-node registration and record/journal handling. The 2026-09-23
package re-org note in the v0.1.8 roadmap recorded this explicitly and named
removing that coupling as 0.2.0 item #1 (see the carried-over ledger below).
This backlog exists to research that decoupling, the repeated module shapes
inside Scout itself, one new gig-shaped idea (interview prep), and a test-suite
diet — all as spikes an operator can review, not as committed work.

## Architecture rule (operator's, non-negotiable)

> GigAI is a platform; a Gig is a self-contained package built on the
> platform. Scout is a Gig (later: trader, shopper, not now). A gig depends
> on and imports gigai core; core never imports any gig. Core provides
> registration and discovery points; gigs plug in.

Every spike in this directory treats this rule as a constraint on its
proposals, not a topic open for debate.

## Spikes

- [Spikes README](spikes/README.md) — ticket-format convention and index.
- [S16 — Core/gig decoupling](spikes/S16-core-gig-decoupling.md): inventories
  every core→`gigai.scout` coupling site and proposes a registration seam
  (Gig protocol/manifest, entry points or a bundled-gig list, enforcement
  test) so core can stop importing a specific gig.
- [S17 — Gig module structure (classes)](spikes/S17-gig-module-structure-classes.md):
  inventories Scout's repeated module families (`documents*`, `interview*`,
  `proposal*`, `report*`, `research*`, `tailor*`, …) and proposes a
  class-based structure for the duplicated validate → record → journal →
  read-back pattern, distinguishing core base classes from Scout-specific
  subclasses.
- [S18 — Scout interview-prep goal graphs](spikes/S18-scout-interview-prep-graphs.md):
  captures the operator's idea for a Scout workflow that researches a role
  and a company's interview process, predicts likely questions with typed
  probabilities (per the Jev/TypeSafe research), and preps the candidate —
  used by an agent (Claude, Codex, or other) as a tool. Research only; not a
  decision to build.
- [S19 — Test-suite diet](spikes/S19-test-suite-diet.md): measures the
  159-file, ~1,200-test, 47%-heavy (subprocess/git/server/process) suite,
  finds the `pyproject.toml` unit/integration/CLI/installed marker
  vocabulary exists but is applied to almost no files, and proposes a new
  `make unit-tests` target plus a phased marker-migration and
  overlap-reduction plan. Requested as a 4th spike mid-dispatch on
  2026-09-23.

## Carried-over 0.2.0 generalization ledger

This table is copied verbatim from
[`docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md`](../v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md#0.2.0-generalization-ledger-record-only-do-not-build)
("0.2.0 generalization ledger (record only; do not build)"), recorded there
during v0.1.8 wave planning. It is carried here because it lists exactly the
seams S16 and S17 now research in depth; the source table remains the v0.1.8
roadmap's own record and is not altered by this copy.

| Seam | 0.1.8 Scout form | What gig #2 needs |
| --- | --- | --- |
| Core → Scout coupling | Core modules (`run.py`, `cli.py`, `external_recording.py`, `application_events.py`, `capability_review.py`, `capability_successor.py`, `default_init.py`, `native_records.py`, `portability.py`, `private_transfer.py`) import `gigai.scout.*` directly for the CLI surface, registry/plugin discovery, graph nodes, and record types | Decouple core from `gigai.scout`: a generic registry/plugin-discovery seam so core never names a specific Gig's package |
| UI consent source | `direct_local_ui_confirm` hardcoded to the find-jobs scope fields (sources, queries, roles, cap, target) | A generic consent-scope schema per Gig, not one fixed field set |
| Network effects | Real enum values `network_read`/`credential_use`, admitted only for this sealed graph's registered capabilities | A generic effect-naming convention any Gig's graph can declare, without a per-gig scheduler carve-out |
| Node registry | `graph_node_registry.py` admits only `scout.find_jobs.*` capabilities for this one sealed graph (D11) | A generic registry any gig's graph can register real callables into, not a Scout-only carve-out in the scheduler |
| Present API + UI shell | `scout_present_api.py`, `ui/` hardcoded to one payload/matrix shape | A generic present contract + pluggable UI panel per Gig |
| Per-gig config file | `<target_root>/find-jobs.json`, one fixed schema | A generic config-file convention keyed by Gig id/version |
| Graph binding | `find-jobs:functional:1`, one callable per goal, no branching | Already generic in the scheduler; confirm no Scout-only assumption leaks in before gig #2 |
| Contracts module split | `scout_find_jobs_contracts.py` mixes generic `NodeContext` with Scout-specific DTOs (`AcquireInput`, `WatchlistEntry`) | Split generic node-context types from per-Gig DTOs so gig #2 doesn't import Scout types |

Note: the ledger's file names predate the 2026-09-23 package re-org (e.g.
`scout_present_api.py` is now `src/gigai/scout/find_jobs/present_api.py`,
`scout_find_jobs_contracts.py` is now `src/gigai/scout/find_jobs/contracts.py`).
S16/S17 use current paths; this table is left as originally recorded in the
v0.1.8 roadmap for traceability.

## Non-claims

- No v0.1.9 roadmap, release scope, or implementation is authorized by this
  directory.
- No code, schema, or test changes were made while writing these documents.
- The architecture rule above is the operator's stated decision; the specific
  registration mechanism, module-class design, and interview-prep graph shape
  in the linked spikes are proposals for review, not decisions.

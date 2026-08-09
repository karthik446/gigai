# Phase 3 Development Goals

This directory is the canonical implementation graph for V14 Phase 3:
read-only and workpad-only Goal Graph execution. Phase 2 remains the separate
deliberative-`create` phase described in the V14 plan; a Run goal must not
silently stand in for it.

| Goal | Outcome | Depends on | Initial state |
|---|---|---|---|
| [G13](G13-sealed-deterministic-run-launch.md) | Sealed, deterministic Run launch | G10 | Complete |
| [G14](G14-sequential-goal-graph-scheduler.md) | Sequential Goal Graph scheduler | G13 | Complete |
| [G15](G15-reference-bundles-and-evaluator-substrate.md) | Reference bundles and evaluator substrate | G14 | Complete |
| [G16](G16-first-review-loop-gig.md) | First Review Loop Gig | G15 | Complete |
| [G17](G17-proposal-capability-inspection.md) | Proposal-time capability inspection and installation review | G15 | Approved / Ready |
| [G18](G18-provider-comparison-and-model-handoff.md) | Provider comparison and model handoff | S18-01..S18-05, S22-01, G16, G17 | Proposed for review |

“Ready” records the initial dependency state; it is not a live tracker. A
completed Phase 3 goal writes durable evidence under
`docs/development/evidence/phase-3/GNN/` and lands as one reviewable change
set.

Phase 3 goals use the already packaged Run schemas and must not change their
meaning by inference. A goal stops for an explicit contract amendment if its
implementation needs a schema, state transition, or authority rule that the
published contract does not define.

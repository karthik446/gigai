# G22 Acceptance Ledger

- Status: Complete; all criteria are accepted and reconciled by the completion audit
- Goal: [G22 deliberative create](../../../../goals/phase-2/G22-deliberative-create-and-proposal-interview.md)
- Activated from: `6729b88`

This ledger keeps G22 evidence and commits aligned. A criterion cluster is
accepted only after its focused tests/evidence pass and the change is committed
as its own reviewable change set. A later cluster may depend on an earlier
cluster's commit, but does not rewrite an accepted cluster silently.

| Criteria | Cluster | Status | Evidence commit |
|---|---|---|---|
| 1 | Dependency/contract gate and implementation baseline | Accepted; dependency and 22-resource baseline are citable | `6729b88`, `91c3ecf` |
| 3 | Short-lived loopback session, token boundary, and bounded lifetime | Accepted; 13 focused tests pass | `303f058` |
| 2 | CLI entry point and deterministic terminal result | Accepted; source and installed black-box flows reach approved without a Run | `aa9ed9a`, `b016591` |
| 4 | Exact selected-reference and byte/digest boundary | Accepted; selected-only input, digest recovery, and symlink negatives pass | `d9418e7` |
| 5–6 | Typed questions, answers, clarification rounds, and blocking | Accepted; 8 focused tests pass | `d22b577` |
| 7 | G18 model-port/provider boundary and deterministic failures | Accepted; factory/port path and explicit network denial pass | `b937291`, `d9418e7`, `fab3dcd` |
| 8 | SQLite trace, recovery, stale-event rejection, and authority split | Accepted; trace guards, workpad recovery, and process interruption pass | `8e4793b`, `571a442` |
| 9 | Explicit durable interview revisions and parentage | Accepted; 15 focused tests pass | `3580c31` |
| 10 | Boundary choices and operator-only approval edge | Accepted; 14 focused tests pass | `6f7dac9` |
| 11 | Proposal approval idempotence and terminal handoff | Accepted; repeat approval is terminal and journal-idempotent | `26a90b0` |
| 12 | Interruption, security, and non-effect fixtures | Accepted; loopback, malformed, expiry, symlink, digest, network, and process-kill negatives pass | `fab3dcd`, `571a442` |
| 13 | S22-01 question-quality corpus and reporting | Accepted; four named cases pass | `238bdb7` |
| 14 | Installed replay, manifests, completion audit, terminal handoff | Accepted by this closeout audit and terminal handoff | Closeout commit |

The ledger never substitutes for the completion audit. At closeout, every row
must link to durable evidence, the completion audit must reconcile the full
goal contract, and the terminal handoff must identify G19 as the next
authorized consumer without granting G19 any authority early.

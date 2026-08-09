# G22 Acceptance Ledger

- Status: Active; criteria are being accepted incrementally
- Goal: [G22 deliberative create](../../../../goals/phase-2/G22-deliberative-create-and-proposal-interview.md)
- Activated from: `6729b88`

This ledger keeps G22 evidence and commits aligned. A criterion cluster is
accepted only after its focused tests/evidence pass and the change is committed
as its own reviewable change set. A later cluster may depend on an earlier
cluster's commit, but does not rewrite an accepted cluster silently.

| Criteria | Cluster | Status | Evidence commit |
|---|---|---|---|
| 1 | Dependency/contract gate and implementation baseline | Accepted by `6729b88`; runtime evidence pending | `6729b88` |
| 3 | Short-lived loopback session, token boundary, and bounded lifetime | Accepted; 13 focused tests pass | `303f058` |
| 2 | CLI entry point and deterministic terminal result | Pending; approval integration remains | — |
| 4 | Exact selected-reference and byte/digest boundary | Pending | — |
| 5–6 | Typed questions, answers, clarification rounds, and blocking | Accepted; 8 focused tests pass | `d22b577` |
| 7 | G18 model-port/provider boundary and deterministic failures | Pending | — |
| 8 | SQLite trace, recovery, stale-event rejection, and authority split | Pending; recovery/trace slice is committed | — |
| 9 | Explicit durable interview revisions and parentage | Accepted; 15 focused tests pass | Pending commit |
| 10 | Boundary choices and operator-only approval edge | Accepted; 14 focused tests pass | `6f7dac9` |
| 11 | Proposal approval idempotence and terminal handoff | Pending; lifecycle path is proven, duplicate-event proof remains | — |
| 12 | Interruption, security, and non-effect fixtures | Pending | — |
| 13 | S22-01 question-quality corpus and reporting | Pending | — |
| 14 | Installed replay, manifests, completion audit, terminal handoff | Pending | — |

The ledger never substitutes for the completion audit. At closeout, every row
must link to durable evidence, the completion audit must reconcile the full
goal contract, and the terminal handoff must identify G19 as the next
authorized consumer without granting G19 any authority early.

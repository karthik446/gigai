# Coordinator triage of Terra W0 review (2026-09-23 00:55)

Terra: 16 citations checked, 15 accurate, 1 too short. Verdict: fix first. **Agree.** My three reviews missed T1: I raised it as a question in Terra's spec, but it should have been a finding.

| id | Terra severity | Coordinator disposition |
| --- | --- | --- |
| T1 | blocker | **CONFIRMED** by reading `run.py:3438-3460` (only `gigai.offline`/`gigai.deterministic` executors, effects == `["write_workpad"]`), `run.py:3546-3561` (`_execute_goal` writes `gigai-offline-ok:<goal>`, never calls a node), `common.schema.json:148-157` (effect enum). The invented `network_public_acquisition`/`network_hosted_model` are invalid. **Use the existing enum values `network_read` + `credential_use` (+ `write_workpad`): no schema change.** The approach needs an operator decision (A: minimal scheduler extension vs B: bypass for M1). |
| T2 | blocker | **PARTLY AGREE.** I-0 does need exact fields, signatures and routes, but writing them twice (prose table, then code) is waste under D10. Disposition: **the I-0 code is the normative freeze.** I-0 delivers the contracts module, fixtures and a test that rejects field/type/route drift. Coordinator + Terra review I-0 before any wave-1b dispatch. The amendment keeps the semantics (D1–D10, invariants) and links to the module as authority. Wave 1b is gated on the I-0 review, not on prose. |
| T3 | major | **AGREE.** C-2 owns the server entry point (e.g. `gigai scout serve` or `python -m gigai.scout_present_api`), the fixed loopback port, lifecycle, and the Vite proxy (C-3 `vite.config.js`). The route table, including run-status polling, is frozen in I-0. |
| T4 | major | **AGREE.** M1 *is* the consented real traversal. The UI shows and redeems scope before allocation. Remove the "then obtain consent" ordering. |
| T5 | major | **AGREE, simpler fix:** one ownership authority. The roadmap DAG is authoritative for per-row ownership; the amendment's packet table becomes a pointer to it. The fixtures dir sits under I-0. The I-2 → I-3 edge is added. |
| T6 | major | **AGREE with D10.** No full `make test` per wave before M1. Focused row checks gate the waves. One full `make test` in a TEST tab after wave 3, before the M1 live click, plus the post-M1 installed lane. |
| T7 | minor | Accept: `scout_report_readers.py:315-337`. |

## Operator decision needed (T1 approach)
A (recommended): a minimal G14 extension. A small registry of bound node callables is admitted only for the sealed find-jobs graph, with `local_capability` capabilities like `scout.find_jobs.acquire`. The existing effects `network_read`/`credential_use` are allowed only for that graph. `_execute_goal` dispatches to the bound callable, with receipt/cancel handling. The Scout graph-set descriptor ceiling (`effect_policy`, `capability_requirements`, `graph_set.py:238-262`) includes them. Owner: I-2 (run.py + new registry module + the Scout descriptor file). This is the one seam every future gig needs; it goes in the 0.2.0 ledger as "generalize registry".
B: bypass for M1. The API calls acquire→assess→present directly, persisting through the existing records, with no Run/graph. Faster; the graph wiring comes later. The downside: it isn't "built as a gig", and the consent/effect path is skipped, then redone.

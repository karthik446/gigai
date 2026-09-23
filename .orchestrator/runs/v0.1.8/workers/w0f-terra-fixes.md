# w0f — Apply Terra review triage (Revision 3, operator T1 approach A)

state: done

## Files changed

- `docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md` (228 lines)
- `docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-amendment-02-find-jobs-functional.md` (270 lines)

## Operator decision applied

T1 approach A (2026-09-23, minimal G14 scheduler extension), recorded as D11
in the amendment, verbatim: "a minimal G14 extension so the scheduler runs
real nodes." No bypass of the Run/graph/consent path for M1.

## Per-finding disposition

| id | Terra severity | Disposition |
| --- | --- | --- |
| T1 | blocker | **Fixed (D11).** Confirmed the bug by reading `run.py:3438-3460` (`_validate_scheduler_policy`: admits only `gigai.offline`/`gigai.deterministic` with `effects == ["write_workpad"]`) and `run.py:3546-3561` (`_execute_goal`: writes fixed `gigai-offline-ok:<goal_id>` string, never calls a node). Confirmed the invented effect names are invalid against `common.schema.json:140-160`'s real enum (`read_target, write_workpad, write_target, network_read, external_write, credential_use`). Added new "Real node execution" section to the amendment: new `src/gigai/graph_node_registry.py` (I-2) maps `(graph_id, graph_version, goal_slug)` → callable/capability/effects; `_validate_scheduler_policy` additionally admits `scout.find_jobs.{acquire,assess,present}` for this sealed graph only, with `network_read`/`credential_use`/`write_workpad`; `_execute_goal` dispatches to the registered callable, keeping the existing target-change interrupt check (`run.py:3557`) and failing closed on an unregistered capability/undeclared effect. Confirmed I-1's descriptor ceiling needs raising by reading `graph_set.py:230-262` (subset checks on `effect_policy`/`capability_requirements`) and `scout_materialization.py:364-365,395` (current ceiling: `["write_workpad"]`/`["gigai.offline"]`). I-3 now depends on I-2 in both docs' DAG rows. Node registry added to the roadmap's 0.2.0 ledger as its own row. |
| T2 | blocker | **Fixed.** Added "Normative freeze" section: I-0's delivered code (`scout_find_jobs_contracts.py` + fixtures + `test_contracts.py`) is now stated as the normative freeze for DTO fields/types/routes, not this document's prose; added the gate "coordinator + Terra review I-0's actual output before any wave-1b dispatch" to both the amendment and the roadmap's wave-1a gate. |
| T3 | major | **Fixed.** Added "Local server entry point" to the amendment: C-2 owns `python -m gigai.scout_present_api` (not a `gigai scout serve` subcommand, since that needs `cli.py`, which no packet owns), fixed loopback port `127.0.0.1:8765`, C-3's `vite.config.js` proxies `/api`; route table named at minimum (`GET /api/config`, `POST /api/run`, `GET /api/runs/{run_id}`, `GET /api/runs/{run_id}/results`), frozen by I-0. Grounded the loopback precedent by reading `proposal_interview.py:655-656` (`InterviewHTTPServer` refuses non-`127.0.0.1`). |
| T4 | major | **Fixed.** Roadmap's Goal/DAG M1 definition rewritten: M1 **is** the already-consented real traversal — the UI shows scope and redeems `direct_local_ui_confirm` before allocation as part of M1 itself, not a separate "then obtain consent" step afterward. Rewrote the Waves table's wave 4 row accordingly and the "Present and consent" row in Frozen Choices. |
| T5 | major | **Fixed.** Replaced the amendment's "Four packets — disjoint ownership" table with a short "Ownership authority" pointer: the roadmap's "Small packet DAG" is now the sole per-row ownership authority; added the I-2 → I-3 edge in the roadmap (already had the fixtures directory under I-0 from Revision 2.1, confirmed still present in I-0's owned-files cell). |
| T6 | major | **Fixed.** Removed the "run full `make test` once in a TEST tab" gate from every wave before M1. Rewrote "Waves and gates": focused row/seam checks gate waves 0-2; one full `make test` runs once, after wave 3, before the M1 click; the installed-wheel lane is explicitly after M1. |
| T7 | minor | **Fixed.** Amendment's citation `scout_report_readers.py:315-331` → `:315-337`; verified by reading lines 315-337 directly, confirming the range covers the full `if "complete" in states / elif "failed" / elif "running"` block (ends at line 337, not 331 as before). |

## Effect-name replacement

Every mention of the invented `network_public_acquisition`/`network_hosted_model`
effect names was replaced with the real enum values `network_read` +
`credential_use` (+ `write_workpad` where applicable), in both the amendment's
prose (D-table, "Real node execution", "Shared contracts", 0.2.0-ledger-adjacent
history notes) and the roadmap (Frozen Choices, I-2's row, B-2's row, the 0.2.0
ledger's "Network effects" row, and the Risks table). No schema change — these
values were already in `common.schema.json`'s enum before this revision.

## Line-count management

Both docs grew substantially from the new "Real node execution"/"Local server
entry point"/"Normative freeze"/"Ownership authority" content. To stay under
the 270-line cap: replaced the amendment's full four-packet ownership table
with a short pointer to the roadmap (T5's own fix did this naturally);
condensed the Revision 1 and Revision 2/2.1 change notes into short paragraphs
instead of per-item bullet lists; tightened prose throughout (Resume trace,
Watchlist storage, Shared contracts) without dropping any citation. Amendment
landed at exactly 270 lines; roadmap at 228.

## Acceptance command outputs

```
$ grep -c -E 'network_public_acquisition|network_hosted_model' docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-amendment-02-find-jobs-functional.md
docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md:0
docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-amendment-02-find-jobs-functional.md:0

$ grep -c 'graph_node_registry.py' docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md
5

$ grep -n '^| I-3' docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md | grep -c 'I-2'
1

$ grep -c 'then obtain' docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md
0

$ wc -l docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-amendment-02-find-jobs-functional.md
228 roadmaps/v0.1.8-find-jobs-functional-roadmap.md
270 phase-2/evidence/P2-FREEZE-04-amendment-02-find-jobs-functional.md
```

All five acceptance checks pass with a clean 0 for the invented effect names
(not merely "except in a note" — the change-note text was reworded to avoid
the literal strings entirely, per the acceptance criterion's "prefer 0").

## READ vs EXECUTED

**READ:** `.orchestrator/reviews/terra-w0-triage.md` and `.orchestrator/reviews/terra-w0.md`
in full; both owned docs in full before editing; source grounding for every
new code claim: `src/gigai/run.py:3438-3460` (`_validate_scheduler_policy`),
`src/gigai/run.py:3546-3561` (`_execute_goal`), `src/gigai/run.py:3557`
(target-change interrupt check), `src/gigai/schemas/common.schema.json:140-165`
(effect enum), `src/gigai/schemas/goal-graph.schema.json:115-150` (executor
schema, confirming `capability` is a free-form pattern-matched string),
`src/gigai/graph_set.py:230-265` (descriptor ceiling subset checks),
`src/gigai/scout_materialization.py:364-365,395` (current ceiling values),
`src/gigai/proposal_interview.py:627-660` (`InterviewHTTPServer` loopback
guard, confirming the exact line numbers 655-656).

**EXECUTED:** read-only `grep`/`sed`, `wc -l` (repeatedly, while trimming both
docs under the line cap), and the five acceptance commands above. No git
stash/reset/clean/add/commit; no source/test/schema/UI file touched; no
network, provider, or model calls; no tests executed. The only writes were to
the two owned docs and this handoff file.

## Question for coordinator

None. All seven Terra findings (T1-T7) applied per the coordinator's triage
disposition and the operator's T1 approach-A decision; both docs internally
consistent, all five acceptance checks pass, both under the 270-line cap.

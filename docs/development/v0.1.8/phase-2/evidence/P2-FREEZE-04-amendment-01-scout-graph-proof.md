# P2-FREEZE-04 Amendment 01: The Phase 3 proof runs as a real, small Scout graph

**Recorded:** 2026-09-22. **Decision owner:** operator. **Recorded by:**
Claude (documentation only).
**Amends:** [P2-FREEZE-04 interface freeze](P2-FREEZE-04-interface-freeze.md),
[dispatch matrix](P2-FREEZE-04-dispatch-matrix.md) and
[ledger](P2-FREEZE-04-ledger.json). The ledger row
`s12_graph_traversal_exploration` is superseded for the Phase 3 proof only.
**This amends the freeze; it doesn't repeat Phase 2.** The AUD-01, AUD-02
and EVAL-03 outputs, and their Terra-reviewed correction
(`ctx_7ff479b90fc9` → `ctx_461e730dc554` → `ctx_57133c5bc2bb`), stand
unchanged.
**Authorizes:** a scope change to the roadmap's *thin scheduled vertical
proof* phase. **Doesn't authorize:** implementation, live acquisition,
model/provider execution or a release claim. Each of those still needs its
own dispatch or authorization.

## Ticket

**Problem:** The freeze kept S12 exploratory ("do not redesign framework or
block Scout"). S12 found that no Scout function runs as a graph today:

- Each selector compiles to a single-node graph with no edges.
- The graph runner's goal step writes placeholder evidence
  (`gigai-offline-ok`).
- `find-jobs`'s real output enters through external recording, which never
  consults graph edges.

See the [S12 task 1 mapping](../../spikes/evidence/S12-scout-graph-mapping.md)
and [task 2 design](../../spikes/evidence/S12-decision-edge-design.md).
Built as the roadmap describes, v0.1.8 would ship a scheduled pipeline next
to the graph model rather than on top of it.

**Decision (operator, 2026-09-22):** Build the Phase 3 proof as a **real,
small, linear Scout graph**. That matches the definition of a Gig, where
one traversal completes one function. It also lets us learn from something
useful before designing reusable composition machinery. Graph execution is
**not** deferred wholesale to v0.1.9.

**Graph:**

```text
acquire ──COMPLETE──▶ assess ──COMPLETE──▶ present
```

**Rules:**

- **Edges:** linear, `COMPLETE` edges only.
- **Assessment labels:** `matches` / `partial` / `no_match` stay inside the
  assessment artifact, and the `present` node reads them there.
- **Custom outcomes:** not needed. D2 (declaring labels beyond
  `COMPLETE`) becomes necessary only when those labels select different
  edges.
- **Branch and gating machinery:** D4 (branch-not-taken), D5 (OR-join)
  and D6 (operator-gated goals) are not needed.

## Acceptance for the first proof

All five must hold in the proof's own receipts:

1. **Acquisition is visible independently.** One bounded acquisition source
   saves postings, and they stay visible even if assessment fails.
2. **Assessment uses saved inputs.** Assessment consumes those saved
   postings plus explicitly selected local evidence.
3. **Presentation in the tracking UI.** Presentation shows results and
   questions in the simple tracking UI.
4. **One traversal owns the run.** A single graph traversal owns the run,
   with per-node status, evidence, and cost/usage where available.
5. **Resume without duplicates.** An interrupted run can resume without
   duplicating acquisition records.

**Constraints:**

- Keep the graph linear, the tools explicit and the interfaces replaceable.
- No catalog, generic graph builder, autonomous improvement workflow or
  orchestration rebuild.
- The freeze's authority rules still apply unchanged. Committed records and
  journal entries are the authority; views are derived; public visibility
  comes before inference; there is no implicit authorization.

## The essential work is real node execution

Returning an outcome isn't enough. Each node needs:

- a **callable implementation**;
- **explicit inputs and outputs**;
- **persisted evidence**;
- **defined failure and restart behavior**.

**This amendment does not size that work.** It shouldn't be called small
until Luna traces the integration. The table below lists what is already
known, from Phase 2 and S12, and the question the trace must answer for
each node.

| Node | Existing operations to reuse (from the freeze's operation vocabulary) | Inputs → outputs | Known seams (source-cited) | Trace must answer |
| --- | --- | --- | --- | --- |
| `acquire` | `acquisition.import_supplied_rows`, `acquisition.status`, `acquisition.resume` (`scout_acquisition_records.py`; `resume_public_acquisition` at `:543`) | Approved public source settings → saved posting snapshots plus a progress/failure record | The freeze records **no current connector or scheduler** (ATS/search row is `proposed`). Today's import classifies already-acquired rows and does no fetching (`scout_discovery_job.py` docstring). | Which one bounded source, and whether it needs a live-source authorization (S09 decision). How the node calls acquisition as a callable. Whether the node's effect declaration fits the scheduler policy (see the executor row below). |
| `assess` | `assessment.select_private_inputs`, `assessment.invoke_local`, `assessment.publish_result`, `assessment.record_revision` (`scout_proposals.py`, `scout_proposal_execution.py`, `scout_proposal_records.py`) | Pinned posting snapshot(s) + selected resume revision + preferences → assessment artifact (`outcome` ∈ matches/partial/no_match, shortlist, exclusions, questions) | Proposal execution today runs as its **own specialized single-goal Run** (`launch_run(proposal_execution=…)`), not as a node in a multi-goal traversal (S12 F6). There's an answer-association conflict (freeze amendment 3). | Whether assessment can run as a node inside the traversal, or needs an adapter around the existing proposal path. Local-model route and no hosted fallback (S10). The failure state when the model is stopped (case `model_stopped_after_acquisition`). |
| `present` | `view.rebuild_projection`, `view.publish_report`, `view.read_report_status` (`scout_projection.py`, `scout_report*.py`) | Assessment artifact + public acquisition rows → tracking UI rows and details, with results and questions | `_run_rows` reports a local run `succeeded` if **any** goal completes ([S12 task 4, V2](../../spikes/evidence/S12-proposed-system-behavior-changes.md), `scout_report_readers.py:329-337`). Multi-node runs need run-level status. | How per-node status, evidence and usage reach the UI (V3: `goal_sets`, `critical_path` and per-goal evidence already exist in run details). |
| Executor and runner | Graph runner `_execute_deterministic` / `_execute_goal` (`run.py`) | Sealed graph → per-goal status, evidence and journal transitions | `_execute_goal` does no domain work (placeholder evidence, `run.py:3546-3561`). `_validate_scheduler_policy` (`run.py:3438`) allows only the `gigai.offline`/`gigai.deterministic` executors and `effects == ["write_workpad"]`, which may conflict with a node that fetches or runs a local model. The actor is hard-coded to `deterministic` (`run.py:3585`), so producer identity is lost. | How a node's callable is bound to its goal, and how the effect and executor policy is amended without weakening it. How producer identity and usage are recorded per node (S12 D1 minus the custom outcome, plus V1: per-node `required_evidence`). |
| Restart | Acquisition dedup/resume; runner interrupt handling | Interrupted traversal → resumed traversal, with no duplicate acquisition records | An interrupted run is marked `interrupted` (`_mark_interrupted`, `run.py:684`). A grep found **no run-level resume function** in `run.py`. Acquisition has its own resume. | Whether "resume" means resuming the same run or starting a new run that re-enters idempotently. Either way, how acquisition dedup guarantees no duplicates (case `duplicate_posting_snapshot`). |
| Graph authoring | Scout compiler `_compiled_snapshot` (`scout_materialization.py`) | Selector → sealed graph | It emits one goal per selector with `edges: []` and `outcomes: ["COMPLETE"]` (S15). | How a three-node `find-jobs` graph is declared explicitly, without a generic builder. |

"Read" findings above come from source; nothing was executed for this
amendment. S12 D1's *custom-outcome* part is not required for this proof.
Its execution-contract parts still are: a callable implementation, producer
identity, evidence, and failure. The trace decides where they live.

## Ownership changes to the dispatch matrix

- **Integration** gains the graph-runner seam: binding node callables,
  amending executor/effect policy, and restart semantics in `run.py`, plus
  the explicit three-node graph declaration in `scout_materialization.py`.
  Integration still doesn't absorb packet behavior.
- **Packets A, B and C** keep their boundaries. Each also exposes its node's
  operation as a callable with the declared inputs and outputs:
  - A: `acquire`
  - B: `assess`
  - C: `present`, plus the run-level status reader (V2) and per-node
    status and evidence display (V3)
- **First step:** Luna traces the integration (the "trace must answer"
  column) **before** the proof is sized or dispatched. Terra reviews the
  trace. Root keeps the final gate.

## What is unchanged

- Release is still blocked by the exact-tag G03 failure (run
  `35660274375`) and by absent publication, UAT, installed, live and
  human-gold receipts. No shipment claim is made.
- The freeze's other blockers are unchanged: the answer-association and
  tracking-vocabulary conflicts, R6/EVAL execution, and the S09 live-source
  decision.
- Everything outside the Phase 3 proof stays off the path: S12's branching
  items (D2, D4–D6), composition/catalog work (S15), S14 follow-up tickets,
  and the Orca N-items.

## Review status

- **Original freeze:** Terra closed it with `ctx_faaa104e3ea4` /
  `msg_a70bbd115366` ("Phase 2 documentation complete, subject to root
  integration"). **That closure predates this amendment and does not cover
  it.**
- **This amendment and Luna's integration trace:** the trace is in progress
  (Luna/max, documentation only, 2026-09-22). Both need their own Terra
  review after Luna finishes.
- **Root:** keeps the final gate.

## Ledger recording note

This amendment is recorded in
[`P2-FREEZE-04-ledger.json`](P2-FREEZE-04-ledger.json) as a top-level
`amendments[0]` entry (`P2-FREEZE-04-A01`). The 18 original rows are
unchanged in content.

**Disclosure:** adding the entry re-serialized the whole file, so the
original whitespace and line wrapping were lost (532 → 657 lines). The
file was untracked, and there was no other copy to restore from. Nothing
pins the ledger's digest. The freeze's recorded validation was re-run
afterwards with the same result as its original receipt:

- `json.tool` exit 0;
- `rows=18 required_fields=21 missing=0 bad_status=0`.

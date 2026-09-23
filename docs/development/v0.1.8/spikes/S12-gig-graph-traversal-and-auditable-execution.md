# S12 — One Gig function as an auditable graph traversal

## Ticket

**Status:** Task 1 (Scout graph mapping) closed 2026-09-22 as a
documentation mapping, reviewed and corrected. Task 2 (decision-edge
design) documented 2026-09-22 and corrected after review. Task 4 (proposed
system-behavior changes) documented 2026-09-22. Task 3 (Orca
acknowledgment check) was run 2026-09-22 with operator go-ahead, and its
results complete task 4's section C. All four tasks are documented and
awaiting operator review.  
**Requested:** 2026-09-21. **Scope:** v0.1.8 spike; no new release gate.  
**Execution:** Documentation-only mapping (task 1) complete and closed. The
three-worker Orca acknowledgment check (task 3) was run 2026-09-22 after
the operator's explicit go-ahead. The N3 blocking-resume check was run
the same day with its own go-ahead. Any further experiment needs another
go-ahead. **Sequencing:
depended on [S15](S15-goal-and-tool-unit-composition-research.md), now
satisfied** — S15 traced `scout_materialization.py`'s actual construction
path (`_compiled_snapshot`) directly, not `graph_set.py`'s validation-only
docstring, and this mapping is built on that finding.

**Current evidence pointer:** [S12 Scout graph mapping](evidence/S12-scout-graph-mapping.md)
(revised twice same-day after review — see its "Revision notes" section)
maps `find-jobs` as it compiles today (one single-goal, zero-edge graph per
S15's traced path). `run.py:3438`'s `_validate_scheduler_policy` currently
rejects recovery edges, non-automatic dependency edges, parallel goals, and
operator-gated goals — but a plain sequential multi-goal graph is not
blocked: existing test coverage (read from source, not executed) asserts a
2-goal automatic-dependency-edge graph executing end to end through
`launch_run`.
The genuinely open runtime question is narrower — whether Scout's specific
proposed stages map onto that mechanism, and whether a goal's execution can
emit a custom typed outcome rather than the hard-coded `COMPLETE` observed
in the traced path — not whether multi-node execution works at all. It
also corrects an earlier conflation of Scout's six independent selectors
with the proposed multi-stage discovery pipeline, and does not claim Orca
is currently integrated into Scout's runtime (only that Orca is established
for development-worker coordination elsewhere). None of this blocks the
rest of S12's scoping on a new investigation.

**Task 2 evidence pointer:** [S12 decision-edge design](evidence/S12-decision-edge-design.md)
answers task 1's open runtime question. The schema, validator
(`undeclared_outcome`) and scheduler already declare, check and route custom
typed outcomes; the routing unit tests were executed this session and
passed. But no execution path emits a label other than `COMPLETE`/`FAILED`,
and Scout's compiler declares only `COMPLETE`. A branch that isn't taken is
recorded `blocked`, which finishes the run `blocked`, and OR-joins are
unsupported. `find-jobs`'s discovery packet already makes a typed
`matches`/`partial`/`no_match` decision that is not connected to graph
routing. The design proposes reusing that decision at an `assess` node and
records candidate changes D1–D6 as input to task 4. These include
alternatives and an optional item, so they are not all required. None of
them is authorized.

**Task 4 evidence pointer:** [S12 proposed system-behavior changes](evidence/S12-proposed-system-behavior-changes.md).
It gives a node-level graph summary (inputs, outputs, finish conditions)
and the D1–D6 register with each item's kind. It adds four
visibility changes (V1–V4), each with code evidence. For example, V2: the
Scout report calls a local run `succeeded` if any goal completes, which
will misreport multi-node runs. Section C lists the notification and
resume findings from task 3 (N1–N5).

**Task 3 evidence pointer:** [S12 Orca acknowledgment check](evidence/S12-orca-ack-check.md),
run `run_ef1121d3c864`. There were three attributable ACKs (heartbeats) and
three separate completion receipts (`worker_done`), all timestamped, with
none missing or duplicated. The coordinator resumed with no polling loops.
The limits are that blocking resume wasn't exercised (the workers finished
before the waits began, and both waits returned in under 200 ms), the
Orca-unreachable path wasn't tested, and token usage isn't exposed.
Heartbeat ACKs don't appear in the coordinator's consuming deliveries.

**N3 evidence pointer:** [S12 blocking-resume check](evidence/S12-orca-blocking-resume-check.md),
run `run_c7a2891bce10`, operator-authorized. A wait that was already
blocked stayed blocked for 76.4 s. It returned within the second the
worker's `worker_done` was created, and the coordinator resumed 8.2 s
later. There was one unplanned coordinator turn: Orca's in-terminal nudge
fired for the ACK heartbeat, even though the wait filtered heartbeats out
(N6). The Orca-unreachable path is still untested.

**Problem:** The operator cannot quickly see what tasks are running, what
finished, why execution moved forward, or which evidence supports a result.
Long orchestration commands and noisy messages obscure the work; completion
notifications have also been unreliable in this session.

**Intended behavior:** Center GigAI on the Gig's node graph: one traversal
completes one defined function with an inspectable result. Reuse existing
execution tools, including Orca; do not rebuild an orchestration framework.
Show the graph, task ownership, dependencies, progress, decisions and evidence
in a form the operator can audit without reading raw dispatch commands.

**Proposed tasks (refine before execution):**

- Map one Scout job-search function onto existing graph/execution capabilities.
- Define where typed, evidence-backed decisions select the next graph edge,
  drawing on the existing TypeSafe/Jev research.
- Investigate the smallest Orca automation/script needed for a three-worker
  acknowledgment check, including completion delivery and coordinator resume.
- Record concrete proposed system-behavior changes and attach evidence for
  each; leave their implementation location open until the spike is reviewed.

**Acceptance / attached evidence:** A short graph and task summary; explicit
node inputs, outputs and finish conditions; and, if separately authorized,
three attributable worker acknowledgments/completion receipts with timestamps
and a trace showing whether the coordinator resumed. Distinguish acknowledgment
from task completion. Record missing/duplicate notifications, coordinator
activity and token usage where available; do not claim zero overhead or graph
correctness from a successful acknowledgment check alone.

**Evidence now:** Conversation notes below, the linked research, and one
experiment: the task 3 acknowledgment check (see its evidence pointer
above). Apart from that operator-authorized check, this ticket authorizes
documentation only.

## Conversation notes

These are a condensed record of the discussion, not a verbatim transcript or
a frozen implementation contract.

1. **Why this came up.** During Luna implementation and Terra verification,
   the operator repeatedly had to ask about completion. Large `worker-start`
   commands and repeated status messages made the work difficult to audit.
   One completion was retrieved from an earlier background wait; another
   worker reported a runtime connection failure while sending its receipt.
   These are observed symptoms, not proof of a single root cause.
2. **Keep the coordinator cheap.** The operator invokes an orchestrator to
   organize a node graph, parallelize independent work and resume when needed.
   It should yield while workers run. Reuse substantial worker packets rather
   than spawning agents for tiny tasks or spending expensive coordinator
   tokens watching them.
3. **The central correction.** The operator emphasized: “we are not rebuilding
   the fucking orchestration framework!” The product idea is the Gig as a
   node graph: “one traversal of a graph completes one function!” Orca is a
   reusable execution mechanism beneath that idea, not the product to rebuild.
4. **Scout example.** The operator described finding data, categorizing it
   through analysis, and determining what follows. A proposed concrete path is
   find postings → save/show them → extract requirements → assess against
   selected evidence/preferences → categorize and explain the result.
   Acquisition visibility should not wait for analysis. Exact nodes, branches
   and the function's final output remain to be defined.
5. **TypeSafe/Jev connection.** Revisit the existing evidence for bounded,
   typed decisions at decision nodes, with evidence support and explicit
   uncertainty/abstention. Such decisions may select graph edges. Valid JSON
   is not correctness, and a decision is not authorization to perform an
   action. Jev adoption is undecided; evidence references must not be presented
   as native Jev output when supplied by a wrapper or grader.
6. **Smallest proposed evaluation.** Start with three Orca workers asked only
   to acknowledge, with minimal prompts and responses. Make their tasks and
   receipts visible and check completion/resume without repeated LLM polling.
   This tests the execution plumbing; it does not establish useful Scout
   analysis or acceptance of the larger graph design. Do not run it now.
7. **Readable tickets first.** New work documents should begin with a short
   Jira-style Markdown ticket: problem, intended behavior change, tasks,
   acceptance criteria and attached evidence. Put conversation notes and
   granular specifications underneath. The operator should understand the
   work without reading hundreds of lines. This is the format to follow;
   details for this spike will be added later.

## Related work and open placement

- [S12 decision-edge design](evidence/S12-decision-edge-design.md) is this
  ticket's documentation-only output for task 2.
- [S12 Scout graph mapping](evidence/S12-scout-graph-mapping.md) is this
  ticket's own documentation-only output for task 1, built directly on S15.
- [S15 — goal and tool-unit composition research](S15-goal-and-tool-unit-composition-research.md)
  is the prerequisite this mapping is built on: it traces how a goal graph is
  actually constructed today (including `scout_materialization.py`'s
  compilation path, not only `graph_set.py`'s validation role) and researches
  goal-unit/tool-unit composition against other harnesses.
- [TypeSafe/Jev structured-decision evidence](evidence/jev-typesafe-ai-structured-decisions-research.md)
  supplies the existing decision-contract and evaluation distinctions.
- [S07 execution modes](S07-execution-modes-and-cost-aware-orchestration.md)
  covers routing, cost and event-driven coordination; reuse that work.
- [ORCA-01 completion delivery](../../followups/ORCA-01-worker-completion-delivery.md)
  records a related external-tooling concern.

Whether a change belongs in GigAI's graph workflow, a small integration script,
or Orca itself is deliberately unresolved. Inventory existing support before
proposing additions. No framework rebuild, live experiment, provider adoption
or implementation follows from recording this ticket.

# S12: Mapping one Scout function onto existing graph/execution capabilities

**Date:** 2026-09-22
**Scope:** Documentation only, per S12's own authorization boundary — this
covers only the first proposed task ("map one Scout job-search function
onto existing graph/execution capabilities"). The three-worker Orca
acknowledgment experiment (S12's third proposed task) is explicitly **not**
covered here and remains gated behind a separate operator go-ahead; nothing
in this document authorizes it. No code, schema, or runtime change is made.
**Prerequisite:** Built directly on [S15](../S15-goal-and-tool-unit-composition-research.md)'s
verified findings, not on `graph_set.py`'s docstring or an assumption about
composition — per S12's own sequencing requirement.

## What S15 established, applied here

S15 traced `scout_materialization.py:226`'s `_compiled_snapshot` directly
and found that **Scout's bundled compiler emits one single-goal, zero-edge
graph per selector** — not a multi-node composed graph. Concretely, for
each of Scout's six selectors (`research-role`, `find-jobs`,
`tailor-application`, `record-application`, `prepare-interview`,
`proposal-assessment`), today's compiled graph has exactly one goal, whose
`entry_goal_ids` and `terminal_goal_ids` both point at that same goal, with
`edges: []` and `tools: []` hard-coded.

This means: **the multi-node Scout flow described in the S12 conversation
notes and in [1.8-chat-09-21-26.md](../../1.8-chat-09-21-26.md) (find
postings → save/show → extract requirements → assess against evidence →
categorize/explain) does not exist as a graph today.** That proposed flow is
a design for stages *within* one job-search function (illustratively,
within `find-jobs`); it is not the same thing as, and should not be
conflated with, Scout's six top-level selectors
(`research-role`/`find-jobs`/`tailor-application`/`record-application`/
`prepare-interview`/`proposal-assessment`), which are six separate
operations a user invokes independently, not six stages of one discovery
pipeline. Each of those six selectors compiles to its own single-goal,
zero-edge graph today (per S15). The proposed multi-stage flow does not
exist inside any of those single nodes either — a single opaque
`gigai.offline` execution against the `find-jobs` goal's output contract is
not shown by this mapping to already perform "discover → extract → assess"
as internal, inspectable steps; it is only established that the graph
structure has one node, not what that node's internal execution logic
actually does stage-by-stage. Mapping "one Scout job-search function" onto
"existing graph/execution capabilities" has to be honest about starting
from one disconnected single-node graph per selector, with no evidence
here about what happens inside that node's execution, and no existing
multi-node `find-jobs` graph to add visibility to.

## Mapping: `find-jobs` as it exists today

Per [`scout_template.py:150-155`](../../../../../src/gigai/scout_template.py),
the `find-jobs` selector is declared as:

```text
selector: "find-jobs"
title: "Find jobs"
purpose: "Compare dated opportunities against explicitly selected preferences."
required_inputs: ("preferences",)
optional_inputs: ("candidate_evidence", "research_revisions", "postings")
outputs: ("discovery",)
```

Per `_compiled_snapshot` (S15's traced path), this compiles today to:

| Graph element | Current value |
| --- | --- |
| `goals` | One goal: `slug: "find-jobs"`, `executor: {kind: "local_capability", capability: "gigai.offline", ...}`, `tools: []` |
| `edges` | `[]` |
| `entry_goal_ids` / `terminal_goal_ids` | Both `[the one goal_id]` |
| `verification` | `{verifier: "gigai.check", acceptance: "Required outputs and declared evidence are present.", required_evidence: ["find-jobs-completion"]}` |
| `output_contract` | Domain `"discovery"`, schema `urn:gigai:scout:discovery-packet:2` (validated by the bundled `discovery.py` validator, per lines 307–323) |

So today's "graph" for this function is a single opaque unit at the graph
level: it runs, and either produces a discovery packet matching the schema
or fails verification. There is no intermediate node the operator can
inspect for "postings found so far" versus "requirements extracted" versus
"assessed against evidence" as separate graph nodes — the graph has one
node, full stop. This document does not trace what the single node's
execution does internally to produce that discovery packet, and does not
claim those stages exist there either; that is simply unexamined by this
mapping. This directly explains the S12 problem statement's symptom ("the
operator cannot quickly see what tasks are running... why execution moved
forward") at the graph-structure level: there is currently only one node to
report on per selector, so any finer-grained visibility a future design
adds has to come from restructuring the graph itself (per the mapping
below), not from an assumption about what the current single node already
does.

## What existing graph/execution capabilities this could map onto

Using only what S15 and this codebase currently establish — no new
capability is proposed here, only how existing pieces already fit:

- **The schema's vocabulary is not the same as what the runner currently
  executes.** `goal-graph.schema.json`'s `goals` array, `edges` array (with
  `on_outcomes`/`kind: dependency|recovery`), and per-goal `tools` array are
  schema-valid for a multi-node graph — but
  [`run.py:3438`](../../../../../src/gigai/run.py)'s
  `_validate_scheduler_policy` is a separate, stricter runtime gate that
  currently **rejects** a schema-valid graph if it has: any `recovery` edge,
  any non-automatic `dependency` edge, `aggregate_budget.max_parallel_goals
  != 1` (no parallel goals), any goal with `activation != "automatic"`
  (operator-gated goals are rejected), or an `executor.capability` outside
  `{"gigai.offline", "gigai.deterministic"}`. A plain sequential multi-goal
  graph is not blocked by this gate, though: an existing test,
  [`test_sequential_scheduler_completes_every_goal_in_dependency_order`](../../../../../tests/behaviors/runtime_run_authority/test_g14_scheduler.py)
  (read, not re-run, for this mapping) already exercises a 2-goal graph
  connected by automatic dependency edges through `launch_run` end to end,
  asserting `status == "succeeded"` with both goals `complete` and
  `realized_max_parallel_goals == 1`. So the narrower, still-open question
  is not "can any multi-node graph run" — sequential automatic-dependency
  execution is already demonstrated — but whether Scout's specific proposed
  stages (discover → extract requirements → assess against evidence →
  present) and a custom, non-`COMPLETE` typed decision outcome are
  supported, which the traced code does not establish either way.
  Separately, whether a goal's execution can emit a custom typed
  `outcome` at all is also unverified — see the correction below.
- **`verification.required_evidence` is the existing hook for auditable
  completion**, already present per-goal. A multi-node version of
  `find-jobs` could attach a distinct `required_evidence` entry per node
  (e.g. `postings-discovered`, `requirements-extracted`,
  `assessment-complete`) instead of one blanket `find-jobs-completion` for
  the whole opaque unit — this is a direct, existing mechanism for the
  "evidence supports a result" half of S12's problem statement, not a new
  concept.
- **`edge.on_outcomes` is a routing mechanism the runner does read** — per
  [`run.py`](../../../../../src/gigai/run.py), `_ready_goals` and
  `_blocked_by_terminal_outcome` both check a completed goal's recorded
  `outcome` against an outgoing edge's `on_outcomes` list to decide whether
  a dependent goal may run. But this is not yet shown to be a usable
  decision-node mechanism for S12's purpose: the traced deterministic
  execution path (e.g. `run.py` lines ~3353/3366/3381) hard-codes
  `"outcome": "COMPLETE"` on success rather than reading a custom typed
  label a goal's own execution produced. Whether any existing goal
  execution path can emit something other than `COMPLETE`/`FAILED` as its
  `outcome` — which a Jev-style typed decision (`NEEDS_MORE_EVIDENCE` vs.
  `SUFFICIENT_EVIDENCE`, for example) would require — is **not established
  by this mapping** and needs its own runtime investigation before being
  treated as an existing capability to build on.
- **Orca's role here is unestablished, not assumed.** S12's own instruction
  is to reuse existing execution tools, including Orca, rather than
  rebuild an orchestration framework — but this mapping does not show that
  Orca is currently wired into Scout's graph-runner execution path at all;
  Orca's established use elsewhere is as a development-worker
  coordination tool, which is a different thing from a Scout runtime
  integration, and this document does not claim or verify one exists. How
  (or whether) a multi-node graph's independent nodes would be
  scheduled/resumed through Orca specifically is unverified and is
  execution-mode territory already scoped separately in
  [S07](../S07-execution-modes-and-cost-aware-orchestration.md)
  (discover/assign/explain, event-driven coordination) — S12 should draw
  on S07's own investigation rather than assume Orca integration as a
  starting fact here.

## Where typed, evidence-backed decisions could select an edge (task 2, mapping only)

Per the [Jev/TypeSafe research](jev-typesafe-ai-structured-decisions-research.md),
the useful transferable concept is a **small, inspectable decision boundary**
between messy model output and deterministic branching — a typed
`choice`/`score`/`noul`-shaped answer, not adoption of Jev itself (still
undecided per that document). Mapped onto GigAI's existing schema: a goal's
`outcomes` field is already the typed-label boundary; the open design
question S12 should carry forward (not resolved here) is where in a
multi-node `find-jobs` graph such a decision would sit — e.g. a node after
"extract requirements" that outputs `SUFFICIENT_EVIDENCE` /
`NEEDS_MORE_EVIDENCE` / `NEEDS_OPERATOR_INPUT` as its `outcomes`, with
`edge.on_outcomes` routing accordingly — **provided a goal's execution can
actually produce a custom `outcome` value**, which is the open runtime
question flagged above, not something this mapping confirms. This mirrors
the Jev material's own caution: a typed decision is not itself
authorization to act, and would still need GigAI's existing
verification/evidence discipline attached, not a bare model label taken as
ground truth.

## What this does not establish

- This is a mapping exercise against **today's compiled structure**, not a
  proposal for what the new multi-node `find-jobs` graph's exact nodes,
  contracts, or executors should be — that remains open, consistent with
  S12's own "exact nodes, branches and the function's final output remain
  to be defined" conversation note.
- **A plain sequential multi-goal graph's runtime execution is already
  demonstrated**, not an open question — see
  `test_sequential_scheduler_completes_every_goal_in_dependency_order`
  above. What remains genuinely unverified, and needs its own
  investigation, is narrower: whether Scout's specific proposed stages
  (discover → extract requirements → assess against evidence → present)
  map cleanly onto that mechanism, and whether any goal's execution can
  emit a custom `outcome` value rather than the hard-coded
  `COMPLETE`/`FAILED` observed in the traced path. These findings do not
  block the rest of S12's scoping on a new investigation; they narrow what
  that investigation, if and when undertaken, needs to answer.
- **Scout's six selectors are not the same thing as the proposed
  multi-stage `find-jobs` pipeline**; this document does not claim the
  six selectors already implement those stages, and does not claim to
  have traced what happens *inside* any single node's execution.
- Orca's role in a Scout runtime execution path is not established here;
  its documented use as a development-worker coordination tool is a
  separate fact from any claim about Scout's own graph runner, and this
  document makes no such integration claim.
- No claim is made here about how visibility/audit UI would surface this to
  the operator (S12's "in a form the operator can audit" requirement) —
  that is a separate, unaddressed design question this document does not
  answer.
- The three-worker Orca acknowledgment check (S12 task 3) is unaddressed
  here by design; it requires separate authorization per S12's own ticket.
- No schema change, new authoring tool, or runtime change is made or
  proposed as ready-to-implement by this document.

## Related work

- [S15 — goal and tool-unit composition research](../S15-goal-and-tool-unit-composition-research.md)
  is the direct prerequisite this document builds on.
- [`run.py`](../../../../../src/gigai/run.py) (`_validate_scheduler_policy`
  at line 3438, and the goal-execution/outcome-recording paths around lines
  3353–3484) is the runtime source read directly to establish the
  scheduler-rejection and hard-coded-outcome findings above — read on
  review, not assumed from the schema alone.
- [`test_g14_scheduler.py`](../../../../../tests/behaviors/runtime_run_authority/test_g14_scheduler.py)
  is existing test coverage, read (not executed) for this mapping; its
  `test_sequential_scheduler_completes_every_goal_in_dependency_order`
  asserts a 2-goal automatic-dependency-edge graph executing end to end
  through `launch_run`, which is why "can any multi-node graph run" is not
  treated as open above. This is coverage read from source, not a passing-run
  receipt from this session.
- [Jev/TypeSafe structured-decision research](jev-typesafe-ai-structured-decisions-research.md)
  grounds the decision-edge mapping above.
- [S07 — execution modes and cost-aware orchestration](../S07-execution-modes-and-cost-aware-orchestration.md)
  is the existing scope for how independent nodes would actually be
  scheduled/resumed through Orca; not re-derived here.
- [1.8-chat-09-21-26.md](../../1.8-chat-09-21-26.md) is the source
  discussion for the illustrative multi-node Scout flow referenced above.

No implementation, schema change, or live experiment follows from this
document.

## Revision notes (same-day correction)

The first version of this document made three errors, corrected here:

1. It treated schema support (`goal-graph.schema.json` allowing multi-goal
   graphs, edges, tools) as equivalent to runtime execution support. Reading
   `run.py:3438`'s `_validate_scheduler_policy` directly shows the runner
   currently **rejects** recovery edges, non-automatic dependency edges,
   parallel goals, and operator-gated goals, and the traced execution path
   hard-codes the `COMPLETE` outcome rather than reading a custom typed
   label. The gap is not established as compiler-only; it needs its own
   runtime investigation. Corrected above with a direct source citation.
2. It implied the six Scout selectors are stages of the proposed
   multi-stage discovery pipeline, and that those stages already exist
   "inside" a node's execution. Neither is established; the six selectors
   are six independent top-level operations, and this document did not
   trace what happens inside any one node's execution. Corrected.
3. It described Orca as "the existing execution mechanism" for this
   mapping without distinguishing Orca's established role (development-
   worker coordination) from an unverified claim of Scout runtime
   integration. Corrected to state this is unestablished.

One broken source link (`scout_template.py`, missing one `../` segment)
is also fixed.

**Second correction pass (same day):** the first revision, in fixing point
1 above, overcorrected into a broader unknown than the evidence supports —
it framed "can the runner execute any multi-node graph at all" as fully
open. Existing test coverage,
`test_sequential_scheduler_completes_every_goal_in_dependency_order` (read
from source, not executed, for this pass), asserts a 2-goal
automatic-dependency-edge graph executing end to end through `launch_run`
with both goals reaching `complete`. The genuinely open question is
narrower: whether Scout's
specific proposed stages and a custom (non-`COMPLETE`) typed decision
outcome are supported, not whether sequential multi-goal execution works
at all. This pass also removes a leftover sentence (in the "`find-jobs` as
it exists today" section) asserting the proposed stages are "internal to
whatever executes" the single current node — an unverified claim that
contradicted the point-1 correction, since this document never traced that
node's internal execution.

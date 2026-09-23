# S15 — Goal and tool-unit composition research

## Ticket

**Status:** Research recorded 2026-09-22.  
**Requested:** 2026-09-22. **Scope:** v0.1.8 spike; no new release gate.  
**Execution:** Research complete. No schema change, runtime change, or new
authoring tool is authorized by this ticket.

**Current evidence pointer:** [S15 research record](evidence/S15-goal-and-tool-unit-composition-research.md)
(revised same-day after review — see its "Revision notes" section) traces
`scout_materialization.py:226`'s `_compiled_snapshot` directly and finds
that **Scout's bundled compiler** does not compose graphs from a reusable
goal-unit/tool-unit catalog: it iterates a fixed tuple of six hand-written
selectors and emits, per selector, a single-goal, zero-edge graph with
`tools` hard-coded to `[]` and a literal `executor` inlined every time —
this finding is scoped to that one traced path, not a claim about every
possible construction path in GigAI. `graph_set.py`/`validate_goal_graph`
confirmed to validate an already-built document's internal consistency, not
assemble one. External survey of LangGraph, Temporal, Anthropic's tool-use
API, and CrewAI finds CrewAI's `Task` and LangGraph's Functional-API
`@task` (both independently-defined units callable outside a fixed graph
position) as the closest precedents for the "goal unit" GigAI wants;
LangGraph also has a genuine reusable tool object (`ToolNode` +
`BaseTool`/callables) and a materially richer graph-composition toolkit
(dynamic `Send`/`Command` routing, subgraphs) than the first draft
credited it with. A sketch-level composition proposal for GigAI, and a
handoff note to S14, are recorded in the evidence document; no
superiority ranking between GigAI's schema and the surveyed frameworks is
claimed.

**Problem:** GigAI validates goal graphs (`goal-graph.schema.json`,
`gig-graph-set.schema.json`) and also already programmatically compiles them
in at least one place: `scout_materialization.py:226`'s `_compiled_snapshot`
iterates `SCOUT_OPERATION_GRAPHS` (declared in `scout_template.py`) and builds
a goal-graph document per selector. Whether that compilation already
constitutes composition from a reusable goal-unit/tool-unit catalog, or is
closer to templated whole-document generation from a fixed, small selector
list, has not been established — this spike needs to read that path directly
rather than infer it from `graph_set.py`'s docstring, which only covers
authority/selection/validation of an already-produced graph, not how Scout's
graphs are built in the first place. We also have not established how other
agent harnesses and LLM frameworks solve goal/tool composition, to compare
against whatever GigAI's actual construction path turns out to be.

**Intended behavior:** Research how leading agent harnesses and LLM
frameworks define and compose (a) tool contracts and (b) task/goal units into
executable graphs or pipelines, and use that research to propose a concrete
path from GigAI's current goal-graph schema toward a goal-unit + tool-unit
composition model — without committing to a redesign or schema change in
this spike.

**Proposed tasks (refine before execution):**

- Document GigAI's current state precisely: what `goal-graph.schema.json`
  already models per goal (`tools`, `executor`, `contract`, `verification`,
  `outcomes`) and per edge (`on_outcomes`, `kind: dependency|recovery`).
- Trace the actual graph-construction path in code, not just validation:
  read `scout_materialization.py`'s `_compiled_snapshot` (compiles graphs
  from `SCOUT_OPERATION_GRAPHS`) and `scout_template.py` (where
  `SCOUT_GRAPHS`/`SCOUT_PROPOSAL_GRAPH` selectors are declared) alongside
  `graph_set.py` (authority/selection/validation of an already-built graph),
  `run.py`, and `validators.py`. Establish, with citations, whether today's
  construction already qualifies as composition from reusable goal-unit and
  tool-unit definitions, or is closer to iterating a small fixed selector
  list to emit whole-document graphs — this is a gap to verify, not a
  predetermined conclusion to confirm.
- Survey how at least 3–4 other frameworks separate "tool" from "task/goal"
  and compose them into a graph, e.g.:
  - LangGraph (node/edge state-machine composition, tool binding).
  - Temporal (workflow/activity separation — already partly covered by
    [backend-development:workflow-orchestration-patterns] conventions used
    elsewhere in this repo's ecosystem).
  - OpenAI/Anthropic function-calling and agent-SDK tool-definition shape
    (schema-first tool contracts, how they're registered and reused
    across tasks).
  - Any open-source agent-graph framework worth citing (e.g. AutoGen,
    CrewAI, or a comparable project) for how it defines a reusable task
    unit distinct from a tool.
  - For each, extract concretely: how is a tool defined once and reused
    across tasks; how is a task/goal unit defined independent of a specific
    graph position; how are they wired into a graph instance; what's the
    minimal contract a tool must satisfy to be swappable.
- Compare each surveyed model's tool-contract shape against GigAI's existing
  `tool` and `executor` sub-schemas in `goal-graph.schema.json` — note gaps
  (e.g. GigAI's `tool.resolution: installed|materialized|blocking` is a
  provisioning-state concept other frameworks may not separate out) rather
  than assuming GigAI must converge to match them.
- Propose, at a sketch level only, what a "goal unit" definition (independent
  of graph position) and a "tool unit" definition (independent of a specific
  goal) would need to contain for GigAI specifically, and how an existing
  graph like Scout's discovery→assessment flow would be expressed by
  composing them — as a proposal for review, not a schema change.
- Note explicitly where this research should hand off to
  [S14](S14-schema-inventory-and-consolidation-audit.md): if a cleaner
  goal-unit/tool-unit model would naturally retire or merge some of today's
  schema variants, name which ones, but do not perform that consolidation
  here.

**Acceptance / attached evidence:** A written comparison table (framework →
tool-definition shape → task/goal-unit shape → composition mechanism), a
grounded description of GigAI's current goal-graph model with code citations,
a gap analysis between the two, and a sketch-level composition proposal for
Scout's existing flow. No schema file is changed, no new authoring CLI/API is
built, and no graph-runner change follows from this spike alone.

**Evidence now:** See [S15 research record](evidence/S15-goal-and-tool-unit-composition-research.md)
above. This ticket authorizes research only; no code change follows from it.

## Conversation notes

Condensed from the 2026-09-22 discussion. The operator's framing: "how does
it currently create a goal graph.. and how can we get to a goal + tool unit,
[and] do some research on other leading harnesses, and other llm repos to get
to that point." This is explicitly two questions — current-state
understanding, then external research — not a request to redesign the schema
immediately.

This continues the same-day discussion recorded in
[1.8-chat-09-21-26.md](../1.8-chat-09-21-26.md), where the operator defined
the target pieces conceptually (tool, goal unit, graph node, graph edge, goal
graph) and asked "how to build the foundation that lets tools and goal units
be composed into a node graph." This spike is the concrete research task that
follows from that discussion; it was deferred there ("not yet created") and
is created now at the operator's request.

## Related work and open placement

- [1.8-chat-09-21-26.md](../1.8-chat-09-21-26.md) — the composition
  discussion this spike operationalizes, including the tool / goal-unit /
  graph-node / graph-edge / goal-graph definitions to validate or revise
  against external research.
- [S14 — schema inventory and consolidation audit](S14-schema-inventory-and-consolidation-audit.md)
  is the companion spike; a consolidation opportunity found here should be
  named and handed to S14 rather than acted on directly.
- [goal-graph.schema.json](../../../../src/gigai/schemas/goal-graph.schema.json),
  [gig-graph-set.schema.json](../../../../src/gigai/schemas/gig-graph-set.schema.json),
  [graph_set.py](../../../../src/gigai/graph_set.py),
  [scout_materialization.py](../../../../src/gigai/scout_materialization.py)
  (`_compiled_snapshot`, line 226), and
  [scout_template.py](../../../../src/gigai/scout_template.py) (selector
  declarations) are the concrete current-state artifacts to ground the "how
  does it currently create a goal graph" half of this research — the
  compilation path, not only the validation path, must be read directly.
- [S12 — auditable graph traversal](S12-gig-graph-traversal-and-auditable-execution.md)
  is adjacent: it addresses *executing and auditing* one traversal, not
  *composing* the graph from units. Check it before duplicating scope.
- [S13 — existing job-discovery solutions research](S13-existing-job-discovery-solutions-research.md)
  is the sibling spike from the prior request; unrelated in subject but
  created under the same "research before building" instruction.

No schema change, new authoring tool, or runtime change is authorized by
recording this ticket.

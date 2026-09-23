# S15: Goal and tool-unit composition research

**Date:** 2026-09-22
**Scope:** v0.1.8 research only. GigAI's current-state findings are read
directly from source (file/line citations below); no code was changed or
run beyond reading. External framework findings are from each framework's
own primary documentation, fetched directly and cited per claim; no
framework was installed or run. No schema change, new authoring tool, or
runtime change follows from this research.
**Question:** How does GigAI actually construct a goal graph today — is it
already composition from a reusable goal-unit/tool-unit catalog, or
something narrower — and how do other agent harnesses/LLM frameworks define
and compose tool contracts and task/goal units, to inform what a GigAI
goal-unit + tool-unit model should look like?

## Summary

**Scout's bundled compiler does not compose goal graphs from a reusable
catalog of goal units and tool units.** This finding is scoped to the one
concrete construction path traced for this spike — `_compiled_snapshot` —
not to every possible path in GigAI; see Open Questions for what wasn't
ruled out. That traced path iterates a small, fixed, hand-written tuple of
five (plus one, so six) selector declarations and emits, per selector, a
**single-goal, zero-edge graph** (one entry node that is also the terminal
node). This is closer to *templated whole-document generation from a fixed
list* than to composing a graph by wiring together independently-defined,
reusable goal units and tool units — the schema has the *vocabulary* for
richer composition (multi-goal graphs, `edges`, `tools` per goal, `executor`
resolution states) but this one construction path doesn't exercise most of
it. `graph_set.py` and `validate_goal_graph` operate entirely on an
already-assembled document's internal consistency (unique IDs, edges
reference real goals, etc.) — they validate structure, they do not assemble
it from parts.

Across the four external models surveyed, the clearest pattern is that
**"tool" and "task/goal unit" are commonly two separate objects with
different lifecycles**, though the degree of separation varies. The
strongest precedent for GigAI's specific need — a goal unit definable
independent of graph position, later wired into a specific graph — is
**CrewAI's `Task`** (a standalone object with `description`,
`expected_output`, and an optional `tools` list, which can be defined
without committing to which agent runs it) and, separately, **LangGraph's
Functional-API `@task`** (an independently-defined, checkpointed unit of
work callable from an entrypoint, another task, or a state-graph node —
see the corrected LangGraph entry below). Temporal's `Activity` contributes
a different, narrower precedent: its idempotency and per-unit retry-policy
discipline is a real reason to split work into separate units, but that
discipline alone does not establish that Activities are independently
*versioned* or drop-in *interchangeable* the way a tool-unit catalog entry
would need to be — Temporal's docs describe reuse and configurable retry,
not a versioning or interchangeability contract, so this spike does not
claim more than that.

## GigAI's current construction path (grounded in code)

### What the schema already models

[`goal-graph.schema.json`](../../../../../src/gigai/schemas/goal-graph.schema.json)
defines, per goal: `tools` (array of `{name, resolution, materialized_by,
blocking_reason}`), `executor` (`{kind, capability, role, resolution,
materialized_by, blocking_reason}`), `contract` (an artifact reference),
`verification` (`{verifier, acceptance, required_evidence}`), and
`outcomes`. Per edge: `{edge_id, from_goal_id, to_goal_id, kind:
dependency|recovery, on_outcomes, automatic}`. This is the field-level
vocabulary compared against each surveyed framework's own objects below;
a different field count or naming granularity does not by itself mean more
composition capability, so no overall richer-than-the-others ranking is
claimed here — see the corrected Gap analysis section for scoped,
concrete comparisons instead.

### How a graph is actually built today

[`scout_materialization.py:226`](../../../../../src/gigai/scout_materialization.py)
(`_compiled_snapshot`) is the concrete construction path traced for this
spike. It:

1. Iterates `SCOUT_OPERATION_GRAPHS` — a fixed, hand-written tuple of six
   `ScoutGraphSource` dataclass instances declared in
   [`scout_template.py:143-190`](../../../../../src/gigai/scout_template.py),
   each with a `selector`, `title`, `purpose`, `required_inputs`,
   `optional_inputs`, and `outputs`.
2. For each selector, emits **one goal graph document containing exactly
   one goal**: `"goals": [{...}]` (a single-element list), `"edges": []`
   (empty), `"entry_goal_ids": [goal_id]` and `"terminal_goal_ids":
   [goal_id]` both pointing at that same one goal
   ([lines 260–281](../../../../../src/gigai/scout_materialization.py)).
3. That one goal's `tools` field is hard-coded to `[]` (line 270) — no
   tools are attached at all in this construction path, despite the schema
   supporting a `tools` array per goal.
4. The goal's `executor` is a fixed literal:
   `{"kind": "local_capability", "capability": "gigai.offline", ...}`
   (line 269) for every selector — not selected from a catalog of
   executor/tool-unit options, just the same value inlined per iteration.
5. Selector-specific behavior (e.g. `research-role` vs. `find-jobs` vs.
   `tailor-application`) is expressed by branching on `item.selector ==
   "..."` string literals (lines 290, 307, 324, 345) to swap in different
   `output_contract` shapes — conditional logic inside the compiler
   function, not a lookup into a registry of reusable goal-unit or
   tool-unit definitions.

**Conclusion on the gap:** this construction path does not use a catalog
of independently-defined, swappable goal units or tool units. It is a
Python function with an `if/elif` chain over six known selectors, each
producing a single-node graph shell around one goal contract (an external
markdown file) and one output-contract shape. Nothing here is wrong for
Scout's current bundled-and-fixed use case, but it is not the composition
mechanism the ticket asked whether already existed — it does not exist yet.

### What validates a graph, and what that does and doesn't tell us

[`graph_set.py`](../../../../../src/gigai/graph_set.py) (docstring: "Immutable
multi-graph authority, selection, and reference validation") and
[`validators.py:542`](../../../../../src/gigai/validators.py)
(`validate_goal_graph`) operate on an already-built graph document: they
check goal IDs are canonical and unique, edges reference real goal IDs,
entry/terminal sets are consistent, and similar structural rules. This is
real, necessary work, but it presupposes a graph already exists as a
JSON document — it says nothing about how that document's goals, tools, or
edges were chosen or assembled in the first place. Confirming this
directly (rather than inferring it from the module docstring alone, per
this spike's own stated method) closes the open question the ticket
posed: validation and construction are separate concerns in the current
codebase, and only the latter is where a goal-unit/tool-unit catalog would
need to live.

## External framework comparison

| Framework/API | Tool-definition shape | Task/goal-unit shape | Composition mechanism | Minimal contract for a swappable tool |
| --- | --- | --- | --- | --- |
| **LangGraph** ([StateGraph reference](https://reference.langchain.com/python/langgraph/graph/state/StateGraph); [ToolNode reference](https://reference.langchain.com/python/langgraph.prebuilt/tool_node/ToolNode); [Functional API](https://docs.langchain.com/oss/python/langgraph/functional-api); [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)) | **Has a genuine, separate tool object.** `ToolNode` accepts a sequence of `BaseTool` instances or plain callables (auto-converted to tools with inferred schemas); tools are independent, reusable objects passed into `ToolNode`'s constructor, decoupled from any specific node's implementation, not merely an inline call inside a node function. | **Has a genuine, separate independently-defined task unit via the Functional API's `@task` decorator** — not only the state-graph node. A `@task`-wrapped function is "a discrete unit of work," executes asynchronously, is automatically checkpointed for resumable execution, and is documented as callable from an entrypoint, another task, or a state-graph node — i.e. definable and reusable independent of one graph's position, a stronger match to the ticket's "goal unit" framing than a plain `StateGraph` node alone provides. | `add_node()`, `add_edge()`, `add_conditional_edges()` for branching, `set_entry_point()`/`set_finish_point()`, then `compile()`. Beyond basic edges, the Graph API also supports **dynamic destinations** — `Send` objects for map-reduce-style fan-out where the number of edges isn't known ahead of time, and `Command` for combining a state update with routing in one return value — and **subgraphs**, including a documented parent-handoff pattern (`Command(graph=Command.PARENT, goto="parent_node")`) for hierarchical/multi-agent composition. This is materially more than plain conditional branching. | For `ToolNode`: a `BaseTool` instance or a plain callable (auto-schema-inferred) — comparable in spirit to Anthropic's schema-first contract. For the Functional API: a `@task`-decorated function, checkpointed and awaitable, callable only from an entrypoint/task/node. |
| **Temporal** ([Activity Definition](https://docs.temporal.io/activity-definition); [Activities](https://docs.temporal.io/activities); [community: reuse across workflows](https://community.temporal.io/t/reuse-activities-across-workflows/6889)) | Not modeled as "tools" at all — the framework's unit is the **Activity**: a plain function/method that touches the outside world (API call, DB write, etc.), explicitly documented as reusable across multiple workflows and expected to be idempotent. | **Workflow** is the composing unit; it orchestrates one or more Activities. Docs explicitly recommend splitting work into multiple Activities specifically so each can have its own retry policy and be reused independently — i.e. the "unit of work" (Activity) is designed from the start to be independent of any one Workflow's graph position. | A Workflow's code calls Activities (by reference/registration), Temporal's runtime handles retries, timeouts, and durability per Activity. Composition is imperative (workflow code calling activities), not a declarative node/edge graph. | Idempotency and independently-configurable retry policy are the framework's explicit criteria for whether something should be its own Activity. Retry and idempotency guidance addresses execution reliability; it does not establish tool interchangeability. |
| **Anthropic tool-use API** ([Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools); [Tool use overview](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview)) | **Schema-first, three-field contract**: `name`, `description`, `input_schema` (JSON Schema). The model relies entirely on these three fields to decide whether/how to call a tool. Documented best practice is a tool registry/cache reused across calls rather than redefining tools per request. | No "goal unit" concept at this layer — tool-use is a per-turn loop (call → `tool_use` → execute → `tool_result` → repeat); task/goal structure is left entirely to the caller's own orchestration above the API. | The caller supplies a `tools` array per request; Claude selects and invokes; multiple tool calls in one turn are matched by `tool_use_id`, not position/order. | The three-field schema itself (`name`/`description`/`input_schema`) is the minimal swappable-tool contract — deliberately thin, with no built-in versioning, provisioning-state, or resolution-status concept (all of which GigAI's `tool.resolution: installed\|materialized\|blocking` adds on top). |
| **CrewAI** ([Tasks docs](https://docs.crewai.com/en/concepts/tasks); [GitHub](https://github.com/crewaiinc/crewai)) | **Tool is a distinct, separate object** passed into a Task via its `tools` parameter ("the tools/resources the agent is limited to use for this task") — not the same object as the task itself. | **Task is the closest external analogue to a GigAI "goal unit."** A standalone object with `description`, `expected_output`, optional `agent` (can be bound at definition time or left for the crew's process to assign), optional `tools`, and `context` (other tasks whose outputs feed this one — directly analogous to a GigAI edge's data dependency). Docs describe tasks as usable independently or as part of a crew, i.e. definable independent of one specific graph position. | Tasks are assembled into a Crew; `context` links one task's inputs to another task's outputs (comparable to a GigAI edge), and the crew's process (sequential or hierarchical) decides execution order/agent assignment. | A Task's fields (`description`, `expected_output`) are its contract with the executor; `tools` is a separate, swappable list attached to the task, not baked into the task's identity. |

## Gap analysis against GigAI's current schema

This section compares concrete capabilities, not overall expressiveness
rankings — a framework having a different field name or a narrower documented
scope for one concept does not mean GigAI's schema is more capable overall,
only that the two model that specific concept differently.

- **GigAI's per-goal `tools` array is a list of swappable tool references
  attached to a goal, which is the same shape CrewAI's Task-level `tools`
  list uses, and structurally similar to what LangGraph's `ToolNode` does
  (a node holding a list of independently-defined `BaseTool`/callable
  objects) — contrary to this document's earlier draft, LangGraph does have
  a comparable tool-as-separate-object pattern, just expressed as a
  prebuilt node type rather than a per-goal field. Temporal's Activities are
  the most different of the four: an Activity is itself the unit of work
  (closer to a GigAI goal/task), not a reference a task holds.
- **`tool.resolution: installed|materialized|blocking` is a GigAI-specific
  provisioning-state field.** None of the four surveyed models were found to
  expose an equivalent named concept in the material read: Anthropic's tool
  contract is deliberately thin (name/description/schema only); CrewAI's
  tools are documented as available once listed, with no provisioning-state
  field found; Temporal encodes availability concerns as retry/timeout
  policy on the Activity, not a separate resolution-state field; LangGraph's
  `ToolNode`/`BaseTool` and `@task` material read for this spike likewise
  showed no equivalent state field. This does not mean no such concept
  exists anywhere in these frameworks — only that this spike's reading of
  their primary docs didn't surface one. GigAI's resolution states may be
  solving a real problem (a tool a goal *could* use but hasn't been
  installed/materialized yet) that these frameworks' documented material
  doesn't show them needing to solve the same way; worth keeping
  deliberately rather than assuming convergence is required.
- **CrewAI's `Task` and LangGraph's Functional-API `@task` are both
  concrete precedents for a unit definable independent of graph position**,
  addressing the ticket's specific framing more directly than a plain
  `StateGraph` node (which is graph-position-bound by construction) or an
  Anthropic tool-use call (which has no task/goal concept at all). Neither
  is a perfect match: CrewAI's docs show tasks commonly defined with an
  agent already bound at creation time, and LangGraph's `@task` is
  documented as callable only from within an entrypoint/task/node, so it
  is not fully free-standing either. Both are closer precedents than this
  document's earlier draft credited, but neither should be read as an
  existing, complete solution to GigAI's specific separation goal.
- **On graph composition mechanisms, LangGraph's Graph API is the most
  capable of the four surveyed**, not the least: beyond `add_conditional_edges`
  (branching on state, comparable to GigAI's edge `on_outcomes`), it
  documents `Send` for dynamic, count-unknown-ahead-of-time fan-out and
  `Command` for combining a state update with routing in one step, plus
  subgraphs with an explicit parent-handoff pattern for hierarchical/
  multi-agent composition. GigAI's `edge.on_outcomes` + `kind:
  dependency|recovery` is a typed-outcome-driven branching model that none
  of LangGraph's, Temporal's, CrewAI's, or Anthropic's documented mechanisms
  mirror in that exact shape (Temporal handles recovery via retry policy,
  not a graph edge; CrewAI's `context` links data, not outcome branching;
  Anthropic's tool-use loop has no graph concept), but that is a
  difference in kind, not a claim that GigAI's edge model is more
  expressive overall than LangGraph's broader composition toolkit.

## Sketch-level composition proposal for GigAI (for review, not adoption)

This is a sketch to ground discussion, not a schema change or an
implementation plan.

- **Tool unit** (independent of any goal): a definition with GigAI's
  existing `tool` shape (`name`, and the resolution/provisioning fields it
  already has) plus, borrowing the Anthropic tool-use pattern, a stable
  `name`+`description`+input/output contract that a goal unit can reference
  by name rather than re-declare inline.
- **Goal unit** (independent of graph position): borrowing CrewAI's Task
  shape mapped onto GigAI's existing goal fields — `contract`,
  `verification`, `outcomes`, and a list of tool-unit *references* (by
  name, resolved against the tool-unit catalog) rather than the tool
  definitions inline as `_compiled_snapshot` currently does implicitly
  (today it hard-codes `"tools": []` and an inlined `executor` literal
  per goal, rather than referencing anything reusable).
- **Graph node**: a goal unit instantiated at a specific graph position,
  analogous to how CrewAI assigns a Task to a Crew or LangGraph registers a
  node function via `add_node()`.
- **Graph edge**: GigAI's existing `on_outcomes`/`kind` shape already
  exceeds what's needed to match the surveyed frameworks; no change
  suggested here.

**Applied to Scout's existing flow (illustrative only):** the `find-jobs`
selector's current single hard-coded goal could instead be expressed as one
or more goal units (e.g. "discover postings," "extract requirements,"
"assess against evidence" — the multi-node flow already described
conceptually in [1.8-chat-09-21-26.md](../../1.8-chat-09-21-26.md) and in
[S12](../S12-gig-graph-traversal-and-auditable-execution.md)'s conversation
notes) each referencing tool units from a shared catalog (e.g. a
"web-search" tool unit, an "ats-poll" tool unit — see
[S13](../S13-existing-job-discovery-solutions-research.md) for what such tool
units might wrap), composed via edges instead of being compiled as one
opaque single-goal graph per selector. This is the same direction the
conversation already pointed at; this spike grounds it against both
GigAI's actual code and four external precedents rather than leaving it as
an unexamined proposal.

## Handoff to S14

If a goal-unit/tool-unit catalog model is adopted, the `tools: []` and
inlined `executor` pattern in `_compiled_snapshot` would likely change
significantly, and any new shared "tool unit" or "goal unit" schema would
be additive to, not a replacement for, `goal-graph.schema.json`'s existing
per-goal `tool`/`executor` sub-schemas (which describe a tool/executor's
state *within* a specific goal instance, not a standalone catalog entry).
This is a naming/scope note for [S14](../S14-schema-inventory-and-consolidation-audit.md)'s
inventory to pick up if and when a catalog schema is proposed — S15 does
not itself find an existing schema that should be retired or merged as a
result of this research; it identifies a schema-design *direction*, not a
consolidation target.

## Open questions

- `_compiled_snapshot` was the only construction path traced in depth.
  `run.py` and other callers of `graph_set.py` were read only enough to
  confirm they consume/validate an already-built graph, not to rule out a
  second construction path elsewhere in the codebase; a broader grep for
  other callers that build a `"goals": [...]` structure would be needed to
  claim this is the *only* construction path, not just the one this spike
  found and traced.
- None of the four external frameworks/APIs were installed or exercised;
  all claims are sourced from each one's own documentation as fetched, not
  from running code. A framework's actual runtime behavior (e.g. how
  LangGraph handles a tool call failure mid-graph) may differ from what its
  docs describe at a conceptual level.
- The sketch-level proposal above was not validated against any of GigAI's
  other graph-adjacent schemas (`gig-graph-set.schema.json`,
  `graph-selection-record*.schema.json`, `run-plan*.schema.json`) beyond
  noting the handoff to S14; a full compatibility check against those was
  out of scope for this spike.

No schema change, new authoring tool, or runtime change follows from this
research; adopting the sketch-level proposal, or any framework's pattern,
is a separate, explicit decision.

## Revision notes (same-day correction)

The first version of this document made four errors, corrected here:

1. It claimed LangGraph has "no distinct tool or task abstraction," which
   is wrong — `ToolNode` accepts reusable `BaseTool`/callable objects, and
   the Functional API's `@task` decorator is a genuine, independently
   defined, checkpointed unit of work. The LangGraph table row and every
   "LangGraph has no tool abstraction" claim in the Gap analysis are
   rewritten above.
2. It asserted GigAI's schema was "richer/more expressive" than all four
   surveyed frameworks in general terms. Different field names or field
   counts don't establish greater capability, and LangGraph's Graph API in
   particular supports conditional routing, dynamic destinations
   (`Send`/`Command`), and subgraphs — capabilities the first draft didn't
   credit it with. The Gap analysis is rewritten to compare specific,
   scoped capabilities instead of ranking overall expressiveness.
3. The headline ("GigAI does not currently compose...") is now scoped to
   what was actually traced: Scout's bundled compiler
   (`_compiled_snapshot`), not every possible construction path in GigAI.
   The Open Questions section already flagged this path as the only one
   traced in depth; the Summary and section headers now say so explicitly
   too, rather than implying a codebase-wide claim.
4. Temporal's idempotency/retry-policy discipline was described as
   establishing Activities are "independently-versioned" — that claim is
   not supported by the material read; Temporal's docs describe reuse and
   configurable retry, not a versioning or interchangeability contract.
   The Summary's Temporal sentence is corrected to claim only what the
   docs actually establish.

All local evidence-document links (to `src/gigai/...`, to
`1.8-chat-09-21-26.md`, and to sibling ticket files `S12`/`S13`/`S14`) were
also broken — missing one `../` for source links and using bare
same-directory paths for the discussion and ticket files, which actually
live one and two directories up from this `evidence/` file respectively.
All are corrected above.

# S12 task 2: Where typed, evidence-backed decisions select the next edge

**Date:** 2026-09-22
**Scope:** Documentation only, per S12's authorization boundary. This covers
S12's second proposed task. It resolves the runtime question left open by
[task 1's mapping](S12-scout-graph-mapping.md): can a goal's execution emit a
custom typed `outcome`? The Orca acknowledgment check (task 3) is not covered
and remains gated. No code, schema or runtime change is made. Everything in
"Proposed design" is a proposal for review, not an authorization.
**Source state:** read at `b01675d` plus this worktree's uncommitted changes.
None of the cited `src/gigai/` files is modified in the working tree. The
cited test file is untracked in this worktree because of the S11 test
reorganization.

## Answer in one paragraph

**Partly.** The contract and the router already support custom typed
outcomes. What's missing is anything that produces one:

- **Contract:** the schema and validator accept any declared upper-case label
  and reject edges that name undeclared ones.
- **Router:** the scheduler routes on whatever label a completed goal
  recorded, and existing tests exercise a non-`COMPLETE` label.
- **Producer:** no execution path today emits a label other than `COMPLETE`
  or `FAILED`, and Scout's compiler only declares `COMPLETE`.
- **Branch semantics:** if a graph did branch, the branch not taken would be
  marked `blocked`, and the whole run would finish `blocked` rather than
  `succeeded`. That is the second concrete gap.

## Verified findings

Each finding says how it was established: *read* means the source was read,
and *executed* means it was run in this session.

| # | Finding | Source | How |
| --- | --- | --- | --- |
| F1 | A goal's `outcomes` is an array of labels matching `^[A-Z][A-Z0-9_]*$`. An edge's `on_outcomes` uses the same pattern. | [`goal-graph.schema.json`](../../../../../src/gigai/schemas/goal-graph.schema.json) (goal `outcomes`, edge `on_outcomes`) | read |
| F2 | The graph validator rejects an edge whose `on_outcomes` is not a subset of its source goal's declared `outcomes` (`undeclared_outcome`). It also requires an automatic edge to name at least one outcome (`missing_automatic_outcome`). So a typed decision vocabulary is already checked when the graph is authored. | [`validators.py:667-684`](../../../../../src/gigai/validators.py) | read |
| F3 | The router does not care what the label is. `_ready_goals` makes a goal ready only when **every** incoming dependency edge's source is `complete` with an `outcome` listed in that edge's `on_outcomes`. `_blocked_by_terminal_outcome` blocks a pending target when its source is terminal with an unlisted outcome. | [`run.py:3463-3486`](../../../../../src/gigai/run.py), [`run.py:3524-3543`](../../../../../src/gigai/run.py) | read |
| F4 | Existing unit tests exercise that routing with a non-`COMPLETE` label. `test_terminal_unlisted_outcome_blocks_dependent` records `outcome: "REJECTED"` and asserts that the dependent is blocked. All 10 tests in the file passed this session. The test drives the helper functions directly, not an end-to-end run producing `REJECTED`. | [`test_g14_scheduler.py`](../../../../../tests/behaviors/runtime_run_authority/test_g14_scheduler.py) | **executed** (`uv run pytest …/test_g14_scheduler.py -q`: `10 passed`) |
| F5 | **Nothing produces a custom label.** On success, `_execute_deterministic` hard-codes `"outcome": "COMPLETE"` (detail and journal front matter). On failure it sets `status: "failed"` without writing an `outcome` to the goal detail; the journal front matter says `FAILED`. `_execute_goal` does no domain work: it writes placeholder evidence `gigai-offline-ok:<goal_id>` and returns only an evidence reference, with no label. | [`run.py:3345-3375`](../../../../../src/gigai/run.py), [`run.py:3546-3561`](../../../../../src/gigai/run.py) | read |
| F6 | The specialized single-goal paths also emit only `COMPLETE`/`FAILED`: interview (`run.py:1218`), tailor (`run.py:1393`) and proposal (`run.py:1413`, `run.py:1520`). | [`run.py`](../../../../../src/gigai/run.py) | read |
| F7 | Scout's compiler declares `"outcomes": ["COMPLETE"]` on every goal it emits. Even with a producer, F2 would reject any edge naming another label until the compiler declares it. | [`scout_materialization.py:276`](../../../../../src/gigai/scout_materialization.py) | read |
| F8 | **A typed domain decision already exists, but it doesn't reach the graph.** `find-jobs`'s real output is a discovery packet that arrives through the external-recording protocol, not the G14 scheduler. The packet carries `outcome ∈ {matches, partial, no_match}` with consistency checks: `matches` requires a shortlist and `no_match` forbids one. The external-recording module reads only graph identity (`_graph_context`) and never consults edges or `on_outcomes`. Its journal outcomes are `RECORDED` and `succeeded`/`cancelled`/`interrupted`. The packet labels are also lower-case, which doesn't match F1's pattern. | [`discovery.py:434-482`](../../../../../src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000074/discovery.py), [`external_recording.py:1468-1491`](../../../../../src/gigai/external_recording.py), [`external_recording.py:755`](../../../../../src/gigai/external_recording.py) | read (no `on_outcomes`/`edges` match in `external_recording.py`) |
| F9 | **There are no branch-not-taken semantics.** When a source completes with outcome X, the target of every outgoing edge that doesn't list X is recorded as `blocked` (`blocked_by_predecessor`). `_terminal_status` then returns `blocked` for the run, because any blocked goal means blocked. So a correctly taken exclusive branch would finish the run as `blocked`. The run-details goal-status enum (`pending … blocked, cancelled`) has no "not taken" or "skipped" state. | [`run.py:3288-3306`](../../../../../src/gigai/run.py) (blocked loop), [`run.py:3491-3496`](../../../../../src/gigai/run.py) (`_terminal_status`), [`run-details.schema.json:139`](../../../../../src/gigai/schemas/run-details.schema.json) | read |
| F10 | **There are no OR-joins.** Because `_ready_goals` requires *all* incoming edges to be satisfied (F3), a node that two alternative branches both lead into can never become ready once only one branch runs. It would be blocked instead. | [`run.py:3480-3485`](../../../../../src/gigai/run.py) | read (not exercised by the executed tests) |

Finding F9 is established by reading the control flow. No test in this
session ran a branching graph end to end.

## What this means for S12

The task 1 mapping asked whether decision nodes can build on an existing
capability. **The routing half can.** F1–F4 show that labels are declared,
validated and routed today, so S12 doesn't need a new graph vocabulary or a
new router.

**The producing half can't.** F5–F8 show that there's no path from a goal's
actual work to a graph outcome. **The branch semantics need a decision**
(F9–F10) before any exclusive branch could finish a run as `succeeded`.

F8 matters most for Scout. `find-jobs` already makes a typed three-way
decision, `matches` / `partial` / `no_match`, and validates it for internal
consistency. It is a bounded, typed decision boundary of the kind the
[Jev/TypeSafe research](jev-typesafe-ai-structured-decisions-research.md)
describes, and it already exists in Scout's domain. It just isn't connected
to graph routing. The smallest honest design reuses this decision rather
than inventing a new one.

## Proposed design (for review; nothing here is authorized)

### Where the decision sits

Here is an illustrative multi-node `find-jobs`. Node names, contracts and
executors are still open, per S12 conversation note 4:

```text
acquire ──POSTINGS_FOUND──▶ extract-requirements ──COMPLETE──▶ assess ──MATCHES──▶ present-shortlist
   │                                                             ├──PARTIAL──▶ present-partial (asks questions)
   └──NO_POSTINGS──▶ (terminal: report empty acquisition)        └──NO_MATCH──▶ present-exclusions
```

- **`acquire`** is a mechanical decision about whether anything was found.
  It keeps acquisition visible before analysis, as S12 conversation note 4
  requires: its evidence is the saved posting snapshots, independent of any
  assessment.
- **`assess`** is the one semantic decision node. Its labels are the
  packet's existing `outcome` values, upper-cased to fit F1's pattern
  (`MATCHES` / `PARTIAL` / `NO_MATCH`). This reuses F8's decision instead of
  introducing `SUFFICIENT_EVIDENCE` / `NEEDS_MORE_EVIDENCE`, which the task 1
  mapping only suggested as examples.
- **Abstention must be an explicit label, not a failure.** If the assessor
  can't decide (for example, because evidence is missing), it should record
  something like `NEEDS_OPERATOR_INPUT`. That label can route to an ordinary
  **automatic** node that presents the open questions and ends the
  traversal. Presenting questions doesn't require the node itself to be
  operator-gated (`activation != "automatic"`), so
  `_validate_scheduler_policy`'s rejection of operator-gated goals doesn't
  block this. Operator-gated goals would only be needed if the traversal
  had to *pause and resume* on the operator's answer within the same run.
  That is a separate choice (D6), not a prerequisite.

### What a decision record must carry

Drawn from the Jev caution that valid JSON is not correctness and a label is
not authorization:

1. **Label:** one of the goal's declared `outcomes`. The runner rejects
   anything else rather than coercing it.
2. **Evidence refs:** the artifacts the label rests on, through the goal's
   existing `verification.required_evidence` (per node, not the blanket
   `find-jobs-completion` used today).
3. **Producer identity:** model, deterministic rule, wrapper or grader. This
   keeps grader-supplied evidence from being presented as native model
   output.
4. **Uncertainty, where the producer supplies it**, recorded as a signal
   rather than a correctness claim. Routing uses the label only, never a
   probability threshold hidden in the runner.
5. **No effect authority:** routing to a node doesn't authorize that node's
   effects. Effects remain governed by the goal's own `effects` and gates.

The journal already has fields for two of these. Each `goal_completed`
transition carries `outcome` and `evidence` in its front matter
([`run.py:3564-3586`](../../../../../src/gigai/run.py)). The rest is not in
place:

- **Producer identity:** `_goal_front_matter` hard-codes
  `actor: {kind: "gigai", id: "deterministic"}`, so there is no way to
  record a model, wrapper or grader as the source of a label.
- **Label validation:** nothing checks a recorded outcome against
  `goal.outcomes` at run time. The validator only checks edges when the
  graph is authored (F2).
- **Uncertainty:** there is no field for it.
- **Branch semantics:** see F9 and F10.
- **Evidence:** per-node evidence would need real producers in place of
  the placeholder `gigai-offline-ok` evidence (F5).

## Candidate system-behavior changes (input to task 4)

These are recorded so task 4 can review them with evidence. Their
implementation location (GigAI runner, a Scout-side adapter, or elsewhere) is
deliberately left open, as S12 requires.

**They are not a list of required work.** Some are alternatives to each
other, some apply only to certain graph shapes, and one is optional. The
"Kind" column says which.

| ID | Change | Kind | Evidence for need |
| --- | --- | --- | --- |
| D1 | Let a goal's execution on the scheduler path return an outcome label, plus producer identity and evidence. The runner records the label only if it is in `goal.outcomes`, and otherwise fails the goal. | **Producer, option A.** Needed if decisions are made on the scheduler path. | F5, F6 |
| D3 | Bridge a recorded domain decision (the discovery packet's `outcome`) into a graph outcome, which connects external recording to routing. | **Producer, option B.** An alternative to D1 for `find-jobs`, because its real output arrives through external recording (F8). It must carry the same guarantees as D1: the bridged label is checked against the target goal's declared `outcomes` and undeclared labels are rejected, never coerced (for example, lower-case `matches` must map explicitly, not implicitly); producer identity (external agent, wrapper or grader) and the packet's evidence refs are preserved into the goal record, not replaced by a generic actor. One producer is enough, but only if it has these guarantees. | F8 |
| D2 | Let compiled graphs declare more than `COMPLETE`, starting with Scout's compiler for `find-jobs`. | Needed for any typed branching, since the validator rejects undeclared labels on edges. | F2, F7 |
| D4 | Add branch-not-taken semantics: a status or reason separate from `blocked`, so a run whose exclusive branch was correctly skipped can finish `succeeded`. | Needed **only if** the graph uses exclusive branches. A graph that routes every label to one node that reads the label wouldn't need it. | F9 |
| D5 | **Either** support OR-joins **or** constrain decision graphs to have no merge after an exclusive branch. | A choice between two alternatives, and only relevant with exclusive branches. | F10 |
| D6 | Allow operator-gated goals. | **Optional.** Only needed if the traversal must pause for an operator answer and resume in the same run. An automatic node can present questions without it (see "Where the decision sits"). | `_validate_scheduler_policy` ([`run.py:3438`](../../../../../src/gigai/run.py)) |

**Minimum combination for the illustrative graph:** one producer (D1 or
D3, with D1's validation and identity guarantees), plus D2. Add D4 with one side of D5 only if exclusive branches are
kept. D6 isn't needed. This minimum is inferred from reading the code, not
tested.

## What this does not establish

- It does not show that any combination of D1–D6 is sufficient, and it
  does not claim that all of them are necessary. They include alternatives
  (D1/D3, and the two sides of D5), shape-dependent items (D4, D5) and one
  optional item (D6).
- It does not test end to end that a branching graph runs. F9 and F10 come
  from reading the source, not from running it.
- It does not claim the discovery packet's `outcome` is a *correct* decision
  on real postings. It is internally consistency-checked (F8), which is not
  an accuracy claim.
- It does not adopt Jev or any provider. It does not specify the operator
  audit view.
- It says nothing about task 3 (Orca), which remains gated.

## Related work

- [S12 task 1 mapping](S12-scout-graph-mapping.md): the open question this
  resolves.
- [S12 ticket](../S12-gig-graph-traversal-and-auditable-execution.md).
- [Jev/TypeSafe structured-decision research](jev-typesafe-ai-structured-decisions-research.md).
- [S15: goal and tool-unit composition](../S15-goal-and-tool-unit-composition-research.md).

No implementation, schema change or live experiment follows from this
document.

## Revision notes (same-day correction after operator review)

1. **"All of D1–D6 are necessary" was an overstatement.** They include
   alternatives (D1 or D3 as producer, and the two sides of D5), items that
   depend on graph shape (D4, D5) and an optional item (D6). The table now
   labels each one and states the minimum combination.
2. **Abstention does not require operator-gated goals.** An automatic node
   can present questions and end the traversal. Operator gating is needed
   only for pause-and-resume within one run. The design and D6 are
   corrected.
3. **"Only the body text and the hard-coded value would need to change" is
   withdrawn.** Producer identity is hard-coded (`_goal_front_matter`),
   labels aren't validated at run time, there is no uncertainty field, the
   evidence is a placeholder, and branch semantics are missing.
4. **D3 gets D1's guarantees (second review).** The earlier wording, "one
   is enough to produce a label", left out the requirements a bridge must
   meet. A D3 bridge must:
   - validate the label against the target goal's declared `outcomes`;
   - reject undeclared labels instead of coercing them;
   - preserve producer identity and the packet's evidence refs.

   Without all three, it doesn't count as a producer.

# I-1 · find-jobs:functional:1 graph authoring — DONE

## State
Complete. Both owned files edited/created; focused acceptance test and the
existing `scout_materialization` regression tests all pass.

## Files touched (owned only)
- `src/gigai/scout_materialization.py` — added the sealed graph.
- `tests/behaviors/scout_find_jobs/test_integration_graph.py` — new, acceptance test.

## Design choices
- **Descriptor `graph_id` string**: the doc's `find-jobs:functional:1` name
  cannot be a literal descriptor `graph_id` — `gig-graph-set.schema.json`'s
  `selector` pattern is `^[a-z][a-z0-9-]{0,63}$` (no colons). Chose
  `find-jobs-functional` as the descriptor/selector string, distinct from the
  historical inert `find-jobs` selector in `SCOUT_GRAPHS` (which is untouched
  and stays byte-identical). Both selectors now coexist in the same Graph Set.
- **Three goals**: `acquire`/`assess`/`present` slugs (matching
  `NodeContext.goal_slug` values used in `test_contracts.py`), each
  `executor.kind = local_capability` bound to
  `ACQUIRE_CAPABILITY`/`ASSESS_CAPABILITY`/`PRESENT_CAPABILITY` from
  `scout_find_jobs_contracts.py`, effects = `sorted(ACQUIRE_DECLARED_EFFECTS)`
  etc. (the contracts module defines these as `frozenset`s, not sorted lists,
  so I sort at construction time per the task's "sorted lists" instruction).
- **Edges**: exactly two automatic `dependency` edges on `COMPLETE`:
  acquire→assess, assess→present. No branch/recovery/custom outcome.
  `max_parallel_goals: 1`, `failure_policy: "fail_gig"` (run.py:3438-3460's
  G14 requirements — I did not touch run.py; I-2 still owns widening the
  scheduler's `_validate_scheduler_policy` capability/effect allowlist).
- **Terminal goal completion evidence**: `validate_goal_graph`'s
  `incomplete_terminal_evidence` check requires the terminal goal
  (`present`) to require ALL of `required_completion_evidence`
  (`acquire-completion`, `assess-completion`, `present-completion`), not
  just its own — discovered by running the semantic validator, not from the
  design doc, which was silent on this. Non-terminal goals require only
  their own completion evidence.
- **Budgets**: the shared single-goal `_BUDGET` (max_cost 0.50, 1 model call)
  can't cover 3 goals without tripping `validate_goal_graph`'s
  `impossible_budget` check (sum of per-goal costs/calls > aggregate). Added
  two new module constants: `_FIND_JOBS_FUNCTIONAL_GOAL_BUDGET` (per-goal,
  0.16 cost / 1 model call each) and `_FIND_JOBS_FUNCTIONAL_AGGREGATE_BUDGET`
  (0.50 cost / 3 model calls) so 3×0.16=0.48 ≤ 0.50 and 3×1=3 ≤ 3.
- **Widened ceiling — graph-set `shared_policy` only**: `graph_set.py:238-262`
  checks each descriptor's `effect_policy`/`capability_requirements`/
  `provider_eligibility`/`budget` are subsets of `shared_policy` (there is
  only one Gig-wide `shared_policy`, not a per-graph one). Widened
  `shared_policy.effects` to add `network_read`/`credential_use`,
  `required_capability_ids` to add the three `scout.find_jobs.*`
  capabilities, `provider_eligibility.providers` to add `ollama_local`/
  `codex_cli`/`openrouter_api`, and `budget` to the max of the old `_BUDGET`
  and the new aggregate budget field-by-field. The other 6 descriptors keep
  their own per-descriptor ceiling (`["write_workpad"]`/`["gigai.offline"]`/
  `["deterministic"]`/`_BUDGET`) completely unchanged — proven unwidened by
  `test_find_jobs_functional_descriptor_widens_only_this_graph`.
- **Byte-identical proof**: `test_existing_scout_graphs_are_byte_identical_to_pre_change_output`
  loads the pre-change `scout_materialization.py` via `git show HEAD:...`
  (HEAD = the commit that predates every wave-1b worker's edits) into an
  isolated module under a distinct `sys.modules` name, freezes
  `datetime.now` and feeds both the old and new `_compiled_snapshot` the
  identical pre-generated UUID sequence, then diffs every compiled artifact
  key that exists in both. All 6 existing per-graph files (goal-graph.json,
  5 contract jsons, review-contract.json, goal-contract.md) are byte-for-byte
  identical; only `compiled/first-graph-set-definition.json` differs (new
  7th descriptor appended + widened `shared_policy`), and the first 6
  descriptors inside that file are asserted equal to the pre-change ones.
- **`goal_ids`**: the 3 new goal IDs are appended to `_compiled_snapshot`'s
  returned `goal_ids` tuple, which `materialize_scout_candidate` feeds into
  `prepared_scout_crud_manifest` — kept consistent so the capability manifest
  scope covers the new goals too.

## Test output
```
$ uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_integration_graph.py -v
tests/behaviors/scout_find_jobs/test_integration_graph.py::test_find_jobs_functional_goal_graph_is_schema_and_semantically_valid PASSED
tests/behaviors/scout_find_jobs/test_integration_graph.py::test_find_jobs_functional_descriptor_widens_only_this_graph PASSED
tests/behaviors/scout_find_jobs/test_integration_graph.py::test_graph_set_validator_accepts_the_widened_definition PASSED
tests/behaviors/scout_find_jobs/test_integration_graph.py::test_new_goal_ids_are_appended_for_the_capability_manifest PASSED
tests/behaviors/scout_find_jobs/test_integration_graph.py::test_existing_scout_graphs_are_byte_identical_to_pre_change_output PASSED
5 passed in 0.28s
```

Also ran the full `scout_find_jobs` directory (includes I-0's `test_contracts.py`,
248 tests) to confirm no cross-contamination in the shared conftest:
```
$ uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/ -q
248 passed in 12.84s
```

Focused `scout_materialization` regression sweep (`grep -rl scout_materialization tests/behaviors`):
```
$ uv run --locked --extra test pytest \
    tests/behaviors/scout_proposals_tools/test_scout05_materialization.py \
    tests/behaviors/scout_discovery/test_scout07_inventory_members.py \
    tests/behaviors/scout_tracking_reporting/test_scout_r5_transfer_corrections.py \
    tests/behaviors/scout_research/test_scout06_source_contract.py \
    tests/behaviors/scout_research/test_scout06_legacy_research_reuse.py \
    -q
43 passed, 1 warning in 118.78s
```
(warning is a pre-existing `zipfile` duplicate-name UserWarning in
`test_scout_r5_transfer_corrections.py`, unrelated to this change.)

## READ vs EXECUTED
**READ:** `src/gigai/scout_find_jobs_contracts.py` (full, both pages);
`docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-amendment-02-find-jobs-functional.md`
(full); `docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md`
(lines 55-98); `src/gigai/scout_materialization.py` (full, before and after
edits); `src/gigai/scout_template.py` (lines 100-199, `SCOUT_GRAPHS`/
`SCOUT_OPERATION_GRAPHS`); `src/gigai/graph_set.py` (validator + schema-kind
mapping + `_budget_within`); `src/gigai/schemas/goal-graph.schema.json` (full);
`src/gigai/schemas/gig-graph-set.schema.json` (full);
`src/gigai/schemas/common.schema.json` ($defs snippets); `src/gigai/run.py`
(lines 3420-3489, `_validate_scheduler_policy`/`_ready_goals`, read-only —
confirmed I-2 still owns this, did not edit); `src/gigai/validators.py`
(lines 542-682 `validate_goal_graph`, and the `impossible_budget`/
`incomplete_terminal_evidence` checks around 950-1065); `src/gigai/canonical.py`
(`EntityPrefix` enum); existing test patterns in
`tests/behaviors/scout_proposals_tools/test_scout05_first_proposal.py` and
`test_scout02_independent.py` (graph-set fixture/validate_graph_set usage
patterns); `tests/behaviors/scout_find_jobs/test_contracts.py` (goal_slug
values); `tests/behaviors/scout_find_jobs/conftest.py`.

**EXECUTED:** `git status`/`git show HEAD:...` (read-only, no stash/reset/
clean/add/commit); `uv run python3` one-off scripts in the scratchpad
directory to compute baseline digests and confirm the byte-identical
comparison approach before encoding it as a test; `pytest` runs listed above
(all via `MockTransport`-free unit-level calls — no network/provider/model
calls, no live run). No edits outside the two owned files.

## Left for other packets
- I-2 (`run.py`, `graph_node_registry.py`) still needs to widen
  `_validate_scheduler_policy`'s allowed capability set beyond
  `{"gigai.offline", "gigai.deterministic"}` and its `effects != ["write_workpad"]`
  exact-match check, to actually run this graph through G14. This packet
  proves the graph and graph-set validators accept the sealed
  `find-jobs-functional` descriptor; it does not touch the scheduler.
- I-3 will bind real A/B/C callables into the registry after A-6/B-2/C-2 land.

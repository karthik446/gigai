# G14 requirement-to-test matrix

| Requirement | Evidence |
|---|---|
| Every sealed Goal is materialized and scheduled serially | `test_sequential_scheduler_completes_every_goal_in_dependency_order` |
| Entry/dependency ordering and ordered handoffs | same test; journal handoff filenames |
| Join waits for all exact predecessors and stable critical path | `test_join_waits_for_all_exact_predecessors_and_critical_path_is_stable` |
| Unsupported parallel policy fails before scheduling | `test_unsupported_parallel_policy_fails_before_scheduling` |
| Unlisted terminal outcome blocks dependent | `test_terminal_unlisted_outcome_blocks_dependent` |
| G13 interruption and no-target-delta behavior | `tests/test_g13_run.py` interruption and success tests |
| Installed artifact behavior | `tools/verify_installed_g14.py` |
| Schema/vector preservation | existing schema and canonical suites in the full matrix |

Unsupported-policy, join, tamper, and malformed-evidence fixtures remain the
next expansion of the G14 regression corpus if the graph surface grows beyond
the shipped two-node fixture.

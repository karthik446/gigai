# G14 requirement-to-test matrix

| Requirement | Evidence |
|---|---|
| Every sealed Goal is materialized and scheduled serially | `test_sequential_scheduler_completes_every_goal_in_dependency_order` |
| Entry/dependency ordering and ordered handoffs | same test; journal handoff filenames |
| Join waits for all exact predecessors and stable critical path | `test_join_waits_for_all_exact_predecessors_and_critical_path_is_stable` |
| Three-Goal multi-entry join executes in canonical order | `test_scheduler_executes_three_goal_join_in_canonical_order` |
| Unsupported parallel policy fails before scheduling | `test_unsupported_parallel_policy_fails_before_scheduling` |
| Unlisted terminal outcome blocks dependent | `test_terminal_unlisted_outcome_blocks_dependent` |
| Non-entry orphan is not vacuously ready | `test_non_entry_orphan_never_becomes_ready` |
| Failed predecessor takes terminal precedence | `test_failed_goal_takes_precedence_over_blocked_dependents` |
| Gate/recovery rejection and sealed Graph tamper | `test_operator_gate_and_recovery_policy_reject_before_scheduling`, `test_tampered_sealed_run_graph_fails_before_goal_execution` |
| G13 interruption and no-target-delta behavior | `tests/test_g13_run.py` interruption and success tests |
| Installed artifact behavior | `tools/verify_installed_g14.py` |
| Schema/vector preservation | existing schema and canonical suites in the full matrix |

Malformed-evidence and adversarial executor fixtures remain follow-up coverage
for the later capability/tool goals; G14's executor is fixed to the installed
deterministic capability and has no provider, shell, or target-write surface.

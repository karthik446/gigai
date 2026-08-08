# G16 requirement-to-test matrix

| Requirement | Evidence |
|---|---|
| 17-resource additive amendment preserves the G15 baseline | `test_g16_additive_schema_inventory_preserves_g15_baseline`, `research/contract_spike/tests/test_schemas.py`, `tools/verify_installed_schemas.py` |
| Sealed Run is required before loop launch, including identity matching | `test_all_profiles_materialize_replayable_complete_loop`, `test_loop_requires_a_sealed_run`, `_sealed_run` fixture |
| Ordered loop lifecycle and journal handoffs | `test_all_profiles_materialize_replayable_complete_loop` (10 ordered handoffs) |
| Five deterministic domain profiles with per-Finding evidence | parametrized profile test and `corpus-manifest.json` |
| Clarification blocks before addressing | `test_clarification_feedback_blocks_before_addressing` |
| Deferred feedback remains distinct and blocks addressing | `test_deferred_feedback_is_distinct_and_blocks_addressing` |
| Unresolved disagreement records adjudication and blocks before addressing | `test_unresolved_disagreement_blocks_before_addressing` |
| Cycle cap blocks without a successful address/report | `test_cycle_limit_blocks_without_successful_address` |
| Partial address cannot close | `test_partial_address_blocks_closure` |
| Tampered Bundle, Report, or addressed artifact fails closed before a repeated loop | `test_divergent_bundle_fails_closed_before_loop`, `test_report_tampering_fails_before_a_repeated_loop_starts`, `test_addressed_artifact_tampering_fails_before_a_repeated_loop_starts` |
| Missing references and invented citations fail closed | `test_missing_reference_and_invented_citation_fail_closed` |
| Addressed-artifact parent mismatch fails closed | `test_addressed_artifact_parent_mismatch_fails_closed` |
| Invalid and malformed loop state is rejected | `test_review_loop_rejects_skipped_state_transition`, `test_malformed_loop_state_fails_closed` |
| Closure replays Report, Bundle, Finding, Feedback, and addressed bytes | terminal replay in `run_review_loop`, `test_all_profiles_materialize_replayable_complete_loop` |
| Accepted Finding reaches resolved only after a complete address | `test_all_profiles_materialize_replayable_complete_loop` |
| Replay order is stable and prior artifacts remain | `test_repeated_runs_preserve_profile_and_stage_order` |
| No provider/network/subprocess effects | `test_review_loop_effect_boundary_has_no_network_or_subprocess_imports` and installed verifier |
| Fresh wheel replay | `tools/verify_installed_g16.py` |
| Schema and canonical contract validity | `research/contract_spike/tests/test_schemas.py`, focused G16 suite |

The adversarial boundary is enforced by the existing offline process harness
and static import checks; G18 provider effects and G19 target effects remain
intentionally absent.

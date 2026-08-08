# G16 requirement-to-test matrix

| Requirement | Evidence |
|---|---|
| 17-resource additive amendment preserves the G15 baseline | `test_g16_additive_schema_inventory_preserves_g15_baseline`, `research/contract_spike/tests/test_schemas.py`, `tools/verify_installed_schemas.py` |
| Sealed Run is required before loop launch | `test_all_profiles_materialize_replayable_complete_loop`, `_sealed_run` fixture |
| Ordered loop lifecycle and journal handoffs | `test_all_profiles_materialize_replayable_complete_loop` (10 ordered handoffs) |
| Five deterministic domain profiles | parametrized profile test and `corpus-manifest.json` |
| Clarification blocks before addressing | `test_clarification_feedback_blocks_before_addressing` |
| Unresolved disagreement blocks before addressing | `test_unresolved_disagreement_blocks_before_addressing` |
| Cycle cap blocks without a successful address | `test_cycle_limit_blocks_without_successful_address` |
| Partial address cannot close | `test_partial_address_blocks_closure` |
| Tampered Bundle fails closed | `test_divergent_bundle_fails_closed_before_loop` |
| Addressed-artifact parent mismatch fails closed | `test_addressed_artifact_parent_mismatch_fails_closed` |
| Invalid loop transition is rejected | `test_review_loop_rejects_skipped_state_transition` |
| Replay order is stable and prior artifacts remain | `test_repeated_runs_preserve_profile_and_stage_order` |
| No provider/network/subprocess effects | `test_review_loop_effect_boundary_has_no_network_or_subprocess_imports` and installed verifier |
| Fresh wheel replay | `tools/verify_installed_g16.py` |
| Schema and canonical contract validity | `research/contract_spike/tests/test_schemas.py`, focused G16 suite |

The adversarial boundary is enforced by the existing offline process harness
and static import checks; G18 provider effects and G19 target effects remain
intentionally absent.

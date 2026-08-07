# G15 requirement-to-test matrix

This matrix is the deterministic Stage 1/2 substrate record. G16-owned loop
execution is intentionally materialized but not executed here.

| Requirement | Evidence |
| --- | --- |
| Exact-byte Bundle materialization and replay | `test_bundle_materializes_and_replays_exact_bytes`, `test_bundle_refuses_invalid_digest_before_writing` |
| Missing, symlinked, stale, and mismatched references fail closed | `test_bundle_rejects_symlinked_reference_bytes`, bundle tamper assertions in `test_bundle_materializes_and_replays_exact_bytes` |
| Contract criteria and deterministic evaluator plan | `test_review_contract_validates_and_rejects_model_stage`, `test_review_contract_rejects_unknown_evaluator` |
| Findings cite real Bundle bytes | `test_findings_require_real_bundle_evidence_and_merge_provenance` |
| Finding lifecycle and terminal states | `test_finding_lifecycle_is_explicit_and_terminal_states_are_closed` |
| Duplicate/disagreement merge and stable identity | `test_findings_require_real_bundle_evidence_and_merge_provenance` |
| Feedback, Adjudication, and Trace boundaries | `test_feedback_adjudication_and_trace_boundaries_are_validated` |
| Redacted, replayable reports | `test_report_replay_redacts_explicit_sentinels`, `test_report_generation_is_byte_stable` |
| Installed wheel replay | `tools/verify_installed_g15.py` |
| Schema inventory and preserved original resources | `test_g15_additive_schema_inventory_is_exact`, `verify_installed_schemas.py`, contract-spike schema tests |

The G15 deterministic tier does not invoke providers, install tools, mutate a
target, or execute G16 partial-address/cycle behavior.

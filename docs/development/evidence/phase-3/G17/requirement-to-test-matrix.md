# G17 requirement-to-test matrix

| Criterion | Evidence |
| --- | --- |
| 1. 17→19 additive schema amendment | `test_g17_additive_schema_inventory_and_baseline_hashes`; `verify_installed_schemas.py`; research contract schema suite |
| 2. Canonical manifest and Bundle linkage | `test_manifest_materializes_and_inspects_installable_state`; `test_capability_manifest_is_linked_by_the_g15_bundle` |
| 3. Six inspection states | `test_inspection_states_are_distinct_and_security_precedes_credentials`; `test_incompatible_and_malformed_manifest_findings_are_deterministic`; installed verifier |
| 4. Explicit stable options and no fallback | manifest semantic validation plus `test_incompatible_and_malformed_manifest_findings_are_deterministic` |
| 5. Pinned approval and fail-closed checks | `test_digest_drift_fails_before_tool_root_write`; `test_source_symlink_and_target_symlink_fail_closed`; refusal fixture |
| 6. Isolated installation and idempotency | `test_install_is_idempotent_and_records_exact_snapshots`; installed verifier |
| 7. Failure/interruption outcomes | `test_refusal_and_interruption_rollback_are_durable`; option-and-installation-state-table |
| 8. Per-Gig provenance | `test_per_gig_provenance_does_not_leak_between_roots`; provenance mutation |
| 9. Negative rejection classes | malformed, duplicate, invented alternative, incompatible, credential, security, digest, symlink, refusal, failure, interruption, rollback fixtures |
| 10. Adversarial effect boundary | `test_capability_module_has_no_effectful_imports`; target/effect sanitized manifest; no subprocess/network imports |
| 11. Mutation coverage | `mutation-report.md`; `tools/run_g17_mutation.py` catches six guards |
| 12. Fresh installed wheel | `tools/verify_installed_g17.py` and `tools/verify_installed_schemas.py` pass in a disposable Python 3.11 wheel environment |
| 13. Completion evidence | This matrix, corpus manifest, state table, mutation report, sanitized manifest, refusal/rollback records, audit, and handoff |

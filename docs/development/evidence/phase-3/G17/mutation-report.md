# G17 mutation report

`tools/run_g17_mutation.py` applies six mutations and requires the focused
tests to fail for each mutant. All six were caught:

| Guard | Regression test |
| --- | --- |
| Source digest check | `test_digest_drift_fails_before_tool_root_write` |
| Approval gate | `test_refusal_and_interruption_rollback_are_durable` |
| Path containment/symlink check | `test_source_symlink_and_target_symlink_fail_closed` |
| Before/after idempotency comparison | `test_install_is_idempotent_and_records_exact_snapshots` |
| Rollback path | `test_refusal_and_interruption_rollback_are_durable` |
| Per-Gig provenance | `test_install_is_idempotent_and_records_exact_snapshots` |

Mutation result: `caught G17 mutations` for all six guards.

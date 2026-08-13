# G26 Mutation Report

- Date: 2026-08-13
- Harness: `tools/run_g26_mutation.py`
- Result: `mutation_killed=9/9`

The harness copied the source tree into a disposable directory for each
mutation and ran one focused regression test against the mutant. Every mutant
caused its associated test to fail:

| Guard | Regression fixture |
| --- | --- |
| Wall-time deadline | `test_bounded_call_times_out_without_accepting_a_late_result` |
| Pre-call cancellation | `test_bounded_call_cancels_before_invocation` |
| In-flight cancellation | `test_bounded_call_cancels_while_provider_is_running` |
| Output-token budget | `test_bounded_call_rejects_request_over_token_budget` |
| Selected-reference filtering | `test_remote_builder_receives_only_selected_reference_content` |
| Interrupted research recovery | `test_interrupted_builder_recovery_terminalizes_without_retry` |
| Duplicate-build refusal | `test_completed_builder_cannot_be_run_again` |
| Unavailable-target terminal record | `test_unavailable_builder_target_writes_terminal_session` |
| Existing-proposal approval identity | `test_create_runs_model_facilitated_build_then_explicit_approval` |

No provider was contacted and no UAT data was used. The harness reports only
mutation names and pass/fail status; it does not capture prompts, references,
credentials, or model output.

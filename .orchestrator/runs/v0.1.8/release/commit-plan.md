# Commit plan — karthik446/gigai-v0.1.8

Ordered, disjoint commits covering every changed/deleted/untracked path in
`git status --porcelain --untracked-files=all` for the FINAL tree (post Scout-package
move, post CI/tooling fixes, post polish, post v0.1.9 docs, post `.orchestrator/` reorg).
Supersedes the pre-reorg plan this file replaces.

Coverage verified with `.orchestrator/runs/v0.1.8/release/verify_coverage.py`: **0 unassigned,
0 duplicates, all 715 entries accounted for** (see "Coverage verification" section at the end).

Note on drift: this tree had concurrent workers actively writing during this scan (a
`release-docs` worker and others). Two files appeared between the initial scan and the
final snapshot used below: `.orchestrator/runs/v0.1.8/workers/release-docs.txt` (a worker spec, folded
into Commit 13) and `src/gigai/catalog.py` (a one-line `CATALOG_REVISION = "v0.1.7"` ->
`"v0.1.8"` version-string bump, almost certainly the `release-docs` worker's version-bump
task in progress; given its own commit, 14, since it doesn't fit any lane in this task's
brief and the release-docs worker's own commit — if it makes one — should supersede this).

Where a file was touched by more than one logical change (e.g. a moved module later
edited by a feature or fix), it is listed once, in the LATEST commit that touches it,
with a note in that commit explaining why. No file appears under more than one commit.

## Commit 1: S11 behavior-test reorganization + aggregate test runner

**Message:**
```
test(s11): migrate to behavior-lane test packages and add the aggregate make test runner

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

**Files (342):**
- `CONTRIBUTING.md` (modify)
- `Makefile` (add)
- `docs/development/v0.1.8/evidence/S11-acquisition-mapping.json` (add)
- `docs/development/v0.1.8/evidence/S11-baseline-collection.json` (add)
- `docs/development/v0.1.8/evidence/S11-baseline-pilot-after.json` (add)
- `docs/development/v0.1.8/evidence/S11-baseline-pilot-before-full.json` (add)
- `docs/development/v0.1.8/evidence/S11-baseline-pilot-before.json` (add)
- `docs/development/v0.1.8/evidence/S11-ci-historical-run.json` (add)
- `docs/development/v0.1.8/evidence/S11-ci-runner-contract.json` (add)
- `docs/development/v0.1.8/evidence/S11-ci-selector-audit.json` (add)
- `docs/development/v0.1.8/evidence/S11-collection-after.json` (add)
- `docs/development/v0.1.8/evidence/S11-full-suite-after.json` (add)
- `docs/development/v0.1.8/evidence/S11-full-suite-baseline.json` (add)
- `docs/development/v0.1.8/evidence/S11-full-suite-environment.json` (add)
- `docs/development/v0.1.8/evidence/S11-full-suite-installed-selection.json` (add)
- `docs/development/v0.1.8/evidence/S11-full-suite-migration-map.json` (add)
- `docs/development/v0.1.8/evidence/S11-full-suite-reorganization.md` (add)
- `docs/development/v0.1.8/evidence/S11-groundwork.md` (add)
- `docs/development/v0.1.8/evidence/S11-lifecycle-uv-environment.json` (add)
- `docs/development/v0.1.8/evidence/S11-lifecycle-uv.json` (add)
- `docs/development/v0.1.8/evidence/S11-pilot-after-full.json` (add)
- `docs/development/v0.1.8/evidence/S11-test-inventory.json` (add)
- `docs/development/v0.1.8/evidence/S11-test-runner-and-ci-performance.md` (add)
- `docs/development/v0.1.8/evidence/S11-unit-marker-pilot.json` (add)
- `docs/development/v0.1.8/spikes/S11-behavior-based-test-organization.md` (modify)
- `pyproject.toml` (modify)
- `tests/behaviors/__init__.py` (add)
- `tests/behaviors/acquisition/__init__.py` (add)
- `tests/behaviors/acquisition/test_public_acquisition_lifecycle.py` (add)
- `tests/behaviors/acquisition/test_public_import_state.py` (add)
- `tests/behaviors/ci_tooling/__init__.py` (add)
- `tests/behaviors/ci_tooling/test_run_ci_tests_wheel_python.py` (add)
- `tests/behaviors/cli_surface/__init__.py` (add)
- `tests/behaviors/cli_surface/test_bug_002_cli_surface.py` (add)
- `tests/behaviors/cli_surface/test_bug_003_offline_surface.py` (add)
- `tests/behaviors/cli_surface/test_cli_and_scenario_harness.py` (add)
- `tests/behaviors/cli_surface/test_cli_invocation_argv.py` (add)
- `tests/behaviors/cli_surface/test_g22_cli_create.py` (add)
- `tests/behaviors/cli_surface/test_g26_cli_builder.py` (add)
- `tests/behaviors/cli_surface/test_g30_cli_adapters.py` (add)
- `tests/behaviors/cli_surface/test_setup_browser.py` (add)
- `tests/behaviors/cli_surface/test_setup_configuration_diagnostics.py` (add)
- `tests/behaviors/installed_release/__init__.py` (add)
- `tests/behaviors/installed_release/test_g03_installed_scenarios.py` (add)
- `tests/behaviors/installed_release/test_g04_installed_scenarios.py` (add)
- `tests/behaviors/installed_release/test_g05_installed_scenarios.py` (add)
- `tests/behaviors/installed_release/test_g06_installed_scenarios.py` (add)
- `tests/behaviors/installed_release/test_g07_installed_scenarios.py` (add)
- `tests/behaviors/installed_release/test_g08_installed_scenarios.py` (add)
- `tests/behaviors/installed_release/test_g10_phase1_audit.py` (add)
- `tests/behaviors/installed_release/test_g11_installed_scenarios.py` (add)
- `tests/behaviors/installed_release/test_g41_package_boundary.py` (add)
- `tests/behaviors/installed_release/test_release_check.py` (add)
- `tests/behaviors/installed_release/test_scout03_package_privacy.py` (add)
- `tests/behaviors/installed_release/test_scout_manifest_publication.py` (add)
- `tests/behaviors/integrity_canonical/__init__.py` (add)
- `tests/behaviors/integrity_canonical/test_canonical.py` (add)
- `tests/behaviors/integrity_canonical/test_canonical_ownership.py` (add)
- `tests/behaviors/integrity_state/__init__.py` (add)
- `tests/behaviors/integrity_state/test_bug_001_gigs_listing.py` (add)
- `tests/behaviors/integrity_state/test_index_projection.py` (add)
- `tests/behaviors/integrity_state/test_project_registry_and_target_binding.py` (add)
- `tests/behaviors/integrity_state/test_registry_v2_migration.py` (add)
- `tests/behaviors/integrity_state/test_workpad_private_git.py` (add)
- `tests/behaviors/research_spikes/__init__.py` (add)
- `tests/behaviors/research_spikes/test_s16_eval_methodology.py` (add)
- `tests/behaviors/research_spikes/test_s18_01_provider_contract.py` (add)
- `tests/behaviors/research_spikes/test_s18_02_cli_feasibility.py` (add)
- `tests/behaviors/research_spikes/test_s18_03_api_local_feasibility.py` (add)
- `tests/behaviors/research_spikes/test_s18_04_handoff_design.py` (add)
- `tests/behaviors/research_spikes/test_s18_05_provider_boundary.py` (add)
- `tests/behaviors/research_spikes/test_s22_01_interview_protocol.py` (add)
- `tests/behaviors/runtime_model_boundary/__init__.py` (add)
- `tests/behaviors/runtime_model_boundary/test_g18_model_exchange.py` (add)
- `tests/behaviors/runtime_model_boundary/test_g18_model_execution.py` (add)
- `tests/behaviors/runtime_model_boundary/test_g30_live_cli.py` (add)
- `tests/behaviors/runtime_model_boundary/test_g43_provider_review.py` (add)
- `tests/behaviors/runtime_model_boundary/test_g43_provider_run_status.py` (add)
- `tests/behaviors/runtime_model_boundary/test_g43_response_framing.py` (add)
- `tests/behaviors/runtime_model_boundary/test_g43_run_plan.py` (add)
- `tests/behaviors/runtime_model_boundary/test_g43_text_media.py` (add)
- `tests/behaviors/runtime_model_boundary/test_model_contracts.py` (add)
- `tests/behaviors/runtime_model_boundary/test_model_invocation_foundation.py` (add)
- `tests/behaviors/runtime_model_boundary/test_ollama_local_adapter.py` (add)
- `tests/behaviors/runtime_model_boundary/test_ollama_local_integration.py` (add)
- `tests/behaviors/runtime_model_boundary/test_runtime_comparison.py` (add)
- `tests/behaviors/runtime_run_authority/__init__.py` (add)
- `tests/behaviors/runtime_run_authority/test_g13_run.py` (add)
- `tests/behaviors/runtime_run_authority/test_g14_scheduler.py` (add)
- `tests/behaviors/runtime_run_authority/test_g15_review_substrate.py` (add)
- `tests/behaviors/runtime_run_authority/test_g16_review_loop.py` (add)
- `tests/behaviors/runtime_run_authority/test_g17_capabilities.py` (add)
- `tests/behaviors/runtime_run_authority/test_g19_target_effect.py` (add)
- `tests/behaviors/runtime_run_authority/test_g19_target_effect_contract.py` (add)
- `tests/behaviors/runtime_run_authority/test_g20_learning_contract.py` (add)
- `tests/behaviors/runtime_run_authority/test_g20_learning_runtime.py` (add)
- `tests/behaviors/runtime_run_authority/test_g21_boundaries.py` (add)
- `tests/behaviors/runtime_run_authority/test_g21_comparison.py` (add)
- `tests/behaviors/runtime_run_authority/test_g21_contract.py` (add)
- `tests/behaviors/runtime_run_authority/test_g21_occurrence.py` (add)
- `tests/behaviors/runtime_run_authority/test_g23_portability.py` (add)
- `tests/behaviors/runtime_run_authority/test_g27_discovery_contract.py` (add)
- `tests/behaviors/runtime_run_authority/test_g27_runtime.py` (add)
- `tests/behaviors/runtime_run_authority/test_g28_evaluation.py` (add)
- `tests/behaviors/runtime_run_authority/test_g28_roles.py` (add)
- `tests/behaviors/runtime_run_authority/test_g28_setup_create.py` (add)
- `tests/behaviors/runtime_run_authority/test_g40_runtime.py` (add)
- `tests/behaviors/runtime_run_authority/test_g42_catalog.py` (add)
- `tests/behaviors/runtime_run_authority/test_journal_locking_recovery.py` (add)
- `tests/behaviors/runtime_run_authority/test_jsl_closeout_regressions.py` (add)
- `tests/behaviors/runtime_run_authority/test_timing_classification.py` (add)
- `tests/behaviors/scout_assessment/__init__.py` (add)
- `tests/behaviors/scout_assessment/test_g22_approval.py` (add)
- `tests/behaviors/scout_assessment/test_g22_http_approval.py` (add)
- `tests/behaviors/scout_assessment/test_g22_lifecycle.py` (add)
- `tests/behaviors/scout_assessment/test_g22_proposal_interview.py` (add)
- `tests/behaviors/scout_assessment/test_g22_proposal_interview_contract.py` (add)
- `tests/behaviors/scout_assessment/test_g22_question_quality.py` (add)
- `tests/behaviors/scout_assessment/test_g26_builder.py` (add)
- `tests/behaviors/scout_assessment/test_g26_builder_contract.py` (add)
- `tests/behaviors/scout_assessment/test_g26_model_call.py` (add)
- `tests/behaviors/scout_assessment/test_g26_model_discovery.py` (add)
- `tests/behaviors/scout_assessment/test_g26_review_actions.py` (add)
- `tests/behaviors/scout_discovery/__init__.py` (add)
- `tests/behaviors/scout_discovery/test_scout07_discovery_bridge.py` (add)
- `tests/behaviors/scout_discovery/test_scout07_discovery_packet.py` (add)
- `tests/behaviors/scout_discovery/test_scout07_discovery_run_flow.py` (add)
- `tests/behaviors/scout_discovery/test_scout07_inventory_members.py` (add)
- `tests/behaviors/scout_discovery/test_scout07_posting_inputs.py` (add)
- `tests/behaviors/scout_proposals_tools/__init__.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout02_acceptance_negative_paths.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout02_graph_set_flow.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout02_independent.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout02_review_corrections.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout03_c1_acceptance.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout03_c3_inputs.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout03_c3_tools.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout03_native_records.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout03_private_records.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout04_external_recording.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout04_input_integration.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout05_bundled_tools.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout05_capability_cli.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout05_capability_prepare_cli.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout05_capability_review.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout05_capability_successor.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout05_first_proposal.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout05_first_version_tools.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout05_init.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout05_init_corrections.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout05_init_recovery.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout05_materialization.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout05_reviewed_manifest_guard.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout05_source_bundle.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout05_tool_crud.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout05_tool_scaffold.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout_proposal_execution.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout_proposal_records.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout_proposal_run.py` (add)
- `tests/behaviors/scout_proposals_tools/test_scout_proposals.py` (add)
- `tests/behaviors/scout_request_tailoring/__init__.py` (add)
- `tests/behaviors/scout_request_tailoring/test_scout08_checks.py` (add)
- `tests/behaviors/scout_request_tailoring/test_scout08_nested_request.py` (add)
- `tests/behaviors/scout_request_tailoring/test_scout08_request_framing.py` (add)
- `tests/behaviors/scout_request_tailoring/test_scout08_run_integration.py` (add)
- `tests/behaviors/scout_request_tailoring/test_scout08_tailoring_packet.py` (add)
- `tests/behaviors/scout_research/__init__.py` (add)
- `tests/behaviors/scout_research/test_scout06_external_domain_channel.py` (add)
- `tests/behaviors/scout_research/test_scout06_iterative_research_reuse.py` (add)
- `tests/behaviors/scout_research/test_scout06_legacy_research_reuse.py` (add)
- `tests/behaviors/scout_research/test_scout06_protocol_replay.py` (add)
- `tests/behaviors/scout_research/test_scout06_research_bridge.py` (add)
- `tests/behaviors/scout_research/test_scout06_research_input_integration.py` (add)
- `tests/behaviors/scout_research/test_scout06_research_inputs.py` (add)
- `tests/behaviors/scout_research/test_scout06_research_packet.py` (add)
- `tests/behaviors/scout_research/test_scout06_research_run_adversarial.py` (add)
- `tests/behaviors/scout_research/test_scout06_research_run_flow.py` (add)
- `tests/behaviors/scout_research/test_scout06_role_request_input.py` (add)
- `tests/behaviors/scout_research/test_scout06_source_contract.py` (add)
- `tests/behaviors/scout_tracking_reporting/__init__.py` (add)
- `tests/behaviors/scout_tracking_reporting/test_scout09_application_events.py` (add)
- `tests/behaviors/scout_tracking_reporting/test_scout_r2_document_records.py` (add)
- `tests/behaviors/scout_tracking_reporting/test_scout_r2_tailor.py` (add)
- `tests/behaviors/scout_tracking_reporting/test_scout_r3_report.py` (add)
- `tests/behaviors/scout_tracking_reporting/test_scout_r4_journey.py` (add)
- `tests/behaviors/scout_tracking_reporting/test_scout_r4_review_corrections.py` (add)
- `tests/behaviors/scout_tracking_reporting/test_scout_r5_interview_transfer.py` (add)
- `tests/behaviors/scout_tracking_reporting/test_scout_r5_transfer_corrections.py` (add)
- `tests/behaviors/system_contracts/__init__.py` (add)
- `tests/behaviors/system_contracts/test_g07_contract_validators.py` (add)
- `tests/behaviors/system_contracts/test_g08_offline_create_lifecycle.py` (add)
- `tests/test_bug_001_gigs_listing.py` (delete)
- `tests/test_bug_002_cli_surface.py` (delete)
- `tests/test_bug_003_offline_surface.py` (delete)
- `tests/test_canonical.py` (delete)
- `tests/test_canonical_ownership.py` (delete)
- `tests/test_cli_and_scenario_harness.py` (delete)
- `tests/test_cli_invocation_argv.py` (delete)
- `tests/test_g03_installed_scenarios.py` (delete)
- `tests/test_g04_installed_scenarios.py` (delete)
- `tests/test_g05_installed_scenarios.py` (delete)
- `tests/test_g06_installed_scenarios.py` (delete)
- `tests/test_g07_contract_validators.py` (delete)
- `tests/test_g07_installed_scenarios.py` (delete)
- `tests/test_g08_installed_scenarios.py` (delete)
- `tests/test_g08_offline_create_lifecycle.py` (delete)
- `tests/test_g10_phase1_audit.py` (delete)
- `tests/test_g11_installed_scenarios.py` (delete)
- `tests/test_g13_run.py` (delete)
- `tests/test_g14_scheduler.py` (delete)
- `tests/test_g15_review_substrate.py` (delete)
- `tests/test_g16_review_loop.py` (delete)
- `tests/test_g17_capabilities.py` (delete)
- `tests/test_g18_model_exchange.py` (delete)
- `tests/test_g18_model_execution.py` (delete)
- `tests/test_g19_target_effect.py` (delete)
- `tests/test_g19_target_effect_contract.py` (delete)
- `tests/test_g20_learning_contract.py` (delete)
- `tests/test_g20_learning_runtime.py` (delete)
- `tests/test_g21_boundaries.py` (delete)
- `tests/test_g21_comparison.py` (delete)
- `tests/test_g21_contract.py` (delete)
- `tests/test_g21_occurrence.py` (delete)
- `tests/test_g22_approval.py` (delete)
- `tests/test_g22_cli_create.py` (delete)
- `tests/test_g22_http_approval.py` (delete)
- `tests/test_g22_lifecycle.py` (delete)
- `tests/test_g22_proposal_interview.py` (delete)
- `tests/test_g22_proposal_interview_contract.py` (delete)
- `tests/test_g22_question_quality.py` (delete)
- `tests/test_g23_portability.py` (delete)
- `tests/test_g26_builder.py` (delete)
- `tests/test_g26_builder_contract.py` (delete)
- `tests/test_g26_cli_builder.py` (delete)
- `tests/test_g26_model_call.py` (delete)
- `tests/test_g26_model_discovery.py` (delete)
- `tests/test_g26_review_actions.py` (delete)
- `tests/test_g27_discovery_contract.py` (delete)
- `tests/test_g27_runtime.py` (delete)
- `tests/test_g28_evaluation.py` (delete)
- `tests/test_g28_roles.py` (delete)
- `tests/test_g28_setup_create.py` (delete)
- `tests/test_g30_cli_adapters.py` (delete)
- `tests/test_g30_live_cli.py` (delete)
- `tests/test_g40_runtime.py` (delete)
- `tests/test_g41_package_boundary.py` (delete)
- `tests/test_g42_catalog.py` (delete)
- `tests/test_g43_provider_review.py` (delete)
- `tests/test_g43_provider_run_status.py` (delete)
- `tests/test_g43_response_framing.py` (delete)
- `tests/test_g43_run_plan.py` (delete)
- `tests/test_g43_text_media.py` (delete)
- `tests/test_index_projection.py` (delete)
- `tests/test_journal_locking_recovery.py` (delete)
- `tests/test_jsl_closeout_regressions.py` (delete)
- `tests/test_model_contracts.py` (delete)
- `tests/test_model_invocation_foundation.py` (delete)
- `tests/test_ollama_local_adapter.py` (delete)
- `tests/test_ollama_local_integration.py` (delete)
- `tests/test_project_registry_and_target_binding.py` (delete)
- `tests/test_registry_v2_migration.py` (delete)
- `tests/test_release_check.py` (delete)
- `tests/test_runtime_comparison.py` (delete)
- `tests/test_s16_eval_methodology.py` (delete)
- `tests/test_s18_01_provider_contract.py` (delete)
- `tests/test_s18_02_cli_feasibility.py` (delete)
- `tests/test_s18_03_api_local_feasibility.py` (delete)
- `tests/test_s18_04_handoff_design.py` (delete)
- `tests/test_s18_05_provider_boundary.py` (delete)
- `tests/test_s22_01_interview_protocol.py` (delete)
- `tests/test_scout02_acceptance_negative_paths.py` (delete)
- `tests/test_scout02_graph_set_flow.py` (delete)
- `tests/test_scout02_independent.py` (delete)
- `tests/test_scout02_review_corrections.py` (delete)
- `tests/test_scout03_c1_acceptance.py` (delete)
- `tests/test_scout03_c3_inputs.py` (delete)
- `tests/test_scout03_c3_tools.py` (delete)
- `tests/test_scout03_native_records.py` (delete)
- `tests/test_scout03_package_privacy.py` (delete)
- `tests/test_scout03_private_records.py` (delete)
- `tests/test_scout04_external_recording.py` (delete)
- `tests/test_scout04_input_integration.py` (delete)
- `tests/test_scout05_bundled_tools.py` (delete)
- `tests/test_scout05_capability_cli.py` (delete)
- `tests/test_scout05_capability_prepare_cli.py` (delete)
- `tests/test_scout05_capability_review.py` (delete)
- `tests/test_scout05_capability_successor.py` (delete)
- `tests/test_scout05_first_proposal.py` (delete)
- `tests/test_scout05_first_version_tools.py` (delete)
- `tests/test_scout05_init.py` (delete)
- `tests/test_scout05_init_corrections.py` (delete)
- `tests/test_scout05_init_recovery.py` (delete)
- `tests/test_scout05_materialization.py` (delete)
- `tests/test_scout05_reviewed_manifest_guard.py` (delete)
- `tests/test_scout05_source_bundle.py` (delete)
- `tests/test_scout05_tool_crud.py` (delete)
- `tests/test_scout05_tool_scaffold.py` (delete)
- `tests/test_scout06_external_domain_channel.py` (delete)
- `tests/test_scout06_iterative_research_reuse.py` (delete)
- `tests/test_scout06_legacy_research_reuse.py` (delete)
- `tests/test_scout06_protocol_replay.py` (delete)
- `tests/test_scout06_research_bridge.py` (delete)
- `tests/test_scout06_research_input_integration.py` (delete)
- `tests/test_scout06_research_inputs.py` (delete)
- `tests/test_scout06_research_packet.py` (delete)
- `tests/test_scout06_research_run_adversarial.py` (delete)
- `tests/test_scout06_research_run_flow.py` (delete)
- `tests/test_scout06_role_request_input.py` (delete)
- `tests/test_scout06_source_contract.py` (delete)
- `tests/test_scout07_discovery_bridge.py` (delete)
- `tests/test_scout07_discovery_packet.py` (delete)
- `tests/test_scout07_discovery_run_flow.py` (delete)
- `tests/test_scout07_inventory_members.py` (delete)
- `tests/test_scout07_posting_inputs.py` (delete)
- `tests/test_scout08_checks.py` (delete)
- `tests/test_scout08_nested_request.py` (delete)
- `tests/test_scout08_request_framing.py` (delete)
- `tests/test_scout08_run_integration.py` (delete)
- `tests/test_scout08_tailoring_packet.py` (delete)
- `tests/test_scout09_application_events.py` (delete)
- `tests/test_scout_acquisition_progress.py` (delete)
- `tests/test_scout_discovery_job.py` (delete)
- `tests/test_scout_manifest_publication.py` (delete)
- `tests/test_scout_proposal_execution.py` (delete)
- `tests/test_scout_proposal_records.py` (delete)
- `tests/test_scout_proposal_run.py` (delete)
- `tests/test_scout_proposals.py` (delete)
- `tests/test_scout_r2_document_records.py` (delete)
- `tests/test_scout_r2_tailor.py` (delete)
- `tests/test_scout_r3_report.py` (delete)
- `tests/test_scout_r4_journey.py` (delete)
- `tests/test_scout_r4_review_corrections.py` (delete)
- `tests/test_scout_r5_interview_transfer.py` (delete)
- `tests/test_scout_r5_transfer_corrections.py` (delete)
- `tests/test_setup_browser.py` (delete)
- `tests/test_setup_configuration_diagnostics.py` (delete)
- `tests/test_timing_classification.py` (delete)
- `tests/test_workpad_private_git.py` (delete)
- `tools/run_ci_tests.py` (add)
- `tools/s11_inventory.py` (add)
- `tools/s11_measure.py` (add)
- `uv.lock` (modify)

## Commit 2: v0.1.8 planning docs, roadmap, phase-2 tickets/evidence

**Message:**
```
docs(v0.1.8): update roadmap, spikes, phase-2 tickets/evidence and planning docs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

**Files (58):**
- `docs/development/followups/CONTRACT-01-handoff-frontmatter-alignment.md` (add)
- `docs/development/followups/CONTRACT-02-tailor-selection-write-validation.md` (add)
- `docs/development/followups/OCCURRENCE-01-unsupported-version-error.md` (add)
- `docs/development/followups/PRIVACY-01-v2-external-recording-guard.md` (add)
- `docs/development/v0.1.8/1.8-chat-09-21-26.md` (add)
- `docs/development/v0.1.8/HANDOFF-2026-09-22-spikes-s12-s15.md` (add)
- `docs/development/v0.1.8/HANDOFF-orca-orchestrator-2026-09-22.md` (add)
- `docs/development/v0.1.8/README.md` (modify)
- `docs/development/v0.1.8/directions/scout-search-first-local-applications.md` (modify)
- `docs/development/v0.1.8/phase-2/evidence/P2-AUD-01-baseline-and-invocation.md` (add)
- `docs/development/v0.1.8/phase-2/evidence/P2-AUD-01-claims.json` (add)
- `docs/development/v0.1.8/phase-2/evidence/P2-AUD-02-interface-ledger.json` (add)
- `docs/development/v0.1.8/phase-2/evidence/P2-AUD-02-scout-operation-map.md` (add)
- `docs/development/v0.1.8/phase-2/evidence/P2-EVAL-03-manifest.json` (add)
- `docs/development/v0.1.8/phase-2/evidence/P2-EVAL-03-pack-and-rubric.md` (add)
- `docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-amendment-01-scout-graph-proof.md` (add)
- `docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-amendment-02-find-jobs-functional.md` (add)
- `docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-dispatch-matrix.md` (add)
- `docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-interface-freeze.md` (add)
- `docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-ledger.json` (add)
- `docs/development/v0.1.8/phase-2/evidence/P3-scout-graph-integration-trace.md` (add)
- `docs/development/v0.1.8/phase-2/evidence/synthetic-evaluation/cases.json` (add)
- `docs/development/v0.1.8/phase-2/evidence/synthetic-evaluation/grader-self-checks.json` (add)
- `docs/development/v0.1.8/phase-2/tickets/P2-AUD-01-v017-baseline-and-s10-invocation-audit.md` (add)
- `docs/development/v0.1.8/phase-2/tickets/P2-AUD-02-scout-operation-record-caller-map.md` (add)
- `docs/development/v0.1.8/phase-2/tickets/P2-EVAL-03-synthetic-evaluation-pack-and-rubric.md` (add)
- `docs/development/v0.1.8/phase-2/tickets/P2-FREEZE-04-shared-interface-and-ownership-freeze.md` (add)
- `docs/development/v0.1.8/phase-2/tickets/README.md` (add)
- `docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md` (add)
- `docs/development/v0.1.8/roadmaps/v0.1.8-scout-search-first-roadmap.md` (modify)
- `docs/development/v0.1.8/spikes/README.md` (modify)
- `docs/development/v0.1.8/spikes/S07-execution-modes-and-cost-aware-orchestration.md` (modify)
- `docs/development/v0.1.8/spikes/S08-cross-model-decision-evaluation-methodology.md` (modify)
- `docs/development/v0.1.8/spikes/S09-local-search-retrieval-capability-sourcing.md` (modify)
- `docs/development/v0.1.8/spikes/S10-ollama-invocation-and-harness-onboarding.md` (modify)
- `docs/development/v0.1.8/spikes/S12-gig-graph-traversal-and-auditable-execution.md` (add)
- `docs/development/v0.1.8/spikes/S13-existing-job-discovery-solutions-research.md` (add)
- `docs/development/v0.1.8/spikes/S14-schema-inventory-and-consolidation-audit.md` (add)
- `docs/development/v0.1.8/spikes/S15-goal-and-tool-unit-composition-research.md` (add)
- `docs/development/v0.1.8/spikes/evidence/S12-decision-edge-design.md` (add)
- `docs/development/v0.1.8/spikes/evidence/S12-orca-ack-check-receipts.json` (add)
- `docs/development/v0.1.8/spikes/evidence/S12-orca-ack-check.md` (add)
- `docs/development/v0.1.8/spikes/evidence/S12-orca-blocking-resume-check.md` (add)
- `docs/development/v0.1.8/spikes/evidence/S12-orca-blocking-resume-receipts.json` (add)
- `docs/development/v0.1.8/spikes/evidence/S12-proposed-system-behavior-changes.md` (add)
- `docs/development/v0.1.8/spikes/evidence/S12-scout-graph-mapping.md` (add)
- `docs/development/v0.1.8/spikes/evidence/S13-existing-job-discovery-solutions-research.md` (add)
- `docs/development/v0.1.8/spikes/evidence/S14-repro/README.md` (add)
- `docs/development/v0.1.8/spikes/evidence/S14-repro/active_pointer_compare.py` (add)
- `docs/development/v0.1.8/spikes/evidence/S14-repro/census.py` (add)
- `docs/development/v0.1.8/spikes/evidence/S14-repro/handoff_scan.py` (add)
- `docs/development/v0.1.8/spikes/evidence/S14-repro/handoff_synthetic.py` (add)
- `docs/development/v0.1.8/spikes/evidence/S14-repro/privacy_guard.py` (add)
- `docs/development/v0.1.8/spikes/evidence/S14-repro/results.txt` (add)
- `docs/development/v0.1.8/spikes/evidence/S14-repro/tailor_selection.py` (add)
- `docs/development/v0.1.8/spikes/evidence/S14-schema-inventory-audit.md` (add)
- `docs/development/v0.1.8/spikes/evidence/S15-goal-and-tool-unit-composition-research.md` (add)
- `docs/development/v0.1.8/tasks/V018-01-workpad-navigation-and-readable-artifacts.md` (modify)

## Commit 3: find-jobs shared contracts + record/projection plumbing

**Message:**
```
feat(scout): add find-jobs shared contracts and extend proposal records/projection

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

**Note:** Files here are listed at their post-move path (`src/gigai/scout/...`) since they land after Commit 10 (the package move) touches them again; see the note on Commit 10.

**Files (26):**
- `src/gigai/scout/find_jobs/contracts.py` (add)
- `src/gigai/scout/materialization.py` (add)
- `src/gigai/scout/projection.py` (add)
- `src/gigai/scout/proposal_records.py` (add)
- `src/gigai/scout/proposals.py` (add)
- `src/gigai/scout/report_readers.py` (add)
- `tests/behaviors/scout_find_jobs/__init__.py` (add)
- `tests/behaviors/scout_find_jobs/conftest.py` (add)
- `tests/behaviors/scout_find_jobs/fixtures/fixture-acquire-batch-v1.json` (add)
- `tests/behaviors/scout_find_jobs/fixtures/fixture-acquire-input-v1.json` (add)
- `tests/behaviors/scout_find_jobs/fixtures/fixture-api-config-request-v1.json` (add)
- `tests/behaviors/scout_find_jobs/fixtures/fixture-api-config-response-v1.json` (add)
- `tests/behaviors/scout_find_jobs/fixtures/fixture-api-run-request-v1.json` (add)
- `tests/behaviors/scout_find_jobs/fixtures/fixture-api-run-response-v1.json` (add)
- `tests/behaviors/scout_find_jobs/fixtures/fixture-api-run-results-response-v1.json` (add)
- `tests/behaviors/scout_find_jobs/fixtures/fixture-api-run-status-response-v1.json` (add)
- `tests/behaviors/scout_find_jobs/fixtures/fixture-assessment-model-denied-v1.json` (add)
- `tests/behaviors/scout_find_jobs/fixtures/fixture-assessment-model-unavailable-v1.json` (add)
- `tests/behaviors/scout_find_jobs/fixtures/fixture-assessment-v1.json` (add)
- `tests/behaviors/scout_find_jobs/fixtures/fixture-find-jobs-config-v1.json` (add)
- `tests/behaviors/scout_find_jobs/fixtures/fixture-node-receipts-v1.json` (add)
- `tests/behaviors/scout_find_jobs/fixtures/fixture-present-payload-v1.json` (add)
- `tests/behaviors/scout_find_jobs/fixtures/fixture-run-input-v1.json` (add)
- `tests/behaviors/scout_find_jobs/fixtures/fixture-ui-consent-v1.json` (add)
- `tests/behaviors/scout_find_jobs/fixtures/fixture-watchlist-v1.json` (add)
- `tests/behaviors/scout_find_jobs/test_contracts.py` (add)

## Commit 4: Runner seam + node registry (incl. pinned golden-digest graph test)

**Message:**
```
feat(gigai): add fail-closed graph node registry and find-jobs runner seam

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

**Note:** `test_integration_graph.py` is the golden-digest regression test (renamed from the git-history-diffing original; see `.orchestrator/runs/v0.1.8/workers/scout-reorg.md` item 5): pins the six pre-existing Scout graphs' compiled-artifact digests as the verified pre-reorg baseline instead of diffing against git history.

**Files (4):**
- `src/gigai/graph_node_registry.py` (add)
- `src/gigai/run.py` (modify)
- `tests/behaviors/scout_find_jobs/test_integration_graph.py` (add)
- `tests/behaviors/scout_find_jobs/test_run_seam.py` (add)

## Commit 5: Acquire node (Exa + ATS boards + watchlist)

**Message:**
```
feat(scout): add find-jobs acquire node (Exa discovery, ATS board clients, watchlist)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

**Files (8):**
- `src/gigai/scout/find_jobs/ats_board_clients.py` (add)
- `src/gigai/scout/find_jobs/exa_client.py` (add)
- `src/gigai/scout/find_jobs/market_acquisition.py` (add)
- `src/gigai/scout/find_jobs/watchlist.py` (add)
- `tests/behaviors/scout_find_jobs/test_acquire_network.py` (add)
- `tests/behaviors/scout_find_jobs/test_ats_board_clients.py` (add)
- `tests/behaviors/scout_find_jobs/test_exa_client.py` (add)
- `tests/behaviors/scout_find_jobs/test_watchlist.py` (add)

## Commit 6: Assess node

**Message:**
```
feat(scout): add find-jobs assess node (requirements x resume matrix)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

**Files (3):**
- `src/gigai/scout/proposal_execution.py` (add)
- `tests/behaviors/scout_find_jobs/test_assess_contract.py` (add)
- `tests/behaviors/scout_find_jobs/test_assess_model_policy.py` (add)

## Commit 7: Present node + localhost API (incl. polish: JSON 500 boundary)

**Message:**
```
feat(scout): add find-jobs present node, localhost API server, and a safe JSON-500 error boundary

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

**Note:** Includes the polish-worker fix (`.orchestrator/runs/v0.1.8/workers/polish.md`): `do_GET` now wraps route dispatch in try/except, returning a redacted JSON 500 instead of leaking a traceback; `test_present_ui.py` gained 3 tests asserting no secret/path leakage on that path. No separate polish commit — same files, latest content.

**Files (2):**
- `src/gigai/scout/find_jobs/present_api.py` (add)
- `tests/behaviors/scout_find_jobs/test_present_ui.py` (add)

## Commit 8: UI (Vite/React find-jobs workflow, incl. polish: waiting/poll-stop states)

**Message:**
```
feat(ui): add Vite/React find-jobs workflow UI under src/gigai/scout/ui

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

**Note:** Includes the polish-worker UI fixes (`.orchestrator/runs/v0.1.8/workers/polish.md`): `NodeStatusList.jsx` shows "waiting" instead of "pending" for a node with no receipt yet; `App.jsx`'s status-poll `.catch` now calls `stopPolling()` before surfacing the error instead of silently rescheduling. No separate polish commit — same files, latest content. UI lives under `src/gigai/scout/ui/` (there is no separate top-level `ui/` in this tree).

**Files (15):**
- `src/gigai/scout/ui/.gitignore` (add)
- `src/gigai/scout/ui/index.html` (add)
- `src/gigai/scout/ui/package.json` (add)
- `src/gigai/scout/ui/src/App.jsx` (add)
- `src/gigai/scout/ui/src/api.js` (add)
- `src/gigai/scout/ui/src/components/AssessmentCard.jsx` (add)
- `src/gigai/scout/ui/src/components/ConfigPanel.jsx` (add)
- `src/gigai/scout/ui/src/components/MatrixBadge.jsx` (add)
- `src/gigai/scout/ui/src/components/NodeStatusList.jsx` (add)
- `src/gigai/scout/ui/src/components/ResultsView.jsx` (add)
- `src/gigai/scout/ui/src/components/RunConfirmDialog.jsx` (add)
- `src/gigai/scout/ui/src/main.jsx` (add)
- `src/gigai/scout/ui/src/styles.css` (add)
- `src/gigai/scout/ui/vite.config.js` (add)
- `src/gigai/scout/ui/yarn.lock` (add)

## Commit 9: Bindings + M1 end-to-end + runbook

**Message:**
```
feat(scout): bind find-jobs nodes to the functional graph and add the M1 runbook

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

**Files (3):**
- `docs/development/v0.1.8/runbooks/M1-find-jobs.md` (add)
- `src/gigai/scout/find_jobs/bindings.py` (add)
- `tests/behaviors/scout_find_jobs/test_m1_end_to_end.py` (add)

## Commit 10: Scout package move: src/gigai/scout_*.py -> src/gigai/scout/ (one commit, so renames are detected)

**Message:**
```
refactor(scout): move Scout into its own src/gigai/scout/ package (find_jobs/, data/, ui/)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

**Note:** Physical move + import-path updates only, no behavior change beyond paths/names (`.orchestrator/runs/v0.1.8/workers/scout-reorg.md`). Deletes the 34 old flat `src/gigai/scout_*.py` modules and the old `src/gigai/data/scout/` tree (22 files), adds their `src/gigai/scout/*.py` and `src/gigai/scout/data/**` equivalents (git will detect these as renames since content is otherwise unchanged), and updates the 9 untouched core modules whose relative-import depth shifted (`cli.py`, `external_recording.py`, `application_events.py`, `capability_review.py`, `capability_successor.py`, `default_init.py`, `native_records.py`, `portability.py`, `private_transfer.py`). `run.py` and `graph_node_registry.py` are NOT here — they're in Commit 4, since `graph_node_registry.py` never had a `scout_` prefix (generic, stays in core) and `run.py`'s find-jobs runner-seam edit is the newer, more specific reason it changed. `find_jobs/__init__.py` (the empty subpackage marker) is here since it was created by this move; the seven `find_jobs/*.py` implementation modules are in their own feature commits (3/5/6/7/9) since those are the newer, more specific commits that added their content — this move commit only carries the subpackage's `__init__.py` and everything that has no more specific home.

**Files (117):**
- `src/gigai/application_events.py` (modify)
- `src/gigai/capability_review.py` (modify)
- `src/gigai/capability_successor.py` (modify)
- `src/gigai/cli.py` (modify)
- `src/gigai/data/scout/CHANGELOG.md` (delete)
- `src/gigai/data/scout/README.md` (delete)
- `src/gigai/data/scout/gig.py` (delete)
- `src/gigai/data/scout/goalgraphs/README.md` (delete)
- `src/gigai/data/scout/goalgraphs/find-jobs.md` (delete)
- `src/gigai/data/scout/goalgraphs/prepare-interview.md` (delete)
- `src/gigai/data/scout/goalgraphs/proposal-assessment.md` (delete)
- `src/gigai/data/scout/goalgraphs/record-application.md` (delete)
- `src/gigai/data/scout/goalgraphs/research-role.md` (delete)
- `src/gigai/data/scout/goalgraphs/tailor-application.md` (delete)
- `src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000071/operation.schema.json` (delete)
- `src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000071/record_tool.py` (delete)
- `src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000071/research.py` (delete)
- `src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000071/research.schema.json` (delete)
- `src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000074/discovery.py` (delete)
- `src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000074/discovery.schema.json` (delete)
- `src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000075/tailoring.py` (delete)
- `src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000075/tailoring.schema.json` (delete)
- `src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000076/research.py` (delete)
- `src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000076/research.schema.json` (delete)
- `src/gigai/data/scout/ui/style.css` (delete)
- `src/gigai/data/scout/ui/template.html` (delete)
- `src/gigai/default_init.py` (modify)
- `src/gigai/external_recording.py` (modify)
- `src/gigai/native_records.py` (modify)
- `src/gigai/portability.py` (modify)
- `src/gigai/private_transfer.py` (modify)
- `src/gigai/scout/__init__.py` (add)
- `src/gigai/scout/acquisition_cli.py` (add)
- `src/gigai/scout/acquisition_records.py` (add)
- `src/gigai/scout/answer_cli.py` (add)
- `src/gigai/scout/bundled_tools.py` (add)
- `src/gigai/scout/checks.py` (add)
- `src/gigai/scout/data/CHANGELOG.md` (add)
- `src/gigai/scout/data/README.md` (add)
- `src/gigai/scout/data/gig.py` (add)
- `src/gigai/scout/data/goalgraphs/README.md` (add)
- `src/gigai/scout/data/goalgraphs/find-jobs.md` (add)
- `src/gigai/scout/data/goalgraphs/prepare-interview.md` (add)
- `src/gigai/scout/data/goalgraphs/proposal-assessment.md` (add)
- `src/gigai/scout/data/goalgraphs/record-application.md` (add)
- `src/gigai/scout/data/goalgraphs/research-role.md` (add)
- `src/gigai/scout/data/goalgraphs/tailor-application.md` (add)
- `src/gigai/scout/data/tools/cap_00000000-0000-4000-8000-000000000071/operation.schema.json` (add)
- `src/gigai/scout/data/tools/cap_00000000-0000-4000-8000-000000000071/record_tool.py` (add)
- `src/gigai/scout/data/tools/cap_00000000-0000-4000-8000-000000000071/research.py` (add)
- `src/gigai/scout/data/tools/cap_00000000-0000-4000-8000-000000000071/research.schema.json` (add)
- `src/gigai/scout/data/tools/cap_00000000-0000-4000-8000-000000000074/discovery.py` (add)
- `src/gigai/scout/data/tools/cap_00000000-0000-4000-8000-000000000074/discovery.schema.json` (add)
- `src/gigai/scout/data/tools/cap_00000000-0000-4000-8000-000000000075/tailoring.py` (add)
- `src/gigai/scout/data/tools/cap_00000000-0000-4000-8000-000000000075/tailoring.schema.json` (add)
- `src/gigai/scout/data/tools/cap_00000000-0000-4000-8000-000000000076/research.py` (add)
- `src/gigai/scout/data/tools/cap_00000000-0000-4000-8000-000000000076/research.schema.json` (add)
- `src/gigai/scout/data/ui/style.css` (add)
- `src/gigai/scout/data/ui/template.html` (add)
- `src/gigai/scout/discovery.py` (add)
- `src/gigai/scout/discovery_job.py` (add)
- `src/gigai/scout/document_records.py` (add)
- `src/gigai/scout/documents.py` (add)
- `src/gigai/scout/documents_cli.py` (add)
- `src/gigai/scout/find_jobs/__init__.py` (add)
- `src/gigai/scout/inputs.py` (add)
- `src/gigai/scout/interview.py` (add)
- `src/gigai/scout/interview_cli.py` (add)
- `src/gigai/scout/interview_records.py` (add)
- `src/gigai/scout/posting_inputs.py` (add)
- `src/gigai/scout/proposal_cli.py` (add)
- `src/gigai/scout/report.py` (add)
- `src/gigai/scout/report_cli.py` (add)
- `src/gigai/scout/research.py` (add)
- `src/gigai/scout/research_inputs.py` (add)
- `src/gigai/scout/research_v3.py` (add)
- `src/gigai/scout/tailor_cli.py` (add)
- `src/gigai/scout/tailor_execution.py` (add)
- `src/gigai/scout/tailor_selection.py` (add)
- `src/gigai/scout/tailoring.py` (add)
- `src/gigai/scout/template.py` (add)
- `src/gigai/scout/tool_adapter.py` (add)
- `src/gigai/scout/tools.py` (add)
- `src/gigai/scout_acquisition_cli.py` (delete)
- `src/gigai/scout_acquisition_records.py` (delete)
- `src/gigai/scout_answer_cli.py` (delete)
- `src/gigai/scout_bundled_tools.py` (delete)
- `src/gigai/scout_checks.py` (delete)
- `src/gigai/scout_discovery.py` (delete)
- `src/gigai/scout_discovery_job.py` (delete)
- `src/gigai/scout_document_records.py` (delete)
- `src/gigai/scout_documents.py` (delete)
- `src/gigai/scout_documents_cli.py` (delete)
- `src/gigai/scout_inputs.py` (delete)
- `src/gigai/scout_interview.py` (delete)
- `src/gigai/scout_interview_cli.py` (delete)
- `src/gigai/scout_interview_records.py` (delete)
- `src/gigai/scout_materialization.py` (delete)
- `src/gigai/scout_posting_inputs.py` (delete)
- `src/gigai/scout_projection.py` (delete)
- `src/gigai/scout_proposal_cli.py` (delete)
- `src/gigai/scout_proposal_execution.py` (delete)
- `src/gigai/scout_proposal_records.py` (delete)
- `src/gigai/scout_proposals.py` (delete)
- `src/gigai/scout_report.py` (delete)
- `src/gigai/scout_report_cli.py` (delete)
- `src/gigai/scout_report_readers.py` (delete)
- `src/gigai/scout_research.py` (delete)
- `src/gigai/scout_research_inputs.py` (delete)
- `src/gigai/scout_research_v3.py` (delete)
- `src/gigai/scout_tailor_cli.py` (delete)
- `src/gigai/scout_tailor_execution.py` (delete)
- `src/gigai/scout_tailor_selection.py` (delete)
- `src/gigai/scout_tailoring.py` (delete)
- `src/gigai/scout_template.py` (delete)
- `src/gigai/scout_tool_adapter.py` (delete)
- `src/gigai/scout_tools.py` (delete)

## Commit 11: CI / tooling fixes (v0.1.7 release blocker root cause + G22 verifier rewrite)

**Message:**
```
ci: fix release.yml and rewrite installed-G03/G22 verifiers for the new runner env

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

**Note:** Root cause (`.orchestrator/runs/v0.1.8/workers/ci-audit.md`): `verify_installed_g03.py`/`verify_installed_g22.py` were the only installed verifiers that didn't pass an explicit `env` (with `--create-model-target`, pinned `HOME`/`PATH`) into their subprocess calls, which broke under the new runner's stricter environment isolation. `pull_request.yaml`'s xdist/make-test rewrite is the S11 runner contract and stays in Commit 1.

**Files (4):**
- `.github/workflows/pull_request.yaml` (modify)
- `.github/workflows/release.yml` (modify)
- `tools/verify_installed_g03.py` (modify)
- `tools/verify_installed_g22.py` (modify)

## Commit 12: docs/development/v0.1.9: 0.2.0 planning spikes (S16-S19)

**Message:**
```
docs(v0.1.9): add S16-S19 spikes for the 0.2.0 core/gig decoupling ledger

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

**Files (6):**
- `docs/development/v0.1.9/README.md` (add)
- `docs/development/v0.1.9/spikes/README.md` (add)
- `docs/development/v0.1.9/spikes/S16-core-gig-decoupling.md` (add)
- `docs/development/v0.1.9/spikes/S17-gig-module-structure-classes.md` (add)
- `docs/development/v0.1.9/spikes/S18-scout-interview-prep-graphs.md` (add)
- `docs/development/v0.1.9/spikes/S19-test-suite-diet.md` (add)

## Commit 13: Orchestrator skill + workpad (.orchestrator/ as the committed coordination record)

**Message:**
```
chore(orchestrator): publish the Orca coordination workpad and orchestrator skill

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

**Note:** Includes this worker's own commit-plan-v2 scan artifacts, and `AGENTS.md` (new, points at the orchestrator skill).

**Files (122):**
- `.claude/skills/gigai-orchestrator/SKILL.md` (add)
- `.claude/skills/gigai-orchestrator/inbox.sh` (add)
- `.claude/skills/gigai-orchestrator/local_check.py` (add)
- `.claude/skills/gigai-orchestrator/run_visible.sh` (add)
- `.claude/skills/gigai-orchestrator/wait.sh` (add)
- `.orchestrator/README.md` (add)
- `.orchestrator/decisions.log` (add)
- `.orchestrator/local-models.jsonl` (add)
- `.orchestrator/local-models.md` (add)
- `.orchestrator/research/oh-my-pi.md` (add)
- `.orchestrator/reviews/i0-coordinator.md` (add)
- `.orchestrator/reviews/terra-i0.md` (add)
- `.orchestrator/reviews/terra-w0-triage.md` (add)
- `.orchestrator/reviews/terra-w0.md` (add)
- `.orchestrator/reviews/terra-w1b-triage.md` (add)
- `.orchestrator/reviews/terra-w1b.md` (add)
- `.orchestrator/reviews/w0-amendment-02.md` (add)
- `.orchestrator/reviews/w0c-roadmap-and-w0r-rev1.md` (add)
- `.orchestrator/reviews/w0d-reconcile.md` (add)
- `.orchestrator/runs/v0.1.8/logs/032303-test-wave1b-a5-a6-b1-b2.log` (add)
- `.orchestrator/runs/v0.1.8/logs/053727-test-b2-fix.log` (add)
- `.orchestrator/runs/v0.1.8/logs/065530-test-scout-find-jobs-dir.log` (add)
- `.orchestrator/runs/v0.1.8/logs/065645-test-make-test-pre-m1.log` (add)
- `.orchestrator/runs/v0.1.8/logs/074338-test-make-test-pre-m1-2.log` (add)
- `.orchestrator/runs/v0.1.8/logs/092523-test-make-test-wheel-post-repair.log` (add)
- `.orchestrator/runs/v0.1.8/logs/092942-test-make-test-wheel.log` (add)
- `.orchestrator/runs/v0.1.8/logs/093737-test-make-test-wheel-v2.log` (add)
- `.orchestrator/runs/v0.1.8/logs/093937-test-make-test-installed.log` (add)
- `.orchestrator/runs/v0.1.8/logs/094319-test-final-make-test.log` (add)
- `.orchestrator/runs/v0.1.8/logs/094550-test-final-v2-make-test.log` (add)
- `.orchestrator/runs/v0.1.8/logs/095045-test-make-test-final.log` (add)
- `.orchestrator/runs/v0.1.8/logs/101033-test-pytest-behaviors.log` (add)
- `.orchestrator/runs/v0.1.8/logs/101047-test-yarn-install-build.log` (add)
- `.orchestrator/runs/v0.1.8/logs/102815-test-proposal-tracking-retest.log` (add)
- `.orchestrator/runs/v0.1.8/logs/104225-test-interview-retest.log` (add)
- `.orchestrator/runs/v0.1.8/logs/104339-test-full-behaviors-final.log` (add)
- `.orchestrator/runs/v0.1.8/logs/104346-test-uv-build.log` (add)
- `.orchestrator/runs/v0.1.8/logs/110400-test-full-behaviors-post-pin.log` (add)
- `.orchestrator/runs/v0.1.8/logs/111655-test-make-test-final-post-reorg.log` (add)
- `.orchestrator/runs/v0.1.8/logs/214311-test-launcher-check.log` (add)
- `.orchestrator/runs/v0.1.8/logs/w0-start.json` (add)
- `.orchestrator/runs/v0.1.8/logs/w0-wait.json` (add)
- `.orchestrator/runs/v0.1.8/release/commit-plan.md` (add)
- `.orchestrator/runs/v0.1.8/release/pr-body.md` (add)
- `.orchestrator/runs/v0.1.8/release/verify_coverage.py` (add)
- `.orchestrator/runs/v0.1.8/workers/a1-exa-client.md` (add)
- `.orchestrator/runs/v0.1.8/workers/a3-ats-clients.md` (add)
- `.orchestrator/runs/v0.1.8/workers/a5-watchlist.md` (add)
- `.orchestrator/runs/v0.1.8/workers/a6-acquire-node.md` (add)
- `.orchestrator/runs/v0.1.8/workers/b1-assess-matrix.md` (add)
- `.orchestrator/runs/v0.1.8/workers/b2-assess-node.md` (add)
- `.orchestrator/runs/v0.1.8/workers/c1-present-node.md` (add)
- `.orchestrator/runs/v0.1.8/workers/c2-present-api.md` (add)
- `.orchestrator/runs/v0.1.8/workers/c2b-async.md` (add)
- `.orchestrator/runs/v0.1.8/workers/c34-ui.md` (add)
- `.orchestrator/runs/v0.1.8/workers/ci-audit.md` (add)
- `.orchestrator/runs/v0.1.8/workers/commit-prep.md` (add)
- `.orchestrator/runs/v0.1.8/workers/fp1.md` (add)
- `.orchestrator/runs/v0.1.8/workers/fp2.md` (add)
- `.orchestrator/runs/v0.1.8/workers/fp3.md` (add)
- `.orchestrator/runs/v0.1.8/workers/i0-contracts.md` (add)
- `.orchestrator/runs/v0.1.8/workers/i0b-run-input.md` (add)
- `.orchestrator/runs/v0.1.8/workers/i0r-rework.md` (add)
- `.orchestrator/runs/v0.1.8/workers/i0t-terra.md` (add)
- `.orchestrator/runs/v0.1.8/workers/i1-graph.md` (add)
- `.orchestrator/runs/v0.1.8/workers/i2-runner-seam.md` (add)
- `.orchestrator/runs/v0.1.8/workers/i3-bind.md` (add)
- `.orchestrator/runs/v0.1.8/workers/omp-research.md` (add)
- `.orchestrator/runs/v0.1.8/workers/polish.md` (add)
- `.orchestrator/runs/v0.1.8/workers/rel/ci-audit.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/rel/commit-prep.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/rel/omp-research.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/rel/polish.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/rel/reorg-golden.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/rel/scout-reorg.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/rel/v019-spikes.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/scout-reorg.md` (add)
- `.orchestrator/runs/v0.1.8/workers/spec-a1b-company.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/spec-b2b-import.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/spec-c2b-async.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/spec-c3-vite-shell.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/spec-c34-ui.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/spec-fp1.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/spec-fp2-regressions.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/spec-fp3-wheel-lane.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/spec-i0-contracts.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/spec-i0b-run-input.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/spec-i0r-rework.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/spec-i0t-terra.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/spec-i3-bind.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/spec-w0-amendment-02.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/spec-w0c-roadmap.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/spec-w0d-reconcile.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/spec-w0e-fixes.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/spec-w0f-terra-fixes.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/spec-w0r-amendment-02-rev.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/spec-w0t-terra-review.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/spec-w1bt-terra.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/v019-spikes.md` (add)
- `.orchestrator/runs/v0.1.8/workers/w0-amendment-02.md` (add)
- `.orchestrator/runs/v0.1.8/workers/w0c-roadmap.md` (add)
- `.orchestrator/runs/v0.1.8/workers/w0d-reconcile.md` (add)
- `.orchestrator/runs/v0.1.8/workers/w0e-fixes.md` (add)
- `.orchestrator/runs/v0.1.8/workers/w0f-terra-fixes.md` (add)
- `.orchestrator/runs/v0.1.8/workers/w0r-amendment-02-rev.md` (add)
- `.orchestrator/runs/v0.1.8/workers/w0t-terra-review.md` (add)
- `.orchestrator/runs/v0.1.8/workers/w1b/a1-exa-client.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/w1b/a3-ats-clients.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/w1b/a5-watchlist.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/w1b/a6-acquire-node.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/w1b/b1-assess-matrix.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/w1b/b2-assess-node.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/w1b/c1-present-node.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/w1b/c2-present-api.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/w1b/dispatch.log` (add)
- `.orchestrator/runs/v0.1.8/workers/w1b/i1-graph.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/w1b/i2-runner-seam.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/w1b/routes.tsv` (add)
- `.orchestrator/runs/v0.1.8/workers/w1bt-terra.md` (add)
- `.orchestrator/status.md` (add)
- `.orchestrator/runs/v0.1.8/workers/commit-plan-v2.md` (add)
- `.orchestrator/runs/v0.1.8/workers/commit-plan-v2.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/release-docs.txt` (add)
- `.orchestrator/runs/v0.1.8/workers/release-docs.md` (add)
- `AGENTS.md` (add)

## Commit 14: version bump + README rewrite for 0.1.8

**Message:**
```
chore(release): bump version to 0.1.8, add CHANGELOG entry, rewrite README for the find-jobs release

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

**Note:** This entire commit is a concurrent `release-docs` worker's task, caught mid-flight
by repeated re-snapshots of `git status` during this run (710 → 711 → 712 → 714 → 715 files
as it progressively touched `catalog.py`, then `CHANGELOG.md`, then `README.md`). That
worker's task also covers `pyproject.toml` (version bump) and `uv.lock` (relock) — both
already appear as `(modify)` in Commit 1's file list (the S11/runner bundle) because they
were already dirty from the test-reorg work before this scan started, so this worker's
version-bump edit to them is folded into that same file entry, not duplicated here (each
path appears in exactly one commit, per this plan's rule). **If the `release-docs` worker
is still running when the operator reads this, re-run `verify_coverage.py` first** — it may
have touched more files since this plan was generated, and/or its own worker report may
supersede this commit's grouping entirely.

**Files (3):**
- `src/gigai/catalog.py` (modify)
- `CHANGELOG.md` (modify)
- `README.md` (modify)

**Total files across all commits: 715**
**Total files in `git status --porcelain --untracked-files=all` at final generation time: 715**
(five concurrent-worker/self files appeared mid-scan and are included: `.orchestrator/workers/
release-docs.txt` and this worker's own `.orchestrator/runs/v0.1.8/workers/commit-plan-v2.md`, both folded
into Commit 13 alongside the other worker reports/specs; `src/gigai/catalog.py`,
`CHANGELOG.md`, and `README.md`, the `release-docs` worker's in-progress release-prep work,
broken out as Commit 14 — see the drift note above. Confirmed stable at 715 across two
re-checks 15s apart before finalizing.)

## Coverage verification

```
$ python3 .orchestrator/runs/v0.1.8/release/verify_coverage.py
Total files in git status: 715
Assigned (unique): 715
Assigned (total mentions): 715
Duplicates: 0
Unassigned (in git status but not in plan): 0
Extra (in plan but not in git status): 0
```

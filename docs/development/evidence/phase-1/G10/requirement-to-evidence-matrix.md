# Phase 1 Requirement-to-Evidence Matrix

This matrix reconciles V14 Phase 1 delivery items and exit gates to the public
Goal contracts, durable evidence, and automated regression surface. “Hosted
G10 CI” means the exact commit's macOS/Ubuntu source matrix, built-wheel lane,
and Debian offline-container lane.

| V14 requirement or gate | Owner | Evidence |
|---|---|---|
| Installable CLI and provisional contracts | G00, G02 | `pyproject.toml`, packaged schemas, `test_installed_help_version_and_goal_approved_commands_are_the_only_surface`, installed schema and CLI verifiers |
| Canonical bytes, IDs, and exact imported bytes | G01 | `tests/test_canonical.py`, `tests/test_canonical_ownership.py`, frozen vector digest |
| Setup, mount authority, deterministic offline endpoint, and reference-only credentials | G03, G11 | `tests/test_setup_configuration_diagnostics.py`, `tests/test_model_invocation_foundation.py`, installed G03/G11 verifiers |
| Path-free target binding and exclusion delta | G04 | `test_git_init_has_exact_ignored_delta_and_is_idempotent`, installed G04 verifier |
| Exact registry v1-to-v2 migration, backup, and private workpad substrate | G05 | `tests/test_registry_v2_migration.py`, `tests/test_g05_installed_scenarios.py`, installed G05 verifier |
| Local-only Git identity, interprocess ordering, and crash recovery | G06 | `tests/test_journal_locking_recovery.py`, installed G06 verifier |
| Proposal, graph, artifact-reference, and semantic validation | G07 | `tests/test_g07_contract_validators.py`, [G07 matrix](../G07/requirement-to-test-matrix.md), installed G07 verifier |
| Offline lifecycle: allocation, proposal, feedback, revision, approval, rejection, and recovery | G08 | `tests/test_g08_offline_create_lifecycle.py`, installed G08 verifier |
| Offline `open`, `workpad path`, reads, `check`, and doctor | G03, G05, G09 | installed G03/G05 scenarios; `tests/test_index_projection.py`; G10 active-open scenario; installed G09 verifier |
| Rebuildable SQLite index and journal reconciliation | G09 | `tests/test_index_projection.py`, including semantic-tamper reconciliation; installed G09 verifier |
| Port/factory and offline deterministic adapter; local-only live evidence boundary | G11 | `tests/test_model_invocation_foundation.py`, `tests/test_g11_installed_scenarios.py`, [G11 runbook](../G11/operator-live-proof-runbook.md) |
| Python and non-Python targets retain the exact init boundary | G04, G10 | G04 installed scenarios and `test_git_init_preserves_dirty_bytes_and_status` |
| Complete Gig exists only under configured private mount | G05, G08, G10 | G05 topology tests and `test_create_orders_first_commit_active_selection_and_valid_proposal` |
| Active `open` and structured `--with-target` argv | G05, G08, G10 | `test_installed_open_without_id_resolves_active_private_workpad_with_target` |
| Every creation transition has text handoff and local commit | G06, G08, G10 | journal sequencing/recovery tests and G08 lifecycle tests |
| Two-process exclusion and atomic replacement on configured mount | G03, G06 | diagnostics tests and `test_eight_process_race_allocates_strict_committed_order` |
| Workpads have no remote | G05, G06 | G05 local-Git checks and `test_timeout_remote_and_identity_ownership_fail_closed` |
| Deleting index preserves canonical `status --json` identity | G09, G10 | `test_index_rebuild_is_disposable_deterministic_and_idempotent` |
| Offline scenarios never use network or tokens | G02, G03, G11, G10 | process audit hook, credential-canary tests, installed deterministic doctor scenario, and Debian `--network none` verifier |
| macOS, Ubuntu, and Debian offline verification | G10 | local locked macOS matrix; Hosted G10 CI Ubuntu/macOS and Debian 12 offline-container lane |

## Explicit non-applicability and deferrals

- G11 live provider diagnostics are operator-local, opt-in, redacted, and
  excluded from CI and the offline scenario audit.
- Anthropic API, Codex CLI, Claude CLI, `improve`, Run creation/execution, and
  scheduler behavior are not Phase 1 completion claims.
- The Debian image build may fetch declared build inputs; its acceptance command
  executes after the image is built with Docker `--network none` and receives no
  host credential environment.

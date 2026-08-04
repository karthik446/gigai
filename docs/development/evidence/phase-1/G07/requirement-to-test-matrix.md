# G07 Requirement-to-Test Matrix

- Goal: [G07 — Contract Validators](../../../goals/phase-1/G07-contract-validators.md)
- Date: 2026-08-04

| V14 Section 6.5 rule | Production surface | Primary evidence |
| --- | --- | --- |
| Eight packaged schemas are the only serialized-contract set. | `validate_serialized_contract`; `SCHEMA_NAMES` | `test_schema_validator_enumerates_the_packaged_contract_set`; `verify_installed_schemas.py` |
| Required proposal files exist and owned Markdown is canonical. | `validate_proposal_workpad` | `test_workpad_validator_proves_digest_pinning_and_markdown_correspondence` |
| The proposal envelope pins exact artifact bytes. | `_validate_artifact_ref` | `test_workpad_validator_rejects_a_stale_proposal_artifact_digest`; `verify_installed_g07.py` |
| Graph Goals map mechanically to `goals/NN-<name>.md`; orphan Goal files fail. | `_validate_goal_markdown` | `test_workpad_validator_proves_digest_pinning_and_markdown_correspondence`; `test_workpad_validator_rejects_unreferenced_goal_markdown` |
| Proposal status remains pre-approval. | `validate_proposal_workpad` | `test_workpad_validator_rejects_approved_status_before_approval` |
| Goal IDs and internal versions are canonical and unique. | `validate_goal_graph` | `test_goal_graph_validator_rejects_duplicate_and_noncanonical_goal_ids`; `test_goal_graph_validator_rejects_an_invalid_internal_version` |
| Entries, terminals, reachability, and acyclicity are valid. | `_validate_reachability` | `test_goal_graph_validator_reports_missing_entry_without_suppressing_cycle`; `test_goal_graph_validator_reports_missing_terminal_path` |
| Automatic edges name typed source outcomes. | Edge validation | `test_goal_graph_validator_requires_a_typed_automatic_outcome`; `test_goal_graph_validator_rejects_an_undeclared_source_outcome` |
| A join names exact dependency predecessors and typed predecessor outcomes. | `_validate_reachability` plus edge validation | `test_goal_graph_validator_accepts_a_multi_parent_join`; `test_goal_graph_validator_rejects_duplicated_join_predecessor`; `test_goal_graph_validator_requires_a_typed_automatic_outcome` |
| Independent writers use distinct surfaces or a shared exclusive resource. | `_validate_parallelism` | `test_goal_graph_validator_rejects_independent_overlapping_writers`; `test_goal_graph_validator_rejects_write_effect_without_surface` |
| Per-Goal budgets fit the aggregate budget. | `_validate_budgets` | `test_goal_graph_validator_rejects_an_impossible_aggregate_budget` |
| Every terminal Goal requires all Gig completion evidence. | `_validate_terminal_evidence` | `test_goal_graph_validator_rejects_incomplete_terminal_evidence` |
| Executors and tools resolve or block explicitly. | `_validate_resolution` | `test_goal_graph_validator_rejects_materializer_that_is_not_a_predecessor`; `test_goal_graph_validator_rejects_a_blocking_resolution_without_reason` |
| `check` is installed, offline, and read-only. | `gigai check`; `allow_semantic_state=True` only for that command | `test_installed_check_validates_a_registered_proposal_without_mutation`; `verify_installed_g07.py` |

## Interpretation recorded for audit

Recovery edges are typed-failure continuations, not join predecessors. G07
therefore defines a join as a Goal with two or more incoming `dependency`
edges; duplicated sources among those edges fail with
`invalid_join_predecessors`. Outcome typing is independently enforced for all
automatic edges by `missing_automatic_outcome` and `undeclared_outcome`.

Cross-version Goal identity and version stability are intentionally absent from
this matrix: G08 compares a proposal graph with a prior approved graph.

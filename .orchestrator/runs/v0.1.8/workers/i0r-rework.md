# I-0 rework handoff

State: **complete**. The shared Scout find-jobs contract freeze was reworked against Terra's T1-T10 review and the coordinator's m1/m2 notes; the focused suite and lint pass below are the evidence boundary.

## Files

Changed only the owned files:

- `src/gigai/scout_find_jobs_contracts.py`
- `tests/behaviors/scout_find_jobs/test_contracts.py`
- `tests/behaviors/scout_find_jobs/fixtures/*.json` (the eight roadmap fixtures, model-unavailable/denied assessment variants, and API request/response samples)
- `.orchestrator/workers/i0r-rework.md`

## Per-finding disposition

- T1 — **fixed**: `NodeReceipt` now carries canonical goal identity/version, executor, outcome, errors, evidence refs, usage, and timestamps; `to_goal_details()` maps all goal statuses and fails closed for run-level `interrupted`. A focused test validates the emitted goal detail inside the repository's `run-details.schema.json` validator.
- T2 — **fixed**: selected posting digests are required; `AssessInput` carries the acquisition batch reference and output digest, serializes selection reasons as a URL-keyed object, and validates uniqueness, exact one-reason correspondence, role eligibility, and cap. `AssessOutput` echoes selected identities, the pinned selection cap/rule, and digest consistency.
- T3 — **fixed**: `PresentPayload` carries `pinned_resume` as the D7 triple, nullable for explicit unavailable state; the non-empty present fixture includes the pinned triple.
- T4 — **fixed**: candidate rows, assessments, and not-assessed rows are validated as a complete, unique, non-overlapping partition; not-assessed reasons include unchanged, duplicate, failed, over-cap, role-mismatch, model-unavailable, and model-denied. Separate assessment fixtures cover model-unavailable and model-denied outcomes.
- T5 — **fixed**: `diff_url_sets` now compares normalized URL-to-content-digest observations and emits mutually exclusive added/removed/unchanged/edited categories; the acquire fixture and URL tests include a true edited observation.
- T6 — **fixed by choice**: `pending` is a progress state (`ProgressStatus.PENDING`), not a row outcome; `RowOutcome` remains exactly new/edited/unchanged/duplicate/failed, and the acquire fixture encodes `progress_status: "pending"`.
- T7 — **fixed**: DTO aggregate status fields use `AggregateStatus`; aggregate precedence covers every level, and present/run payloads validate status agreement with node receipts where receipts are present, plus PresentOutput agreement with its payload.
- T8 — **fixed**: table-driven closed-key mutation tests cover every public DTO and nested DTO sample, enum mutations cover every public enum-bearing DTO, and the route test asserts exact method/path/request type/response type/status tuple.
- T9 — **fixed**: fixture round-trips cover success plus model-unavailable/denied assessment variants, a non-empty present payload with rows/failure/matrix/suggestions/questions/not-assessed/three receipts/pinned resume, and API samples for all four frozen routes.
- T10 — **fixed**: URL tests cover malformed input, userinfo, invalid and explicit non-default ports, fragments, tracking parameters, sorting, and normalized dedup; non-default ports are preserved.
- m1 — **fixed**: `SelectionRule.NEW_OR_EDITED_ROLE_MATCH` serializes as `new_or_edited_role_match` in acquire/run DTOs and fixtures.
- m2 — **fixed**: both Greenhouse hosts accept only `embed/job_board?for=<token>` and reject other embed paths; all four admitted domains and a negative are tested.

## Public DTO negative-test IDs

`test_every_public_dto_rejects_closed_set_mutations` is table-driven over these IDs (each has unknown-key, missing-key, and wrong-type cases): `source_toggles`, `find_jobs_config`, `posting_row`, `posting_row_result`, `failure_row`, `watchlist_first_seen`, `watchlist_entry`, `watchlist_fixture`, `node_context`, `selected_posting`, `selection_reason`, `pinned_resume`, `acquire_input`, `url_observation`, `edited_url`, `url_set_diff`, `acquire_output`, `requirement_matrix_row`, `not_assessed_row`, `assessment_result`, `usage_block`, `producer`, `goal_error`, `artifact_ref`, `node_failure`, `node_receipt`, `node_receipt_fixture`, `assess_input`, `assess_output`, `present_input`, `present_payload`, `present_output`, `consent_actor`, `ui_consent`, `config_request`, `config_response`, `run_request`, `run_response`, `run_lookup_request`, `run_status_response`, and `run_results_response`.

`test_every_public_enum_rejects_bad_enum` covers: `find_jobs_config.default_model_target`, `posting_row.provider`, `posting_row_result.outcome`, `failure_row.source_kind`, `watchlist_first_seen.source_kind`, `watchlist_entry.provider`, `node_context.model_target`, `selection_reason.reason`, `acquire_input.selection_rule`, `acquire_output.progress_status`, `requirement_matrix_row.status`, `not_assessed_row.reason`, `producer.model_target`, `node_receipt.status`, `assess_input.model_target`, `assess_output.model_target`, `present_payload.status`, `present_output.aggregate_status`, `consent_actor.kind`, `ui_consent.action`, `run_request.selection_rule`, `run_response.status`, and `run_status_response.status`. DTOs with no enum-valued field are still covered by the closed-key/type table.

## Verification

```text
$ uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_contracts.py -q
........................................................................ [ 39%]
........................................................................ [ 79%]
......................................                                   [100%]
182 passed in 0.18s

$ uv run --locked --extra test ruff check src/gigai/scout_find_jobs_contracts.py tests/behaviors/scout_find_jobs
All checks passed!
```

READ: both review notes (`.orchestrator/reviews/terra-i0.md`, `.orchestrator/reviews/i0-coordinator.md`), Amendment 02 Rev 3, the v0.1.8 find-jobs roadmap/fixture table, `run-details.schema.json`, `common.schema.json`, and the requested house-style modules (`canonical.py`, `scout_proposals.py`, `scout_acquisition_records.py`, `adapters/factory.py`, and the run consent field set).

EXECUTED: focused offline pytest and Ruff commands above, JSON fixture parsing as part of the focused tests, and the existing schema validator from `gigai.validators`; no network/provider/model/API server call, no full `make test`, no schema/run.py edit, and no install/release/live-readiness claim.

Choices made: pending is progress-only; `PresentPayload.pinned_resume: null` is the explicit unavailable state; interrupted remains aggregate/run-level and is rejected by `NodeReceipt.to_goal_details()` rather than invented as a goal status; API request/response DTOs use explicit schema-version fields for closed-set validation.

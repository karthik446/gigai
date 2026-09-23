# Coordinator review: I-0 contracts (2026-09-23)
Re-ran `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_contracts.py -q`: 22 passed. Scope: only the owned files (untracked src module + tests/behaviors/scout_find_jobs).
OK: strict closed-key parsing; reuses canonical_json_digest / validate_entity_id / digest_imported_bytes; aggregate_status precedence correct; consent envelope matches run.py:2187-2213's field set; assess declared superset + ASSESS_LOCAL_EFFECTS; RunRequest carries config_digest (lets the server refuse a run if the config changed after the dialog was shown).
Scope bonus: normalize_url / diff_url_sets / content_hash / parse_board_url are implemented in the contracts module, so A-4 is redundant. Drop A-4; A-6 uses these directly.
Minor: m1 `RunRequest.selection_rule` is a free str; make it an enum (`new_or_edited_role_match`). m2 `parse_board_url` returns token "embed" for `boards.greenhouse.io/embed/job_board?for=<token>`; handle it (token from `for=`) or reject it.

## After Terra (terra-i0.md): verdict rework, and I agree
I verified T1 (run-details goal_details requires goal_id/goal_version/executor/outcome/errors/evidence; its status enum has ready/waiting_for_gate/verifying and no interrupted), T5 (`edited=()` hardcoded, :1059) and T9 (present-payload fixture empty). My first pass checked the parts, not whether downstream rows could build on them, and missed all of these. Lesson: for a contract freeze, walk each consumer row.

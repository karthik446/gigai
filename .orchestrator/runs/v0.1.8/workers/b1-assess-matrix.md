# B-1 assessment matrix

State: implemented; focused legacy tests pass. No live network, provider, or model calls were made.

Files changed:

- `src/gigai/scout_proposals.py`: additive bounded matrix/suggestions/questions validation, plus `parse_assessment_proposal` using frozen `AssessmentResult.from_json` and `FindJobsContractError`.
- `src/gigai/scout_proposal_records.py`: additive `scout-assessment-revision:1` record shape, `save_assessment_revision`, reader support, and export; v1 records remain on the existing validation/redeem path.

READ:

- `src/gigai/scout_find_jobs_contracts.py` DTOs and error authority.
- Rev 3 evidence/design and find-jobs roadmap B-1 row.
- Existing proposal validator, record writer/reader, and assessment fixture.

EXECUTED:

- `python -m compileall -q src/gigai/scout_proposals.py src/gigai/scout_proposal_records.py` — passed.
- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_assess_contract.py tests/behaviors/scout_proposals_tools/test_scout_proposals.py tests/behaviors/scout_proposals_tools/test_scout_proposal_records.py -q` — passed (`43 passed in 38.27s`).
- `git diff --check` was run; it reports pre-existing trailing whitespace in unrelated modified docs only.

Choices: kept old proposal/revision shape unchanged for compatibility; the new assessment record is versioned and read without coercing into v1 lineage fields. The new API resolves an active workpad from `home_root`/`target` (or accepts a `ResolvedWorkpad`) and returns the committed revision artifact path.

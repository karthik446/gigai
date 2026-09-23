# I-0b run-input DTO handoff

State: **complete**. Added the sealed `FindJobsRunInput` contract and its fixture/coverage without changing any existing DTO definition or any file outside the I-0-owned contracts, focused tests, and fixtures.

## Change

`FindJobsRunInput` is a frozen DTO with the exact schema version `scout-find-jobs-run-input:1` and fields `config`, `config_digest`, `selection_cap`, `selection_rule`, `model_target`, and required `pinned_resume`. Its parser is closed-key, validates the cap and enums, requires the D7 pinned resume triple, and fails with stable `invalid_value` when `config_digest` does not equal `config.digest()`; canonical `to_json()`/`digest()` behavior comes from the shared contract base. The DTO is exported and covered by `fixture-run-input-v1.json`, fixture round-trip tests, all standard unknown/missing/wrong-type/bad-enum table cases, and a dedicated digest-mismatch test.

## Files

- `src/gigai/scout_find_jobs_contracts.py`
- `tests/behaviors/scout_find_jobs/test_contracts.py`
- `tests/behaviors/scout_find_jobs/fixtures/fixture-run-input-v1.json`
- `.orchestrator/workers/i0b-run-input.md`

## Verification

```text
$ uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_contracts.py -q
........................................................................ [ 38%]
........................................................................ [ 76%]
............................................                             [100%]
188 passed in 0.20s

$ uv run --locked --extra test ruff check src/gigai/scout_find_jobs_contracts.py tests/behaviors/scout_find_jobs
All checks passed!
```

READ: the existing shared contracts module and focused contract tests/fixtures to preserve current DTOs and house style; no review or schema/run.py changes were needed for this additive DTO.

EXECUTED: the focused pytest and Ruff commands above, plus an offline direct round-trip/digest check confirming the fixture's `config_digest` equals `config.digest()`; no network/provider/model/API calls, no full `make test`, and no changes to existing DTO definitions.

Choices made: none beyond the requested exact field order/schema name; the config digest is the canonical `FindJobsConfig.digest()` value and mismatch is rejected before construction.

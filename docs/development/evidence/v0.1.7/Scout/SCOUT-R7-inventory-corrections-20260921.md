# SCOUT R7 inventory corrections — 2026-09-21

Status: **focused inventory/schema lane green; R7 remains unaccepted**.

This bounded correction addressed only the 15 shared schema-setup errors and
the two assigned inventory failures in the captured R7 source matrix. No
production schema, validator, installed verifier, provider/model, private
`.gigai` data, activation, publication, commit, reset, or broad/full-matrix
rerun was performed.

## Evidence reviewed

The captured source matrix remains the authority for the starting failures:

- `/private/tmp/gigai-r7-candidate-20260920/logs/full-source-matrix-20260920.stdout.log`
- `/private/tmp/gigai-r7-candidate-20260920/logs/full-source-matrix-20260920.junit.xml`
- captured status: exit 1, 1551 collected, 1482 passed, 53 failed, 1
  skipped, 15 errors, 1 warning, 4123.03 seconds

The 15 setup errors all reported the same frozen schema-resource mismatch:
the checked-out package exposed 82 `.schema.json` resources while the test
set named only 63 resources. `src/gigai/validators.py` separately records a
frozen historical `SCHEMA_NAMES` tuple of 64 names plus 18 explicitly
versioned resources; `src/gigai/schemas/SHA256SUMS` records the exact current
digest for all 82 resources.

## Corrections

`research/contract_spike/tests/test_schemas.py`

- Pinned the expected resource set to the exact 82 packaged schema names,
  including all 19 resources reported as additional by the captured setup
  failure.
- Added representative valid synthetic goldens for every newly registered
  resource, including application events, model invocation v2/v3, runtime
  comparison resources, Scout answer/discovery/proposal/document/interview/
  transfer/import/tailor resources.
- Added strict unknown-root and missing-required-field negatives for every
  added resource, plus nested unknown-field negatives where the schema declares
  a closed nested object. The discovery-job `public_source` and `provenance`
  objects are intentionally open in the current schema, so no false
  rejection assertion was added for those fields.

`tests/test_g17_capabilities.py`

- Retained the exact frozen 64-name `SCHEMA_NAMES` count assertion.
- Retained exact SHA256 identity assertions. The unchanged installation
  baseline remains `c21641988e728cd94a8617a994ec1e3f5ffa9ae38ca020a2f7406916d6d083c0`.
  The capability manifest assertion follows the accepted additive
  `tool_binding` contract and its `SHA256SUMS` entry:
  `2d36b8e0552c810f1ec17e50d4edbc68be39cc1572c0f535073bf7f59731b5a7`.
  No hash assertion was replaced by a discovered count or presence check.

`tests/test_scout06_source_contract.py`

- Pinned the exact allowed Scout source map to 21 members, including the
  explicitly accepted `goalgraphs/proposal-assessment.md` member.
- Audited the new proposal-assessment selector's instructions, required and
  optional inputs, output, and generated definition entry.
- Confirmed the closed CRUD manifest remains exactly the wrapper, CRUD entry,
  and CRUD schema; research/discovery/tailoring/proposal source members do not
  silently enter CRUD authority.

## Focused verification

Command:

```text
rtk proxy .venv/bin/pytest -q \
  tests/test_g17_capabilities.py::test_g17_additive_schema_inventory_and_baseline_hashes \
  tests/test_scout06_source_contract.py \
  research/contract_spike/tests/test_schemas.py \
  --junitxml=/private/tmp/gigai-r7-candidate-20260920/logs/inventory-corrections-20260921.junit.xml
```

Result: **33 passed, 262 subtests passed in 0.82s, exit 0**.

`git diff --check` passed for all three assigned test files. No production
source/schema/validator file was edited by this correction lane; unrelated
dirty worktree and G43 changes remain preserved.

## Remaining defects and release boundary

The captured full source matrix still has 53 failures outside this lane,
including canonical ownership, G08/G21/G22/G26, G41/G42, G43 provider review
and run status, JSL closeout, setup-browser, subprocess shell handling, and a
separate Scout06 materialization source-contract diagnostic. Installed
affected verification remains the previously captured 36-pass result. This
focused green result does not establish source-matrix green, installed-wheel
release acceptance, provider/model execution, Linux/Windows behavior,
publication authorization, or personal UAT.

# SCOUT R7 source failure triage — v0.1.7

Date: 2026-09-21 (America/Denver)  
Status: **read-only triage of the captured source matrix; no source, test,
schema, or tool correction was made.**

This report uses the exact captured source evidence requested by the R7
correction handoff. It preserves the dirty worktree, including unrelated G43
changes, and does not claim release acceptance, installed-package acceptance,
provider/model execution, or live socket proof.

## Evidence identity and accounting

The source matrix evidence is:

```text
/private/tmp/gigai-r7-candidate-20260920/logs/full-source-matrix-20260920.stdout.log
/private/tmp/gigai-r7-candidate-20260920/logs/full-source-matrix-20260920.junit.xml
/private/tmp/gigai-r7-candidate-20260920/logs/full-source-matrix-20260920.status
```

The status file records the exact command, `GIGAI_G30_UAT=0`, matrix Python
3.11.14, exit code 1, and:

```text
1551 collected; 1482 passed; 53 failed; 15 errors; 1 skipped; 1 warning
wall time: 4123.03s (1:08:43)
```

The 53 failures and 15 errors are fully accounted for below. The 15 schema
errors are one setup failure repeated across 15 tests; they are not 15
independent schema behavior failures. The 35 G43/JSL failures are also
short-circuited by one exact-environment MIME classification defect; their
intended provider lifecycle assertions remain untested until that gate is
repaired.

The exact-wheel installed affected lane remains a separate result: 36 passed
in 33.66s, as recorded in
`SCOUT-R7-verifier-corrections-20260920.md`. It does not erase the source
failures below.

## Root-cause map

| Matrix group | Count | Current classification and exact evidence | Minimum correction owner / acceptance |
|---|---:|---|---|
| Canonical SHA ownership | 1 | **Real implementation defect.** `tests/test_canonical_ownership.py:41` reports `src/gigai/scout_interview_records.py` importing `hashlib` and calling `sha256`; the local operation receipt is `src/gigai/scout_interview_records.py:463`. This bypasses the product-wide canonical digest owner. | Canonical/interview owner: route the operation-key digest through the canonical helper while preserving the existing filename format. Frozen invariant: product modules other than `src/gigai/canonical.py` contain no `hashlib` import or `.sha256()` call. Acceptance: `.venv/bin/pytest -q tests/test_canonical_ownership.py`. |
| G08 Gig identity ownership | 1 | **Real implementation defect.** `tests/test_g08_offline_create_lifecycle.py:333` finds a non-lifecycle `generate_entity_id(EntityPrefix.GIG, ...)`; the call is `src/gigai/default_init.py:690-698`. Lifecycle owns the canonical Gig allocator at `src/gigai/lifecycle.py:2594-2602`. | G08/default-init owner: call the lifecycle-owned Gig allocation boundary; do not add a second identity generator. Frozen invariant: only lifecycle allocates `EntityPrefix.GIG`; init may reserve/use an authenticated lifecycle result. Acceptance: `.venv/bin/pytest -q tests/test_g08_offline_create_lifecycle.py`. |
| G17 schema baseline | 1 | **Stale inventory/baseline fixture, not a runtime failure.** `tests/test_g17_capabilities.py:73-79` still requires 64 names and the old manifest hash `17844f...9ec4cd`; the current manifest hash is `2d36b8...31b5a7`, while the installation hash remains `c21641...083c0`. The source inventory correction is explicitly owned by the other worker. | Inventory owner: update only the exact historical fixture/baseline contract after confirming the intended current manifest; retain strict hash and additive assertions. Acceptance: `.venv/bin/pytest -q tests/test_g17_capabilities.py::test_g17_additive_schema_inventory_and_baseline_hashes`. |
| Contract-spike schema setup | 15 errors | **One stale schema inventory fixture short-circuits 15 tests.** `research/contract_spike/tests/test_schemas.py:1488` raises `schema resource set mismatch: missing=[], additional=[...]` for the 20 current R5/Scout schema resources listed in the captured trace. This is setup failure, not 15 independent schema validation failures; the central packaged inventory has additional registered schemas and is owned by the other inventory worker. | Inventory/contract-spike owner: synchronize the fixture's exact expected resource set and golden inventory without weakening closed-schema validation. Acceptance: `.venv/bin/pytest -q research/contract_spike/tests/test_schemas.py`. |
| G21 prepared occurrence | 1 | **Real lifecycle/reconciliation defect.** `tests/test_g21_occurrence.py:343-381` retries `reconcile_occurrence`; `src/gigai/occurrence.py:233-238` calls `read_run_details` without handling its transient reconciliation refusal. `src/gigai/run.py:804-824` deliberately rejects bytes differing from the journal commit with `run_details_reconciliation_required`. The test therefore sees an uncommitted/intermediate Run mutation instead of retrying or reconciling. | G21 owner: treat `run_details_reconciliation_required` as retryable in occurrence reconciliation, without adopting uncommitted bytes or relaunching the Run. Frozen invariant: occurrence terminalization reads only a committed journal-authenticated Run outcome; transient writer state remains `run_prepared`. Acceptance: `.venv/bin/pytest -q tests/test_g21_occurrence.py::test_prepared_occurrence_reconciles_without_relaunch tests/test_g21_occurrence.py::test_interruption_after_run_preparation_is_terminal_failure`. |
| G22/G26/setup-browser loopback | 5 | **Environment-refused bind; not a product defect established by this run.** `tests/test_g22_http_approval.py:47`, `tests/test_g22_proposal_interview.py:286,338`, `tests/test_g26_review_actions.py:107`, and `tests/test_setup_browser.py:51` all fail before assertions at `src/gigai/proposal_interview.py:783` or `src/gigai/setup_interview.py:171` with `PermissionError: [Errno 1] Operation not permitted`. This is sandbox socket permission refusal, not evidence that loopback routing or token handling is incorrect. | Coordinator/reviewer: rerun these five tests only in an environment permitting loopback binds. Do not weaken the loopback-only or token boundary to accommodate the sandbox. Acceptance: `.venv/bin/pytest -q tests/test_g22_http_approval.py tests/test_g22_proposal_interview.py tests/test_g26_review_actions.py tests/test_setup_browser.py`. |
| G41/G42 package init | 7 | **Stale test fixtures against the accepted username gate.** Six G41 tests (`tests/test_g41_package_boundary.py:45,77,141,156,196,231`) and one G42 adoption test (`tests/test_g42_catalog.py:75-113`) invoke `init` without `--username`. The current contract intentionally refuses noninteractive init at `src/gigai/cli.py:1984-1995` with `username_required`; all seven fail before package/adoption assertions. | G41/G42 owner: add an accepted synthetic username to the test invocations; preserve the noninteractive username requirement and package authority checks. Acceptance: `.venv/bin/pytest -q tests/test_g41_package_boundary.py tests/test_g42_catalog.py`. |
| G43 provider review and JSL closeout | 35 | **Real source defect short-circuiting downstream lifecycle coverage.** Exact matrix Python `-I` reports `mimetypes.guess_type("requirements.md") == (None, None)`. `src/gigai/run_plan.py:190-206` therefore rejects the baseline as `application/octet-stream`; ordinary inputs are similarly stamped at `src/gigai/run_plan.py:1688-1698`. The first G43 failures show this directly at `tests/test_g43_provider_review.py:51-109,115-299,356-374`; all 15 JSL parameterized cases begin at `tests/test_jsl_closeout_regressions.py:151` and fail before closeout mutation checks. The 11 G43 run-status cases (`tests/test_g43_provider_run_status.py:99-457`) create ordinary `.md` plans, then provider execution rejects the sealed input media before the intended status/authentication/crash assertions; observed `failed`/missing-result outcomes are downstream of this short circuit, not proof that every status assertion is independently fixed or stale. | Run-plan/provider-review owner: replace host MIME-database dependence with a deterministic accepted text suffix/media mapping (at minimum `.md`/`.markdown` -> `text/markdown`, `.txt` -> `text/plain`) at both source ingestion sites, and add an isolated `python -I` probe. Frozen authority invariant: the deterministic media type is sealed with exact input bytes and digest; provider execution accepts only that sealed text/Markdown reference; Run success/blocked/interrupted status is published only with authenticated journaled provider evidence and never by falling back to offline Goals. Acceptance after correction: `/private/tmp/gigai-r7-candidate-20260920/matrix-venv/bin/python -I -m pytest tests/test_g43_provider_review.py tests/test_g43_provider_run_status.py tests/test_jsl_closeout_regressions.py --junitxml=/private/tmp/gigai-r7-candidate-20260920/logs/r7-g43-jsl-narrow.xml`. |
| Scout06 source inventory | 1 | **Stale inventory fixture, not a source-authority failure.** `tests/test_scout06_source_contract.py:29-45` asserts `len(scout_source_files()) == 20`; the current helper returns 21, while the test also requires the research source/schema to remain outside the closed CRUD manifest. The extra member is why the assertion reports `21 == 20`; the strict outside-inventory checks were not reached. Source inventory corrections are owned by the other worker. | Transfer/inventory owner: update the expected count/member fixture only after reviewing the exact 21-member source set; retain the research-outside-CRUD assertions. Acceptance: `.venv/bin/pytest -q tests/test_scout06_source_contract.py::test_research_source_is_outside_closed_crud_inventory`. |
| `scout_materialization` subprocess | 1 | **Real source-contract defect.** `tests/test_setup_configuration_diagnostics.py:248-261` requires every product `subprocess.run` call to use a literal argv list and `shell=False`; `src/gigai/scout_materialization.py:178-180` omits `shell=False`. No execution is needed to establish this AST violation. | Scout materialization owner: add literal `shell=False` while retaining the fixed `git -C ... ls-tree` argv. Frozen invariant: no product subprocess boundary relies on the default shell setting. Acceptance: `.venv/bin/pytest -q tests/test_setup_configuration_diagnostics.py::test_product_subprocesses_are_literal_argv_with_shell_disabled`. |

## G43/JSL short-circuit proof and safe correction boundary

The exact isolated-runtime probe was:

```text
/private/tmp/gigai-r7-candidate-20260920/matrix-venv/bin/python -I -c \
  'import mimetypes; print(mimetypes.guess_type("requirements.md")); print(mimetypes.guess_type("input.md"))'
=> (None, None)
=> (None, None)
```

That explains all observed baseline failures at `run_plan.py:199-201` and
ordinary provider input failures caused by `run_plan.py:1697-1698` assigning
`application/octet-stream`, which `_sealed_text_inputs` rejects at
`provider_review.py:551-562`. It does not prove the downstream G43 run-status
or closeout assertions; those need the narrow rerun after deterministic media
classification is repaired. The correction must remain offline and synthetic.

The G43 owner must preserve these authority rules while correcting media
classification:

1. Exact input bytes, digest, size, and deterministic media type are sealed in
   the Run Plan and revalidated before provider execution.
2. Provider results remain a separate journaled terminal evidence family; a
   missing, foreign, or unauthenticated `result.json` cannot become Run
   success, blocked, or closeout evidence.
3. Provider review never executes sealed offline Goals as a fallback, and a
   clean provider result is not a no-fix approval or publication authority.

## Runtime explanation from JUnit durations

The JUnit has 4103.447 seconds of testcase time versus the captured wall time
4123.03 seconds. The run was not 69 minutes of uniformly cheap assertions:

- 119 tests took at least 10s and account for 2688.508s.
- 24 tests took at least 30s and account for 1181.712s.
- The ten slowest tests account for 714.166s; the slowest is
  `tests/test_scout_r4_journey.py::test_r4_full_tailor_report_application_journey`
  at 265.153s.
- Other dominant cases include the R4 document journey (76.114s), iterative
  research reuse (76.067s), proposal-record persistence (55.132s), legacy
  research reuse (42.303s), and both-document tailoring integration (41.316s).
- The provider-run-status group accounts for 46.497s despite failing, because
  each case still creates disposable workpads and journal state before the
  media gate is reached.

This duration profile explains the 1:08:43 wall time from the captured JUnit
without rerunning the timing matrix. No timing or broad-suite rerun was done
for this triage.

## Remaining release truth

The captured source matrix remains non-green. Installed 36-pass evidence is
bounded to the corrected affected lane. No live provider/model, Docker,
publication, personal data, activation, or cross-platform proof was performed.
The separate correction owners must resolve or disposition the listed groups,
then run the narrow acceptance commands above and capture a fresh full source
matrix only as the next authorized R7 gate.

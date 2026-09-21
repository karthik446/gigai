# SCOUT-02 — Independent verification

**Date:** 2026-09-08  
**Reviewer:** Luna  
**Scope:** Read-only verification of the completed SCOUT-02 implementation against
the implementation plan/record and accepted SCOUT-00 A02 contract. No source,
test, schema, private Gig, provider, commit, publication, or cleanup operation was
performed. This report is the only project file written by this verification.

## Executive result

The SCOUT-02-focused source checks passed, including the two-graph fixture flow,
independent identity/policy cases, G43 run-plan/run regressions, G43.1 review and
status/framing regressions, and JSL closeout regressions: **74 passed in 150.73s**.
Compilation, the relevant Ruff check, and the source-installed schema verifier also
passed. The complete source suite is **not clean**: it returned **729 passed, 15
failed, 6 errors, 1 skipped, and 7 subtests passed in 447.63s**; the failures are
recorded exactly below.

The implementation was also built as a new wheel and installed into a fresh
temporary venv, separate from the repository `.venv`. Packaged schema, canonical,
and CLI checks passed from that venv. This is genuine built-wheel evidence, not an
editable-import inventory; the wheel imports resolved from temporary
`site-packages`.

## Contract and implementation inputs read

- `SCOUT-02-implementation-plan.md`: additive Graph Set/version path, acceptance
  cases, captured legacy schema digests, and verification boundary.
- `SCOUT-02-implementation.md`: delivered `graph-set -> selection -> v2 Plan ->
  v2 Run` path and owner-recorded 29-pass claim.
- `SCOUT-02-caller-audit.md`: caller/authority map and compatibility hazards.
- `SCOUT-00-contract-amendments.md`, section 1 and A02 matrix: selector versus
  Goal Graph UUID, alias normalization, pre-allocation refusal, historical
  byte-preservation, additive schema versions, agent provenance distinct from
  operator consent, and journal authority.

## Source verification

Commands were run against the current dirty worktree without changing its source
or tests:

```text
rtk .venv/bin/python -m compileall -q src/gigai
  PASS (exit 0)

GIGAI_G30_UAT=0 .venv/bin/pytest -q \
  tests/test_scout02_independent.py \
  tests/test_scout02_graph_set_flow.py \
  tests/test_g43_run_plan.py tests/test_g13_run.py \
  tests/test_g43_provider_review.py \
  tests/test_g43_provider_run_status.py \
  tests/test_g43_response_framing.py \
  tests/test_jsl_closeout_regressions.py
  PASS: 74 passed in 150.73s (0:02:30)

rtk ruff check src/gigai/graph_set.py src/gigai/lifecycle.py src/gigai/run.py \
  src/gigai/run_plan.py src/gigai/cli.py src/gigai/workpad.py \
  tests/test_scout02_independent.py tests/test_scout02_graph_set_flow.py \
  tools/verify_installed_schemas.py
  PASS (exit 0)

.venv/bin/python tools/verify_installed_schemas.py
  PASS: verified 44 installed GigAI schemas
```

The source schema resource and registry inventory each contain **44** strict
schema resources; `src/gigai/schemas/SHA256SUMS` also has 44 entries. The previous
G15-era inventory expected by several unchanged regression tests is **37**, with
these seven additive resources now present:

```text
active-gig-version-v2.schema.json
gig-graph-set.schema.json
gig-proposal-v2.schema.json
graph-selection-record.schema.json
graph-selection-record-v2.schema.json
run-manifest-v2.schema.json
run-plan-v2.schema.json
```

The six pre-SCOUT-02 schema byte digests captured in the implementation plan were
also exercised by `tests/test_scout02_independent.py` as part of the 74 passing
focused cases.

## Complete source suite

Command:

```text
GIGAI_G30_UAT=0 .venv/bin/pytest -q
```

Result:

```text
729 passed, 15 failed, 1 skipped, 6 errors, 7 subtests passed
in 447.63s (0:07:27)
```

The one skipped test was the intentional provider UAT gate:
`tests/test_g30_live_cli.py:19` (`set GIGAI_G30_UAT=1 to invoke real local model CLIs`).
No provider or model call was made.

### Deterministic findings remaining in the complete suite

These ten failures are not socket-permission artifacts:

```text
FAILED tests/test_cli_and_scenario_harness.py::test_installed_help_version_and_goal_approved_commands_are_the_only_surface
FAILED tests/test_g15_review_substrate.py::test_g15_additive_schema_inventory_is_exact
FAILED tests/test_g16_review_loop.py::test_g16_additive_schema_inventory_preserves_g15_baseline
FAILED tests/test_g17_capabilities.py::test_g17_additive_schema_inventory_and_baseline_hashes
FAILED tests/test_g19_target_effect_contract.py::test_g19_adds_the_twenty_third_schema_resource
FAILED tests/test_g20_learning_contract.py::test_g20_adds_exactly_two_schema_resources
FAILED tests/test_g21_contract.py::test_g21_adds_exactly_two_packaged_resources
FAILED tests/test_g22_proposal_interview_contract.py::test_g22_schema_is_additive_and_validates_approved_snapshot
FAILED tests/test_g26_builder_contract.py::test_g26_adds_two_packaged_contract_resources
FAILED tests/test_g27_discovery_contract.py::test_g27_adds_one_schema_resource_and_accepts_create_manifest
```

The nine G15/G16/G17/G19/G20/G21/G22/G26/G27 failures assert
`len(SCHEMA_NAMES) == 37`; the current additive registry is 44. The installed
surface failure's exact assertion expects the old bare-command diagnostic text
without the current `run-plan` command wording. No checks were relaxed or fixed
during this review.

### Environment findings from the complete suite

Five failures were caused by the sandbox denying loopback socket binding, not by
the implementation. The exact failures were:

```text
FAILED tests/test_g22_http_approval.py::test_http_answers_then_operator_approval_reaches_terminal_lifecycle
FAILED tests/test_g22_proposal_interview.py::test_loopback_http_requires_token_and_preserves_session_boundary
FAILED tests/test_g22_proposal_interview.py::test_loopback_http_rejects_malformed_payload_and_expires
FAILED tests/test_g26_review_actions.py::test_builder_review_can_revise_rebuild_and_reject_without_activation
FAILED tests/test_setup_browser.py::test_setup_page_uses_human_model_labels_and_reports_cli_detection
```

The same source files were rerun with socket permission in a bounded escalated
environment:

```text
GIGAI_G30_UAT=0 .venv/bin/pytest -q \
  tests/test_g22_http_approval.py tests/test_g22_proposal_interview.py \
  tests/test_g26_review_actions.py tests/test_setup_browser.py
  PASS: 18 passed in 9.85s
```

Six schema setup errors all share one cause: the contract-spike test fixture's
legacy `EXPECTED_SCHEMA_NAMES` rejects the seven additive files. Exact errors:

```text
ERROR research/contract_spike/tests/test_schemas.py::SerializedContractTests::test_all_schema_documents_are_valid_draft_2020_12
ERROR research/contract_spike/tests/test_schemas.py::SerializedContractTests::test_goal_graph_semantics_accept_valid_graph
ERROR research/contract_spike/tests/test_schemas.py::SerializedContractTests::test_goal_graph_semantics_reject_cycles
ERROR research/contract_spike/tests/test_schemas.py::SerializedContractTests::test_goal_graph_semantics_reject_unreachable_required_goal
ERROR research/contract_spike/tests/test_schemas.py::SerializedContractTests::test_one_golden_instance_for_every_serialized_boundary
ERROR research/contract_spike/tests/test_schemas.py::SerializedContractTests::test_unknown_field_missing_required_field_and_malformed_id_fail
```

## Isolated built-wheel verification

The initial offline build attempt could not resolve the declared
`setuptools>=77` requirement because the local uv cache was unavailable to the
sandbox. A copied source tree was then built under the fresh temp root
`/var/folders/bd/84nzd4j57q38v18c2mwz35tc0000gn/T/tmp.fdLfrL52dU` with uv's normal
resolver; the repository source tree, project `dist`, and shared `.venv` were not
used as install targets. The resulting artifact was:

```text
gigai-0.1.6-py3-none-any.whl
```

It was installed, with declared dependencies, into the separate temp venv at
`.../tmp.fdLfrL52dU/venv`; no install was performed over `.venv`. The installed
module locations were all under that venv's `site-packages`, including
`gigai/canonical.py`, `gigai/cli.py`, `gigai/graph_set.py`, and `gigai/schemas`.

Packaged checks:

```text
env -u PYTHONPATH <temp-venv>/bin/python tools/verify_installed_schemas.py
  PASS: verified 44 installed GigAI schemas

<temp-venv>/bin/python canonical vector verifier
  PASS: all canonical-vectors.json bytes and SHA-256 digests matched

<temp-venv>/bin/python registry/resource verifier
  PASS: SCHEMA_NAMES and packaged resources matched, 44/44

<temp-venv>/bin/gigai --version
  PASS: gigai 0.1.6

<temp-venv>/bin/gigai --help
  PASS: public CLI help emitted; graph-set and run-plan present

<temp-venv>/bin/gigai graph-set --help
  PASS: Graph Set command help emitted

<temp-venv>/bin/gigai run-plan --help
  PASS: v2-aware run-plan help emitted
```

The wheel's `0.1.6` version is the current `pyproject.toml` metadata observed in
this v0.1.7 worktree; it is recorded as provenance, not silently treated as a
v0.1.7 package claim.

## Verification boundary and disposition

No provider/network model calls, private G44 Gig operations, activation, commit,
publishing, or broad cleanup occurred. The focused SCOUT-02 evidence supports the
implementation's structural two-graph and compatibility behavior, and the
isolated wheel proves packaged schema/canonical/CLI resources are present and
verifiable. A clean SCOUT-02 completion cannot be claimed from this run while
the complete-suite legacy 37-resource assertions, six schema setup errors, and
the installed-surface diagnostic mismatch remain recorded findings; no source or
test edits were made to resolve them.

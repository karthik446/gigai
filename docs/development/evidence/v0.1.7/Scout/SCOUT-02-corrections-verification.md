# SCOUT-02 — Correction verification

**Date:** 2026-09-08  
**Reviewer:** Luna (independent verifier)  
**Worktree:** `/Users/kar/orca/workspaces/gigai/gigai-v0.1.7`  
**Observed branch/HEAD:** `karthik446/gigai-v0.1.7` / `fda4857` (`docs: hand off G43.1 provider dogfood`)

## Scope and boundary

This is an independent verification of Terra's correction handoff against the
original SCOUT-02 verification, the original independent review, accepted
SCOUT-00/A02 contracts, and the accepted user-owned Gig amendment. Source was
frozen for this run. No source, test, schema, private Gig, provider/model,
commit, tag, publication, or broad cleanup operation was performed; this file
is the only project file written by this verification.

The worktree was already dirty with coordinator-owned SCOUT-02 files and
unrelated G43.1 work. Those changes were preserved. The source/test state was
checked before and after the run; no new source or test path was added by this
verification.

## Baseline and correction context

The original Luna report (`SCOUT-02-verification.md`) recorded 74 focused
passes and a non-clean full run of 729 passed, 15 failed, 6 errors, 1 skipped,
and 7 subtests. Its deterministic findings were stale 37-resource assertions,
six contract-spike schema setup errors, and one bare-command diagnostic
expectation; five additional failures were restricted-sandbox loopback socket
bind failures. The correction implementation handoff recorded strict nested
v2 schemas, version-aware readers, README/inventory synchronization, exact
journaled agent-invocation provenance, v2 provider-review recovery, and
updated fixture/assertion coverage; its recorded full result was 747 passed,
5 loopback failures, 1 skipped, and 94 subtests.

Those handoff numbers are recorded inputs, not substituted for this run's
execution. The checks below were rerun from the current frozen worktree.

## Focused correction and legacy regressions

Exact command:

```text
GIGAI_G30_UAT=0 .venv/bin/pytest -q tests/test_scout02_review_corrections.py tests/test_scout02_graph_set_flow.py tests/test_scout02_independent.py tests/test_g43_run_plan.py tests/test_g13_run.py tests/test_g43_provider_review.py tests/test_g43_provider_run_status.py tests/test_g43_response_framing.py tests/test_jsl_closeout_regressions.py tests/test_canonical.py tests/test_canonical_ownership.py tests/test_g23_portability.py tests/test_g21_comparison.py tests/test_g21_occurrence.py tests/test_index_projection.py tests/test_bug_001_gigs_listing.py tests/test_journal_locking_recovery.py
```

Result: **209 passed in 220.29s (0:03:40)**.

This execution covered the correction vectors for unknown/missing/wrong-type or
enum/malformed nested v2 Plan and Run-manifest members; valid v2 listing;
typed inspection-only portability refusal; v2 occurrence/comparison reads and
cross-selected-graph identity; v2 provider-review recovery; and missing,
unjournaled, tampered, and actor-mismatched agent invocation evidence. The
legacy slices retained v1 identity/bytes, canonical behavior, portability,
comparison, occurrence, listing, index projection, journal recovery, G43/G43.1
status/framing, and JSL closeout behavior.

## Compile, Ruff, and schema inventory

The first attempted Ruff path, `.venv/bin/ruff`, was unavailable (`no such
file or directory`). The available system `ruff` was version `0.9.2`; using
that executable for the same targeted check produced the following exact
result:

```text
.venv/bin/python -m compileall -q src/gigai
  exit 0

ruff check src/gigai/graph_set.py src/gigai/lifecycle.py src/gigai/run.py src/gigai/run_plan.py src/gigai/cli.py src/gigai/workpad.py src/gigai/listing.py src/gigai/portability.py src/gigai/comparison.py src/gigai/occurrence.py tests/test_scout02_review_corrections.py tests/test_scout02_graph_set_flow.py tools/verify_installed_schemas.py
  All checks passed!

.venv/bin/python tools/verify_installed_schemas.py
  verified 44 installed GigAI schemas

.venv/bin/python -c 'from pathlib import Path; from gigai.validators import SCHEMA_NAMES; names=sorted(p.name for p in Path("src/gigai/schemas").glob("*.schema.json")); sums=[line.split()[1] for line in Path("src/gigai/schemas/SHA256SUMS").read_text().splitlines() if line.strip()]; print(f"source_resources={len(names)} registry={len(SCHEMA_NAMES)} checksums={len(sums)} source_registry_equal={names==sorted(SCHEMA_NAMES)}")'
  source_resources=44 registry=44 checksums=44 source_registry_equal=True
```

The seven additive SCOUT-02 resources are present in the source resource
directory, registry, checksum file, README, and verifier inventory. The six
pre-SCOUT-02 schema byte digests remained covered by the independent focused
tests.

## Complete offline source suite

Exact command:

```text
GIGAI_G30_UAT=0 .venv/bin/pytest -q
```

Restricted-sandbox result:

```text
747 passed, 5 failed, 1 skipped, 94 subtests passed in 458.70s (0:07:38)
```

The one skip is the deliberate provider gate:
`tests/test_g30_live_cli.py:19` (`set GIGAI_G30_UAT=1 to invoke real local
model CLIs`). No provider or model call was made.

All five failures were loopback socket-binding permission errors, not
correction failures:

```text
tests/test_g22_http_approval.py::test_http_answers_then_operator_approval_reaches_terminal_lifecycle
tests/test_g22_proposal_interview.py::test_loopback_http_requires_token_and_preserves_session_boundary
tests/test_g22_proposal_interview.py::test_loopback_http_rejects_malformed_payload_and_expires
tests/test_g26_review_actions.py::test_builder_review_can_revise_rebuild_and_reject_without_activation
tests/test_setup_browser.py::test_setup_page_uses_human_model_labels_and_reports_cli_detection
```

The exact permission-enabled rerun was:

```text
GIGAI_G30_UAT=0 .venv/bin/pytest -q tests/test_g22_http_approval.py tests/test_g22_proposal_interview.py tests/test_g26_review_actions.py tests/test_setup_browser.py
  18 passed in 10.02s
```

The rerun used the normal approved sandbox escalation solely to permit the
tests' loopback binds. No test, source, fixture, or assertion was changed.
Consequently, the post-correction complete suite has no remaining deterministic
SCOUT-02 inventory, schema-fixture, or CLI-expectation failures; the residual
full-suite limitation is the restricted environment's socket policy.

## Independent clean-room wheel and isolated installation

A fresh source copy was created at:

```text
/private/tmp/gigai-scout02-independent.ibKVOU
```

The copy excluded `.git`, the project `.venv`, and the project `dist`; the
wheel was built into the copy's `dist/`, not the repository `dist/`. The first
build using the default shared uv cache failed because the sandbox could not
open `/Users/kar/.cache/uv/sdists-v6/.git`. A retry with a task-local
`UV_CACHE_DIR=/private/tmp/gigai-scout02-independent.ibKVOU/uv-cache` used
normal resolution but could not resolve `setuptools>=77` because DNS/network
access was unavailable. This is the known cache/system-tooling limitation
(system setuptools is 75.8, below the declared build requirement), not a
relaxed build.

After the normal dependency-resolution escalation was approved, this exact
command succeeded:

```text
UV_CACHE_DIR=/private/tmp/gigai-scout02-independent.ibKVOU/uv-cache uv build --out-dir /private/tmp/gigai-scout02-independent.ibKVOU/dist
  Successfully built dist/gigai-0.1.6.tar.gz
  Successfully built dist/gigai-0.1.6-py3-none-any.whl
```

The wheel metadata is `0.1.6`, which is the current `pyproject.toml` value in
this v0.1.7 worktree; it is recorded as artifact provenance and is not a
v0.1.7 release claim.

A fresh isolated environment was created at
`/private/tmp/gigai-scout02-independent.ibKVOU/venv`. Normal installation with
declared runtime dependencies initially hit the same restricted DNS failure
(while resolving `questionary`); after the normal dependency-resolution
escalation, the wheel installed 17 packages successfully. The repository
`.venv` and repository `dist` were not used as installation targets.

With `PYTHONPATH` explicitly unset, import paths proved the installed package,
not the checkout, was loaded:

```text
gigai=/private/tmp/gigai-scout02-independent.ibKVOU/venv/lib/python3.13/site-packages/gigai/__init__.py
canonical=/private/tmp/gigai-scout02-independent.ibKVOU/venv/lib/python3.13/site-packages/gigai/canonical.py
cli=/private/tmp/gigai-scout02-independent.ibKVOU/venv/lib/python3.13/site-packages/gigai/cli.py
graph_set=/private/tmp/gigai-scout02-independent.ibKVOU/venv/lib/python3.13/site-packages/gigai/graph_set.py
schemas=/private/tmp/gigai-scout02-independent.ibKVOU/venv/lib/python3.13/site-packages/gigai/schemas/__init__.py
```

Installed-package checks, all with `env -u PYTHONPATH`, passed:

```text
tools/verify_installed_schemas.py
  verified 44 installed GigAI schemas
tools/verify_installed_canonical.py
  verified installed GigAI canonical identity API
tools/verify_installed_cli.py
  verified installed GigAI CLI: help, version, setup, doctor, init, create, feedback, revise, approve, reject, gigs, proposals, status, show, history, plan, run, run-details, workpad path, check, and open only

venv/bin/gigai --version
  gigai 0.1.6
venv/bin/gigai --help
  exit 0; graph-set and run-plan markers present
venv/bin/gigai graph-set --help
  exit 0
venv/bin/gigai run-plan --help
  exit 0
installed canonical-vectors.json check
  verified installed canonical vectors=3
```

This is genuine wheel/site-packages evidence. It is not an editable-import
check and did not use the project `.venv`.

## Acceptance limits and final outcome

**Final outcome: SCOUT-02 correction verification passes for the requested
offline/source/package evidence.** Current focused corrections and legacy
regressions pass, strict v2 nested validation and exact agent provenance are
exercised, all 44 schema resources agree across source/registry/checksums and
installed package, and the clean-room wheel/isolated CLI/schema/canonical
checks pass.

This report does not claim provider/model execution, installed Codex or Claude
external recording, live G30 UAT, private/user-owned Gig workflow acceptance,
activation, publishing, or a v0.1.7 release. The deliberate live-provider test
remains skipped, and the complete suite's five raw failures are environment
permission failures proven green by the permission-enabled 18-test rerun.
SCOUT-02 acceptance remains subject to the coordinator's acceptance gate and
the accepted A02 evidence boundary; no provider or user-state authority is
implied by this verification.


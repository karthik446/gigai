# SCOUT-03 implementation handoff

**Date:** 2026-09-08  
**Status:** Implemented and offline-fixture verified; not accepted.  
**Worker:** Terra (`task_ec34c05cade5`, `ctx_8ab74b3542cd`)

## Delivered boundary

- Added additive strict schemas for `workpad-layout:2`, G45
  `private-reference:1` and `run-input-record:1`, Scout
  `private-record-revision:1`, and typed operation receipts.  Existing schema
  bytes remain unchanged; package inventory, installed verifier, golden
  fixtures, and legacy additive inventory assertions now recognize 49 schemas.
- Added explicit v1-to-v2 layout migration.  It journals an exact ignore policy
  and `manifests/workpad-layout.json`; v1 remains unchanged until that action,
  while unknown/forged layouts, roots, markers, redirects, and root collisions
  refuse.
- Added journal-backed G45 local UTF-8/Markdown imports at canonical paths,
  idempotent digest/kind imports, strict immutable wrappers, parent-CAS record
  revisions, operation-key receipts, metadata-only listing/context, and the
  sole explicit `record read --content` payload path.  Imports do not fetch,
  call a provider, allocate a Run, or grant disclosure authority.
- Added `layout migrate`, `reference add/list/show`, `run-input add/show`, and
  `record create/read` CLI boundaries.  The deferred external Plan/Run executor
  was not added.  Managed G43 Plan construction and Run ingress now refuse
  sources from private `references/`, `run-inputs/`, `records/`, or `docs/`
  roots before provider use.
- Added a shared state-database flock (`journal writer -> database` order),
  wired the lifecycle G22 trace writer through it, and preserve the closed
  Scout projection table family when rebuilding the legacy index.  Scout tables
  and `indexes/context.json` are rebuildable views; records and receipts remain
  journal authority.

## Acceptance mapping

| Requirement | Evidence / implementation |
| --- | --- |
| 1 G45 exact immutable imports | `private_records.import_reference` / `import_run_input`; `tests/test_scout03_private_records.py` proves byte preservation and equivalent-import reuse. |
| 2 CAS and historical revision identity | `create_record` checks current parent and operation receipt identity; focused fixture proves stale parent refusal. |
| 3 recoverable authority then projection | journal transaction publication remains the mutation path; `rebuild_scout_projection` follows committed publication and never promotes SQLite edits. |
| 4 G22 + Scout shared SQLite | `index.database_lock`, lifecycle trace lock, closed table preservation, and `tests/test_index_projection.py`. |
| 5 v1/v2 boundary | `workpad.py` retains v1 policy; `migrate_workpad_layout` is explicit and journal-authenticates v2. |
| 6 metadata recovery and no provider leak | redacted context projection / named content read; managed Plan/Run private-root refusal. |
| 7 schemas and installed resources | schema verifier and isolated built wheel passed below. |

## Files owned in this wave

- Runtime: `src/gigai/private_records.py`, `canonical.py`, `workpad.py`,
  `journal.py`, `index.py`, `lifecycle.py`, `run_plan.py`, `run.py`, `cli.py`,
  `validators.py`.
- Schemas/inventory: five new files in `src/gigai/schemas/`, `SHA256SUMS`,
  schema README, and `tools/verify_installed_schemas.py`.
- Tests: `tests/test_scout03_private_records.py`, schema golden fixtures, and
  existing schema inventory-count assertions required by the additive family.

## Commands and results

| Command | Result |
| --- | --- |
| `rtk .venv/bin/python -m compileall -q src/gigai` | Pass. |
| `rtk .venv/bin/pytest -q tests/test_index_projection.py tests/test_workpad_private_git.py tests/test_canonical.py research/contract_spike/tests/test_schemas.py` | Pass: 81 tests, 97 subtests. |
| `rtk .venv/bin/pytest -q tests/test_scout03_private_records.py` | Pass: 2 tests. |
| `rtk .venv/bin/pytest -q tests/test_canonical_ownership.py tests/test_g15_review_substrate.py` | Pass: 13 tests. |
| `rtk .venv/bin/pytest -q -x tests/test_cli_and_scenario_harness.py tests/test_bug_002_cli_surface.py tests/test_scout02_independent.py tests/test_scout02_review_corrections.py tests/test_scout03_private_records.py` | Pass: 44 tests. |
| `rtk .venv/bin/python tools/verify_installed_schemas.py` | Pass: 49 installed schemas. |
| `rtk uv build` | Pass: built `dist/gigai-0.1.6.tar.gz` and wheel. |
| `/private/tmp/gigai-scout03-wheel.LLin9H/venv/bin/pip install --no-deps dist/gigai-0.1.6-py3-none-any.whl` then its Python running `tools/verify_installed_schemas.py` | Pass: isolated installed wheel reports 49 schemas. |

## Verification limitation and follow-up

The initial aggregate `rtk .venv/bin/pytest -q` exposed the canonical-owner
test failure caused by a local `hashlib` use; that use was replaced with the
central canonical digest API, and the focused canonical suite then passed. A
later aggregate full-suite invocation reached 9% with no new failure before
this environment's fixed 30-second foreground-command cutoff; attempts to
detach it were terminated by the runner before it flushed a result. Therefore
the focused and wheel evidence above is recorded as successful, but an
uninterrupted full offline-suite result remains a coordinator/reviewer follow-up
and must not be inferred from this handoff.

No provider/network calls, user `.gigai` mutations, configuration changes,
commits, init/default-template workflows, registry-v3 work, or external Run
executor were performed.

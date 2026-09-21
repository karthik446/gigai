# SCOUT-05 init corrections

**Date:** 2026-09-09  
**Scope:** bounded remediation of `SCOUT-05-init-review.md` against the
accepted user-owned-workspace amendment §2–§3 and contract amendment B3.

## Delivered corrections

- New default instances still start from the existing safe v1 substrate, then
  immediately publish the existing authenticated `workpad_layout_migrated`
  journal transition before their template binding.  The committed v2 marker
  and exact v2 ignore policy are therefore the admitted layout authority; no
  writable marker or unjournaled ignore change is accepted.
- `gigai init --json` now keeps `DefaultInitError.code` in the standard typed
  error envelope.  Successful payloads include `setup_steps`; the result now
  accurately reports `binding_only_unready` and says source/proposal preparation
  remains pending, rather than claiming a proposal exists.
- Resume reads only the immutable committed
  `manifests/template-instance-binding.json` while holding the writer lock.
  A missing artifact and an unborn workpad are typed states; a replacement,
  malformed publication, or mismatched working binding produces
  `template_reconciliation_required`.  Existing registry cache hits are
  rechecked against that committed binding and its digest/commit before reuse.
- Pending intents pin a canonical owner-row payload digest over project ID,
  owner ID, and username.  Resume verifies the current row and username before
  any new publication.  Inventory mismatch remains a deliberate refusal and
  now directs the operator to re-run with the package that started the pinned
  batch; it never recreates unavailable inventory from IDs.
- Username validation now rejects Unicode `Cc` controls (including C1) in both
  user input and stored registry rows.  It deliberately continues to permit
  legitimate joiners and private-use code points: the contract says no control
  characters, not a blanket ban on all Unicode category-C code points.
- Registry migration is now explicit: read-only `open_project_registry` calls
  refuse a v1/v2 registry with `registry_migration_required`; init is the
  allowed migration entry and reports observed v3 setup steps.  The existing
  additive backup/transaction procedure is retained.

## Finding disposition

| Finding | Disposition |
| --- | --- |
| B1 | Fixed by journal-authenticated v2 migration for each new default before the binding transition. |
| B2 | Fixed by `_raise_cli_error(..., code=...)` for init failures. |
| C1 | Fixed with single-artifact committed lookup and cache authority verification; unrelated `manifests/` working files no longer make a binding appear absent. |
| C2 | Fixed with intent `owner_row_sha256` and retry verification. |
| C3 | Fixed narrowly for Unicode controls (`Cc`) on input and stored rows; no unsupported broad Unicode ban. |
| C4 | Fixed with an explicit `allow_migration` registry boundary and init `setup_steps`. |
| C5 | Fixed only as an actionable deliberate conflict diagnostic; this slice cannot reconstruct a missing newer inventory from its IDs. |
| C6 | No disk-order change: the previous `intent = {}` assignment is in-memory and `_atomic` replaces the intent with a complete pending object, so adding a second disk operation would not close a real persistence window. |
| C7 | Fixed by typed `JournalUnbornError`; no exception-message branch remains. |
| C8 | Fixed by resolving `home_root` once at entry and passing that value through all reads/writes. |
| C9 | Preflight still occurs before package writes.  The documented effective order is target/package initialization lock, registry transaction/migration boundary, then per-workpad journal writer; the default-init lock serializes the post-package pinned batch.  It was not moved ahead of target safety checks. |

## Focused verification

All commands used disposable synthetic targets and registries; no real `.gigai`
home, provider, approval, activation, tool execution, or commit was used.

| Command | Result |
| --- | --- |
| `.venv/bin/pytest -q tests/test_scout05_init.py tests/test_scout05_init_corrections.py` | 14 passed in 16.48s during final regression run. |
| `uv run ruff check src/gigai/default_init.py src/gigai/registry.py src/gigai/target_binding.py src/gigai/workpad.py src/gigai/journal.py tests/test_scout05_init.py tests/test_scout05_init_corrections.py` | Passed. |
| `.venv/bin/python -m py_compile ...` for the owned modules, init CLI, and focused tests | Passed. |

The wider `cli.py` has unrelated pre-existing Ruff failures in private-record
commands, so it was compiled but not mass-reformatted outside this task's init
CLI ownership.

## Remaining gates

This does not copy inventoried template source, create a source-backed proposal,
or make Scout release-eligible.  Root tool source approval binding, default
source/proposal integration, central schema/resource inventory updates, and
the remaining SCOUT-05 through SCOUT-10 domain/release evidence remain separate
work.

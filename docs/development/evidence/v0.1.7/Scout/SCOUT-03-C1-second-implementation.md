# SCOUT-03 C1 — Second storage correction implementation

**Date:** 2026-09-08  
**Status:** Bounded C1 correction implemented; independent review and final
verification remain required.  
**Boundary:** C2 native/default/archive behavior and C3 tool/Plan selection are
not implemented or claimed here.

## Corrected storage behavior

- Private import, receipt, and revision enumeration now comes from a single
  writer-locked, pinned committed Git snapshot. The snapshot verifies every
  expected working-tree private file and rejects missing, changed, redirected,
  or extra uncommitted private evidence; unrelated editable Gig roots remain
  outside that check.
- Request identity is formed before generated record/revision IDs. Replay of a
  create without an explicit record ID returns the original strict receipt and
  original IDs without a new commit; explicit IDs, actor, origin, media,
  content, and parent remain semantic conflict inputs. Content reads return the
  verified snapshot bytes only.
- Scout records and receipt projections are derived from the same pinned HEAD.
  The shared SQLite inode is updated under the database lock after a closed
  object/schema check; unexpected views, triggers, indexes, or tables refuse
  before mutation, preserving G22 traces. Context publication uses unique,
  symlink-safe staging under that same lock and carries the pinned cursor.
- A post-commit projection failure is represented by separate operation-result
  fields (`projection_pending`, `rebuild_action`), leaving committed receipt
  bytes schema-valid and unchanged. Retry invokes only rebuild recovery.
- Journal recovery retains ordinary legacy mutable-artifact replacement,
  preserves private immutable no-clobber semantics, and can recover only a
  scope-bound prepared layout migration. Normal v2 admission never accepts
  prepared state; v1 `tools/` and `reports/` remain legitimate migration state.

## Focused evidence

```text
.venv/bin/pytest -q tests/test_scout03_c1_acceptance.py
# 11 passed in 26.00s

.venv/bin/pytest -q tests/test_scout03_private_records.py \
  tests/test_index_projection.py tests/test_workpad_private_git.py \
  tests/test_journal_locking_recovery.py \
  tests/test_g22_proposal_interview_contract.py
# 47 passed in 33.17s

.venv/bin/pytest -q tests/test_g22_proposal_interview.py \
  tests/test_g22_http_approval.py
# 13 passed in 2.70s (loopback socket-enabled run)

.venv/bin/python -m compileall -q src/gigai
ruff check <scoped C1 files/tests>
.venv/bin/python tools/verify_installed_schemas.py
git diff --check
```

The coordinator acceptance file now additionally covers hidden-current CAS,
extra private working evidence, and the allowed v1 `tools/`/`reports/` layout
migration case. These are focused offline tests; no full suite, provider call,
private UAT, C2, or C3 evidence is implied.

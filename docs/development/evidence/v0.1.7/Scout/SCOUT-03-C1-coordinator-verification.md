# SCOUT-03 C1 — Coordinator verification

**Status:** Changes requested; C2 must not start on this handoff.  
**Scope:** Completed C1 source only; no provider calls or private user data.

Terra's initial C1 handoff reports 47 focused tests and 13 G22 HTTP tests
passing. The handoff is useful progress, but the following coordinator tests
reproduce five remaining failures:

```text
.venv/bin/python -m pytest -q tests/test_scout03_c1_acceptance.py
5 failed in 8.37s
```

All operations used disposable pytest workpads. No implementation worker was
active during these probes; no broad suite ran concurrently.

| Case | Observed result | Required correction |
| --- | --- | --- |
| Identical create without an explicit record ID | Second call raises `private_operation_conflict` | Normalize caller intent before generating record IDs; replay original IDs/receipt with no commit |
| Modify an imported snapshot after creating its wrapper | Exact content read succeeds using the uncommitted replacement | Validate the selected complete reference and every path component; refuse divergence and return authenticated bytes, never re-read an unchecked working file |
| Remove the latest committed revision from the working copy | Current read silently returns the older revision | Enumerate from pinned committed history; missing working evidence must be diagnosed, never treated as absence from history |
| Projection raises `RuntimeError` after publication | Caller receives an exception although the import committed | Return the committed receipt plus a separate pending-projection result; replay/rebuild must repair without another event |
| Pre-create `indexes/.context.tmp` as a symlink | Rebuild overwrites the symlink target outside the Gig | Unique bounded staging plus component validation; preserve unrelated bytes and serialize/coherently publish the view |
| Add an unexpected trigger to a managed Scout table | Rebuild executes the trigger and deletes the seeded G22 trace row | Validate the complete closed database object/schema inventory under the writer lock before any mutation; refuse unknown executable SQL without altering trace |

The sixth case was added and run separately after the first five:

```text
.venv/bin/python -m pytest -q tests/test_scout03_c1_acceptance.py -k trigger
1 failed, 5 deselected in 1.88s
```

The database probe used a disposable table with the exact accepted G22 column
shape and one seeded event. The unexpected trigger fired during
`DELETE FROM scout_records`, leaving zero interview events. Matching table
names/column names alone do not satisfy the closed database contract.

Code paths: `private_records.py:create_record`, `_publish`, `read_record`,
`list_revisions`, `rebuild_scout_projection`; `journal.py:read_committed_artifact`.
The exact test names and setup are retained in
`tests/test_scout03_c1_acceptance.py`.

The source also still scans working directories and reads moving journal HEADs
when producing the purported HEAD-pinned Scout view. Merely adding a cursor
field is not snapshot consistency. The pending independent C1 review will
check this and the remaining migration/authentication/shared-database cases
before a consolidated correction dispatch.

## Evidence disposition

- Do not change the original handoff to claim these tests passed. Its test
  coverage is narrower than the delivered-boundary prose.
- The new tests must remain real acceptance assertions, not xfails/skips or
  assertions rewritten around the current bugs. Update earlier tests that
  incorrectly endorse tampered working evidence to match the accepted C1
  contract.
- Native/default/archive records and tool/Plan binding remain C2/C3. Those
  omissions are not findings against this bounded C1 pass.
- C2 task `task_d8d515fa409f` is not dispatched. Its dependency task settling
  does not override this coordinator acceptance gate.
- Independent Claude review is task `task_38e35bb47331`, dispatch
  `ctx_0b3a8bd0519f`, in this worktree. It is report-only and has no permission
  to change source or run a competing test suite.

## Subsequent disposition

The [independent review](SCOUT-03-C1-review.md) is complete and changes requested.
Two additional recovery regressions bring the reproduced set to eight cases;
their exact results and a fixture correction are recorded in the
[consolidated second-pass brief](SCOUT-03-C1-second-pass.md). It also corrects
several overly broad or optimistic statements in the independent report.
Second-pass Terra implementation is active as `task_33a635107c45` /
`ctx_28cc3ba4652c`. C2 remains blocked. No acceptance is implied by dispatch.

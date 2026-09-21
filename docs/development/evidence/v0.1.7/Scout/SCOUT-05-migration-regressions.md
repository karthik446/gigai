# SCOUT-05 explicit registry migration regressions

## Contract applied

The registry migration boundary is explicit in the current implementation:

- `open_project_registry(..., create=False)` is a read-only opener and must
  refuse v1 or predecessor v2 with typed `RegistryMigrationRequired` before
  creating a backup or changing any database bytes.
- A migration-intending caller must pass `allow_migration=True`; this is the
  existing init-authorized path and is not inferred from `create=False`.
- v1 is pinned as predecessor 1, v2 is pinned as predecessor 2, and current
  schema 3 adds `workspace_owners` and `template_instances` to the retained
  `projects`, `workpads`, and `active_workpads` tables.

## Regression changes

`tests/test_registry_v2_migration.py` now:

- asserts the v1/v2/current constants and exact additive current table set;
- runs populated-v1 migration with explicit `allow_migration=True`, retaining
  exact project/workpad/active rows and asserting both the retained v1 backup
  and v2 predecessor backup;
- asserts read-only populated-v1 and populated-v2 opens fail with
  `RegistryMigrationRequired`, with byte-identical live databases and no
  backup publication;
- preserves v1 backup conflict, malformed/partial-schema, crash, failpoint,
  concurrent-opener, and old-reader guarantees while passing explicit
  migration authority through every migration-intending embedded script;
- adds a populated-v2 fixture built from the canonical v1 fixture and tests
  exact v2 rows survive additive v2→v3 migration;
- covers v2→v3 exception failpoints and bounded subprocess crashes, checking
  that only v2 or complete v3 versions are observable, v2 backup integrity is
  preserved, and recovery converges to v3 without temporary artifacts;
- keeps the legacy v1 reader's deliberate refusal of live v3 and adds a
  deliberate exact-schema legacy-v2 reader refusal of live v3.

No production source, registry implementation, init, package, template,
schema, private `.gigai` home, provider, activation, commit, or release file
was changed by this lane. No new v3 fixture file was needed: the test creates
the predecessor v2 database from the tracked synthetic v1 fixture using the
existing schema constants and deterministic rows.

## Exact focused verification

```text
.venv/bin/pytest -q tests/test_registry_v2_migration.py
# 37 passed in 0.89s

ruff check tests/test_registry_v2_migration.py
# All checks passed!

python -m py_compile tests/test_registry_v2_migration.py
# passed (no output)
```

The subprocess crash/concurrency waits remain bounded at 20 seconds for
crash/convergent openers and 10 seconds for coordinated lock-release checks.
No full repository suite, wheel build, real init, provider execution, or
commit was run.

## Remaining boundary

The explicit migration flag remains a caller authority decision: ordinary
read-only callers must continue to surface `registry_migration_required`,
while `gigai init` (and only another explicitly authorized migration caller)
may use `allow_migration=True`. This evidence does not claim package/resource
inventory completion or release readiness; those remain with the coordinator
and Terra's separate initialization/inventory work.

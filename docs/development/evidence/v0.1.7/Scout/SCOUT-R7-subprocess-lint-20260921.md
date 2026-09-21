# SCOUT R7 subprocess shell lint — 2026-09-21

Status: **owned capability-review correction complete; source diagnostic remains
blocked by an unowned next site.**

This bounded correction changed only the worker-owned
`src/gigai/capability_review.py` subprocess boundary. Existing literal argv,
timeout behavior (none was present), capture behavior, `check=False`, and
capability-review authority decisions were preserved. No provider/model call,
private data, activation, publication, model download, commit, reset, or broad
suite rerun was performed.

## Owned correction

`src/gigai/capability_review.py:201-206` now passes the explicit literal
`shell=False` keyword to the fixed-argv `git -C <workpad> show HEAD:<path>`
call. The call retains `capture_output=True` and `check=False`; no other
subprocess call exists in the owned file.

## Focused verification

Command:

```text
rtk proxy .venv/bin/pytest -q \
  tests/test_setup_configuration_diagnostics.py::test_product_subprocesses_are_literal_argv_with_shell_disabled \
  tests/test_scout05_capability_review.py \
  tests/test_scout05_reviewed_manifest_guard.py \
  tests/test_scout05_capability_successor.py
```

Result: **55 capability-review tests passed; the diagnostic failed at the next
unowned product site**, for a total of `55 passed, 1 failed` in 152.29s. The
diagnostic no longer reports `capability_review.py`.

The next exact correction site is `src/gigai/application_events.py`:

- `:86-91`, `_journal_sequence()` `git log` subprocess call lacks
  `shell=False`.
- `:97-102`, `_journal_sequence()` `git show --name-only` subprocess call
  lacks `shell=False`.
- `:111-115`, `_journal_sequence()` handoff-content `git show` subprocess call
  lacks `shell=False`.

That unowned file was not edited.

## Ruff

The global Ruff executable was confirmed before linting:

```text
rtk sh -c 'command -v ruff'
=> /Users/kar/.pyenv/shims/ruff
```

The owned file and all changed core/media correction Python files were linted
without the shared uv cache:

```text
rtk ruff check \
  src/gigai/scout_interview_records.py src/gigai/default_init.py \
  src/gigai/lifecycle.py src/gigai/occurrence.py \
  src/gigai/scout_materialization.py src/gigai/run_plan.py \
  src/gigai/capability_review.py
=> clean
```

Read-only Ruff lint also covered the other-owner correction files:
`research/contract_spike/tests/test_schemas.py`,
`tests/test_g17_capabilities.py`, `tests/test_scout06_source_contract.py`,
`tests/test_g43_text_media.py`, `tests/test_canonical_ownership.py`,
`tests/test_g08_offline_create_lifecycle.py`, `tests/test_g21_occurrence.py`,
`tests/test_g41_package_boundary.py`, and `tests/test_g42_catalog.py`; no Ruff
errors were reported and none of those files was altered.

## IPC and release boundary

The required coordinator follow-up checks before work, after the test run, and
before completion returned `runtime_unavailable` because the running Orca app
was unreachable. This IPC failure did not block local evidence capture; the
coordinator should independently inspect the exact next-site list above.

This report is bounded source evidence only. It does not establish full source
matrix health, installed-wheel acceptance, live provider proof, publication
authorization, or UAT readiness.

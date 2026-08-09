# S18-01 verification report

The matrix and replay boundary are executable research artifacts, not runtime
adapter code.

```text
uv run pytest -q tests/test_s18_01_provider_contract.py
3 passed

rtk git diff --check
pass
```

The tests run against local bytes in the worktree only. They make no network calls,
resolve no credentials, start no CLI, and modify no target repository.

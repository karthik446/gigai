# A-6 acquire node

State: complete; focused verification passed.

Files changed (owned only):

- `src/gigai/scout_market_acquisition.py` — injected Exa/ATS orchestration, lazy/flexible watchlist integration, role filtering, URL/content-digest diffing, selection cap, failure rows, and existing acquisition journal handoff.
- `tests/behaviors/scout_find_jobs/test_acquire_network.py` — supplied-row and live Exa/watchlist behavior coverage.

`src/gigai/scout_acquisition_records.py` and `src/gigai/scout_acquisition_cli.py` were read but required no additive changes.

Choices made: supplied rows bypass provider calls; live Exa results auto-add board tokens with `first_seen` provenance; active watchlist boards are queried through the injected ATS protocol; role matching is a local title/company/location equivalent; public journal rows use normalized URL and content digest identities; prior input batches are scanned for URL/content observations; selection honors new/edited, role, and cap semantics. Provider exceptions are redacted into `FailureRow` values.

READ: `.claude/skills/gigai-orchestrator/SKILL.md`, the Rev-3 Amendment-02 design, the find-jobs roadmap, frozen contracts, existing acquisition records/CLI, and acquire/config/watchlist fixtures.

EXECUTED:

```text
python -m py_compile src/gigai/scout_market_acquisition.py
uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_acquire_network.py tests/behaviors/acquisition/test_public_acquisition_lifecycle.py tests/behaviors/acquisition/test_public_import_state.py -q
19 passed in 36.81s
```

The first exact test attempt was blocked because the owned new test file did not yet exist; after adding it, the exact command passed. Orca follow-up checks reported `runtime_unavailable`; no provider, model, live network, schema migration, stash/reset/clean/add/commit, or broad-suite action was performed.

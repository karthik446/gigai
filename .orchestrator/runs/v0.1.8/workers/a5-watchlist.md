# A-5 watchlist worker handoff

State: succeeded. Orca coordinator commands (`check`, `ask`, `send escalation`) were attempted but the Orca app was unavailable (`runtime_unavailable` / `Orca is not running`), so no coordinator reply was available.

Files changed (owned only):

- `src/gigai/scout_watchlist.py`
- `tests/behaviors/scout_find_jobs/test_watchlist.py`

Implemented `JournalWatchlistClient` and module-level `add_to_watchlist`, using the frozen `WatchlistEntry` DTO without redefining it. Records are journaled at `records/scout-watchlist/{watchlist_id}.json`; idempotency uses `scout_watchlist:{provider}:{board_token}`, and `list_active(home_root, target, gig_id=None)` reads authenticated journal snapshots and returns contract DTOs. Because no public `_publish` equivalent exists, the implementation uses public `run_with_journal_writer`, `JournalArtifact`, `JournalTransition`, and writer snapshots while reproducing the minimal receipt/idempotency shape locally; this was reported to the coordinator but approval could not be obtained due to Orca outage.

READ:

- `src/gigai/scout_find_jobs_contracts.py` (WatchlistEntry and WatchlistClient)
- `src/gigai/private_records.py:306-326` (`_publish` receipt/idempotency pattern)
- `src/gigai/scout_acquisition_records.py` (journal writer usage)
- `src/gigai/journal.py` (public writer/snapshot APIs and valid transitions)
- Amendment-02 Rev 3 watchlist storage section and v0.1.8 roadmap A-5 row
- `tests/behaviors/scout_find_jobs/fixtures/fixture-watchlist-v1.json`
- sibling temp-workpad helper `tests/behaviors/scout_research/test_scout06_research_inputs.py::_fixture`

EXECUTED:

- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_watchlist.py -q`
- Result: `2 passed in 5.59s`
- `git diff --check -- src/gigai/scout_watchlist.py tests/behaviors/scout_find_jobs/test_watchlist.py` (clean)

No live network, provider, model, schema migration, git add/commit, stash/reset/clean, or unowned-file changes.

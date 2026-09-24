# Worker: s2a-discovery-engine (S23/S24 stage 2, packet A)

**Status:** Done. Implementation + offline tests pass, `make unit-tests`
green, live smoke run succeeded under budget.

## READ vs EXECUTED

**READ** (before writing code):
- `docs/development/v0.1.9/spikes/S23-scout-setup-interview-exa-agent.md`
  (§2 interview questions, §3 query template, §4 schema, Recommendation).
- `docs/development/v0.1.9/spikes/S24-company-discovery-bakeoff.md`
  (OpenAI setup/pricing/TPM finding, H-1B pipeline, re-rank suggestion).
- `research/discovery_bakeoff/{run_openai_search.py,h1b_baseline.py,
  probe_h1b_boards.py,check_boards.py}` and
  `research/exa_agent_spike/check_boards.py`.
- Existing product code: `contracts.py`, `ats_board_clients.py`,
  `exa_client.py`, `watchlist.py`, `secrets_store.py`, `secrets_catalog.py`,
  `run_supervisor.py` (storage-path convention), `scout_cli.py`.

**EXECUTED**:
- All new modules under `src/gigai/scout/find_jobs/discovery/`.
- Four (later five) new test files, run against MockTransport/synthetic
  fixtures only -- zero live calls during the offline test pass.
- `uv run --locked --extra test pytest
  tests/behaviors/scout_find_jobs/test_discovery_prefs.py
  tests/behaviors/scout_find_jobs/test_discovery_openai.py
  tests/behaviors/scout_find_jobs/test_discovery_h1b.py
  tests/behaviors/scout_find_jobs/test_discovery_merge.py -q` -- **43
  passed** (also ran the 5th file, `test_discovery_init.py`, for the
  operator's mid-task rule -- 48 passed together).
- `make unit-tests` -- **927 passed** (unchanged from before this packet;
  my tests are correctly classified `integration`, not `fast_unit`, by
  `tests/conftest.py`'s AST-based lane classifier, since they import
  `httpx`).
- Full `tests/behaviors/scout_find_jobs/` directory (excluding one S2-B
  file, see "Cross-packet finding" below): **640 passed, 1 known xfail**.
- One live smoke run: `gigai scout discover --runs 1` in a TEMP
  GIGAI_HOME/target (nothing copied from the operator's real home; Scout
  installed fresh via `gigai setup`/`gigai init`/`gigai scout install`),
  key passed through subprocess env only (never printed -- grepped the
  full output and the temp home for the key's `sk-` prefix afterward,
  zero matches), $0.50 hard budget cap (worst-case reservation was
  $0.04), H-1B probe capped at 50 employers via
  `GIGAI_DISCOVERY_H1B_PROBE_TOP_N=50`. **Result: succeeded, $0.088463
  actual cost (17.7% of the $0.50 cap), 3 new boards found and added to
  the watchlist** -- Afresh (Greenhouse), LaunchDarkly (Greenhouse),
  Reddit (Greenhouse), all `evidence_verified: true`, all `found_by:
  ["openai_web_search"]` (H-1B ran too, since the smoke-test prefs set
  `visa_sponsorship_required=True`, but its 50-employer-capped probe found
  0 usable boards this run -- consistent with S24's own finding that a
  small top-N-by-case-count slice is lossy; `sources` in the result shows
  both ran: `openai_web_search` 1 run/$0.088463, `h1b` 1 run/$0.00).
  Verified post-run: watchlist has exactly the 3 entries
  (`first_seen.source_kind: "ats"`, per the Option B contract decision),
  the evidence sidecar has matching sponsorship evidence for all 3, and
  the real 240MB DOL LCA file downloaded into the temp home's
  `cache/scout/h1b/` (never the repo -- confirmed via `git status`).

## Interface contract delivered

`src/gigai/scout/find_jobs/discovery/__init__.py` exports exactly:
`run_discovery`, `latest_discovery`, `load_prefs`, `save_prefs`,
`DiscoveryPrefs`, `DiscoveryResult`, `DiscoveryPrefsError`,
`DiscoveryBudgetExceeded` -- matching the packet's frozen contract.
Storage: `<home>/scout/<project_id>/discovery/{prefs.json,runs/<id>.json,
evidence.json}` (never under the target).

## Files (packet's OWNED FILES list)

- `src/gigai/scout/find_jobs/discovery/__init__.py` -- `run_discovery`,
  `latest_discovery`, budget guard, exclusion-set assembly, source
  orchestration.
- `src/gigai/scout/find_jobs/discovery/prefs.py` -- `DiscoveryPrefs`
  (frozen dataclass, 11 S23 fields + cadence/budget), load/save (atomic
  write, plain JSON -- not `canonical_json_bytes`, since
  `budget_usd_per_session` is a float and GigAI's canonical JSON forbids
  floats; see Non-obvious findings).
- `src/gigai/scout/find_jobs/discovery/storage.py` -- shared path/atomic-
  write helpers (`<home>/scout/<project_id>/discovery/...`,
  `<home>/cache/scout/h1b/`).
- `src/gigai/scout/find_jobs/discovery/types.py` -- `Candidate`,
  `SourceRunOutcome` (shared shapes both sources emit).
- `src/gigai/scout/find_jobs/discovery/openai_source.py` -- OpenAI
  Responses API `web_search` + strict structured output, query built from
  prefs (S23 §3 template), 429/TPM backoff (`retry-after+45s`, 6
  attempts, per S24's live finding), price table, missing-key skip
  (never raises), `gpt-6-luna` default with env/pref override.
- `src/gigai/scout/find_jobs/discovery/h1b_source.py` -- DOL LCA file
  discovery/download (shows size before downloading, caches in
  `<home>/cache/scout/h1b/`, refreshes only on a newer fiscal quarter),
  employer extraction/ranking (S24's 5 SOC-code filter + a staffing/
  outsourcing-firm name-pattern filter per S24 Recommendation point 2),
  board-token slug-guessing + verification (bounded, serial, reuses
  `ats_board_clients`).
- `src/gigai/scout/find_jobs/discovery/merge.py` -- union/dedupe (by
  board key OR normalized company), exclusion filtering, board-check,
  evidence-URL verification (HEAD then GET fallback), watchlist
  integration.
- `src/gigai/scout/scout_cli.py` -- added `gigai scout discover [--runs N]
  [--status] [--json]` only (did not touch the `install`/`resume`/`run`/
  `stop`/`status` commands already there).
- `pyproject.toml` -- added `openpyxl>=3.1` to `dependencies` (was a
  research-only, not-installed dependency; `h1b_source.py` is product
  code now, so it needs to be a real dependency, not
  `uv run --with openpyxl`).
- Five new test files under `tests/behaviors/scout_find_jobs/`:
  `test_discovery_prefs.py`, `test_discovery_openai.py`,
  `test_discovery_h1b.py`, `test_discovery_merge.py` (the four named in
  ACCEPTANCE) plus `test_discovery_init.py` (added mid-task to cover the
  operator's H-1B-conditional-on-sponsorship rule at the orchestration
  level -- none of the four named files own `run_discovery` itself).
- `tests/behaviors/scout_find_jobs/fixtures/discovery/
  build_synthetic_lca.py` -- builds a tiny synthetic (8-row) LCA workbook
  in-test via `openpyxl`, not a committed binary fixture; never the real
  240MB+ DOL file.
- Did **not** touch `watchlist.py` -- see "Contract-change decision"
  below; no additive helper was needed there in the end (the sidecar
  approach lives entirely in `merge.py`/`storage.py`).
- Did **not** touch `secrets_catalog.py` -- `"openai": "OPENAI_API_KEY"`
  was already present.

## Contract-change decision (escalated, coordinator answered)

`WatchlistEntry`/`WatchlistFirstSeen` are a versioned journal contract
(`scout-watchlist:1`, Amendment 02) with no field for sponsorship
evidence and a closed `SourceKind` enum (`exa`/`ats`/`hiringcafe` --
`hiringcafe` itself is rejected in `from_json`). Per the packet's own
"if WatchlistEntry needs a new field... STOP and ask" instruction, I
asked before implementing. **Coordinator answer: Option B** -- leave
`WatchlistEntry`/`SourceKind` untouched; store sponsorship evidence in a
sidecar (`discovery/evidence.json`, keyed by `provider:board_token`);
`first_seen.source_kind` set to `ats` (the board was confirmed by our own
ATS poll) with the true origin (`openai_web_search`/`h1b`/both) recorded
only in the sidecar; exposed a read helper `merge.evidence_for(...)` for
S2-B. Implemented exactly as directed. Follow-up debt noted (per
coordinator): a `scout-watchlist:2` with native evidence fields could
replace the sidecar later.

## Operator rule (mid-task, coordinator relayed)

"The H-1B (DOL LCA) source only runs when `prefs.visa_sponsorship_required`
is true; when false, skip it entirely (no download, no cache refresh, no
board probing), record it in `sources` as skipped with reason
`sponsorship_not_required`, run OpenAI alone. Drop the sponsorship clause
from the OpenAI query and don't require sponsorship fields when not
needed." Implemented:
- `__init__.py`'s `run_discovery` branches on `prefs.visa_sponsorship_required`
  before touching `h1b_source` at all (zero I/O in the false branch,
  verified by a test that fails loudly if `h1b_source.run` is even called).
- `openai_source.build_query` no longer asks for "visa sponsorship
  evidence with a source URL" when not required -- asks for a plain board
  source URL instead. (Note: OpenAI's `strict: true` structured-output
  mode requires every schema property in `required` -- fields can't
  actually be made JSON-Schema-optional under strict mode, so
  `sponsorship`/`sponsorship_evidence` stay in the schema's `required`
  list always, but the query itself no longer asks the model to find
  real evidence for them, so it's expected to answer `"unknown"`/`""`
  rather than fabricate. This is the correct reading of "don't require
  sponsorship fields in the schema output" given strict mode's actual
  constraint -- flagging the tension explicitly rather than silently
  picking one interpretation.)
- New tests: `test_h1b_skipped_and_no_io_when_sponsorship_not_required`,
  `test_h1b_runs_when_sponsorship_required` (both in
  `test_discovery_init.py`), plus two `build_query` tests in
  `test_discovery_openai.py`.

## Cross-packet finding (S2-B, not my file, flagged not fixed)

`tests/behaviors/scout_find_jobs/test_present_setup_discover.py` (S2-B's
own test file, uncommitted, landed concurrently in this worktree) fails
**only when run in the same pytest process as my real
`gigai.scout.find_jobs.discovery` package** (9 failures; passes 100% in
isolation). Root cause, confirmed directly:
`present_api.py:649`'s `_discovery_module()` uses
`from gigai.scout.find_jobs import discovery` to do its lazy/optional
import. Once the *real* `discovery` submodule has been imported anywhere
earlier in the process (which my own tests do), Python's `from X import Y`
resolves via the already-cached `X.Y` attribute rather than re-consulting
`sys.modules['X.Y']` -- so the test file's
`monkeypatch.setitem(sys.modules, "gigai.scout.find_jobs.discovery", fake)`
stops working, and the real package (which needs a fully set-up
`GIGAI_HOME`/registry the bare `tmp_path` fixture doesn't have) runs
instead. Verified directly with a 6-line repro
(`from X import Y` vs `importlib.import_module("X.Y")` after a
`sys.modules` swap -- the latter respects the override, the former
doesn't). **Fix (one line, in `present_api.py`, which S2-B owns, not
touched by me):** `_discovery_module()` should use
`importlib.import_module("gigai.scout.find_jobs.discovery")` instead of
`from gigai.scout.find_jobs import discovery`. Ran
`tests/behaviors/scout_find_jobs/` excluding that one file to confirm
everything else (640 tests) is unaffected either way.

## Non-obvious findings

1. **`canonical_json_bytes` forbids floats.** `DiscoveryPrefs.
   budget_usd_per_session` is a float; GigAI's canonical/identity-bearing
   JSON (`gigai.canonical`) raises `InvalidCanonicalValueError` on any
   float. `prefs.py`/`merge.py`'s evidence sidecar/`__init__.py`'s
   `DiscoveryResult` all use plain `json.dumps`, matching
   `run_supervisor.py`'s `ScoutRunState` (also plain JSON), not
   `find-jobs.json`'s canonical pattern.
2. **`_dedupe` bug caught by my own tests before it shipped**: an early
   cut deduped candidates by board-key-or-company *before* grouping for
   `found_by` union, which silently dropped the second source's
   contribution to `found_by` on a genuine dedupe. Fixed to group first
   (by board key OR normalized company, whichever a candidate carries),
   then union `found_by` across the whole group --
   `test_dedupes_by_provider_and_board_token` catches this directly
   (asserts `found_by == {"h1b", "openai_web_search"}`).
3. **`list_active`/`add_to_watchlist` need an "active Gig" binding**,
   which a bare `resolve_bound_project`-only setup (no `gig use`) doesn't
   have. `run_discovery`'s frozen contract takes no `gig_id` parameter (by
   design -- it relies on the target's own active-Gig binding, same as
   any other Scout CLI command after `gigai scout install` +
   implicit/explicit `gig use`), so this is only a test-fixture concern,
   not a product gap -- `test_discovery_merge.py`/`test_discovery_init.py`
   both explicitly bind the active Gig in their fixtures the way
   `test_watchlist.py` already does.
4. **OpenAI's `strict: true` structured output cannot make a field
   optional** (every property must be in `required`) -- see "Operator
   rule" above for how this shaped the sponsorship-conditional schema.
5. Added a `GIGAI_DISCOVERY_H1B_PROBE_TOP_N` env override (undocumented
   in the CLI surface, an internal tuning knob) purely so the smoke test
   could honor the task's "H-1B probe limited to 50 employers" instruction
   without changing the frozen `run_discovery(*, ..., runs=3, ...)`
   signature or adding a new CLI flag beyond what CHANGE #6 specifies.

## Exclusions honored

- No schema/storage migration of existing records.
- Never printed a key (grepped the smoke-test wrapper script and its
  output for the literal key value before reporting -- clean).
- No commits made (coordinator commits).
- Temp GIGAI_HOME/target only for the live run; nothing copied from the
  operator's real `~/.gigai` except reading the `OPENAI_API_KEY` value
  itself via `secrets_store.get(home_root=<operator's real home>)`,
  passed through the child process environment only.
- The DOL LCA raw file (if downloaded during the smoke run) landed under
  the temp home's `cache/scout/h1b/`, never the repo, never git-tracked.

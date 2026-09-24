# Worker: acquire-country-filter

**Task:** fix 0.1.8.1 live-UAT tickets B1 (filter at acquire, not UI), B3
(sponsorship 69/69 unknown), and the B5 part (a run where every source
fails still reports "succeeded") — plus the operator-approved country fix:
read Lever/Ashby's structured country fields and replace the hand-typed
country/US-state alias tables with `pycountry`, keeping the 45-city list as
a deliberate short list. Dispatched under Orca task `task_461018bb4fcc`.

**Status:** Done. All owned files changed; nothing outside the owned-files
list touched (confirmed via `git status`/`git diff --stat`).

READ vs EXECUTED: all root-cause findings below were READ against the
evidence run's real raw payloads (gunzipped and inspected with `python3`,
no network calls) and the module source, then EXECUTED as pure-Python
one-liners/pytest runs to confirm the fix; no live Exa/ATS/provider network
call was made at any point (`MockTransport`/fixtures only, as required).

## Files changed

- `src/gigai/scout/find_jobs/filters.py` — country/US-state tables now
  generated from `pycountry` at import time; kept as small, labelled policy
  lists: the UK constituent-country fold, the bare "ca"/"in"/"de"
  disambiguation (now derived generically from whichever alpha-2 codes
  collide with a US state abbreviation), and the 45-city table (unchanged).
  `country_match()` gained an optional `structured_countries` param that
  wins outright over text parsing when supplied. Added `"will not sponsor"`
  to the not-offered phrase table (B3).
- `src/gigai/scout/find_jobs/ats_board_clients.py` — Lever now reads
  `country` (validated ISO alpha-2) + `categories.allLocations`; Ashby now
  reads `address.postalAddress.addressCountry` (+ `secondaryLocations`),
  both normalized to alpha-2 via a new `_normalize_country` helper
  (`pycountry.countries.lookup` only — never `search_fuzzy`, which
  fuzzy-matches garbage like "AMER" to a real country). Fixed
  `html_to_text` to `html.unescape()` before checking for `<` (B3 root
  cause — see below).
- `src/gigai/scout/find_jobs/contracts.py` — `PostingRow.countries: tuple[str,
  ...] | None = None` (additive, ISO alpha-2, reuses the existing
  `_country_codes` validator); new `DropCount` DTO +
  `AcquireOutput.dropped_counts: tuple[DropCount, ...] = ()` (additive).
  Both follow the repo's existing C0 additive-field pattern exactly
  (`_object_with_optional`, omitted at default).
- `src/gigai/scout/find_jobs/market_acquisition.py` — B1: after
  `_merge_exa_and_ats_rows`, drop any row that fails `exclusion_reason`
  (country/sponsorship) or role match, right after fetch, before
  NEW/EDITED/selection accounting; per-reason counts recorded on
  `AcquireOutput.dropped_counts`. `raw/` (written separately, unchanged)
  still keeps everything. B5: rewrote the all-sources-failed check from
  `source_outcomes`-boolean tracking to `not rows and failures` (see root
  cause below). Also fixed a latent bug in `_merge_exa_and_ats_rows`: a
  positional `PostingRow(...)` rebuild on URL-normalization was replaced
  with `dataclasses.replace` so it doesn't silently drop the new
  `countries` field (or any future additive field).
- `pyproject.toml` / `uv.lock` — added `pycountry>=24` (resolved
  26.2.16, matching the research doc's read exactly).
- Tests: `test_filters.py`, `test_ats_board_clients.py`,
  `test_acquire_network.py`, `test_contracts.py` — new coverage for all of
  the above, including a replay test against small fixtures derived from
  the operator's real evidence run
  (`fixtures/fixture-uat-0181-replay-postings.json`, provenance noted
  in-file) and a regression test that reproduces the exact B5 bug
  mathematically before/after the fix.
- `.orchestrator/workers/acquire-country-filter.md` (this file).

## Root causes found (not just symptoms)

1. **B3's real bug was `html_to_text`, not the phrase list.** Greenhouse's
   `content` field is *HTML-escaped HTML* (`"&lt;p&gt;...&lt;/p&gt;"`, not
   `"<p>...</p>"` — confirmed against the evidence run's raw
   `raw/greenhouse/0.json.gz`). The old `"<" not in html` check saw no
   literal `<` in that escaped string and took the "already plain text"
   branch, returning the entity-escaped soup completely unprocessed. Every
   Greenhouse row's stored `text` in `outputs/acquire.json` still carried
   literal `&amp;nbsp;`/`&lt;li&gt;`, which broke phrase matching
   system-wide for that provider (31/69 of the run's unknowns). Fix:
   `html.unescape()` before the `<` check. The other 38/69 unknowns
   (Ashby rows) were checked individually and are **correctly** unknown —
   none of those 38 real postings mention sponsorship, visa, or work
   authorization anywhere in their text (only a false-positive-avoided "...
   Authorization Platform" job title). Also added the ticket's one missing
   required phrase, `"will not sponsor"`.
2. **B5's real bug was a vacuous-success hole, not the `not rows` gate.**
   ATS sets `ats_ok = True` whenever its watchlist has no boards to fetch —
   a legitimate "nothing to do" on its own. But on a first run (or any run
   where Exa is the only source that would populate that watchlist and Exa
   itself fails), that same "no boards yet" state is indistinguishable from
   "ATS succeeded": `{"exa": False, "ats": True}` made
   `any(source_outcomes.values())` true, so the node returned COMPLETE with
   an empty batch instead of raising. Verified this mathematically
   (old-logic vs new-logic on the same inputs) before fixing. Rewrote the
   check to `not rows and failures` — the ticket's own stated invariant ("0
   postings and >=1 source failure") — which doesn't depend on ATS's
   vacuous bookkeeping at all.
3. **B1**: confirmed via the evidence run that Ashby's `addressCountry` is
   a full country *name* ("United States", "Czechia", "The Netherlands"),
   never already-ISO — every structured-country read goes through
   `pycountry.countries.lookup` (never `search_fuzzy`, which was confirmed
   to fuzzy-match "AMER" to American Samoa/Cameroon/the US — exactly the
   live UAT bug). Region tokens (AMER/EMEA/APAC/LATAM/"Remote - Americas")
   are simply absent from pycountry's alias data, so they resolve
   ambiguous (`None`), same as "Remote" always has — never a false match,
   and (per `exclusion_reason`'s existing "ambiguous is never excluded"
   contract) also never dropped, only a *definite* non-match is.

## Replay test (evidence run, `countries=["US"]`)

9 small fixture postings (title/location/address shapes only, no resume or
credential content) derived from the real evidence run's raw Ashby/
Greenhouse payloads, covering: `United States` (structured), `Mountain
View, USA` (free text), `AMER` (region, ambiguous), `India`, `Australia`,
`Canada`, `Singapore`, `Seoul, South Korea`, and the internal
`"z-Test & Templates Only"` label.

**Before (no country filter): 9 rows. After (`countries=["US"]`): 4 rows,
5 dropped** (India/Australia/Canada/Singapore/Seoul, all
`location_mismatch`). Survivors: the 2 US-matching rows plus `AMER` and
`z-Test & Templates Only` (both correctly ambiguous, not excluded — see
root cause #3). No Bengaluru/Hyderabad/Seoul/Singapore/Australia/Canada/
AMER-only row is counted as a false match; `dropped_counts` accounts for
exactly the 5 definite non-matches.

## Test results

- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_filters.py
  tests/behaviors/scout_find_jobs/test_ats_board_clients.py
  tests/behaviors/scout_find_jobs/test_acquire_network.py
  tests/behaviors/scout_find_jobs/test_contracts.py -q` → **419 passed**.
- exclusion_reason/proposal_execution-selection-dependent files (grepped:
  `test_assess_model_policy.py`, `test_scout_proposal_execution.py`,
  `test_scout_proposal_records.py`, `test_scout_proposal_run.py`,
  `test_scout_r4_journey.py`) → **all pass** (slow — several minutes total,
  pre-existing, unrelated to this packet's changes).
- `tests/behaviors/scout_find_jobs/test_m1_end_to_end.py` (offline) → **23
  passed, 1 xfailed** — the xfail is a pre-existing, already-documented,
  operator-acknowledged regression ("0.1.8.1: the offline M1 end-to-end run
  regressed after the acquire/assess changes ... ship 0.1.8.1 and handle it
  in v0.1.9"), not caused by this packet.
- `make unit-tests` → **909 passed**.
- Wheel-size delta from `pycountry`: **+7.67 MiB** (pycountry-26.2.16 wheel,
  8,044,600 bytes per `uv.lock`) — matches the research doc's ~8 MB
  estimate exactly.

## Not done / explicitly out of scope

- `config.remote`-based filtering: confirmed (grep) this field is not used
  for filtering anywhere in the current codebase and no test exercises it;
  B1's drop stage covers country/location (`exclusion_reason`) and role
  (existing `_role_match`) only, per the operator's stated bug (country
  filter, not remote). Implementing a new remote-match semantic was judged
  out of scope for this packet and not attempted.
- No schema migration: grepped `src/gigai/schemas/` — no posting/acquire
  JSON Schema file exists; `PostingRow`/`AcquireOutput`'s own
  `to_json`/`from_json` are the only validation layer, and both new fields
  follow the repo's existing additive-field pattern with no version bump.
- No JSON schema file touched, no `git add`/commit/stash, no live network
  call, no edits outside the owned-files list.

---

## r1 (coordinator review fix): region tokens are a definite non-match

**Task:** `task_50c345e0c123`. Coordinator accepted the r0 packet as-is
(structured Lever/Ashby countries, additive `PostingRow.countries`,
`DropCount`, the B3 root cause, the B5 invariant, the pycountry tables) and
asked for one fix: the operator's explicit B1 rule — "Region tokens (AMER,
EMEA, APAC, LATAM, 'Remote - Americas') must not count as US" — wasn't
actually satisfied in r0. A region-only location resolved to
`country_match(...) is None` (ambiguous), and `exclusion_reason` never
excludes ambiguous, so an "AMER" row still passed a US-only filter: the
exact UAT complaint, unfixed.

**Status:** Done.

READ vs EXECUTED: confirmed the coordinator's probe first (EXECUTED,
`python3 -c` against the r0 code: `location_countries('AMER') == set()`
and `country_match('AMER', ('US',)) is None`, same for `'Remote -
Americas'`/`'EMEA'`) before changing anything.

### Change

- `filters.py`: added `_REGION_TOKENS` (`amer`, `americas`, `emea`,
  `apac`, `latam` — matches the ticket's exact named tokens; whole-segment
  match, same mechanism as `_CITY_COUNTRIES`, so "AMERica Story Inc" never
  false-matches as a substring) and `_is_region_only(location)`: true when
  every signal-bearing segment (after the same comma/semicolon/" - "
  splitting `location_countries` already does) is a bare region token and
  none resolves to a real country/state/city. `country_match` now returns
  `False` (not `None`) when `location_countries` finds nothing AND
  `_is_region_only` is true. A location with a region token *and* a real
  country match ("AMER; Denver, CO", "Remote - US") is unaffected — it
  still matches, since `location_countries` finds the country first and
  the region-only branch is never reached. A genuinely unrecognized token
  with no region signal at all ("Remote" alone, an unknown city) is also
  unaffected — stays ambiguous, exactly as before.
- `contracts.py`: added `NotAssessedReason.REGION_ONLY` (additive StrEnum
  value — no exhaustive switch over this enum exists anywhere in `src/`,
  confirmed by grep, so this is safe). `exclusion_reason` itself still
  returns the coarse, stable `LOCATION_MISMATCH` for a region-only
  location — its existing contract, which `proposal_execution.py`'s
  not-assessed labeling relies on, is unchanged. New
  `filters.location_mismatch_detail(posting, config)` is a separate,
  additive helper that distinguishes `REGION_ONLY` from `LOCATION_MISMATCH`
  for finer-grained accounting only.
- `market_acquisition.py`: the acquire drop loop now calls
  `location_mismatch_detail` (falling back to `exclusion_reason`'s own
  `LOCATION_MISMATCH` when the detail helper doesn't apply, e.g.
  structured-country mismatches, which are never region-only by
  definition) to choose the `DropCount` key, so `dropped_counts` reports
  `region_only` separately from `location_mismatch` — auditable, per the
  coordinator's request — while the actual drop/keep decision (still
  `exclusion_reason`) is unchanged.
- Tests: `test_filters.py` — r0's
  `test_country_match_region_tokens_never_match_a_single_country` asserted
  the (now-corrected) pre-fix ambiguous behavior; replaced with
  `test_country_match_region_only_locations_are_a_definite_non_match` (all
  5 named tokens) plus new coverage for the mixed/substring/multi-region/
  unrecognized-token-without-region-signal cases, and
  `exclusion_reason`/`location_mismatch_detail` region-only tests.
  `test_contracts.py` — `REGION_ONLY` round-trip. `test_acquire_network.py`
  — extended the replay fixture with EMEA-only and APAC-only Ashby rows
  (synthetic, same shape as the real evidence-run rows; EMEA/APAC weren't
  present in this specific evidence run — noted in the fixture's
  `_provenance`) and rewrote the replay test's assertions + `dropped_counts`
  check for the new region-only-is-dropped behavior.

### Replay test (updated)

11 fixture postings (was 9 in r0 — added EMEA-only and APAC-only rows),
`countries=["US"]`:

**Before: 11 rows. After: 3 rows, 8 dropped** (5 `location_mismatch`:
India/Australia/Canada/Singapore/Seoul; 3 `region_only`: AMER/EMEA/APAC).

Surviving rows (company | location | countries):
```
acme | Mountain View, USA | None
acme | United States | ('US',)
acme | z-Test & Templates Only | None
```
(`z-Test & Templates Only` has no region token and no country signal at
all, so it stays ambiguous/kept — unaffected by this fix, same as before.)

### Test results

- `uv run --extra test pytest tests/behaviors/scout_find_jobs/test_filters.py
  tests/behaviors/scout_find_jobs/test_ats_board_clients.py
  tests/behaviors/scout_find_jobs/test_acquire_network.py
  tests/behaviors/scout_find_jobs/test_contracts.py -q` → **432 passed**
  (was 419 in r0; +13 new tests, 0 failed).
- `tests/behaviors/scout_find_jobs/test_m1_end_to_end.py` (offline) → same
  pre-existing, already-documented xfail as r0, unrelated to this change.
- `make unit-tests` → **922 passed** (was 909 in r0; +13, same tests as
  above are in the `fast_unit` marker set).
- No live network call, no `git add`/commit/stash/reset/clean, no edits
  outside the owned-files list (confirmed via `git status`).

# Worker: p0-country-words

**Task:** PR #37 review P0-1 (CONFIRMED by the coordinator): the pycountry-built
alias table in `src/gigai/scout/find_jobs/filters.py` matched ISO alpha-2/
alpha-3 codes as ordinary lowercase words in free-text locations, so real US
postings were dropped at acquire. Dispatched under Orca task `task_5675ce48d4c3`.
Source: `.orchestrator/reviews/pr37-review-findings.md`.

**Status:** Done. Owned files changed; reproduction tests failed before the
fix and pass after (`184` -> `257` passing in `test_filters.py`, all
`22` in `test_acquire_network.py` pass, `1023` in `make unit-tests`).

READ vs EXECUTED: READ `filters.py` in full, `.orchestrator/research/
country-data.md` (referenced by the module docstring, confirming what's
labelled-hand-made vs pycountry-generated), `ats_board_clients.py`'s Ashby
location/`secondaryLocations`/`address` extraction (to match the corpus to
what acquire actually reads), the raw UAT evidence-run payloads (gzip'd
JSON), `fixture-uat-0181-replay-postings.json`, and both
`research/exa_agent_spike` and `research/discovery_bakeoff`'s
`*.boardcheck.json` fixtures. EXECUTED: the coordinator's three repro
strings via `uv run --locked python3 -c "..."` before any fix (confirmed
AD/PE/EE exactly as reported, plus `Remote (US)` -> `[]`/`None` and NYC/
"New York City" unrecognized); the full `test_filters.py`,
`test_acquire_network.py`, and `make unit-tests` suites after each change.

## Root cause

`_build_country_aliases()` put the bare alpha-2 code (`"us"`, `"ad"`, `"pe"`,
...) and the alpha-3 code (`"usa"`, `"est"`, ...) into the *same*
case-insensitive alias table as full country names, matched against the
lowercased/folded segment tokens. Any ordinary English word that happens to
spell a code -- "and" (AD), "per" (PE), "est" (EE's alpha-3, from folding
"EST") -- silently resolved to that country.

## Fix (filters.py only)

- Split the alias table in two: `_COUNTRY_ALIASES` now holds only
  name/official_name/common_name aliases (still case-insensitive, matched on
  folded segments -- unchanged for those). `_CODE_ALIASES` holds the bare
  alpha-2/alpha-3 codes, matched **only** as an uppercase, delimited token in
  the **original, unfolded** location string (new `_CODE_TOKEN_RE` +
  `_code_countries_in_original`, wired into `location_countries` alongside
  the existing folded-segment path). A code embedded in a longer word, or
  written lowercase/mixed-case, never matches.
- The three labelled policy lists are untouched in scope and behavior: the
  UK constituent-country fold (`_UK_FOLD_ALIASES`), the CA/IN/DE-as-US-state
  disambiguation (still computed from state-abbreviation collision, now
  against the uppercase code for `_CODE_ALIASES` and unchanged for the
  folded `_US_STATE_ABBREVIATIONS` path), and `_CITY_COUNTRIES` (the 45-city
  list). The region-only rule (`_is_region_only`/`_REGION_TOKENS`) is
  unchanged.
- Added `_TIMEZONE_TOKENS` (EST/EDT/CST/CDT/MST/MDT/PST/PDT/ET/CT/MT/PT/GMT/
  UTC/CET/CEST/AEST/AEDT/BST): guarded out of the uppercase-code scan so none
  of them are ever read as a country (EST/Estonia's alpha-3, ET/Ethiopia,
  PT/Portugal collide today; the rest are guarded defensively). **Coordinator
  review correction:** these are deliberately *not* a positive US signal
  either -- EST/ET/CST/PST etc. are shared with Canada (Ontario/Quebec), so
  treating them as US would create a new false positive for non-US remote
  roles. A location carrying only a timezone token stays ambiguous (`None`,
  kept -- not excluded), same as bare `"Remote"`.
- Added the task's required common US forms: "US"/"USA" moved into
  `_CODE_ALIASES["US"]` (still uppercase-only, so lowercase "us"/"usa" inside
  a word is never read as US); "Remote (US)"/"US-based"/"USA only"/"United
  States (Remote)" now resolve via the existing code/name paths once the
  code is matched against the original string instead of a folded one.
  "NYC" and "New York City" needed a new small `_US_CITY_ALIASES` table
  (city names aren't in ISO-3166 data at all) with the same whole-segment/
  substring-match logic `_CITY_COUNTRIES` already used, extended to also
  substring-match inside a non-comma-delimited phrase ("New York City and
  Remote") the way multi-word country aliases already did.

## Regression corpus

`tests/behaviors/scout_find_jobs/fixtures/location-corpus.json` (new): 73
de-duplicated `location` strings, collected read-only from:

1. The operator's UAT evidence run's raw ATS payloads --
   `~/.gigai/workpads/projects/project_e12ab112-32eb-435f-9f03-9e1bf49c3ff1/
   gigs/gig_015b1fc0-502a-4179-9b49-794cd4ecd651/runs/
   run_d73cb030-62a3-4d5e-9629-a33977d90194/raw/{greenhouse,ashby}` --
   greenhouse's `location.name`, ashby's top-level `location` plus each
   `secondaryLocations[].location`.
2. The committed `fixture-uat-0181-replay-postings.json`'s ashby/greenhouse
   job location fields.
3. `research/exa_agent_spike` and `research/discovery_bakeoff`'s
   `*.boardcheck.json` fixtures -- checked, contain **no** `location` field
   at all (board-resolution records: `board_token`/`company`/`resolved`/
   `posting_count`/`is_usable_board`), so nothing to add from there.

Each entry has hand-labelled `expected_countries` (a set of ISO alpha-2
codes) and `expected_us_match` (`true`/`false`/`null`), verified by hand
against the string's actual place-name content -- not generated from the
code under test. New test `test_location_corpus_matches_hand_labels` in
`test_filters.py` parametrizes over all 73 and asserts both
`location_countries()` and `country_match(..., ("US",))`.

None of these 73 real evidence-run strings happen to contain the specific
P0-1 false-positive words ("and"/"per"/"EST"/"be"), so none of them changed
classification before vs after this fix -- the before/after table below uses
the coordinator's reported repro strings plus the task's required new
US-form additions instead.

## Before/after table (P0-1 reported strings + required additions)

`country_match(location, ("US",))`, before this fix vs after:

| Location | Before | After |
|---|---|---|
| `New York City and Remote` | `False` (AD/"and") | `True` |
| `Hybrid (3 days per week)` | `False` (PE/"per") | `None` (ambiguous, kept -- see note) |
| `Remote - EST timezone` | `False` (EE/"est") | `None` (ambiguous, kept -- see note) |
| `Remote, must be in office 2 days` | `False` (BE/"be") | `None` (ambiguous, kept) |
| `Remote (US)` | `None` | `True` |
| `US-based` | `None` | `True` |
| `USA only` | `True` (already worked) | `True` |
| `United States (Remote)` | `True` (already worked) | `True` |
| `NYC` | `None` | `True` |
| `New York City` | `None` | `True` |

**Note on `Hybrid (3 days per week)` / `Remote - EST timezone`:** confirmed
with the coordinator via `orca orchestration ask` mid-task. Both strings
carry zero actual country/city/state signal once their respective false
positive (PE/"per", EE/"est") is removed -- there's nothing left in either
string that names a place. Forcing them to a definite `True` would require a
new blanket "no signal -> US" rule, which risks a *new* false positive for
any non-US posting using the same generic phrasing ("days per week") or a
shared timezone abbreviation (EST/ET/CST/PST are also observed in Canada).
Coordinator agreed: both resolve to `None` (ambiguous, **kept** -- not
excluded), which is what actually fixes the acquire-drops-postings bug the
review flagged, without introducing a new over-broad rule. Acceptance
criteria's literal "-> US match" for these two was updated to "-> not
dropped" accordingly; the corpus/acceptance tests assert `is None`, not
`is True`, for these two specifically.

## Files changed

- `src/gigai/scout/find_jobs/filters.py` -- the fix (see above).
- `tests/behaviors/scout_find_jobs/test_filters.py` -- reproduction tests
  (failing before, passing after) for the three reported strings, the
  timezone guard, the uppercase-vs-lowercase code distinction, and the new
  location-corpus parametrized test.
- `tests/behaviors/scout_find_jobs/fixtures/location-corpus.json` (new) --
  the regression corpus.
- `.orchestrator/workers/p0-country-words.md` (this file).

## Test budget used

- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_filters.py -q`
  -- 257 passed (184 pre-existing untouched in behavior, all still pass; 73
  new).
- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_acquire_network.py -q`
  -- 22 passed (includes the UAT replay test, unaffected).
- `make unit-tests` -- 1023 passed, 1432 deselected.

No `git add`/`commit`/`stash`/`reset`/`clean` used.

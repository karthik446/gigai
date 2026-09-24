# Worker: country-data-research

**Task:** research-only (no code changes) — investigate whether library/JSON
data sources can replace the hand-made `_COUNTRY_ALIASES`,
`_COUNTRY_ALIASES_EXTRA`, and `_CITY_COUNTRIES` lookup tables in
`src/gigai/scout/find_jobs/filters.py`, per the operator's objection to
hand-made lookup tables shipped in 0.1.8.1. Dispatched under Orca task
`task_9b8d138e10f0`.

**Status:** Done. Both owned files written; no other files touched.

## Files written

- `.orchestrator/research/country-data.md` (new) — the full investigation:
  what `filters.py` needs, structured-data availability from Scout's ATS/Exa
  sources, a library/license/size comparison table, US-state disambiguation
  options, and a recommendation with pseudocode sketch.
- `.orchestrator/workers/country-data-research.md` (this file).

## What I found (summary)

1. **`filters.py`'s job**, traced through the whole file (327 lines) plus
   its two callers (`market_acquisition.py`'s `exclusion_reason`,
   `ats_board_clients.py`/`exa_client.py`'s `sponsorship_from_text`) and its
   test file (308 lines): country-name/code → ISO alpha-2, bare-city →
   country, US-state-vs-country-code disambiguation (the "ca"/"in"/"de"
   exclusions), and free-text segment parsing ("Remote - USA" style joins).
2. **No structured country field is being ignored.** Read the actual
   parsing code for Greenhouse (`location.name`), Lever
   (`categories.location`), and Ashby (`location`) — all three are parsed
   as flat strings in this codebase today, matching each client module's
   own documented assumed API shape; no `offices[].location` or
   `address.postalAddress.addressCountry`-style structured field is read
   anywhere in `src/` or exercised by any test fixture. Exa's client
   hardcodes `location=""` — it carries no location data at all.
3. **`pycountry`** (LGPL-2.1, pure Python, ~8 MB wheel, Debian
   `iso-codes`-sourced — literally the "json" the operator asked about) is
   the recommended required dependency: it replaces the country-alias
   tables and, via its ISO 3166-2:US subdivisions, the hand-made US-state
   tables too, with no second package needed for states. `geonamescache`
   (MIT, ~35 MB) is an optional add if city-level matching should stay;
   `country_converter` (GPL-3.0) and `us` (BSD, but pulls in the
   Rust-compiled `jellyfish` package — an offline-wheel risk for
   `make test-debian-offline`) are noted as weaker alternatives, not
   recommended over pycountry.
4. What stays hand-made either way: the UK-demonym folding
   (England/Scotland/Wales → GB), the bare "ca"/"in"/"de"
   disambiguation *policy* (a product decision, not data), and
   `_CITY_COUNTRIES` unless `geonamescache` is explicitly adopted.

## READ vs EXECUTED

**Read:** `src/gigai/scout/find_jobs/filters.py` (full, 327 lines);
`tests/behaviors/scout_find_jobs/test_filters.py` (full, 308 lines);
`src/gigai/scout/find_jobs/ats_board_clients.py` (parsing logic for
Greenhouse/Lever/Ashby, lines 1-60 and 200-370);
`src/gigai/scout/find_jobs/exa_client.py` (full, 183 lines);
`tests/behaviors/scout_find_jobs/test_ats_board_clients.py` (grepped
matches, lines 247/255/288/320); `pyproject.toml` (full dependency/license
metadata); `LICENSE` (header, confirms Apache-2.0); `CONTRIBUTING.md`
(offline/Debian test-gate lines 14-18); PyPI project pages for `pycountry`,
`iso3166`, `us`, `geonamescache` (fetched 2026-09-23 via WebFetch, cited
with version/date in the research doc); `.orchestrator/workers/*.md` (one
existing report) to match this repo's worker-report conventions.

**Executed (side-effect-free only):** `wc -l` on `filters.py` and the test
file; `grep -rn`/`grep -n` for callers of `location_countries`/
`country_match`/`exclusion_reason`/`sponsorship_from_text` and for
`postalAddress`/`addressCountry`/`isRemote` across `src/`/`tests/`;
`find` for fixture files; `python3 -c "json.load(...)"` to print fixture
contents (`fixture-acquire-batch-v1.json`, `fixture-present-payload-v1.json`,
`fixture-acquire-input-v1.json`) — read-only, no file written; `ls`/`head`
on `LICENSE`; `WebSearch`/`WebFetch` calls to PyPI pages and general
package-metadata pages for `pycountry`, `country-converter`, `us`,
`geonamescache`, `iso3166`, `jellyfish`, and Babel — all reads of public
documentation pages, not package installs. No package was installed into
any environment (throwaway or project); all library facts came from
reading PyPI/GitHub metadata pages, and are labeled as such (not verified
by import) in the research doc.

**Not done, per exclusions:** no code/schema/test/pyproject/lock edits; no
`git add`/commit/stash/reset/clean; no pip/uv install anywhere; no live
Exa/ATS network calls (the ATS/Exa API shapes discussed are this
codebase's own documented assumptions, read from the client modules'
docstrings, not verified against a live endpoint).

## What's left

- Confirm the `~8 MB` pycountry wheel and (if adopted) `~35 MB`
  geonamescache wheel against any wheel-size budget for gigai's own
  distribution — no such budget doc exists in this repo today, so this is
  an open question for the operator, not something I could check.
- If `us` is ever reconsidered instead of pycountry's ISO 3166-2:US data,
  its `jellyfish` dependency's prebuilt-wheel availability needs an
  explicit check against the actual `make test-debian-offline` container
  before adoption — flagged as a risk in the research doc, not resolved
  here.
- Whether Greenhouse/Lever/Ashby's *live* public APIs expose additional
  structured location/country fields beyond what this codebase currently
  parses is a live-API question explicitly excluded from this dispatch;
  the research doc notes it as a separate, authorized follow-up if wanted.
- The actual library adoption (replacing the tables, updating tests) is
  implementation work, not covered by this research-only dispatch.

## r1 (fix §2's circularity; check what the APIs actually return)

**Task:** coordinator review found §2 circular — it proved only what
Scout's own `ats_board_clients.py` reads out of the JSON it already
receives, then concluded no structured field was being thrown away without
ever checking what the providers' APIs actually document. Dispatched as
`country-data-research-r1` under Orca task `task_6662906d7be3`.

**Status:** Done. Same two owned files; §3-§5's library findings kept as
accepted, §2 rewritten from each provider's own public docs, §5 updated to
recommend the structured field first with a pycountry-backed fallback.

### What I found

- **Lever**: its own `postings-api` GitHub README documents `"country"`
  (ISO 3166-1 alpha-2, or `null`) and `"categories.allLocations"` as fields
  returned by the exact `?mode=json` request `ats_board_clients.py:137`
  already builds. `ats_board_clients.py:296-298` reads only
  `categories.location` (a string) — the ISO country field is already in
  the response and unread.
- **Ashby**: its own current public-API doc
  (`developers.ashbyhq.com/docs/public-job-posting-api`) documents
  `address.postalAddress.addressCountry`, `isRemote`, `secondaryLocations`
  (each with its own `address.addressCountry`), and `workplaceType` as
  fields on the same `GET .../posting-api/job-board/{token}` request
  `ats_board_clients.py:330` already makes (no extra param needed).
  `ats_board_clients.py:345-346` reads only the flat top-level `location`
  string — all four richer fields are unread. Corroborated by a second
  independent source (a WebSearch-surfaced integration write-up quoting
  the same official doc) showing the identical `addressCountry` shape.
- **Greenhouse**: confirmed via `docs.greenhouse.io/job-board.html` (the
  live redirect target of `developers.greenhouse.io/job-board.html`) that
  neither the base endpoint nor `?content=true` (which Scout's client
  already requests) exposes anything beyond `location.name`/
  `offices[].location.name` — no structured country code exists in this
  API at all. r0's conclusion for Greenhouse was right, just previously
  unverified against the actual docs.
- **Exa**: confirmed via `exa.ai/docs/reference/search` (the live redirect
  target of `docs.exa.ai/reference/search`) that `SearchResultOutput` has
  no location/address/country field. r0's conclusion for Exa was likewise
  right but previously unverified.

Rewrote §2 with this evidence (field quotes, doc URLs, and Scout's own
request/read lines cited for each of the four sources) and a "corrected
conclusion" stating plainly that Lever and Ashby's string-parsing is
avoidable — a structured field already arrives unread — while Greenhouse
and Exa still need the free-text fallback. Rewrote §5 to lead with
"structured field first, pycountry fallback only where a source gives free
text," per-source (Lever reads `country`+`allLocations`; Ashby reads
`address.postalAddress.addressCountry`+`secondaryLocations`; Greenhouse and
Exa keep today's approach as their only path), and updated the pseudocode
sketch to show the `ats_board_clients.py`-layer read plus a new
`PostingRow.location_country`-style field, flagging that this is a
contract change, not a `filters.py`-internal-only swap. §3 and §4 were left
untouched, as instructed (already accepted).

### READ vs EXECUTED

**Read:** `ats_board_clients.py:136-138` (the three `_*_URL` constants) to
cite Scout's exact request URLs/params; Lever's `postings-api` README on
GitHub (WebFetch); Ashby's `developers.ashbyhq.com/docs/public-job-posting-api`
(WebFetch, the correct URL — an earlier guess at
`developers.ashbyhq.com/reference/jobpostingsync` 404'd, and
`.../docs/job-postings-api` also 404'd; found the right path via
WebSearch first); Greenhouse's `docs.greenhouse.io/job-board.html` (WebFetch,
after `developers.greenhouse.io/job-board.html` 301-redirected there); Exa's
`exa.ai/docs/reference/search` (WebFetch, after `docs.exa.ai/reference/search`
307-redirected there); one WebSearch-surfaced third-party integration
write-up quoting Ashby's official `secondaryLocations` shape, used only as
corroboration of the primary doc fetch, not as a standalone source.

**Executed:** none beyond the one `grep -n` to pull the three `_*_URL`
constants' line numbers (side-effect-free, no state changed).

**Not fetched / could not confirm:** none — every doc page needed was
reached, though two URLs had to be corrected after a 404 (Ashby) and two
redirects had to be followed manually (Greenhouse, Exa) since WebFetch does
not auto-follow cross-host redirects; each is noted inline in §2 with both
the originally-tried and the actually-fetched URL.

**Not done, per exclusions:** no code/test/lock edits; no live/authenticated
ATS or Exa API calls (only public documentation pages were fetched); no
installs; no `git add`/commit/stash/reset/clean.

### What's left

- Whether Ashby's `addressCountry` value always arrives as ISO alpha-2 or
  sometimes as a full country name/other form (the one documented example
  value was `"USA"`, not `"US"`) was not confirmed — the doc page didn't
  give a definitive answer, and confirming it would need a live spot check,
  excluded from this dispatch. Flagged in §5 as a pre-implementation check.
- Implementing the structured-field-first read (a `PostingRow` contract
  change plus `ats_board_clients.py` edits) is implementation work for a
  separate, future dispatch — not part of this research-only task.

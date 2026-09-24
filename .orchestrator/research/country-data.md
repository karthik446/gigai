# Research: country/city/US-state data for `filters.py` — libraries vs hand-made tables

**Status:** Research only, 2026-09-23; revised 2026-09-23 (r1, see Change
log). No code, schema, test, pyproject, or lock file was edited. No
`git add`/commit/stash/reset/clean was run. No pip/uv install into the
project env. No live Exa/ATS network calls. Every claim below is marked
READ (verified in this repo or a fetched doc/PyPI page) or EXECUTED (a
side-effect-free shell/python command run to check a fact, e.g. `wc -l`,
`grep`, `python3 -c "print(json...)"` on local fixtures).

Operator's objection (verbatim): "figure out if there are already libraries
that define this or a json; we shouldn't make up shit." Target: the
hand-made tables in `src/gigai/scout/find_jobs/filters.py` — `_COUNTRY_ALIASES`
(:43), `_COUNTRY_ALIASES_EXTRA` (:136), `_CITY_COUNTRIES` (:108), and the
special-case exclusions of bare `"ca"`/`"in"`/`"de"`.

---

## 1. What `filters.py` actually needs (READ: full file, 327 lines, and
`tests/behaviors/scout_find_jobs/test_filters.py`, 308 lines)

`filters.py` is pure, I/O-free, called from two places (READ,
`grep -rn` for callers):

- `market_acquisition.py:45,472` — `exclusion_reason` (the acquire
  selection loop's country/sponsorship filter).
- `ats_board_clients.py:39` and `exa_client.py:45` — `sponsorship_from_text`
  only (not location-related).

Four public functions, each with one job:

1. **`location_countries(location: str) -> set[str]`** — parse a free-text
   location string (comma/semicolon-segmented, "COUNTRY - City" joins) into
   zero or more ISO-3166 alpha-2 codes. Empty result = ambiguous, never a
   false negative.
2. **`country_match(location, countries) -> bool | None`** — three-valued:
   `True` (matches a wanted country), `False` (recognized country/countries,
   none wanted), `None` (ambiguous/empty — callers must not drop the row).
3. **`sponsorship_from_text`** — phrase matching, unrelated to
   country/geography; out of scope for this research.
4. **`exclusion_reason`** — combines `country_match` + sponsorship into the
   acquire/assess exclusion rule.

Sub-needs `location_countries` depends on, read directly from the module:

- **Country name/abbreviation/demonym → ISO alpha-2** (`_COUNTRY_ALIASES`,
  `_COUNTRY_ALIASES_EXTRA`): "USA"/"U.S."/"united states" → US, "UK"/"Great
  Britain"/"England"/"Scotland"/"Wales" → GB, etc. Note GB's alias list
  already conflates the sovereign state with its constituent
  countries/demonyms (England/Scotland/Wales) — a modeling choice, not
  something ISO-3166 alone encodes.
- **Bare tech-hub city name (no country/state at all) → ISO alpha-2**
  (`_CITY_COUNTRIES`, 45 cities): the P1b fix for ~50 real postings like
  bare "Bengaluru"/"Hyderabad" (docstring :98-107, and
  `test_country_match_p1b_operator_real_strings` / test_..._bare_tech_hub_cities`
  in the test file, READ).
- **US-state name/abbreviation (no country word) → US**
  (`_US_STATE_ABBREVIATIONS`, `_US_STATE_NAMES`): "Denver, CO", "Atlanta,
  Georgia" → US.
- **Disambiguation between a country's alpha-2 code and a colliding US
  state's postal abbreviation**: bare `"ca"` (Canada vs California), `"in"`
  (India vs Indiana), `"de"` (Germany vs Delaware) are excluded from the
  country-alias table specifically so a state-abbreviation-only segment
  doesn't get misread as the foreign country (comment at :45-47, :50-53,
  confirmed by test `test_country_match_bare_ca_does_not_force_canada_or_california_exclusion`,
  :118-123).
- **"Remote - COUNTRY" / "Remote, COUNTRY" style strings**: handled by the
  generic segment splitter (`_segments`, `_segment_countries`), not a
  separate code path — "Remote" itself carries no country signal and
  correctly resolves to ambiguous alone (`test_country_match_genuinely_ambiguous_locations_stay_none`,
  :185-188).
- **Diacritic/case folding** (`_fold`, unicodedata NFKD): "São Paulo" /
  "Sao Paulo", "Zürich"/"Zurich" must hit the same table entry
  (`test_country_match_bare_city_diacritic_insensitive`, :178-182).

## 2. What the APIs return vs. what Scout's clients read (r1: corrected —
see r0's finding and why it was circular, in the Change log)

r0 of this section only checked what `ats_board_clients.py` *reads out of*
the JSON it already receives, then concluded "no structured field is being
thrown away." That's circular: it can't show a field is unused if it never
looked at what the field-set actually is. This revision reads each
provider's own public API documentation (WebFetch, 2026-09-23) instead, and
compares it against the exact request URL Scout's client builds (READ,
`ats_board_clients.py:136-138`).

### Lever — `country` and `categories.allLocations` exist and are unused

- **Scout's request** (READ, `ats_board_clients.py:137,280`):
  `GET https://api.lever.co/v0/postings/{token}?mode=json` — the list
  endpoint, JSON mode.
- **Documented fields** (READ, `github.com/lever/postings-api` README,
  fetched 2026-09-23): "In JSON mode, each job posting is a JSON object
  with the following fields," including:
  - `"country"` — *"An ISO 3166-1 alpha-2 code for a country / territory
    (or null to indicate an unknown country). This is not filterable."*
  - `"workplaceType"` — *"Describes the primary workplace environment for
    a job posting. May be one of `unspecified`, `on-site`, `remote`, or
    `hybrid`. Not filterable."*
  - `"categories"` object containing `location`, `commitment`, `team`,
    `department`, and **`allLocations`** (an array including the primary
    location alongside any others).
  - The README states these fields apply to "each job posting" returned in
    JSON mode generally — it does not give a separate, narrower field list
    for the list vs. single-posting endpoint, so they read as applying to
    the exact `?mode=json` list call Scout already makes.
- **Does Scout call it today?** No. `ats_board_clients.py:296-298` reads
  only `job["categories"]["location"]` (a string) into `PostingRow`. Scout
  never reads `job["country"]`, `job["workplaceType"]`, or
  `job["categories"]["allLocations"]`, even though its existing request
  already returns them at no extra cost (same endpoint, no extra param).

### Ashby — `address.postalAddress.addressCountry`, `isRemote`, `secondaryLocations`, `workplaceType` all exist and are unused

- **Scout's request** (READ, `ats_board_clients.py:138,330`):
  `GET https://api.ashbyhq.com/posting-api/job-board/{token}` (no
  `includeCompensation` param).
- **Documented fields** (READ, `developers.ashbyhq.com/docs/public-job-posting-api`,
  fetched 2026-09-23 — this is Ashby's own current public-API doc, not a
  third party): a single job object includes, alongside the flat
  `"location"` (string) Scout already reads:
  - `"address"` object → `"postalAddress"` object → `"addressLocality"`,
    `"addressRegion"`, **`"addressCountry"`**.
  - `"isRemote"` (boolean) — *"Whether the job is remote."*
  - `"workplaceType"` (string) — one of `OnSite`, `Remote`, `Hybrid`.
  - `"secondaryLocations"` (array), each item with its own `"location"`
    string and `"address"` object (same `addressLocality`/`addressRegion`/
    `addressCountry` shape) — covers multi-location postings the same way
    `filters.py`'s semicolon-segment parsing does today, but structured.
  - Confirmed independently via a second source (`WebSearch`, an
    Ashby-integration write-up quoting the official docs) showing the
    identical `secondaryLocations[].address.addressCountry` shape with a
    `"USA"` example value — corroborates the primary fetch, not a
    substitute for it.
- **Does Scout call it today?** No. `ats_board_clients.py:345-346` reads
  only the top-level `job["location"]` string. None of `address`,
  `isRemote`, `secondaryLocations`, or `workplaceType` is read anywhere in
  `src/` (confirmed by the r0 grep for `postalAddress`/`addressCountry`/
  `isRemote`, zero matches, still true — nothing new was added since). The
  request itself needs no change to get these fields; Ashby's job-board
  endpoint returns them unconditionally.

### Greenhouse — no structured country field exists at all; r0's read stands

- **Scout's request** (READ, `ats_board_clients.py:136,212`):
  `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`.
- **Documented fields** (READ, `docs.greenhouse.io/job-board.html` —
  `developers.greenhouse.io/job-board.html` redirects here as of
  2026-09-23 — fetched at the redirected URL): without `?content=true`,
  `"location"` is an object with only `"name"`. With `?content=true`
  (Scout's own param), the added fields are `"content"`, `"departments"`
  (`id`/`name`/`parent_id`/`child_ids`), and `"offices"` (`id`/`name`/
  `location`/`parent_id`/`child_ids`) — and `offices[].location` is
  documented as the same free-text `name` shape, not an ISO country code.
  No field in this API — with or without `content=true` — carries a
  structured country code.
- **Does Scout call it today?** Scout's `?content=true` request already
  pulls everything Greenhouse's job-board API has to offer for location;
  `location.name` (what `ats_board_clients.py:227-230` already reads) is
  the richest location signal this source has. r0's conclusion for
  Greenhouse specifically was correct, just for the wrong reason (it never
  checked the docs to confirm no richer field was being skipped — it just
  happened to be right that none exists).

### Exa — confirmed no location field, as expected

- **Documented fields** (READ, `exa.ai/docs/reference/search` —
  `docs.exa.ai/reference/search` redirects here as of 2026-09-23): a
  `SearchResultOutput` object's fields are `title`, `url`, `publishedDate`,
  `author`, `id`, `image`, `favicon`, `text`, `highlights`,
  `highlightScores`, `summary`, `subpages`, `entities`, `extras` — no
  `location`, `address`, or `country` field. (`entities` can carry
  structured company/person data that *might* include a headquarters or
  person-location property for some entity types, but that's
  entity-metadata, not a per-result job-location field, and Scout's own
  request body — READ, `exa_client.py:148-153` — doesn't request an
  `entities` extraction in the first place.) This matches
  `exa_client.py:131`'s hardcoded `location=""` and confirms r0's
  conclusion for Exa was correct on the facts, not just on Scout's code.

### Corrected conclusion

Two of Scout's three ATS sources — **Lever and Ashby** — return
structured, ISO-shaped country/location data on the exact endpoint and
parameters Scout already calls, and Scout's clients discard it today,
reading only the free-text `location`/`categories.location` string
instead. This is exactly the case the operator was asking about: for these
two sources, `location_countries`'s string-parsing (hand-made alias tables
or a pycountry-backed replacement) is unnecessary work being done on data
that was already thrown away upstream — a structured field would resolve
these rows deterministically with no ambiguity risk at all, no alias
table, no city table, no "ca"/"in"/"de" collision problem (Lever's
`country` field is a plain ISO alpha-2 that never collides with a US state
abbreviation the way a bare two-letter free-text token can).
**Greenhouse and Exa** are the sources where r0's "string-parsing is
necessary" framing genuinely holds — Greenhouse's API has no structured
country field to fall back on, and Exa has no location field of any kind.
So `location_countries`'s free-text parsing isn't dead code to delete; it
becomes the necessary fallback for Greenhouse/Exa rows and for any
Lever/Ashby row where the structured field itself comes back null/missing
(Lever's own doc: `country` can be `null` "to indicate an unknown
country").

## 3. Libraries for country names/codes (READ: PyPI project pages, fetched
2026-09-23; "not verified live" = PyPI-page claims taken as authoritative,
no package was executed except where marked EXECUTED under a throwaway venv)

| Library | Version (as read) | License | Pure Python? | Size | Covers | Fuzzy/alt-name search | Notes |
|---|---|---|---|---|---|---|---|
| **pycountry** | 26.2.16 (2026-02-17) | **LGPL-2.1-only** | Yes | wheel ~8.0 MB | ISO 3166-1 (249 countries, alpha-2/3/numeric, official + common names), ISO 3166-2 (5,046 subdivisions incl. US states), ISO 3166-3 (historic/withdrawn), ISO 4217 currencies, ISO 15924 scripts, ISO 639-3 languages (7,923) | Yes — `pycountry.countries.search_fuzzy()`, unicode-normalizing, prioritizes name matches | Data sourced from Debian's `pkg-isocodes`/`iso-codes` (matches the operator's own "or a json" framing — this *is* that json, wrapped in a Python API). No demonyms (won't match "British"/"Scottish" out of the box) and no city data. |
| **iso3166** | 3.0.0 (2026-09-03) | **MIT** | Yes | ~19 KB total (8.9 KB wheel) | ISO 3166-1 only: alpha-2, alpha-3, numeric, country names. Indexed dicts (`countries_by_alpha2` etc.) | No fuzzy search | Much smaller than pycountry; no subdivisions/US-states, no historic codes, no demonyms. |
| **country_converter (coco)** | 1.3.2 | **GPL-3.0** | Yes | small (pure-Python + a bundled data file) | Converts between many naming systems (ISO2/3/numeric, UN region, continent, "regex"-based fuzzy name matching) | Yes, regex-based | **License is copyleft (GPL-3.0)** — a stronger condition than pycountry's LGPL. For a project distributed under Apache-2.0, a GPL-3.0 *dependency* doesn't relicense gigai itself, but it does mean anyone distributing gigai's *combined work* linked with coco is bound by GPL-3.0's terms for that combination in ways Apache-2.0 alone doesn't require. Needs a real license read (not just my note here) if this option is chosen. |
| **Babel** | (already a mature, widely-used package; not indep. re-verified here) | BSD-3-Clause | Yes | data-heavy (CLDR-derived, several MB) | `Locale(...).territories` dict — territory *names* in any locale, not built for alpha-2 lookup/matching by alias; no city or US-state data | No | Wrong tool for this job: built for i18n display strings, not free-text parsing/matching. Mentioned because it's a common "isn't this already in Babel" question — it isn't a good fit here. |
| **pycountry-convert** | (a separate, smaller, less-maintained wrapper around pycountry-like data) | — | Yes | small | alpha-2 ↔ alpha-3 ↔ country name ↔ continent conversions | Some | Thin convenience wrapper; doesn't add anything pycountry doesn't already have, and is less actively maintained per its own PyPI/GitHub metadata. Not recommended over pycountry directly. |

**License-compatibility read** (READ: `pyproject.toml:11-12` — gigai is
`license = "Apache-2.0"`; `LICENSE` file, 199 lines, Apache-2.0 text
confirmed by header): pycountry's LGPL-2.1 is a *weak* copyleft license
designed exactly for library-dependency use (the FSF's own guidance is that
LGPL libraries may be used by proprietary or differently-licensed
applications without relicensing the application, provided the LGPL'd
library's own source/modifications stay available and dynamic
linking/import is used — which a normal `pip`/`uv` dependency import is).
pycountry is broadly used as a dependency inside Apache/MIT/BSD-licensed
projects for this reason. `country_converter`'s GPL-3.0 is a stronger
condition and deserves an explicit sign-off before adoption; `iso3166` and
`geonamescache`'s MIT and `us`'s BSD are unambiguously compatible.

## 4. US-state disambiguation library (READ: PyPI page, fetched 2026-09-23)

**`us`** (PyPI `us`, from the `unitedstates` project) — version 3.2.0
(2024-07-22), **BSD** license, pure-Python core data (states as Python
objects, not pickled blobs), Python ≥3.8. Covers all 50 states + DC +
territories, postal abbreviations, AP-style abbreviations, FIPS codes,
capitals, statehood years, time zones, and (its one non-trivial dependency)
phonetic/fuzzy name lookup via `jellyfish` (`us.states.lookup("misisipi")`
→ Mississippi).

**Caveat found (READ, `us`'s own PyPI/GitHub metadata plus a general check
on `jellyfish`):** `jellyfish` 1.x is **not pure Python** — the project
rewrote its string-matching algorithms in Rust and ships prebuilt
platform/version-specific wheels (Linux/macOS/Windows, multiple
architectures including aarch64). For `make test-debian-offline`'s
constraint (an offline container — no network to fetch a wheel at test
time), this is a real risk unless the exact Debian container's Python
version + architecture already has a cached/vendored `jellyfish` wheel; a
source build with no network and no Rust toolchain in the container would
fail. This needs a direct check against the offline container's actual
wheel cache before adopting `us`, not assumed to be fine. If only exact
state-name/abbreviation lookup is needed (not phonetic fuzzy matching, which
`filters.py` doesn't currently do for states — it does exact whole-segment
set-membership checks), `us`'s core state objects can likely be used without
ever calling the `jellyfish`-backed phonetic lookup path, but `jellyfish`
would still be an installed transitive dependency needing its own wheel.

**Simpler alternative:** `pycountry`'s ISO 3166-2:US subdivisions (already
counted in its 5,046 subdivision entries, §3) give US state names and
codes as pure data with zero extra dependency, if `pycountry` is adopted
for countries anyway — no need for a second package just for states. This
avoids the `jellyfish` question entirely.

## 5. Recommendation

**Use the structured field first; fall back to pycountry string matching
only where a source gives free text (r1, following §2's corrected
finding).** Concretely, by source:

- **Lever:** read `job["country"]` (ISO alpha-2, or `null`) directly —
  same request Scout already makes (`?mode=json`), no new call. When
  non-null, this *is* the answer; no segment parsing needed. Fall back to
  today's string parsing (on `categories.location`) only when `country` is
  `null`. `categories.allLocations` can similarly replace the
  semicolon-segment-splitting `_segments()` does today for Lever's
  multi-location postings, since it's already a structured array instead
  of one string to split.
- **Ashby:** read `job["address"]["postalAddress"]["addressCountry"]`
  first (same request, no new param) — Ashby's example value shape is a
  country name/code string (e.g. `"USA"`), so this still likely needs a
  pycountry lookup to normalize to ISO alpha-2 (not confirmed here whether
  Ashby always returns ISO-2 vs. a longer form — worth a small live spot
  check before implementation, excluded from this research dispatch), but
  it's a single trusted field instead of free-text segment parsing.
  `secondaryLocations[].address.addressCountry` replaces multi-location
  segment-splitting the same way Lever's `allLocations` does. Fall back to
  parsing the flat `location` string only when `address` is absent.
- **Greenhouse:** no structured field exists (§2) — string parsing via
  `location.name` (today's hand-made tables, or pycountry-backed aliases,
  per below) remains the only option.
- **Exa:** no location field exists (§2) — stays permanently ambiguous
  (`location=""`), exactly as `filters.py`'s docstring already documents.

This means `location_countries`/`_segment_countries`'s free-text parsing
does **not** get deleted — it becomes the fallback path for Greenhouse,
Exa, and any Lever/Ashby row where the structured field is null/missing —
but it stops being the *only* path, and stops being asked to parse rows
where a trustworthy structured answer was available all along and simply
wasn't read.

**Required (for the free-text fallback path, still needed per above):**
- **`pycountry`** (LGPL-2.1, pure Python, ~8 MB wheel) — replaces
  `_COUNTRY_ALIASES` + `_COUNTRY_ALIASES_EXTRA`'s ISO-standard alpha-2 ↔
  official/common-name mapping, and (via its ISO 3166-2:US subdivisions)
  replaces `_US_STATE_NAMES`/`_US_STATE_ABBREVIATIONS`'s data — all sourced
  from Debian's `iso-codes`, i.e. exactly the "or a json" data source the
  operator asked about, not hand-made. 8 MB is the main cost; confirm it
  doesn't blow any wheel-size budget for gigai's own distribution before
  adopting (not checked here — no such budget doc was found in this repo).
  Also useful to normalize Ashby's `addressCountry` string to ISO alpha-2
  if it doesn't already arrive as one (see the Ashby bullet above).

**Optional (only if the operator wants city-level matching kept, rather
than dropped as a P1b nice-to-have):**
- **`geonamescache`** (MIT, pure Python, bundled/offline data, ~35 MB
  wheel) — replaces `_CITY_COUNTRIES`'s 45 hand-picked cities with a real
  GeoNames-derived city→country dataset (34K+ cities at the default
  population threshold, tunable down to smaller towns). Notably heavier
  (35 MB vs pycountry's 8 MB) for a feature that only fixed ~50 real
  postings in one run (P1b's own numbers, filters.py :22-28) — worth an
  explicit operator call on whether that trade is worth it, or whether the
  much smaller current 45-city hand list stays as the pragmatic "kept
  intentionally hand-made and small" exception, now that the *country* and
  *US-state* lookups are no longer hand-made.

**Alternative (not recommended as primary, but noted since it was asked
after):**
- **`country_converter`** could substitute for pycountry's country-name
  matching (and adds regex-based fuzzy matching pycountry's exact/whole-word
  segment approach doesn't need), but its GPL-3.0 license is a strictly
  worse fit than pycountry's LGPL-2.1 for an Apache-2.0 project, for a
  capability pycountry already covers.
- **`us`** could replace pycountry's subdivision data for US states
  specifically, but only if its `jellyfish` dependency's offline-wheel
  availability is confirmed for the Debian container — otherwise it adds
  offline-install risk for no data pycountry doesn't already have via ISO
  3166-2.

**Sketch of how this would work end to end (pseudocode, not a patch — no
code was written; structured-field-first per r1, pycountry fallback per
r0):**

```
import pycountry

# Build once, at import time, from library data instead of hand-typed tuples:
#   for country in pycountry.countries:
#       aliases = {country.alpha_2.lower(), country.name.lower()}
#       if hasattr(country, "official_name"): aliases.add(country.official_name.lower())
#   -> _COUNTRY_ALIASES equivalent, generated, not hand-maintained.
#
# US states, from pycountry.subdivisions.get(country_code="US"):
#   each subdivision has .code ("US-CA"), .name ("California")
#   -> strip "US-" prefix for the postal abbreviation form.

def _segment_countries(segment: str) -> set[str]:
    ... # same segment-splitting/matching structure as today
    ... # but look aliases up against pycountry-derived tables, not
    ... # hand-typed _COUNTRY_ALIASES / _US_STATE_NAMES / _US_STATE_ABBREVIATIONS
    ... # _CITY_COUNTRIES stays exactly as-is (kept hand-made, deliberately
    ... # small — see §5 "Optional" above) unless geonamescache is adopted too

# --- at the ATS-client layer (ats_board_clients.py), before filters.py ever
# --- sees a location string:

def list_lever_board(...):
    for job in payload:
        ...
        structured_country = job.get("country")  # ISO alpha-2 or null
        row = PostingRow(
            ...,
            location=location_name,          # kept as-is, for display + fallback
            location_country=structured_country,  # NEW: trusted, or None
        )

def list_ashby_board(...):
    for job in payload:
        ...
        address = job.get("address") or {}
        postal = address.get("postalAddress") or {}
        structured_country = postal.get("addressCountry")  # may need
                                                             # pycountry normalization
        row = PostingRow(..., location=location_name, location_country=structured_country)

# --- in filters.py: prefer the structured field, fall back to parsing

def country_match(posting_location_country, location, countries):
    if posting_location_country is not None:
        code = _normalize_to_alpha2(posting_location_country)  # pycountry lookup
        return code in wanted if countries else True
    return _country_match_from_text(location, countries)  # today's logic, pycountry-backed
```

This is a `PostingRow`-contract change (a new field) plus a client-layer
change, not just a `filters.py`-internal swap — flagged here since it's
bigger than r0's original sketch implied; actual scoping/sequencing is an
implementation decision for whoever picks this up, not decided by this
research dispatch.

**What would stay hand-made, and why:**
- The demonym-to-country associations gigai currently folds into GB
  ("England"/"Scotland"/"Wales" → GB) and similar editorializing choices
  are a *product* decision about how to bucket UK constituent-country
  mentions, not something any ISO library encodes as-is — this logic (a
  thin mapping layer on top of pycountry's official names) would remain,
  but as a much smaller, explicitly-labeled "we chose to fold these" list
  instead of a full alias table.
- `_CITY_COUNTRIES` (city → country), unless `geonamescache` is explicitly
  adopted per the "Optional" recommendation above.
- The bare `"ca"`/`"in"`/`"de"` exclusion logic itself — this is a
  *disambiguation policy* (prefer US-state reading over country reading for
  these three specific 2-letter collisions), not data a library ships;
  whichever library is adopted, this policy decision stays a few lines of
  gigai-specific logic layered on top of the library's data.

**New dependency needed:** yes — `pycountry` is not currently in
`pyproject.toml`'s `dependencies` (READ, `pyproject.toml:22-28`: only
click, httpx, jsonschema, questionary, referencing today). Size: ~8 MB
wheel, pure Python, no transitive dependencies beyond Python's stdlib
(READ, PyPI page — "Dependencies: Python >=3.10"; gigai requires
`>=3.11`, compatible). If `geonamescache` is also adopted: an additional
~35 MB wheel, MIT, pure Python + bundled data (offline-safe, no network
calls at import or runtime).

**Debian-offline-container constraint:** both `pycountry` and
`geonamescache` publish universal pure-Python wheels with bundled data (no
compiled extensions, no runtime network calls) — the standard case
`make test-debian-offline`'s direct-mount gate (READ,
`CONTRIBUTING.md:14-18`) is built for, same as gigai's existing
dependencies. `us` (if it were chosen instead, not recommended per §4)
would be the one exception needing an explicit offline-wheel-availability
check because of its `jellyfish` dependency's Rust-compiled wheels.

---

## Change log

- 2026-09-23: initial research, dispatched as `country-data-research`
  worker task under Orca task `task_9b8d138e10f0`.
- 2026-09-23 (r1): coordinator review flagged §2 as circular — it proved
  only what Scout's own clients read, not what the APIs return, then
  answered a question ("would a structured field make string-parsing
  unnecessary") it never actually checked. Rewrote §2 from each provider's
  own public API documentation (Lever's `postings-api` GitHub README,
  Ashby's `developers.ashbyhq.com/docs/public-job-posting-api`, Greenhouse's
  `docs.greenhouse.io/job-board.html`, Exa's `exa.ai/docs/reference/search`
  — all WebFetch reads, no live authenticated calls), compared against the
  exact request URLs `ats_board_clients.py` builds. Finding changed
  materially: Lever's `country` (ISO alpha-2) and `categories.allLocations`,
  and Ashby's `address.postalAddress.addressCountry`/`isRemote`/
  `secondaryLocations`/`workplaceType`, all exist on the same endpoint/params
  Scout already calls and are simply not read today — Greenhouse and Exa's
  "no structured field" conclusion stood unchanged. Rewrote §5's
  recommendation to "structured field first, pycountry-backed string
  fallback only where a source gives free text" (Lever/Ashby get the
  structured path; Greenhouse/Exa keep the fallback as their only path),
  updated the pseudocode sketch to show the ATS-client-layer read plus a
  `PostingRow` contract addition, and flagged that this is bigger in scope
  than a `filters.py`-internal-only change. §3 (library license/size table)
  and §4 (US-state library options) were not touched — the task instruction
  said the coordinator judged them accepted. Task `task_6662906d7be3`.

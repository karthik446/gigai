# exa-query-errors — worker report

Task: 0.1.8.1 live-UAT tickets B1 (Exa part) and B5 (Exa part).

READ: `.orchestrator/workers/specs/scout-uat-0181-bugs.brief.txt`,
`.orchestrator/workers/specs/scout-run-command.brief.txt` (addendum),
`src/gigai/scout/find_jobs/contracts.py` (`FindJobsConfig`, `_country_codes`,
`_COUNTRY_CODE`), `src/gigai/scout/find_jobs/filters.py` (how `countries` /
`location` are used downstream), Exa's `/search` API reference
(`https://docs.exa.ai/reference/search`, redirects to
`https://exa.ai/docs/reference/search`) — fetched the documented
`SearchRequest` schema (no live Exa calls made).

EXECUTED: edited `src/gigai/scout/find_jobs/exa_client.py` and
`tests/behaviors/scout_find_jobs/test_exa_client.py` only; ran the focused
test file and `make unit-tests`.

## 1. Query shaping

Exa doc URL: https://docs.exa.ai/reference/search (OpenAPI `SearchRequest`
schema). Exact parameter names used, both already-documented, non-invented:

- **`startPublishedDate`** (ISO-8601 datetime string) <- `FindJobsConfig.published_after`.
  This mapping already existed before this change; left as-is. Omitted from
  the request body when `published_after is None`.
- **`userLocation`** (string, two-letter ISO-3166-1 alpha-2 country code) <-
  `FindJobsConfig.countries`, sent **only when `countries` has exactly one
  entry** (`body["userLocation"] = config.countries[0]`). `countries` is
  already validated elsewhere (`contracts.py`'s `_COUNTRY_CODE = r"\A[A-Z]{2}\Z"`)
  so no reformatting is needed.
  - `countries == ()` (absent/default): field omitted — no location
    constraint requested.
  - `len(countries) > 1`: field omitted. `userLocation` is a single string,
    not a list, so it cannot express "US or CA"; the docs give no multi-value
    or free-text location param, and I was told not to invent one. Recorded
    as a soft gap in the module docstring — Exa's index has no per-posting
    country field to filter by server-side either way, so B1's hard
    downstream country filter (`market_acquisition.py`/`filters.py`) is what
    actually enforces `countries` — this is a best-effort steer to reduce
    irrelevant boards from Exa itself, not a guarantee.
- **`config.location`** (free text, e.g. "Denver, CO"): confirmed the Exa
  schema has **no** location field besides `userLocation` (no `location`,
  no query-text location operator documented). `FindJobsConfig` is
  operator-authored and `merged_queries` is built/authored elsewhere (not by
  this client), so splicing `location` into query text would mean this
  module inventing logic that belongs to whichever code builds
  `merged_queries` — out of scope for `exa_client.py` and the owned-files
  list. Left untouched; documented in the module docstring as an
  intentional gap for the coordinator to route to whoever owns query
  construction.

## 2. Errors

`ExaClientError` for HTTP failures now reads: `"Exa returned {status} ({reason})"`,
e.g. `"Exa returned 402 (payment required / out of credits)"`,
`"Exa returned 429 (rate limited)"`. Reason strings come from a fixed
status-code -> short-string table (`_EXA_HTTP_REASONS`) inside
`exa_client.py`, never from the response body or request (so nothing an
attacker-controlled or leaky response echoes can end up in the message).
`error.code` is still `exa_http_{status}` (unchanged shape). `exa_missing_key`
message is unchanged (still names `gigai secrets add exa`). No header or API
key is ever included — verified by tests asserting the key value and the
literal string `"x-api-key"` are both absent from every raised message.

## Tests added (`test_exa_client.py`)

- `_config()` now includes `countries=("US",)` by default (override-able).
- `test_request_shape_and_mapping` asserts `body["userLocation"] == "US"`.
- `test_omits_user_location_when_countries_empty`
- `test_omits_user_location_when_multiple_countries`
- `test_sends_user_location_for_single_country` (uses `"GB"`)
- `test_http_error_status_raises_redacted_error` extended to include 402,
  and now also asserts the status code string and absence of `"x-api-key"`
  appear/don't appear in the message.
- `test_402_error_names_payment_required`
- `test_429_error_names_rate_limited`

## Results

- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_exa_client.py -q` — **24 passed**.
- `make unit-tests` — **872 passed, 1269 deselected**.

## Scope / exclusions honored

- Only touched `src/gigai/scout/find_jobs/exa_client.py`,
  `tests/behaviors/scout_find_jobs/test_exa_client.py`, and this report.
- Did not touch `filters.py`, `ats_board_clients.py`, `contracts.py`,
  `market_acquisition.py` — read-only. `sponsorship_from_text` call left
  exactly as it was.
- No live Exa calls (docs pages fetched via WebFetch only).
- Never read `~/.gigai/.env`.
- No `git add`/`commit`/`stash`/`reset`/`clean`.
- Ran only the focused test file plus `make unit-tests` (no full/slow suite).

## What's left

- Nothing outstanding for this worker's scope (B1 Exa-query-shaping part and
  B5 Exa-error-surfacing part are both done). The multi-country `userLocation`
  gap and the `location`-free-text gap are documented above for the
  coordinator to decide whether/where to address (likely in whatever module
  builds `merged_queries`, not in `exa_client.py`).

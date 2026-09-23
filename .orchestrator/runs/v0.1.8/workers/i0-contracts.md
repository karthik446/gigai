# I-0 shared find-jobs contracts

## State

`complete` for the owned wave-1a contract surface.  This is source-checkout
implementation and focused offline verification only; it is not live/provider,
model, installed, release, or M1 acceptance.

## Files

- `src/gigai/scout_find_jobs_contracts.py`
  - Frozen stdlib DTOs with closed-key `from_json`, canonical `to_json`, and
    `sha256:` canonical `digest()` values.
  - Find-jobs config/source toggles, normalized posting rows and outcomes,
    redacted failures, exact `scout-watchlist:1`, serializable `NodeContext`,
    acquire/assess/present inputs and outputs, matrix/not-assessed rows, node
    receipts, producer/usage/failure blocks, and aggregate status precedence.
  - Typed node-callable and A-1..A-5 client protocols, four-domain
    `parse_board_url`, URL normalization/diff/content hashing, capability/effect
    constants, loopback API bind, consent envelope, request/response DTOs, and
    the frozen four-route table.
- `tests/behaviors/scout_find_jobs/__init__.py`
- `tests/behaviors/scout_find_jobs/conftest.py`
- `tests/behaviors/scout_find_jobs/test_contracts.py`
- `tests/behaviors/scout_find_jobs/fixtures/fixture-find-jobs-config-v1.json`
- `tests/behaviors/scout_find_jobs/fixtures/fixture-acquire-input-v1.json`
- `tests/behaviors/scout_find_jobs/fixtures/fixture-watchlist-v1.json`
- `tests/behaviors/scout_find_jobs/fixtures/fixture-acquire-batch-v1.json`
- `tests/behaviors/scout_find_jobs/fixtures/fixture-assessment-v1.json`
- `tests/behaviors/scout_find_jobs/fixtures/fixture-ui-consent-v1.json`
- `tests/behaviors/scout_find_jobs/fixtures/fixture-node-receipts-v1.json`
- `tests/behaviors/scout_find_jobs/fixtures/fixture-present-payload-v1.json`

## Verification

Focused tests (EXECUTED, no provider/model/network calls):

```text
rtk uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_contracts.py -q
......................                                                   [100%]
22 passed in 0.03s
```

The first required `uv` invocation was retried with the approved external
cache read because the sandbox could not open `/Users/kar/.cache/uv`; the test
itself remained read-only and offline.

Ruff configuration check (EXECUTED): no `[tool.ruff]` section or `ruff` key is
present in `pyproject.toml`.  The requested focused lint was nevertheless run
as a read-only probe and passes:

```text
rtk uv run --locked --extra test ruff check src/gigai/scout_find_jobs_contracts.py tests/behaviors/scout_find_jobs --output-format concise
All checks passed!
```

## Choices made

- D9 serializes the three source switches as `sources: {exa, ats,
  hiringcafe}`; `hiringcafe` is required in the closed shape and defaults to
  `false` in `SourceToggles`, preserving the post-M1 cut without a hidden
  fallback.
- Config and node DTOs use explicit `schema_version` constants; nested DTOs
  have only their declared fields.  Canonical digests reuse
  `canonical_json_digest`, so the returned form is the repository-standard
  `sha256:<hex>` value.
- `ROUTES` is a tuple of `RouteSpec` named tuples so callers can destructure a
  frozen `(method, path, request_type, response_type, status_codes)` row.  The
  route statuses are `(200,404,422)`, `(202,400,403,409,422)`, `(200,404)`, and
  `(200,404)` for config, run, run status, and results respectively.
- `fixture-watchlist-v1` and `fixture-node-receipts-v1` use small frozen wrapper
  DTOs so each plural fixture remains one strict JSON object while carrying the
  roadmap's multiple entries/receipts.  `fixture-ui-consent-v1` is the exact
  seven-key D5 envelope; scope is carried by `RunRequest` rather than added to
  that closed envelope.
- `aggregate_status([])` returns `pending` because an empty graph has not
  completed any required node; all non-empty `complete` statuses return
  `succeeded`, and all other statuses follow the frozen precedence.
- `parse_board_url` accepts HTTP(S) URLs and the first board-token path segment
  for both Greenhouse hosts plus Lever and Ashby; it returns `None` for other
  hosts, missing tokens, malformed URLs, and non-HTTP(S) schemes.

## READ vs EXECUTED

READ: project `AGENTS.md` and RTK instructions; Amendment 02 Revision 3;
the find-jobs roadmap and fixture/DAG tables; canonical helpers; proposal and
acquisition closed-key patterns; adapter IDs; common effect enum; and the
existing consent validator route.

EXECUTED: read-only git status, focused contract pytest, focused ruff check,
Python bytecode/import probes, and Orca heartbeat/follow-up checks.  No
provider/network/model calls, no API server, no full suite, no schema changes,
no edits outside the owned files, and no git add/commit/stash/reset/clean.

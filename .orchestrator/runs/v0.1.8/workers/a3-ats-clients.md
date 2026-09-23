# A-3 · ATS board clients + board-token extraction

## State
Done. Acceptance command passes:

```
uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_ats_board_clients.py -q
```

Output:
```
........................                                                 [100%]
24 passed in 0.11s
```

## Files (both new; no other files touched)
- `src/gigai/scout_ats_board_clients.py`
- `tests/behaviors/scout_find_jobs/test_ats_board_clients.py`

## Design choices
- Implemented `ATSBoardClients.list_board(client, provider, board_token, config) -> tuple[PostingRow, ...]`,
  matching the frozen `ATSBoardClient` protocol signature in
  `scout_find_jobs_contracts.py:1374-1375` exactly. Also exposed
  `list_greenhouse_board` / `list_lever_board` / `list_ashby_board` as
  standalone functions for direct/focused testing.
- **Board-token parsing**: did NOT reimplement URL→(provider, token)
  extraction. `parse_board_url` already exists in
  `scout_find_jobs_contracts.py:1388-1422` and covers all four admitted
  hostnames (`boards.greenhouse.io`, `job-boards.greenhouse.io`,
  `jobs.lever.co`, `jobs.ashbyhq.com`), including Ashby's `embed/job_board`
  form. I re-export it from this module (`__all__`) so callers who need
  parsing alongside listing can import it from one place, but the logic
  itself stays in contracts, per the "never redefine" instruction.
- **`matches_roles(title, roles) -> bool`**: pure function, case-insensitive.
  A role matches when every whitespace-separated token in that role string
  is a substring of the lowercased title (so "software engineer" matches
  "Senior Software Engineer" and "Engineer, Software Platform" but not
  "Software Manager"). Any one role matching is enough. Empty `roles` matches
  nothing (fail closed). Documented in the function's docstring per the task.
- **content_sha256**: computed via `contracts.content_hash` over
  `f"{title}\n{description_or_content}".encode("utf-8")` — i.e. the
  posting's own text bytes (title + free-text body), not the raw HTTP
  response bytes, since the task says "content_sha256 = contracts.content_hash
  of the posting's text bytes (UTF-8)" and each provider's raw payload
  includes fields irrelevant to content identity (ids, categories, etc).
- **query_key**: derived deterministically as `f"ats:{provider}:{board_token}"`
  (e.g. `ats:greenhouse:acme`). Contracts requires a non-empty string but
  does not prescribe ATS query-key shape; this makes provenance obvious and
  is stable across runs so acquire-side dedup/URL-diff logic in A-4/A-6 sees
  a consistent key per board without me guessing which configured role
  string produced the match.
- **published_at**: Greenhouse/Ashby ISO-8601 strings are validated
  (must parse via `datetime.fromisoformat` with a timezone) before being
  passed through as-is; malformed/naive timestamps become `None` rather than
  failing the whole row. Lever's `createdAt` is epoch-milliseconds and is
  converted to an ISO-8601 UTC string (`Z` suffix).
- **Role filtering happens locally, before mapping to `PostingRow`** — the
  task says "Local role filter", so non-matching jobs are dropped inside
  each `list_*_board` function rather than returned for a caller to filter
  later.
- **Redacted errors**: `ATSBoardClientError(code, message)` — codes are
  `network_error`, `http_error`, `bad_json`, `unsupported_provider`. Messages
  only ever contain the provider name and board token, never response
  bodies, headers, or exception text from the underlying `httpx` error
  (verified in tests that injected "secret"/IP-bearing strings never leak
  into the raised message).
- Each provider function independently validates the top-level JSON shape
  (`{"jobs": [...]}` for Greenhouse/Ashby, a bare list for Lever) and raises
  `bad_json` on any mismatch — malformed individual job dicts inside the
  list are skipped rather than failing the whole batch (matches the "public,
  best-effort, unauthenticated" nature of these feeds).

## Assumed API shapes (recorded per task instruction)
- **Greenhouse** `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`
  → `{"jobs": [{"id", "title", "absolute_url", "location": {"name"},
  "updated_at", "content"}]}`.
- **Lever** `GET https://api.lever.co/v0/postings/{token}?mode=json`
  → `[{"id", "text", "hostedUrl", "categories": {"location"},
  "createdAt" (epoch ms), "descriptionPlain"}]`.
- **Ashby** `GET https://api.ashbyhq.com/posting-api/job-board/{token}`
  → `{"jobs": [{"id", "title", "location", "jobUrl", "publishedAt",
  "descriptionPlain"}]}`.

These are exactly the shapes given in the task brief; I did not hit any live
endpoint to verify them (no live network was used — see below), so if a
provider's real shape differs, the `bad_json`/`http_error` handling degrades
gracefully (redacted failure) rather than crashing on an unexpected field
(unknown/extra keys are ignored via `.get()`).

## READ vs EXECUTED
**READ:**
- `src/gigai/scout_find_jobs_contracts.py` (full file: DTOs, `ATSBoardClient`
  protocol at line 1374, `parse_board_url` at 1388, `normalize_url` at 1425,
  `content_hash` at 1501, `FindJobsConfig`/`PostingRow` dataclasses).
- `docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md`
  (A-3 row and surrounding DAG/ownership table, fixtures table, cut-list).
- `docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-amendment-02-find-jobs-functional.md`
  (Revision 3 change note, to confirm T2: contracts code is the frozen
  authority, not this doc's prose).
- `tests/behaviors/scout_find_jobs/fixtures/fixture-find-jobs-config-v1.json`,
  `fixture-acquire-batch-v1.json`, `fixture-watchlist-v1.json` (read-only,
  for shape/convention reference — board tokens, company names, query_key
  patterns for `source_kind: ats` rows).
- `tests/behaviors/scout_find_jobs/conftest.py`, `test_contracts.py` (grepped
  for `query_key` usage — no existing convention constraint found).
- `src/gigai/canonical.py` (`digest_imported_bytes`, `canonical_json_digest`
  signatures only, to confirm what `content_hash` wraps).
- `pyproject.toml` (confirmed `httpx>=0.27` is a dependency).

**EXECUTED:**
- `git status --porcelain` (twice, scoped, to confirm only my two new files
  changed).
- `mkdir -p .orchestrator/workers`.
- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_ats_board_clients.py -q`
  (passed, 24/24) — this is the only test command run; no `make test`, no
  live network, no other packet's tests were executed.
- One throwaway `python3 -c` one-liner to confirm the epoch-ms → ISO
  conversion I hardcoded in a test assertion (`1758326400000` →
  `2025-09-20T00:00:00+00:00`), not part of the shipped module.

No live network or provider calls were made anywhere; all HTTP in tests goes
through `httpx.MockTransport` via an injected `httpx.Client`, per the
exclusions. No git stash/reset/clean/add/commit was run.

## What's left
Nothing outstanding for A-3 itself. Downstream: A-6 (acquire orchestration)
will need to call `ATSBoardClients().list_board(...)` per active watchlist
entry and merge results with Exa rows; A-5 (watchlist) is the one that turns
a first-seen ATS/Exa row into a `WatchlistEntry` using `parse_board_url` —
that wiring is out of scope for this packet and untouched here.

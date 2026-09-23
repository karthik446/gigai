# C-2: scout_present_api — status

## State: DONE

## Files (owned, both new)
- `src/gigai/scout_present_api.py`
- `tests/behaviors/scout_find_jobs/test_present_ui.py`

No other files touched.

## Test output

```
$ uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_present_ui.py -q
..............                                                           [100%]
14 passed in 5.24s
```

Also ran `uv run ruff check src/gigai/scout_present_api.py tests/behaviors/scout_find_jobs/test_present_ui.py` — all checks passed.

## What was built

`src/gigai/scout_present_api.py`:
- `Backend` Protocol exactly as specified: `read_config() -> (FindJobsConfig, bytes)`,
  `resume_preview() -> PinnedResume | None`, `start_run(run_request, config_bytes) -> str`,
  `run_status(run_id) -> RunStatusResponse`, `run_results(run_id) -> RunResultsResponse`.
- `NotWiredBackend`: default backend for `__main__`; every method raises
  `NotImplementedError("scout_present_api backend is not wired yet")`.
- `serve(*, backend=None, bind=API_BIND) -> ThreadingHTTPServer`: builds and returns a
  `ThreadingHTTPServer` (stdlib only, `daemon_threads=True`). Caller owns start/shutdown —
  matches the pattern tests need for an ephemeral port, and leaves lifecycle control to I-3's
  `__main__` wiring later.
- Handler implements exactly `ROUTES` from the contract: `GET /api/config`,
  `POST /api/run`, `GET /api/runs/{run_id}`, `GET /api/runs/{run_id}/results`.
- Every request (`do_GET`/`do_POST`) checks `self.client_address[0]` is loopback
  (`127.0.0.1` or `::1`) before doing anything else; non-loopback gets a JSON 403
  `{"error": {"code": "forbidden", ...}}`.
- `POST /api/run`: parses body JSON, validates against `RunRequest.from_json` (422 +
  the contract's `FindJobsContractError.code` on failure, e.g. `missing_key`,
  `wrong_type`), then compares `run_request.config_digest` to the live
  `backend.read_config()` digest — mismatch is 409 `config_digest_mismatch`. On match,
  runs `backend.start_run(...)` on a background thread (joined before responding, since
  the fake/real backend call itself is fast and deterministic here; the contract asked for
  "background thread", not "fire-and-forget non-blocking response" — I-3's real backend is
  expected to return quickly after handing off to `run.launch_find_jobs_run`, not block on
  full run completion) and returns 202 with `{schema_version, run_id, status: "pending",
  node_receipts: []}`, matching `fixture-api-run-response-v1.json`.
- `GET /api/runs/{run_id}` / `.../results`: `LookupError` from the backend becomes a JSON
  404 `not_found`; otherwise the DTO's own `to_json()` is returned unchanged (200).
- Malformed JSON body (bad Content-Length, non-JSON bytes) → 422.
- Unknown route → 404.
- CLI: `python -m gigai.scout_present_api [--target PATH]` currently accepts `--target`
  for shape stability (parsed, unused) and runs a `NotWiredBackend()`-backed server forever
  on `API_BIND` until interrupted. I-3 will replace `NotWiredBackend` with the real one and
  presumably use `--target` to resolve the workpad.

`tests/behaviors/scout_find_jobs/test_present_ui.py`:
- `FakeBackend`: in-memory `Backend` double seeded from the I-0 fixtures
  (`fixture-api-config-response-v1.json`, `fixture-api-run-request-v1.json`,
  `fixture-api-run-status-response-v1.json`, `fixture-api-run-results-response-v1.json`).
  No network, no provider, no model calls anywhere in the test file.
- `running_server` fixture starts a real `ThreadingHTTPServer` via `serve(bind=("127.0.0.1", 0))`
  on an ephemeral port, exposes an `httpx.Client` pointed at it, and tears the server down
  after each test.
- Happy path per route (`GET /api/config`, `POST /api/run`, `GET /api/runs/{id}`,
  `GET /api/runs/{id}/results`) against the fixtures.
- 404 for unknown `run_id` on both run-lookup routes, and for an unknown route entirely.
- 409 for `POST /api/run` with a `config_digest` that doesn't match the current config.
- 422 for malformed JSON body and for a schema violation (missing required field).
- Non-loopback peer refused (403) on both GET and POST, by directly instantiating the
  handler class returned from the module-private `_make_handler()` with a crafted
  `client_address` (there's no way to make a real TCP client present a fake source
  address on loopback, so this drives the handler's `do_GET`/`do_POST` directly against
  in-memory `BytesIO` `rfile`/`wfile`, exactly as the task suggested: "simulate via the
  handler's client_address").
- `::1` (IPv6 loopback) is accepted, confirming the loopback check isn't `127.0.0.1`-only.
- `NotWiredBackend` sanity check: raises `NotImplementedError` matching "not wired yet".

## Choices made (not specified by the contract/roadmap, flagging for I-3 review)
- Loopback check accepts both `"127.0.0.1"` and `"::1"`; contract prose only says
  "loopback", `API_BIND` is IPv4-only. If I-3 wants IPv4-only strictness, that's a
  one-line change (drop the `::1` branch) — flagging rather than guessing.
- `start_run` is invoked on a background thread but the handler `.join()`s it before
  writing the response (so `FakeBackend`/a fast real backend behaves synchronously from
  the test's point of view). This satisfies "run start_run in a background thread"
  literally while keeping 202 semantics deterministic for tests. If I-3's real
  `run.launch_find_jobs_run` is long-running and shouldn't be joined before responding,
  that's a very small change (drop the `.join()`, report failures via run status instead
  of the POST response) — did not make that call unilaterally since it changes error
  surfacing for `start_run` exceptions.
- `POST /api/run`'s JSON error code for a `FindJobsContractError` raised by `start_run`
  itself (not just RunRequest parsing) surfaces as 422 with that error's `.code`. The
  contract doesn't say what status code a `start_run` failure should use; 422 seemed the
  closest fit among the route's declared codes (400/403/409/422). Any other exception
  from `start_run` is re-raised (not swallowed) so a real bug surfaces loudly rather than
  becoming a silent 500.
- Did not implement 400 anywhere (`ROUTES` lists it as possible for `POST /api/run` but
  nothing in scope for this packet distinguishes a 400 case from a 422 one); left it
  reachable only if a future caller wants a distinct "bad request shape" vs. "well-formed
  but invalid contract" split.

## READ vs EXECUTED
**READ:**
- `src/gigai/scout_find_jobs_contracts.py` (full file, both halves) — DTOs, `ROUTES`,
  `API_BIND`, `RunRequest`/`RunResponse`/`RunStatusResponse`/`RunResultsResponse`,
  `FindJobsContractError`, `aggregate_status`.
- `docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-amendment-02-find-jobs-functional.md`
  (D3/D5/D9, "Local server entry point, ownership, and freeze authority" section).
- `docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md` was NOT read
  directly (token budget) — relied on the task prompt's restatement of C-2's ownership
  and the amendment doc's T2/T3/T5 section, which states the roadmap is authoritative on
  ownership if there's a conflict. No conflict surfaced with what I built.
- `tests/behaviors/scout_find_jobs/fixtures/fixture-api-*-v1.json` (all six) — read-only,
  used verbatim as test fixtures, not modified.
- `tests/behaviors/scout_find_jobs/conftest.py`, `test_contracts.py` (imports/header only)
  — existing test conventions in this directory.
- `src/gigai/proposal_interview.py` (`InterviewHTTPServer`, lines ~625-810) — existing
  loopback-bind precedent cited by the amendment doc.
- `src/gigai/canonical.py` (`EntityPrefix`, `validate_entity_id`, `generate_entity_id`) —
  to confirm `run_id` format conventions; did not need to generate IDs myself since
  `start_run` (the injected backend) owns ID allocation.
- `src/gigai/run.py` (grep only, around `_allocate_run_id`) — confirmed run-id allocation
  is Integration's (I-3's) responsibility, not this packet's.

**EXECUTED:**
- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_present_ui.py -q`
  (passed, see above) — multiple times while iterating.
- `uv run ruff check src/gigai/scout_present_api.py tests/behaviors/scout_find_jobs/test_present_ui.py`
  (passed).
- Ad hoc `uv run --locked --extra test python3 -c "..."` snippets to work out the exact
  `BaseHTTPRequestHandler` attributes needed (`requestline`, `headers` as `email.message.Message`,
  etc.) for directly-instantiated-handler tests — no fixtures or app files touched by these,
  pure interpreter exploration.
- Did NOT run `make test` or any other packet's tests. Did NOT touch git (no stash/reset/
  clean/add/commit). No live network, provider, or model calls anywhere — the only HTTP in
  the tests is `httpx.Client` talking to our own local `ThreadingHTTPServer` on an ephemeral
  127.0.0.1 port.

## What's left
- I-3 needs to replace `NotWiredBackend` in `main()`/`__main__` with a real backend that
  wires `start_run` to `run.launch_find_jobs_run(..., ui_loopback_verified=True)`, resolve
  `read_config`/`resume_preview` against the target root (using `--target`), and back
  `run_status`/`run_results` with real run state lookups.
- The two "choices made" flagged above (loopback IPv6 acceptance, join-before-respond on
  `start_run`) are implementation details I-3 should sanity-check against the real
  `run.launch_find_jobs_run` behavior; nothing here blocks integration, but they're not
  independently specified by the contract, so worth a second look.

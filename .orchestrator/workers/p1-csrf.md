# Worker: p1-csrf

**Task:** PR #37 review P1 finding #6 — CSRF on the Scout local API's
state-changing routes. Loopback-only checks (`_check_loopback`) don't stop
a malicious web page open in the operator's browser: a same-machine,
no-cors `fetch(url, {method: "POST", mode: "no-cors"})` passes the loopback
check and can spend the OpenAI/Exa budget (`POST /api/discover`,
`POST /api/run`) or overwrite discovery prefs (`PUT /api/setup`).
Dispatched under Orca task `task_3f45112525ab`.

**Status:** Done. All owned files touched except `ui/dist` (not needed —
see below); nothing outside the owned-files list changed (confirmed via
`git status`).

READ vs EXECUTED: the vulnerability was READ from `present_api.py`
(`_check_loopback` checks only `self.client_address[0]`, no Origin/Host/
Content-Type check anywhere) and confirmed EXECUTED by writing the failing
repro test first (`test_present_csrf.py`) and running it against the
unmodified code before any fix: 6 of 12 tests failed exactly as predicted
(cross-origin POST/PUT accepted, `text/plain` and
`application/x-www-form-urlencoded` bodies accepted, Host-header mismatch
accepted). All test runs below were EXECUTED with `uv run --locked --extra
test pytest`; no live network call.

## Files changed

- `src/gigai/scout/find_jobs/present_api.py` — added `Handler._check_csrf()`
  and `Handler._bound_port()`; wired `_check_csrf()` into `do_POST` and
  `do_PUT` right after `_check_loopback()` (before route dispatch, so it
  covers every current and future POST/PUT route uniformly — there are no
  PATCH/DELETE routes today, confirmed via
  `grep -n "do_POST\|do_PUT\|do_PATCH\|do_DELETE" present_api.py`). Three
  checks, in order:
  1. `Content-Type` must be exactly `application/json` (ignoring a
     `; charset=...` suffix) → 415 `unsupported_media_type`. This is the
     check that actually defeats the no-cors attack: a "simple" cross-origin
     request cannot set this header to a non-form value without the browser
     forcing a CORS preflight, which this server never answers with
     `Access-Control-Allow-Origin`.
  2. `Origin`, only when the header is present at all, must equal
     `http://127.0.0.1:<port>` or `http://localhost:<port>` for the actual
     bound port (read from `self.server.server_address[1]`, not the
     `API_BIND` constant, so a non-default `--port` is still enforced
     correctly) → 403 `forbidden_origin`. No `Origin` header (same-origin
     fetch, curl, the CLI) is let through — browsers always send `Origin`
     on a cross-origin write, so requiring it unconditionally would have
     broken same-origin tooling without stopping any real attack.
  3. `Host` must equal `127.0.0.1:<port>` or `localhost:<port>` for the
     bound port → 403 `forbidden_origin`. This is the DNS-rebinding guard
     the ticket asked for: without it, an attacker-controlled DNS name that
     resolves to 127.0.0.1 could make a real browser send an honest,
     matching `Origin` header and pass check 2 alone.
  Never sets `Access-Control-Allow-Origin` anywhere (grepped after the
  change to confirm). Module docstring updated to describe both guards.
  GET routes (`do_GET`) untouched.
- `src/gigai/scout/ui/src/api.js` — **no change needed.** Read it first, per
  the ticket: `request()` (api.js:49-59) already sends
  `headers: body ? { "Content-Type": "application/json" } : undefined` on
  every call that has a body, and all three writes always pass a body —
  `startRun`/`putSetup` pass the real payload, and `startDiscovery()` passes
  `{}` explicitly (api.js:145-147) rather than omitting the body, so it also
  gets the header. `ui/dist` therefore also needed no rebuild (confirmed via
  `git status --short src/gigai/scout/ui/dist`: no diff).
- `tests/behaviors/scout_find_jobs/test_present_csrf.py` (new) — 13 tests
  against a real `serve()` instance on an ephemeral port (`bind=("127.0.0.1",
  0)`), same `running_server` fixture pattern as the sibling present_api
  test files: cross-origin evil-Origin POST/PUT on all three routes (403 or
  415 — Content-Type is checked first, so a `text/plain` cross-origin body
  correctly gets 415, and a separate test isolates the pure Origin-mismatch
  case with a JSON content-type to confirm 403 `forbidden_origin`
  specifically); `text/plain` and `application/x-www-form-urlencoded`
  content-type rejection; Host-header mismatch rejection; the UI's own
  request shape (same-origin `Origin`, `application/json`, matching Host)
  accepted on all three routes and actually reaching the backend (asserted
  via a `_CsrfBackend` double's `started_run`/`wrote_setup`/
  `started_discovery` flags, not just the status code); no-`Origin`-header
  same-Host request still accepted (curl/native-fetch shape);
  `localhost:<port>` Origin also accepted; and a check that no response ever
  carries `Access-Control-Allow-Origin`.
- `.orchestrator/workers/p1-csrf.md` (this file).

## Repro-first result (before the fix)

`uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_present_csrf.py -q`
against the unmodified `present_api.py`: **6 failed, 6 passed** — every
cross-origin/wrong-content-type/Host-mismatch case was accepted (200/202)
and the backend's write flag flipped `True`, exactly the reported hole.

## Test results (after the fix)

- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_present_csrf.py -q`
  → **13 passed**.
- `uv run --locked --extra test pytest
  tests/behaviors/scout_find_jobs/test_present_setup_discover.py
  tests/behaviors/scout_find_jobs/test_present_ui.py
  tests/behaviors/scout_find_jobs/test_present_api_static.py -q`
  (the three regression files named in the ticket) → **75 passed** — the
  UI-shape writes in these existing tests all use `httpx.Client(...).post/
  put(..., json=...)`, which sets `Content-Type: application/json`
  automatically and never sets a conflicting `Host`, so they were unaffected
  by the new guard.
- `make unit-tests` → **1023 passed, 1450 deselected** (this new file and
  its present_api siblings use a real threaded HTTP server, so — like the
  other present_api test files — they aren't in the `fast_unit`-marked
  subset `make unit-tests` selects; run directly above instead, per the
  ticket's test budget).

## Not done / explicitly out of scope

- No CORS headers added anywhere (the ticket explicitly says never to add
  `Access-Control-Allow-Origin`, and this fix doesn't need one — the guard
  works by making the browser's own preflight requirement unsatisfiable).
- No per-run CSRF token / double-submit cookie scheme — the ticket's
  Content-Type + Origin + Host combination was the specified fix and is
  sufficient against the no-cors attack shape described.
- `ui/dist` not touched — confirmed unnecessary rather than skipped (see
  above).
- No `git add`/commit/stash/reset/clean/push; no edits outside the
  owned-files list (confirmed via `git status`).

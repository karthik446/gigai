# Worker: s2b-setup-interview-ui

**Task:** S23 stage 2, packet B — the setup interview in the Scout UI + the
"Discover companies" panel. Dispatched under Orca task `task_f951e3e00855`.

**Status:** Done. All owned files changed; nothing outside the owned-files
list touched (confirmed via `git status`; `pyproject.toml`, `uv.lock`,
`src/gigai/scout/scout_cli.py`, and `src/gigai/scout/find_jobs/discovery/**`
changed too, but from S2-A's concurrent packet landing in the same shared
worktree, not from this dispatch).

READ vs EXECUTED: READ the S23 spike doc (11 interview questions, §2),
`present_api.py`, `contracts.py`'s `FindJobsConfig`/validation helpers,
`scout_cli.py`'s `STARTER_FIND_JOBS_CONFIG`, every existing UI source file
(`App.jsx`, `api.js`, `ConfigPanel.jsx`, `RunConfirmDialog.jsx`,
`styles.css`), the existing `test_present_ui.py`/`test_present_api_static.py`
conventions, and — once it landed mid-dispatch — S2-A's real
`discovery/__init__.py`, `discovery/prefs.py`, and `discovery/types.py` to
confirm my contract assumptions against the real dataclasses. EXECUTED every
pytest run and every `yarn build` run below; no live Exa/OpenAI/H-1B/model
network call anywhere (temp homes/targets only, fake `discovery` module
installed via `monkeypatch` for the backend-level tests).

## Design

- **`present_api.py`** (setup/discover routes only, per owned-files):
  - `GET /api/setup`: 200 with saved `DiscoveryPrefs` JSON, or 404
    `prefs_missing` carrying `prefill` (derived from the current
    `find-jobs.json` via `_prefs_prefill_from_config` — `roles`, `countries`,
    `city`/`work_mode`, `visa_sponsorship_required`; the rest default the
    same way the PUT validator would). 503 `discovery_unavailable` if the
    discovery module can't be imported.
  - `PUT /api/setup`: `_validate_setup_body` validates all 11 fields
    (`roles` non-empty, `countries` ISO alpha-2, `work_mode` enum,
    `cadence_days`/`budget_usd_per_session` positive, unknown top-level keys
    rejected) and returns 400 with `field_errors` (one message per bad
    field) on failure — never a single opaque string. On success,
    `ScoutFindJobsBackend.write_setup` calls `discovery.save_prefs` **and**
    atomically rewrites `find-jobs.json`'s `roles`/`merged_queries`
    (mirrored from `roles`, matching `STARTER_FIND_JOBS_CONFIG`'s own
    convention)/`location` (from `city`)/`remote` (derived from
    `work_mode`)/`countries`/`visa_sponsorship_required`, keeping every
    other `FindJobsConfig` field (`published_after`, `sources`,
    `default_assess_cap`, `default_model_target`) from the existing file
    unchanged (or `FindJobsConfig`'s own defaults if no file exists yet).
    The write uses a new `_atomic_write_json` (write-temp + fsync +
    `os.replace`, the same pattern `capabilities.py`'s `_atomic_write` and
    `config.py` already use elsewhere in this codebase).
  - `POST /api/discover`: starts `run_discovery` on a **background thread**
    inside `ScoutFindJobsBackend`, guarded by a `threading.Lock` so a second
    POST while one is running gets 409 `discovery_running` before a second
    thread is even started. Returns 202 with a **request-scoped tracking
    id** (`discovery_req_<uuid>`), not `DiscoveryResult.discovery_id` — the
    real id only exists once `run_discovery` (synchronous, 5-30 min per the
    contract) returns; `GET /api/discover/latest` is the authority for the
    real id and live state, the same relationship `/api/run`'s `run_id` has
    to `/api/runs/{id}` status. 404 `prefs_missing` if no prefs saved yet
    (checked before starting); 503 `discovery_unavailable` if the module
    can't be imported. If `run_discovery` itself raises (the contract says
    it never should for a provider error, but S2-A's real
    `DiscoveryBudgetExceeded` can fire *before* any spend) the background
    thread records a synthetic `"failed"` result instead of the exception
    vanishing into a daemon thread with nothing observing it.
  - `GET /api/discover/latest`: `latest_discovery` JSON (or `null`),
    `running` (bool), and `days_ago` (computed from `finished_at`, `None`
    on an unparsable timestamp rather than raising — display-only field).
    While a run is in progress, this reads the in-memory
    `_discovery_progress` snapshot (updated by `on_progress` and on
    completion) instead of the sealed file, so the panel shows live status
    without needing S2-A to define a progress-file format.
  - CHANGE #3's "code against the contract with a test stub" is
    structural, not just test-only: every setup/discover method routes
    through `ScoutFindJobsBackend._discovery_module()` (a `@staticmethod`
    doing `from gigai.scout.find_jobs import discovery`), so the whole API
    still starts and every other route still works with the discovery
    package absent — the only user-visible effect is the four setup/
    discover routes returning 503. Confirmed directly: importing
    `present_api` succeeds and `NotWiredBackend`/`ScoutFindJobsBackend`
    construct fine with no `discovery` package installed.
  - Left `contracts.py`'s DTO classes and `ROUTES` tuple untouched (not an
    owned file); setup/discover responses are plain dicts, matching how the
    B4 progress route already does this for a non-frozen-contract shape.

- **UI (`ui/src/**`)**:
  - `SetupInterviewForm.jsx` (new): all 11 questions in order (roles;
    titles to avoid; countries; work-mode select + conditional city field;
    a yes/no toggle for visa sponsorship; exclude/watch companies; company
    stage/size + industries in/out; must-have/dealbreaker stack;
    cadence + budget). Free-text list fields use the new `TagListInput.jsx`
    (type, Enter/comma to add, × to remove, backspace on empty draft pops
    the last tag); countries use `CountryPicker.jsx` (same tag mechanics,
    client-side uppercased 2-letter validation mirroring the server's own
    `_setup_countries` regex, so a bad code is caught before the round
    trip). Submitting sends the exact `PUT /api/setup` shape; a 400's
    `field_errors` render per-field under the relevant input via `errors`
    from `ApiError.field_errors` (new: `api.js`'s `ApiError` now carries
    every extra error-body field, not just `code`/`message`).
  - `DiscoverPanel.jsx` (new): "last run N days ago"
    (`never run`/`today`/`1 day ago`/`N days ago`), cost, status, a
    "Start discovery session" button (disabled while running/starting),
    a running-state callout, per-source cost/run-count with the
    coordinator's FYI applied (an H-1B `skip_reason` of
    `sponsorship_not_required` renders as "not used, since sponsorship
    isn't required", not folded into the same styling as a real `error`),
    and a card per new board: linked company name, ATS provider, a
    sponsorship mark (reusing the existing sponsorship badge classes) and
    a **verified/unverified evidence mark** (`evidence_verified`), evidence
    text, evidence source link, and matching-US-postings count. Cadence is
    shown ("Runs roughly every N days... shown, not scheduled; gigai has no
    scheduler") never scheduled, per the packet.
  - `App.jsx`: new `useSetup()` hook mirrors `useConfig()`'s shape
    (loading/prefs/prefill/prefsMissing/error). `gigai scout run` opening
    this UI with no discovery prefs saved shows **only** the interview
    (`showFirstRunInterview`), gating the rest of the app (config panel,
    run button, results, Discover panel) behind it — CHANGE #2's "asked
    ONCE... shows the interview first." Once prefs exist, a "Preferences"
    link next to the run button reopens the same form in-place for
    editing, and the Discover panel appears below the run/results section,
    loading `/api/discover/latest` once and polling every 4s only while a
    session is `running` (covers a page reload mid-session too).
  - Coordinator's `evidence_for()` FYI: not wired — the coordinator's own
    message says `DiscoveryResult.new_boards` is enough for the Discover
    panel unless evidence is shown next to *watchlist* boards elsewhere in
    the UI, which this packet doesn't do (no existing watchlist view in
    `ui/src`).
  - `yarn build`: builds clean (33 modules), run twice with a byte-diff
    (`diff -rq`) between the two `dist/` outputs — identical: deterministic.

## Test isolation bug found and fixed mid-dispatch

The first cut of `test_present_setup_discover.py`'s fake-discovery-module
tests passed in isolation but failed when run alongside S2-A's
`test_discovery_prefs.py` in the same process: `ScoutFindJobsBackend.
_discovery_module()` does `from gigai.scout.find_jobs import discovery`,
which resolves via an **attribute lookup on the already-imported parent
package**, not only a `sys.modules` dotted-name lookup. Once any other test
in the same pytest run had really imported
`gigai.scout.find_jobs.discovery` first, that import bound `discovery` as
an attribute on the `gigai.scout.find_jobs` package object; patching only
`sys.modules["gigai.scout.find_jobs.discovery"]` (the original fix) no
longer had any effect on a later `from ... import discovery` in that same
process, since Python finds the attribute before consulting `sys.modules`
again for that name. Fixed by also `monkeypatch.setattr`-ing the fake
module onto the `gigai.scout.find_jobs` package object itself in
`_install_fake_discovery_module`. Confirmed with a minimal repro (`pytest
test_discovery_prefs.py test_present_setup_discover.py`, which reproduced
the failure before the fix and passes after) and with the full
`tests/behaviors/scout_find_jobs/` directory (673 passed, 1 pre-existing
xfail) run twice for good measure.

## Test results

- Exact acceptance command: `uv run --locked --extra test pytest
  tests/behaviors/scout_find_jobs/test_present_setup_discover.py
  tests/behaviors/scout_find_jobs/test_present_ui.py
  tests/behaviors/scout_find_jobs/test_present_api_static.py -q` →
  **74 passed**.
- `test_present_setup_discover.py` (new, 33 tests): HTTP-routing/validation
  tests against a `Backend`-protocol double (`prefs_missing` 404 + prefill,
  200 with saved prefs, 503 when the discovery module is unavailable, 400
  with `field_errors` for empty roles / bad country code / bad work_mode /
  unknown field, defaults applied for optional fields, 202 with a
  discovery_id, 409 when already running, 404 `prefs_missing` on discover
  with no prefs, `days_ago`/`running` shape on latest); `_validate_setup_body`
  unit tests (happy path round-trips all 11 fields, non-object body,
  negative budget, non-positive cadence); `ScoutFindJobsBackend`-level tests
  against a fake `discovery` module matching S2-A's real contract exactly
  (read/write setup, `find-jobs.json` written correctly both when absent
  and when other fields must be preserved verbatim, discovery runs on a
  background thread and `discovery_running()` reflects it, a second start
  while running raises `DiscoveryConflictError`, starting with no prefs
  raises `SetupPrefsMissingError` and releases the lock, latest_discovery
  before/after a run) plus one test confirming the 503 degrade via a
  patched `_discovery_module`.
- Full `tests/behaviors/scout_find_jobs/` directory (includes S2-A's own
  new discovery tests, landed mid-dispatch): **673 passed, 1 xfailed**
  (306s) — the xfail is the pre-existing, already-documented,
  operator-acknowledged M1 regression, untouched by this packet.
- `make unit-tests` → **927 passed** (was 922 before this dispatch per the
  prior b4-progressive-cards report; the +5 are S2-A's own new
  fast_unit-marked discovery tests landing concurrently, not from this
  packet).
- `yarn build` in `src/gigai/scout/ui`: builds clean (33 modules). Ran it
  three times total across this dispatch (after the initial UI write, and
  again after the coordinator's skip_reason FYI) and diffed the resulting
  `dist/` byte-for-byte each time — identical: deterministic.

## Not done / explicitly out of scope

- No live Exa/OpenAI/H-1B/model network call anywhere; temp homes/targets
  only (`tmp_path`); no `git add`/commit/stash/reset/clean.
- `evidence_for()` (the coordinator's contract addition with S2-A): not
  wired, per the coordinator's own message that `DiscoveryResult.new_boards`
  is sufficient for this packet's Discover panel.
- No change to `contracts.py`, `scout_cli.py`, or the `discovery/` package
  itself — all outside this packet's owned files (S2-A's).
- No scheduler/cron wiring for the weekly cadence — shown as a hint
  ("Runs roughly every N days"), never scheduled, per the packet's own
  instruction ("gigai has no scheduler").

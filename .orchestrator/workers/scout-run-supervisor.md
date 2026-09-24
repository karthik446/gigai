# scout-run-supervisor — packet C of 4 for `gigai scout run` (U13/U23 + config-missing message)

## READ (context, no changes)
- `.orchestrator/workers/specs/scout-run-command.brief.txt` (full brief incl. the one ADDENDUM
  present in the file — 0.1.8.1 UAT, 2026-09-24: non-git active-gig, U2 workaround, the
  misleading missing-config message, hidden Exa failure).
- `.orchestrator/workers/scout-ui-package.md` (packet A report — UI served at `/` via
  `importlib.resources`, `ui/dist` package-data, `_ui_dist_root()`/`_resolve_static_resource()`).
- `.orchestrator/workers/scout-setup-cmds.md` (packet B report — `scout_cli.py`'s
  `install`/`resume add`, `template.py`'s `install_scout()`/`ScoutInstallResult`,
  `write_starter_find_jobs_config()`, the non-git active-gig fix via the registry).
- `src/gigai/scout/find_jobs/present_api.py` (full file: `Backend` protocol, `ScoutFindJobsBackend`,
  routing, static serving, `main()`'s argv).
- `src/gigai/scout/scout_cli.py`, `src/gigai/scout/template.py` (full files, packet B's output).
- `src/gigai/scout/ui/src/{api.js,App.jsx,components/ConfigPanel.jsx}`.
- `tests/behaviors/scout_find_jobs/{test_present_ui.py,test_present_api_static.py,test_scout_setup_cli.py}`
  (existing patterns: `running_server` fixture, `FakeBackend`, the `_setup_and_init` non-interactive
  `gigai setup` + `gigai init` sequence).
- `src/gigai/workpad.py` (`resolve_bound_project`, `BoundProject.project_id`),
  `src/gigai/target_binding.py` (`_process_is_alive` precedent using `os.kill(pid, 0)`),
  `src/gigai/adapters/process.py` (`os.killpg`/SIGTERM-then-SIGKILL precedent),
  `src/gigai/setup.py` (`default_home_root`).
- `src/gigai/cli.py` (confirmed `scout_group` registration point; not touched further).

## EXECUTED (changes)

### 1. `src/gigai/scout/run_supervisor.py` (new)
`start()` / `stop()` / `status()` plus `ensure_scout_ready()`:
- `ensure_scout_ready`: calls packet B's `install_scout` (idempotent bind/approve/activate) and
  `write_starter_find_jobs_config` (never overwrites) on every `run` call.
- Reuse check: state file (`<home>/run/scout/<project_id>.json`, keyed by
  `BoundProject.project_id`, not the target path) + `os.kill(pid, 0)` liveness + a real
  `GET /api/health` — all three must pass to reuse; otherwise the state is treated as stale,
  removed, and reported (`cleaned_stale: true`).
- Start: `subprocess.Popen([sys.executable, "-m", "gigai.scout.find_jobs.present_api", "--home",
  ..., "--target", ..., "--port", ...], stdin=DEVNULL, stdout/stderr appended to
  `<home>/logs/scout-<project_id>.log`, start_new_session=True)`. Port: default 8765
  (`present_api`'s existing `API_BIND[1]`); if occupied, a clear `scout_run_port_in_use` error
  naming `--port` (I did not auto-pick a free port — the brief left this to me and an explicit
  "the port you asked for is busy, here's the flag to change it" seemed more predictable for a
  local dev tool than silently landing on a different port).
- Health wait: polls `GET /api/health` up to 15s; on failure, stops the child and raises with the
  log path + last 40 lines in the message; the CLI turns that into a non-zero exit.
- `--foreground`: runs `present_api`'s server in-process (still writes/removes the state file
  around it; Ctrl-C via `_run_forever`'s existing `KeyboardInterrupt` handling stops it).
- `stop()`: SIGTERM, poll up to 5s, SIGKILL if still alive, remove state; returns `False` (not an
  error) when nothing was running.
- `status()`: `running` / `stopped` / `crashed` (state present, pid dead) with url/pid/log/started_at.

**Bug found and fixed along the way**: `_process_is_alive` (modeled on `target_binding.py`'s
existing `os.kill(pid, 0)` pattern) can't tell a truly-dead pid from a *zombie* — a child that has
exited but was never `waitpid`-reaped. `os.kill(pid, 0)` keeps succeeding on a zombie's pid. This
only bites when the same process both started the child and is now stopping it (the case in every
test here, and technically also true for anyone scripting `run` then `stop` in one long-lived
Python process) — a normal two-separate-CLI-invocations flow doesn't hit it, because `init`
reaps orphans once the `run` process exits. Fixed with a best-effort non-blocking
`os.waitpid(pid, os.WNOHANG)` before every liveness check (`_reap_if_child`); harmless
`ChildProcessError` when we aren't the parent. Before the fix, `stop` cycled through a full 5s
SIGTERM wait for a process already dead, then sent a no-op SIGKILL, and the pid never reported as
gone — the "no orphan process left" acceptance check failed reliably until this landed.

### 2. `src/gigai/scout/scout_cli.py` — added `run` / `stop` / `status`
Thin Click wrappers around `run_supervisor`, matching `install`'s `--json`/plain-output and
`_fail`/`_emit` conventions already in this file. `run_supervisor` is imported lazily inside each
command body (it in turn lazily imports `scout_cli.write_starter_find_jobs_config`, so the two
modules don't import each other at module load time).

### 3. `src/gigai/scout/find_jobs/present_api.py` (config/resume error codes + `/api/health` only —
static serving untouched)
- New `--port` argument on `main()`/`argparse` — `present_api`'s process args didn't have one
  before this packet; `run_supervisor` needs it to bind an ephemeral test port and to honor
  `--port`. Defaults to the existing `API_BIND[1]` (8765) when omitted, so this is additive.
- New `GET /api/health` → `200 {"status": "ok"}`, checked before `/api/config` in `do_GET`'s
  routing so it never falls through to the config/run/static branches.
- New `ConfigMissingError(LookupError)` (carries the missing path). `ScoutFindJobsBackend.read_config`
  now raises this specifically (was a bare `LookupError`) when `find-jobs.json` doesn't exist.
  Both `/api/config` and `/api/run` catch it ahead of the generic `LookupError` branch and return
  `404 {"error": {"code": "config_missing", "message": "<path> does not exist yet. Run \`gigai
  scout install\` or \`gigai scout run\` ... then edit it."}}` — the exact UAT-addendum ask
  (name the file, name the fix), instead of the old blanket `"config not found"` (backend) /
  `"That run could not be found."` (UI, see below).
- `/api/config`'s success payload gained one additive field: `"resume_missing_hint"` — `null` when
  a resume is pinned, else the literal string `"gigai scout resume add <file>"`. `read_config`'s
  return shape and every other field are unchanged; existing tests that don't know about the new
  field still pass (dict equality checks in the existing suite compare against fixtures that
  don't include it — none broke).

### 4. UI (`src/gigai/scout/ui/src/**`, rebuilt `ui/dist/**`)
- `api.js`: fixed two real bugs while wiring the new message through. (a) it read
  `payload.message`, but the actual error body shape is `{"error": {"code", "message"}}` — the
  detail was never reaching `messageForStatus` at all, for *any* error code, not just this one.
  (b) `messageForStatus` always preferred the generic per-status text over the backend detail, so
  even a fixed `payload.error.message` read would still have shown "That run could not be found."
  for a 404. Fixed both: `request()` now reads `payload.error.{code,message}`; a small
  `CODES_WITH_OWN_MESSAGE` allowlist (currently just `config_missing`) lets specific backend
  codes show their own message verbatim, while unknown-run 404s etc. keep the existing generic
  text (I did not make every code override the generic text — most of those generic messages
  ["reload and try again", "the server timed out"] are intentionally softer/more actionable than
  a raw backend string would be, so only the codes that need a specific, addressed message opt in).
- `App.jsx`: threads `resume_missing_hint` through to `ConfigPanel`; the "no resume" callout under
  the Run button no longer duplicates the exact command (that lives once, in `ConfigPanel`, to
  avoid saying it twice on screen).
- `ConfigPanel.jsx`: replaced the stale, wrong, undocumented `gigai reference add --kind resume ...`
  in the "no resume saved" callout with the real command, sourced from the backend's
  `resume_missing_hint` field (falls back to the literal string if the field is ever absent, e.g.
  against an old cached response).
- Rebuilt via `yarn --frozen-lockfile install && yarn build`: new hashed bundle
  `assets/index-B1B-R8ZJ.js` (238.18 kB, gzip 73.72 kB) replaces the old
  `index-lxtSZ0ZQ.js`; `index.html` updated to reference it; confirmed `config_missing`,
  `resume_missing_hint`, and `gigai scout resume add` all appear in the built JS.

### 5. Tests
- `tests/behaviors/scout_find_jobs/test_scout_run_supervisor.py` (new, 7 cases): temp
  `GIGAI_HOME`, temp non-git target (via the same non-interactive `gigai setup` + `gigai init`
  sequence packet B's tests use), ephemeral ports (`socket.bind(("127.0.0.1", 0))` then close).
  Covers: run → status running → real `GET /` (UI html) + `GET /api/config` + `GET /api/health` →
  find-jobs.json exists → stop → status stopped, with the pid confirmed gone; a second `run`
  reuses the same pid/port; `stop` is idempotent when nothing is running; `status` on a clean
  project is `stopped`; a planted stale state file (dead pid) is cleaned and reported
  (`cleaned_stale: true`) and a fresh instance starts; a forced health-check failure (monkeypatched
  `_health_ok`) exits non-zero and leaves `status` as `stopped`, with the started child's pid
  confirmed reaped and gone (no orphan).
- `tests/behaviors/scout_find_jobs/test_present_ui.py` (config-missing cases only, +7 tests): a
  `_ConfigMissingBackend`/`_NoResumeBackend` pair drives `GET /api/health` → 200;
  `GET /api/config` with no file → `404 config_missing` naming `find-jobs.json` and
  `gigai scout install`/`gigai scout run`; `POST /api/run` with no file → same code; a config
  response with no resume carries `resume_missing_hint`; one with a resume has `null`.

## Verification run
```
uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_scout_run_supervisor.py \
  tests/behaviors/scout_find_jobs/test_present_ui.py \
  tests/behaviors/scout_find_jobs/test_present_api_static.py -q
```
→ **48 passed** (7 new supervisor + 31 present_ui [24 existing + 7 new] + 10 present_api_static,
run again after the UI rebuild to confirm the new bundle didn't regress static serving).

`make unit-tests` → **909 passed, 1298 deselected** (fast lane; my new tests use real
processes/filesystem/network so they're correctly outside `fast_unit`, same as the pre-existing
`test_present_ui.py`/`test_present_api_static.py`).

### `gigai scout --help`
```
Usage: cli scout [OPTIONS] COMMAND [ARGS]...

  Install and configure the Scout gig for this project.

Options:
  --help  Show this message and exit.

Commands:
  install  Bind, approve, and activate Scout for the bound project; safe...
  resume   Manage the resume Scout uses for find-jobs runs.
  run      Install/activate Scout if needed, then start (or reuse) its...
  status   Show whether this project's Scout instance is running,...
  stop     Stop this project's running Scout instance, if any.
```

### Real run in a temp home (default port 8765, non-git target)
```
$ gigai scout run --home $GIGAI_HOME --target $TARGET --no-browser
Scout is running at http://127.0.0.1:8765 (log: .../home/logs/scout-project_....log).

$ gigai scout status --home $GIGAI_HOME --target $TARGET
running: http://127.0.0.1:8765 (pid 34713, log: .../home/logs/scout-project_....log)

$ gigai scout stop --home $GIGAI_HOME --target $TARGET
Stopped Scout.

$ gigai scout status --home $GIGAI_HOME --target $TARGET
stopped
```
No orphan `present_api` process or listener remained after `stop` (checked with `ps`/`lsof`);
confirmed no zombie (`ps aux | grep defunct`) after the fix in item 1.

## Debt / notes for the coordinator
- `main()`'s new `--port` argument is the only change to `present_api.py`'s process-args surface;
  everything else about that file (`Backend` protocol, static serving, routing) is untouched.
- `_stop_pid`'s reap fix (`_reap_if_child`) is defensive and only actually matters for a caller
  that is the child's own parent (true in every test here, and for `--foreground` conceptually,
  since that runs in the calling process). A real two-CLI-invocation `run` then `stop` from a
  shell was also verified directly (see the transcript above) and works with or without the fix,
  since `init`/launchd reaps the orphaned child once the `run` process exits — the fix just makes
  the same code correct for both cases instead of being lucky in one of them.
- Did not change `gigai scout`'s group-level help text ("Install and configure the Scout gig for
  this project") to mention `run`/`stop`/`status`; Click's per-command help already describes each
  one and the brief didn't call out group help text as in scope. Flagging in case the coordinator
  wants it reworded once all 4 packets land.
- README "Scout: install and approve" (U7) update to the one-command flow is out of scope here
  (not an owned file) — packet B's report flagged the same gap; whichever packet documents the
  end-to-end flow should cover `gigai scout run` too now that it exists.

## READ vs EXECUTED
Everything under "READ" above was read-only. Everything under "EXECUTED" was written/changed by
me. No `git add`/`commit`/`stash`/`reset`/`clean` was run. No live Exa/ATS/provider call was made
(the supervisor tests only exercise config/UI serving — `read_config`, `resume_preview`, `/`, and
`/api/health` — never `POST /api/run` against the real backend). Every process started in testing
or manual verification was stopped; final `ps`/`lsof` checks after the last manual run found no
orphan process or bound port. The operator's real `~/.gigai` was never targeted (every invocation
used `--home`/`GIGAI_HOME` pointed at a `/tmp` directory); confirmed by inspecting
`~/.gigai/run/scout/` (does not exist) after this session's work.

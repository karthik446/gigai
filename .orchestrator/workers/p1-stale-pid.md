# P1: stale pid in run_supervisor.py (pr37-review-findings.md #9)

## Finding
`run_supervisor.py` `stop`/`status` trusted the pid in the state file after
only `os.kill(pid, 0)` (liveness). If the Scout server died and the OS
reused the pid for an unrelated process, `gigai scout stop` would signal
that unrelated process, and `status` would report `"running"`.

## Repro (EXECUTED)
Added `test_stop_and_status_do_not_trust_a_pid_reused_by_an_unrelated_process`
to `tests/behaviors/scout_find_jobs/test_scout_run_supervisor.py`: spawns a
real `sleep 300` child, plants a state file pointing at its pid, then calls
`status`/`stop` through the CLI. Before the fix this failed:
`status` reported `"running"` for the unrelated process.

## Fix (src/gigai/scout/run_supervisor.py)
Added `_pid_is_our_server(pid)`: liveness (`_process_is_alive`) **and**
identity — the pid's command line must match our server. Identity is
determined portably, no `psutil` dependency:
- Linux: reads `/proc/<pid>/cmdline`.
- macOS/BSD (no `/proc`): shells out to `ps -o command= -p <pid>`
  (`shell=False`, fixed argv — required by the repo-wide
  `test_product_subprocesses_are_literal_argv_with_shell_disabled` check).
- Two command-line markers are accepted: the background child's module path
  (`gigai.scout.find_jobs.present_api`) and, for `--foreground` mode where
  the pid *is* the CLI process itself, the tokens `scout`, `run`,
  `--foreground` all present.
- If identity can't be determined at all (pid gone, `ps` unavailable,
  permission denied), it conservatively returns `False` — never treats
  "unknown" as "confirmed ours."

Wired into all three read sites:
- `_existing_live_state` (used by `start`'s reuse check) — a live-but-foreign
  pid is now treated as stale, cleaned up, and a fresh instance starts.
- `stop` — only signals the pid when `_pid_is_our_server` is true; otherwise
  still removes the stale state file and reports `stopped: False`, and never
  calls `os.kill` on it.
- `status` — a live-but-foreign pid is reported `"stopped"` (state file
  cleaned up), not `"running"`.

Did **not** touch `present_api.py` (owned by another worker per the task
brief) — the command-line identity check alone was sufficient, so no health
endpoint change or cross-worker ask was needed.

## Tests (EXECUTED)
- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_scout_run_supervisor.py -q`
  → 8 passed (new test + all 7 pre-existing: real run/stop, second-run
  reuse, idempotent stop, clean status, dead-pid stale cleanup, health-check
  failure + no-orphan-process).
- `make unit-tests` → 1023 passed (repo-wide `fast_unit` lane; also caught
  and required fixing a missing `shell=False` on the new `ps` subprocess
  call to satisfy `test_product_subprocesses_are_literal_argv_with_shell_disabled`).
- Verified no orphan `sleep 300` process survives the test run (`ps aux`
  checked after).

## Acceptance checklist
- [x] State file pointing at a live unrelated process (`sleep` child) → `stop`
      does not kill it, cleans the state; `status` says not running.
- [x] Real server → `stop`/`status` work as before (existing tests still pass).
- [x] Dead pid → stale cleanup (existing test still passes).
- [x] Every process spawned by the new test is killed (`finally` block
      terminates/kills the `sleep` child; verified no leftovers).

## Left for the coordinator
None outstanding for this ticket. Out of scope, noted only: identity here is
command-line-based, not cryptographic (no run-token in the health response);
sufficient for the stated threat (accidental pid reuse), not a security
boundary against a maliciously spoofed command line on the same machine.

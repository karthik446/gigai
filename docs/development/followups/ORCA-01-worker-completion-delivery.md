# ORCA-01 — Reliable worker completion delivery

**Status:** Recorded 2026-09-08; deferred. No Orca fix implemented.
**Owner/system:** Orca orchestration, not GigAI runtime or Scout.
**Priority:** Recurring agent-development reliability issue; not a new Scout release gate.

## Observed failure

The SCOUT-02 Terra worker finished its implementation and wrote
[its handoff](../evidence/v0.1.7/Scout/SCOUT-02-implementation.md), but both its
`worker_done` and escalation RPCs failed to connect. The coordinator could
still reach the same running Orca app (1.4.193), while the worker CLI reported
"Orca is not running." The dispatch remained outstanding and process liveness
was mistaken for ongoing work. The operator noticed the terminal's final answer.

Trace identity: task `task_7a0c7f937d13`, dispatch `ctx_7d65aec2788b`,
orchestration Run `run_43c92f4fc427`. Inspect through Orca's dispatch transcript;
do not copy launch/capability tokens into reports.

Installed-code inspection found that `out/cli/runtime/transport.js:47-51`
discards the socket error and emits a generic `runtime_unavailable` restart
message. `out/cli/format.js:71-76` then appends the app-not-running diagnosis.
A worker-environment/socket permission restriction is plausible, but the original
OS error was discarded, so the exact transport cause remains unconfirmed.

## Proposed correction and acceptance

1. Preflight runtime connectivity from the actual worker execution environment,
   not just the coordinator. Preserve safe OS error codes and distinguish
   permission denial, stale endpoint, refused connection and timeout.
2. Persist a dispatch-bound completion receipt before delivery; acknowledge it
   and retry idempotently through an Orca-managed host delivery path. Keep
   completion delivery independent of agent sandbox connectivity where possible.
3. Detect a worker returning to its prompt without an acknowledged completion.
   Surface `completion_unconfirmed` and prompt reconciliation; never infer
   successful implementation or review merely from idle/process liveness.
4. Test lost connection, permission denial, duplicate delivery, app restart and
   worker exit after report publication. Preserve exact dispatch identity and
   distinguish handoff receipt from verification/acceptance.

## Interim operating rule

Let implementation agents finish their own work and tests without mid-flight
code review. Wait on lifecycle messages, but use bounded terminal-status/final
handoff checks when delivery is missing. A confirmed final handoff can trigger
explicit coordinator recovery with its provenance recorded; do not impersonate
`worker_done`, blindly restart the app, or disable sandboxing wholesale.

Resume Scout work; this note is not authorization to modify Orca or broaden
GigAI v0.1.7/v0.1.8 product scope.

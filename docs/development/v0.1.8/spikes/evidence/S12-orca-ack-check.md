# S12 task 3: Three-worker Orca acknowledgment check

**Date:** 2026-09-22 (run at 20:43–20:45Z)
**Authorization:** The operator gave explicit go-ahead on 2026-09-22 after
Luna's work packets finished and the timing window was clear.
**Scope:** An Orca plumbing check only. The workers acknowledged and
completed; they read, searched and edited no files. No GigAI code, Scout
analysis or graph design is exercised. **A successful check doesn't
establish graph correctness, useful Scout work or zero overhead** (S12
acceptance criteria).

## Setup

| Item | Value |
| --- | --- |
| Orca | 1.4.206, local runtime `ready` / `connected` |
| Run | `run_ef1121d3c864`. The coordinator is this Claude Code session's terminal. |
| Workers | 3 × `--agent claude --model claude-haiku-4-5-20251001 --worktree current`, started by `worker-start --spec` |
| Worker instruction | Send exactly one `heartbeat` with subject `ACK w<n>`, then exactly one `worker_done` (`--outcome succeeded`, subject `DONE w<n>`), then idle. Don't read or edit files or run other commands. |
| Acknowledgment vs completion | The **acknowledgment** is the `heartbeat` `ACK w<n>`. **Completion** is the `worker_done` `DONE w<n>`. They are two separate lifecycle messages. |
| Coordinator waiting | `orchestration check --wait --types worker_done,escalation,question --timeout-ms 900000`, run as a **background shell command**. The Claude Code harness re-invokes the coordinator when that command exits, so there are no LLM turns while waiting. |

| Worker | Task | Dispatch |
| --- | --- | --- |
| w1 | `task_05ca78084e80` | `ctx_25e19e34827a` |
| w2 | `task_555f20cd0e0d` | `ctx_67db84a1ff18` |
| w3 | `task_e17e38138a84` | `ctx_87b49c93c680` |

## Timeline

Coordinator events have millisecond timestamps from the coordinator host.
Message times are Orca's `created_at` values, which are only precise to the
second.

| Time (UTC) | Source | Event |
| --- | --- | --- |
| 20:43:41 | Orca | Run created |
| 20:43:52.465 → 20:43:56.197 | coordinator | `worker-start` w1 (3.7 s, stage `input_accepted`) |
| 20:43:56.347 → 20:43:59.584 | coordinator | `worker-start` w2 (3.2 s) |
| 20:43:59.730 → 20:44:02.986 | coordinator | `worker-start` w3 (3.3 s) |
| 20:44:05 | w1 | **ACK w1** (heartbeat) |
| 20:44:07 | w2 | **ACK w2** |
| 20:44:09 | w1 | **DONE w1** (`worker_done`, succeeded) |
| 20:44:10 | w2 | **DONE w2** |
| 20:44:10 | w3 | **ACK w3** |
| 20:44:10.099 → 20:44:10.282 | coordinator | Wait #1 returned after 183 ms with delivery `delivery_210dc4bb705b` = [DONE w1, DONE w2] |
| 20:44:12 | w3 | **DONE w3** |
| 20:44:29.400 / .630 | coordinator | `worker-release` w1 and w2, both `ok` |
| 20:44:35.453 → 20:44:35.616 | coordinator | Ack of delivery 1, then wait #2 returned after 163 ms with delivery `delivery_2530efee823e` = [DONE w3], `replayed: false` |
| 20:45:02.674 / .837 | coordinator | `worker-release` w3; non-waiting ack of delivery 2 (remaining count 0); `worker-list --terminal-state reclaimable` returned 0 rows |

From a worker being ready to its **ACK** took about 7–9 s. From ready to
**DONE** took about 10–13 s. These times are dominated by agent startup and
model latency and were not broken down further.

## Results against S12's acceptance criteria

| Criterion | Result | How established |
| --- | --- | --- |
| Three attributable acknowledgments with timestamps | **Yes.** Three `heartbeat` messages, `ACK w1/w2/w3`, each carrying its own dispatch ID and phase `investigating`. | executed; `check --all` (read-only) |
| Three attributable completion receipts with timestamps | **Yes.** Three `worker_done` messages, `DONE w1/w2/w3`, each with its dispatch ID and `outcome: succeeded`. | executed |
| Acknowledgment distinguished from completion | **Yes.** They are separate message types, and each ACK is timestamped before its own DONE. The acknowledgments **were not in the coordinator's consuming deliveries**. Only `worker_done` rows were delivered. The heartbeats were visible only through read-only `check --all`. | executed. Whether Orca excludes heartbeats from deliveries by design wasn't checked in Orca's docs or code. |
| Missing notifications | **None.** 3 of 3 ACKs and 3 of 3 DONEs. | executed |
| Duplicate notifications | **None.** Each `worker_done` appeared in exactly one consuming delivery (`replayed: false` on the second). The harness sent exactly one background-completion notification per wait. | executed |
| Coordinator resumed without repeated LLM polling | **Yes, but the blocking-resume path wasn't exercised.** The coordinator took two harness-triggered turns, one per wait, plus one voluntary read-only `--all` inspection. There were no polling loops. However, **both waits returned immediately (183 ms and 163 ms) because the `worker_done` messages were already queued.** The workers finished within about 10 s of starting, so the coordinator never actually blocked on an in-flight worker and woke on arrival. | executed |
| Worker compliance with the minimal task | **Yes.** Each transcript shows exactly two `orca orchestration send` calls and no file tools. | executed; `worker-read --source transcript` |
| Coordinator activity and token usage | **Activity:** 3 `worker-start`, 2 blocking waits, 1 read-only inspection, 3 releases, 2 acks, 1 reclaimable check and 3 transcript reads. **Tokens: not available.** `worker-list` and the worker transcripts expose no token or usage fields, and this session's coordinator can't measure its own token cost. | executed (activity); token usage is recorded as unavailable |

## Observations beyond the criteria

- **Workers stay `live` after release.** After `worker-release` settled
  all three and the reclaimable list was empty, `worker-list` still
  reported each worker's liveness as `live`, meaning an idle agent terminal.
  The guide describes release as ownership cleanup, not process stop, so
  this may be expected. It is recorded as observed, not as a defect.
- **The Orca-unavailable failure mode wasn't tested.** Earlier completion
  failures (ORCA-01, and Luna A's failed notification in this session, which
  the operator reported) involved workers that couldn't reach Orca. The
  runtime was reachable throughout this check, so it says **nothing** about
  that failure mode.
- **A sanitized receipt summary is in the repo:**
  [S12-orca-ack-check-receipts.json](S12-orca-ack-check-receipts.json).
  It is built from an allow-list of fields: message ID, type, subject,
  body, `created_at`, task/dispatch ID, outcome and phase, plus the
  deliveries, the coordinator timeline and the start receipts. It was
  scanned for capability and terminal-handle patterns. Worker preambles,
  transcripts and terminal handles are excluded, because the raw
  transcripts contain capability values. The raw JSON stays in the session
  scratchpad.

## What this does not establish

- It doesn't establish that the coordinator wakes correctly when it is
  blocked *before* completion arrives. That was tested afterwards, in the
  separately authorized
  [blocking-resume check](S12-orca-blocking-resume-check.md).
- It doesn't establish behavior when Orca is unreachable from the worker
  (ORCA-01).
- It says nothing about token cost.
- It doesn't establish anything about Scout, GigAI graph execution or
  whether the S12 graph design is correct.

## Related

- [S12 ticket](../S12-gig-graph-traversal-and-auditable-execution.md)
- [Task 4: proposed system-behavior changes](S12-proposed-system-behavior-changes.md),
  whose section C is filled in from this check
- [ORCA-01: worker completion delivery](../../../followups/ORCA-01-worker-completion-delivery.md)

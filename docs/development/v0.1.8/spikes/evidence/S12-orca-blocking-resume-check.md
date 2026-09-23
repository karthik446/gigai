# S12 N3: Blocking-resume check

**Date:** 2026-09-22 (run at 20:49–20:51Z)
**Authorization:** The operator gave go-ahead on 2026-09-22, scoped as "one
worker waits briefly before completing, while the coordinator is already
blocked in one `check --wait`." The run had no file changes and no polling
loop.
**Purpose:** The [three-worker acknowledgment check](S12-orca-ack-check.md)
never tested a wait that was actually blocked, because both of its waits
returned from mail that was already queued. This check tests exactly that
path and nothing else.
**Sanitized receipts:** [S12-orca-blocking-resume-receipts.json](S12-orca-blocking-resume-receipts.json),
built the same way as the ack-check receipts (an allow-list, scanned for
capability values and terminal handles).

## Setup

| Item | Value |
| --- | --- |
| Run | `run_c7a2891bce10` (a new Run, so the inbox started empty) |
| Worker | 1 × `--agent claude --model claude-haiku-4-5-20251001 --worktree current`: task `task_52ad01725b28`, dispatch `ctx_d2383ea30513` |
| Worker instruction | `heartbeat` `ACK n3`, then `sleep 60`, then `worker_done` `DONE n3` (`succeeded`). No files, no other commands. |
| Order | The coordinator's background `check --wait --types worker_done,escalation,question` started **before** `worker-start`. |

## Timeline

Coordinator times have millisecond precision. Orca's `created_at` and
`delivered_at` are precise only to the second.

| Time (UTC) | Source | Event |
| --- | --- | --- |
| 20:49:09.474–.637 | coordinator | Run created |
| **20:49:22.126** | coordinator | **Wait start.** The wait blocks with an empty inbox and no worker yet. |
| 20:49:24.194 → 20:49:27.524 | coordinator | `worker-start` (3.3 s, `input_accepted`) |
| 20:49:30 (delivered 20:49:31) | worker | `ACK n3` heartbeat |
| **20:49:39.532** | coordinator | **Unplanned resume.** Orca injected "You have 1 orchestration message" into the coordinator terminal, which started an LLM turn. The trigger was the ACK heartbeat, even though the wait's `--types` excluded heartbeats. The background wait kept blocking. |
| 20:49:48.663 | coordinator | One read-only `check --all` to identify the trigger. Nothing was consumed. The turn then ended. |
| — | coordinator wait | 5 keepalives, the last at `elapsedMs` 75006 |
| **20:50:38** | worker | **Completion:** `DONE n3` `worker_done`, `succeeded`, 68 s after the ACK |
| **20:50:38.532** | coordinator | **Wait returned** with delivery `delivery_ab48c2d04db9` = [DONE n3], `replayed: false`, `timedOut: false` |
| **20:50:46.752** | coordinator | **Coordinator resume** through the harness background-task notification |
| 20:51:00.211 / .371 | coordinator | `worker-release`, then ack of the delivery (remaining 0). `worker-list --terminal-state reclaimable` returned 0. |

**Intervals:**

- The wait was blocked for **76.4 s**.
- From completion `created_at` to the wait returning: **under 1 s.** Both
  fall within 20:50:38; `created_at` is only second-precise.
- From the wait returning to the coordinator's resume: **8.2 s.** That is
  harness notification plus turn startup.
- From completion to coordinator resume: **about 8–9 s.**

## Results

| Question | Result | How established |
| --- | --- | --- |
| Does a coordinator that is already blocked in `check --wait` wake when a completion arrives? | **Yes.** The wait returned within the second the `worker_done` was created, and the coordinator resumed 8.2 s later. | executed |
| Is the completion delivered exactly once? | **Yes.** One delivery with one message, `replayed: false`, acked with 0 remaining. | executed |
| Did the worker stay in scope? | **Yes.** Its transcript shows two `orca orchestration send` calls and one `sleep 60`, which its Claude Code ran as a background command. There were no file tools; the only "Read" match is harness boilerplate about background output. | executed; `worker-read` |
| Was the wait free of LLM polling? | **Only partly.** The wait itself used no LLM turns while blocked. But Orca's in-terminal nudge for the **heartbeat** started one unplanned coordinator turn, even though heartbeats weren't in the wait's `--types`. No nudge-driven turn happened for the `worker_done`; that wake came from the wait's own return. | executed |

## Findings

1. **Blocking resume works on the reachable-runtime path.** The completion
   was delivered within the second, and the coordinator resumed in about
   8 s. This is the evidence N2 lacked, for this one path.
2. **Non-actionable messages can cost coordinator turns.** Orca's terminal
   nudge fired for a heartbeat. That's a message the wait was filtering out
   and the coordinator doesn't need to act on. With N workers each sending
   ACKs or heartbeats, a coordinator running in an Orca terminal could take
   up to N extra LLM turns. This check shows it for one message only; the
   rate isn't measured. Whether the nudge can be filtered by type wasn't
   checked in Orca's options.
3. **Heartbeats are delivered through a different channel.** The ACK
   heartbeat has `delivered_at` set (20:49:31) but never appeared in a
   consuming delivery. The `worker_done` has `delivered_at: null` but *was*
   in the consuming delivery. The same pattern appeared in the ack check.
   That suggests heartbeats go to the terminal-nudge channel and lifecycle
   messages go to the inbox delivery. This comes from observing two runs,
   not from Orca's documentation.

## What this does not establish

- It doesn't cover recovery when Orca is unreachable from the worker
  (ORCA-01, and Luna A's failed notification). The runtime was reachable
  throughout.
- It doesn't establish how many nudge-driven turns a larger wave would
  cause, or what they cost in tokens. Token usage still isn't exposed.
- It uses one worker, one wait and one short sleep. It doesn't cover long
  tasks, many concurrent completions or wait timeouts.
- It doesn't establish anything about Scout or GigAI graph execution.

## Related

- [Three-worker acknowledgment check](S12-orca-ack-check.md)
- [Task 4: proposed system-behavior changes](S12-proposed-system-behavior-changes.md), section C
- [ORCA-01](../../../followups/ORCA-01-worker-completion-delivery.md)

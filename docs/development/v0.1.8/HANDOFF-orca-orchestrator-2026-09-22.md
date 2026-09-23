# Orca orchestration handoff

**For:** an agent coordinating work in this GigAI worktree.  
**Purpose:** run bounded worker packets, receive their results, and leave an auditable handoff without spending coordinator turns watching terminals.  
**Last checked:** 2026-09-22, Orca CLI 1.4.207. Re-read the installed guide; command behavior and runtime state can change.

## Start here

1. Read the session's `AGENTS.md` instructions and `/Users/kar/.codex/RTK.md`; check for additional instructions in the checkout. On this Mac, shell commands start with `rtk`; use `rtk proxy orca ...` when RTK's command rewriting would obscure an Orca receipt.
2. Resolve the Orca executable for the environment, then run `orca skills get orchestration`. For coordination, read its version-matched `references/coordinator-loop.md`; for delivery and recovery, read `references/messaging-and-gates.md` and `references/recovery-and-cleanup.md` only when needed. The installed guide is authoritative over examples here.
3. Inspect `orca status --json`, the current Run, worker list, branch, worktree and dirty files. This v0.1.8 tree has substantial uncommitted work; do not broad-stage, reset, clean or overwrite it.
4. Give each worker a substantial task with target, owned files, change, constraints and observable acceptance. Launch independent packets together. If B reads A's result, make B depend on A and review A's receipt before accepting B.
5. In this project, use Luna/max for implementation and Terra/medium for independent verification **after** the corresponding Luna packet finishes. The coordinator owns integration and the final evidence boundary. A worker report alone is not a test or release verdict.

This document describes **development-worker orchestration**. It does not mean Orca is wired into Scout's product graph runner. The [S12 mapping](spikes/evidence/S12-scout-graph-mapping.md) keeps those concerns separate.

## Working model and minimal loop

- A **Run** is the durable namespace and coordinator inbox. A **Task** names work. A **Dispatch** is one authoritative attempt to perform a Task. A terminal title, visible pane, copied task ID or idle prompt is not lifecycle authority.
- The worker's injected preamble supplies its exact executable, handle, dispatch capability and completion command. The worker must copy those arguments exactly. Never paste capability values into Markdown, logs, prompts or a new agent session.
- This Mac uses the Orca executable `orca`. Substitute the executable selected by the installed skill when working elsewhere. These are sketches; obtain the exact flags from the installed guide before execution:

```sh
rtk proxy orca status --json
rtk proxy orca skills get orchestration
rtk proxy orca orchestration run-create --objective 'bounded objective' --json
run_id='paste runId from the run-create receipt'
rtk proxy orca orchestration worker-start --run "$run_id" --worktree current --agent codex --model gpt-5.6-luna --effort max --task-title 'owned packet' --spec 'target; owned files; acceptance; exclusions' --json
rtk proxy orca orchestration check --run "$run_id" --json
```

Choose a model and effort only when the operator names them. On a fresh worker, compare `launch.requested` with `launch.effective`. `--model` and `--effort` cannot be combined with `--terminal` reuse. Use `worker-show` to recover a proven existing terminal handle; a handle from an earlier runtime epoch can be stale.

For a real DAG, create dependent Tasks with `task-create --deps <json_array>` and start ready Tasks. Do not use dependencies merely to serialize unrelated work. Keep file ownership disjoint; a test owner should have sole control of aggregate timing while another worker edits the runner, then freeze runner files for measurement.

The coordinator should launch the wave, report a checkpoint and yield. A single background `check --wait --types worker_done,escalation,question` can resume through a host notification. Keep one waiter per wave, not repeated LLM polling. A wait timeout or empty result is a checkpoint, not proof that workers finished. Some clients require their background command to return before the agent turn resumes; verify that behavior in the current host.

## Inbox, completion and cleanup

`check` returns the oldest FIFO **Delivery**. It replays the same batch until `check --ack <delivery_id>`. Read **every** message first: answer a `question` using `reply --id <message_id>`, check a `worker_done` against the active Dispatch and its report, and decide the terminal's next owner. Only then acknowledge the delivery. `--peek` and `--all` inspect without consuming or advancing the inbox. A `send` receipt proves enqueue, not that its wake was delivered or the recipient read it.

A heartbeat or explicit worker ACK means liveness or acknowledgment, **not completion**. `worker_done` names the Task, Dispatch and outcome and settles that attempt. If the worker reports success but its substantive evidence is narrower, report the narrower result. In particular, source test timing does not establish installed-wheel, provider, UAT, publication or full `make test` acceptance.

After an accepted `worker_done`, choose one: reuse that exact agent for immediate follow-up, retain it when the operator wants it kept, or `worker-release --dispatch <id>`. If a start failed after creating a terminal, follow its exact recovery receipt and release that terminal. An idle agent or heartbeat alone does **not** permit release. Check `worker-list --run <run_id> --terminal-state reclaimable --json` before declaring cleanup complete.

Use `worker-list --run <run_id> --json` for fleet liveness and `worker-show --dispatch <id> --json` for details. A live PTY does not prove an active agent, and a released worker can still appear `live` as an idle terminal. Preserve `live`, `unverifiable` and `exited` as distinct findings. If a command response is lost, inspect `request-show --request <id>` and the affected Task/Dispatch before retrying; an absent request receipt is not proof that nothing happened.

## Issues observed in these sessions

**Work finished, but completion could not reach Orca.** Luna A wrote a complete [S11 report](evidence/S11-full-suite-reorganization.md) with a 467.91-second source run. Its `worker_done` failed with “Orca is not running.” The final transcript confirmed that its turn ended, but Orca had no accepted completion. [ORCA-01](../followups/ORCA-01-worker-completion-delivery.md) records an earlier instance; its transport cause is still unconfirmed. Keep the report as substantive evidence, inspect the transcript and Dispatch, and follow the recovery guide. For Luna A, the coordinator explicitly abandoned the stale Dispatch without stopping a process and retained the terminal. Do not impersonate `worker_done` or call that Task Orca-completed.

**Messages were queued without a timely coordinator wake.** During this Run, the operator repeatedly prompted a manual `orchestration check` to retrieve queued messages. Enqueue, nudge and coordinator resume are separate events. Check the durable inbox and active Dispatch before assuming a worker is still running. Do not restart a worker because a notification was not seen.

**ACK and DONE used different channels.** In the [three-worker S12 check](spikes/evidence/S12-orca-ack-check.md), three heartbeat ACKs preceded three distinct `worker_done` receipts. ACKs appeared through read-only `check --all`; DONEs arrived in two consuming deliveries. Both waits read already-queued completions in under 200 ms. This proved accounting, not waking from a blocked wait.

**A filtered wait still cost a coordinator turn.** The [N3 check](spikes/evidence/S12-orca-blocking-resume-check.md) blocked for 76.406 seconds; DONE returned from the wait within the same second, and the coordinator resumed 8.22 seconds later. An ACK heartbeat caused an injected coordinator turn even though the wait's `--types` excluded heartbeats. Treat wait filtering and Orca's terminal nudge as separate mechanisms. The worker-unreachable path was not tested by N3, and token cost was unavailable.

**Terminal reuse can fail.** `worker-start --terminal` returned `agent_unconfigured` for one old terminal. After an Orca runtime epoch change, another returned `terminal_handle_stale`. Inspect the terminal, Dispatch and request state; start a fresh worker if the old handle is no longer a recognized agent. Never silently treat a stale handle as the current attempt.

**Fresh Codex startup can stop before dispatch.** On 2026-09-22, two fresh Luna/max starts failed at `agent_readiness` with `agent-update-prompt`. Orca created terminals, but neither Task turn began. The coordinator released both using their recovery receipts. Resolve the CLI update prompt before depending on that route. This handoff was written directly after those failures.

**Shell quoting can break lifecycle commands.** A comma-separated `--files-modified` value was split into separate shell arguments in an earlier worker turn. Quote the entire CSV value, copy lifecycle arguments from the exact worker preamble, and inspect the structured receipt. For raw command behavior here, use `rtk proxy`. Never print dispatch capabilities while debugging.

The [S12 three-worker receipts](spikes/evidence/S12-orca-ack-check-receipts.json) and [N3 receipts](spikes/evidence/S12-orca-blocking-resume-receipts.json) are sanitized summaries; raw worker transcripts contain capability values and should stay out of the repository. S12 used Haiku for a minimal plumbing check. It does not establish Scout graph execution or fix ORCA-01.

## Next-session checklist

- Recheck Orca runtime, guide version, Run ID, active Dispatches and dirty files. Read only the evidence needed for the current packet.
- Make the next wave reviewable: clear ownership, actual prerequisites, exact model/effort, acceptance evidence and an expected checkpoint. Send Terra only after Luna has finished the relevant files.
- Process the durable inbox before acknowledging it; reconcile worker reports against files and tests. If lifecycle delivery fails, preserve the substantive report and record that Orca settlement is missing.
- Yield while workers run. Return on a real completion, question, escalation or user signal. A source test receipt, a task receipt and a release decision are three different facts.

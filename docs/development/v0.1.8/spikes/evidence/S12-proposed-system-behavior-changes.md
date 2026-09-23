# S12 task 4: Proposed system-behavior changes, with evidence

**Date:** 2026-09-22
**Scope:** Documentation only. This is S12's fourth task: record concrete
proposed system-behavior changes and attach evidence for each. As S12
requires, it leaves open where each change would be implemented (GigAI
runner, a Scout-side adapter, Orca, or a small integration script).
**Nothing here is authorized for implementation.**

**Section C was completed after task 3 ran (2026-09-22)**, once the
operator cleared the timing window. Sections A and B were written first and
don't depend on task 3.

**Source state:** read at `b01675d` plus this worktree's uncommitted
changes. `src/gigai/` is unmodified.

## Graph and task summary

This part of S12's acceptance criteria asks for a short graph and task
summary with explicit node inputs, outputs and finish conditions. Below is
the **illustrative** multi-node `find-jobs` from the
[decision-edge design](S12-decision-edge-design.md). It is a proposal: node
boundaries, contracts and executors are still open (S12 conversation
note 4).

Today, `find-jobs` compiles to one node with no edges
([task 1 mapping](S12-scout-graph-mapping.md)). Its declared inputs and
outputs come from
[`scout_template.py:150-155`](../../../../../src/gigai/scout_template.py).

| Node | Inputs | Outputs | Finish condition (proposed) | Outcomes → next |
| --- | --- | --- | --- | --- |
| `acquire` | `preferences` (required); `postings` (optional, user-provided) | Saved posting snapshots, visible before any analysis | Snapshot evidence is recorded (a per-node `required_evidence`, V1) | `POSTINGS_FOUND` → `extract-requirements`; `NO_POSTINGS` → end, reporting an empty acquisition |
| `extract-requirements` | Snapshots from `acquire` | Per-posting requirements | Extraction evidence is recorded | `COMPLETE` → `assess` |
| `assess` | Requirements; `candidate_evidence` and `research_revisions` (optional) | Discovery packet: `outcome`, `shortlist`, `exclusions`, `questions` (existing packet fields, `discovery.py:434`) | Packet validates (existing `scout-job-discovery:2` validator) and the label is declared | `MATCHES` / `PARTIAL` / `NO_MATCH` / `NEEDS_OPERATOR_INPUT` → a present node |
| `present-*` | The packet | Operator-readable result: shortlist, partial matches with questions, exclusions, or the open questions | Report artifact is recorded | Terminal |

Every node is `automatic`. None needs operator gating (see D6).

## A. Decision routing (from task 2)

Full evidence is in the [decision-edge design](S12-decision-edge-design.md)
(findings F1–F10). It is summarized here so this register is complete. These
items are **not all required**: the "Kind" column says which are
alternatives, which depend on graph shape, and which are optional.

| ID | Proposed change | Kind | Evidence |
| --- | --- | --- | --- |
| D1 | A goal on the scheduler path returns an outcome label, producer identity and evidence. The runner rejects labels not in `goal.outcomes`. | Producer, option A | F5, F6: only `COMPLETE`/`FAILED` are produced (`run.py:3345-3375`); placeholder evidence (`run.py:3546-3561`); actor hard-coded (`run.py:3585`) |
| D3 | Bridge the discovery packet's `outcome` into a graph outcome. It must carry the same guarantees as D1: the bridged label is checked against the target goal's declared `outcomes` and undeclared labels are rejected, never coerced (for example, lower-case `matches` must map explicitly, not implicitly); producer identity (external agent, wrapper or grader) and the packet's evidence refs are preserved into the goal record, not replaced by a generic actor. | Producer, option B. Either D1 or D3 is enough to *produce* a label, **but only with its validation guarantees**. A bridge without them doesn't qualify. | F8: the packet decision exists (`discovery.py:434-482`); external recording never consults edges |
| D2 | Compiled graphs declare labels beyond `COMPLETE`. | Needed for any typed branching | F2, F7: `validators.py:667-684`; `scout_materialization.py:276` |
| D4 | Branch-not-taken state, separate from `blocked`. | Only with exclusive branches | F9: the skipped branch is recorded `blocked`, so the run finishes `blocked` (`run.py:3288-3306`, `3491-3496`) |
| D5 | OR-joins, **or** forbid merges after an exclusive branch. | Either/or; only with exclusive branches | F10: `_ready_goals` requires all incoming edges (`run.py:3480-3485`) |
| D6 | Operator-gated goals. | Optional: only for pause-and-resume in the same run | `_validate_scheduler_policy` (`run.py:3438`) |

The minimum for the graph above is one producer (D1, or D3 with D1's validation and identity guarantees) plus D2, and
D4 with one side of D5 if exclusive branches are kept. This is inferred
from reading the code, not tested.

## B. Auditability and operator visibility

These address S12's problem statement ("cannot quickly see what tasks are
running, what finished, why execution moved forward, or which evidence
supports a result"). They don't depend on task 3.

| ID | Proposed change | Kind | Evidence | How established |
| --- | --- | --- | --- | --- |
| V1 | Give each node its own `verification.required_evidence` entry (for example `postings-discovered`, `requirements-extracted`, `assessment-complete`) instead of one blanket `find-jobs-completion`. | Needed for per-node audit | Scout's compiler attaches one completion kind per selector ([`scout_materialization.py:275`](../../../../../src/gigai/scout_materialization.py)). The mechanism already exists per goal in `goal-graph.schema.json`. | read |
| V2 | For multi-node graphs, the Scout report's run status must come from run-level terminal semantics, not "any goal complete". | Needed once there is more than one node; harmless today | `_run_rows` ([`scout_report_readers.py:329-337`](../../../../../src/gigai/scout_report_readers.py)) reports a local run as `succeeded` if **any** goal is `complete`, and checks this before `failed`. So a 2-goal run with one complete and one failed goal would show as `succeeded`. It also ignores the run's own `status` whenever goals are present. With today's single-goal graphs, "any complete" and "all complete" are the same thing. | read (not executed) |
| V3 | Show the operator the per-goal state that already exists: `goal_sets` (pending/ready/active/complete/failed/blocked/gated/cancelled), `critical_path`, and each goal's `outcome` and `evidence`. Show it in terms of the graph, not raw dispatch output. | Reuse, not new data | `_refresh_details` writes these on every goal transition ([`run.py:3589-3615`](../../../../../src/gigai/run.py)). `goal_completed`/`goal_failed` journal transitions carry the outcome and evidence ([`run.py:3564-3586`](../../../../../src/gigai/run.py)). No CLI command was found that shows `run-details.json` for a scheduler run as a graph. The existing `status`/`show`/`inspect` commands cover reports, acquisition, external runs, run plans and comparisons (`cli.py`, `scout_report_cli.py:48`, `scout_acquisition_cli.py:91`, `external_cli.py:192`). | read (a grep for `status`/`show`/`inspect` commands; a surface elsewhere could have been missed) |
| V4 | Once D4 exists, show a skipped branch as "not taken because `<label>`" rather than "blocked". | Depends on D4 | F9 | read |

V3 answers the conversation note's concern that "long orchestration
commands and noisy messages obscure the work" for **graph runs**. It
doesn't address Orca worker dispatch, which is section C.

## C. Notifications, completion delivery and coordinator resume (from task 3)

Evidence:

- The [three-worker acknowledgment check](S12-orca-ack-check.md)
  (`run_ef1121d3c864`).
- The follow-up [blocking-resume check](S12-orca-blocking-resume-check.md)
  (`run_c7a2891bce10`).

Both ran on 2026-09-22 with the Orca runtime reachable throughout. Both
have sanitized receipt summaries.

**What the check showed:**

- All three workers delivered an attributable acknowledgment (a `heartbeat`,
  `ACK w<n>`) and a separate completion receipt (`worker_done`,
  `DONE w<n>`), each timestamped.
- No notifications were missing and none were duplicated.
- The coordinator used no polling loops: two harness-triggered turns, one
  per blocking wait.

**What it didn't show:**

- **Blocking resume was covered later, by N3.** In the ack check, the
  workers finished within about 10 s, so both waits returned from
  already-queued mail in under 200 ms. The N3 check then showed a wait that
  was actually blocked. It stayed blocked for 76.4 s and returned within
  the second the `worker_done` was created. The coordinator resumed 8.2 s
  later. That was one worker, on the reachable-runtime path.
- **The Orca-unreachable failure mode** behind ORCA-01 and Luna A's failed
  notification wasn't tested.
- **Token usage** isn't exposed.

| ID | Proposed change | Kind | Evidence |
| --- | --- | --- | --- |
| N1 | Show the operator acknowledgments separately from completions. Today only `worker_done` reaches the coordinator's consuming delivery. Heartbeat ACKs were visible only through read-only `check --all`. | Visibility; applies wherever the operator's view is built | Ack check, "Acknowledgment distinguished from completion" (executed). Whether Orca excludes heartbeats from deliveries by design wasn't verified. |
| N2 | Use one background blocking wait per wave as the coordinator's default waiting mechanism. | **Proposal.** It is now supported on one path: one worker, reachable runtime. Many workers, long tasks, timeouts and an unreachable Orca are untested. See also N6. | Ack check (queued-mail path); N3 check (blocked path) |
| N3 | Run a check where the worker is still running when the wait starts. | **Done 2026-09-22.** The blocked wait resumed correctly. | [Blocking-resume check](S12-orca-blocking-resume-check.md) |
| N4 | Test and fix the Orca-unreachable path under ORCA-01. This check doesn't bear on it. | Existing follow-up | [ORCA-01](../../../followups/ORCA-01-worker-completion-delivery.md), plus the Luna A failure the operator reported on 2026-09-22 |
| N6 | Stop non-actionable messages, such as heartbeats and ACKs, from waking the coordinator's LLM. Options: make Orca's in-terminal nudge honor a type filter, or run the coordinator where the nudge doesn't start turns. | Coordinator cost; location open (Orca or coordinator setup) | N3 check: an ACK heartbeat triggered an injected "You have 1 orchestration message" turn even though the wait's `--types` excluded heartbeats. Seen once; the rate isn't measured. Whether the nudge can be filtered wasn't checked. |
| N5 | Expose per-dispatch token and usage figures, so S12's "record token usage" criterion can be met. | Visibility; location open (Orca, or a wrapper that reads provider usage) | Ack check: `worker-list` and the transcripts expose no usage fields |

A successful acknowledgment check tests plumbing only. It doesn't establish
graph correctness or zero overhead.

## What this does not establish

- It does not establish that any combination of changes is sufficient. The
  D-items include alternatives and optional items. V1–V4 are proposals
  grounded in the cited code, not measured improvements.
- It does not show that V2's misreport happens in practice. No multi-goal
  Scout run exists today, and the claim comes from reading the code.
- Section C rests on two small checks: three workers that finished fast,
  and one worker with a blocked wait. It doesn't cover the Orca-unreachable
  path, larger waves or token cost.
- It doesn't decide where anything is implemented, and doesn't authorize
  implementation.

## Related work

- [S12 ticket](../S12-gig-graph-traversal-and-auditable-execution.md)
- [Task 1: Scout graph mapping](S12-scout-graph-mapping.md)
- [Task 2: decision-edge design](S12-decision-edge-design.md)
- [S07: execution modes and cost-aware orchestration](../S07-execution-modes-and-cost-aware-orchestration.md)
- [ORCA-01: worker completion delivery](../../../followups/ORCA-01-worker-completion-delivery.md)

## Revision notes

1. **D3 gets D1's guarantees (operator review, 2026-09-22).** D3 was
   described as "either this or D1 is enough" without the validation and
   identity requirements. The external-recording bridge must:
   - reject undeclared labels;
   - map the packet's lower-case labels explicitly;
   - preserve producer identity and evidence refs.

   The D3 row and the minimum-combination sentence now say so.

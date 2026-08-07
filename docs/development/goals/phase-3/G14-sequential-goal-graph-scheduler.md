# G14 — Sequential Goal Graph Scheduler

- Status: Proposed for review
- Depends on: G13 (complete and merged; extends its sealed Run path)
- Consumes: G07 Goal Graph validators, G08 approved Gig lifecycle, G09 journal/read surfaces
- Unblocks: later review-loop execution, capability/tool, gate, recovery, and parallelism goals

## Outcome

Replace G13's one-ready-Goal execution assumption with a small, deterministic
sequential scheduler for an already sealed Run. The scheduler consumes the
immutable approved Goal Graph captured by G13, derives pending, ready, active,
verifying, and terminal Goal state, executes one eligible Goal at a time,
honors typed dependency outcomes and joins, and persists `goal_started` and
`goal_completed` handoffs.

The result is a truthful multi-node execution spine: a bounded workpad-only
Run can move through an approved sequential Goal Graph without mutating the
Graph that authorized it. This goal does not make GigAI a general agent
runtime; it proves scheduling state and ordering before adding providers,
operator gates, recovery, parallelism, or target effects.

## In scope

- Extend the existing sealed `gigai run` path after G13 preparation; do not add
  a second scheduler command or a parallel execution API.
- Revalidate the sealed Goal Graph and compare its canonical digest with the
  Run Brief, sealed manifest, initial RunDetails, and committed `run_started`
  handoff before scheduling. A missing, malformed, or divergent graph fails
  closed before a Goal executor starts.
- Materialize every Goal in the sealed Graph in RunDetails. A Goal begins in
  `pending`, becomes `ready` only when its declared dependency conditions are
  satisfied, becomes `running` while its executor is active, and becomes
  `verifying` while its proof and typed outcome are checked. It becomes
  terminal only after that verification succeeds. The schema has no aggregate
  `verifying` set, so a verifying Goal remains the sole member of
  `goal_sets.active` until it becomes `complete` or terminally fails; the
  aggregate active set never contains two Goals.
- Initialize entry Goals from `entry_goal_ids`. A non-entry Goal is eligible
  only after every incoming dependency edge has a terminal source Goal whose
  outcome is named by that edge's `on_outcomes`. A multi-parent Goal is a join;
  no join may start after only one predecessor completes.
- Execute at most one Goal at a time. If more than one eligible automatic Goal
  exists, choose the lowest canonical Goal ID and persist that scheduling
  decision in Run evidence before starting it. No hidden wall-clock or list-
  order choice may change the schedule.
- Support only a sealed Graph whose `aggregate_budget.max_parallel_goals` is
  exactly `1`. A Graph declaring a value greater than `1` is an unsupported
  scheduling policy and fails closed before any Goal executor starts; G14 does
  not silently serialize a Graph that requested parallel capacity.
- Pre-scheduling policy rejections are Run-level failures. Unsupported parallel
  capacity, failure policy, operator gate, or recovery edge is recorded against
  the already-started Run without inventing a Goal-scoped failure handoff;
  `goal_failed` and `goal_blocked` are reserved for a Goal that has reached
  scheduling or execution.
- Honor typed automatic dependency transitions. Goals executed by this goal
  must have `activation: "automatic"`, and dependency edges used by the
  scheduler must be automatic and outcome-typed. An outcome not named by an
  outgoing edge blocks the dependent Goal and terminalizes the Run rather than
  leaving it pending indefinitely. G14 supports only
  `failure_policy: "fail_gig"`; any other policy stops before execution
  rather than silently selecting recovery or continuation behavior.
- Support only the existing deterministic `local_capability` executor used by
  G13, with the declared workpad-only effect. The executor receives the sealed
  Goal contract and Run-scoped inputs and may write only declared evidence
  under that Run's workpad surface.
- Persist one schema-valid `goal_started` handoff before each executor starts
  and one schema-valid `goal_completed` handoff after its proof and outcome
  validate. For Goal-scoped failures after scheduling, persist a
  `goal_failed` or `goal_blocked` handoff before the failed or blocked Run is
  terminalized. Failure and blocked handoffs have the same durability rule as
  starts: they are committed before the worker exits, the parent reports the
  terminal Run state, or any later Goal is considered.
  Handoffs retain Goal ID, Goal version, Run ID, Graph digest, outcome,
  evidence references, usage, actor, ordered journal linkage, and the
  corresponding RunDetails update.
- Recompute ready and pending sets after each completed Goal and continue
  until all required terminal Goals complete or the Run reaches a truthful
  failed/blocked terminal state. A successful Run records all executed Goal
  details, terminal evidence, workpad commit, and `run_succeeded`.
- Preserve G13's interruption behavior. A worker failure is recorded as an
  interruption or failure according to the existing Run contract, with no
  automatic retry or graph rewrite.

## Out of scope

- Parallel execution, concurrency limits beyond one active Goal, effect
  conflict scheduling, or background daemons.
- Operator gates, `continue`, interactive blocking questions, or any behavior
  that waits for a human transition. A Goal with `activation:
  "operator_gate"` is detected and stops the Run as unsupported; it is not
  auto-approved.
- Recovery edges, automatic recovery, retries, rollback, or resuming a failed
  Goal. A Graph that requires a recovery edge stops truthfully rather than
  following it implicitly.
- Model/API/CLI-provider invocation, external network access, arbitrary tools,
  shell strings, capability discovery, installation, or provider fallback.
- Target writes, target mutation, profile changes, mutable source imports, and
  effects outside the declared Run workpad evidence surface.
- Deliberative `create`, user-facing Review Loop Gigs, example Gigs, scheduling,
  recurring Runs, `improve`, dynamic Goal creation, or Graph rewiring during a
  Run.
- Changes to packaged schemas, canonical vectors, completed goal contracts, or
  the release/distribution lane. If an existing record cannot express a
  scheduler transition, stop for a contract amendment.

## Acceptance criteria

1. A sealed G13 Run revalidates one immutable Goal Graph whose canonical digest
   matches every preparation record. Tampering with the Graph, Goal Markdown,
   or Graph digest prevents scheduling and leaves no executor effect or target
   delta.
2. A valid multi-node sequential fixture materializes all Goal IDs in
   RunDetails, initializes entry Goals correctly, and moves Goals through
   `pending`, `ready`, `running`, `verifying`, and `complete` without ever
   placing two Goals in the aggregate active set. During `verifying`, the
   Goal remains in the aggregate `active` set because the schema provides no
   separate verifying set.
3. Each started Goal has a committed `goal_started` handoff before its
   deterministic executor observes the launch. Each completed Goal has a
   committed `goal_completed` handoff only after its declared evidence and typed
   outcome validate. Sequence numbers, parent handoffs, Run ID, Goal version,
   and Graph digest remain consistent and strictly ordered. Failure and blocked
   paths commit their `goal_failed` or `goal_blocked` handoff before
   terminalizing the Run under criterion 7.
4. Dependency edges are enforced by exact source outcomes. A downstream Goal
   remains pending while any required predecessor is incomplete, but an
   unlisted terminal outcome blocks that dependent Goal and terminalizes the
   Run rather than leaving it pending forever. A multi-parent join starts only
   after every exact predecessor condition is satisfied. Invalid
   predecessor/outcome fixtures fail closed rather than becoming ready
   vacuously.
5. When multiple automatic Goals are simultaneously eligible, the scheduler
   selects the lowest canonical Goal ID, records the decision, completes it,
   then reevaluates readiness. Repeated Runs over the same sealed Graph produce
   the same Goal order, subject only to explicitly variable timestamps and
   usage fields.
6. A successful sequential Run reaches `succeeded` only when every required
   terminal Goal is complete and its terminal evidence validates. RunDetails
   contains correct pending/ready/active/complete/failed/blocked/gated/cancelled
   sets, per-Goal outcomes, `realized_max_parallel_goals: 1`, critical path,
   aggregate usage, remaining budget, terminal handoff, and workpad commit.
   G14 defines `critical_path` as the longest dependency path from an entry
   Goal to a required terminal Goal; ties use the lexicographically lowest
   canonical Goal-ID tuple. No Goal is reported complete from an exit code
   alone.
7. A pre-scheduling rejection for unsupported failure policy, parallel
   capacity, operator gate, or recovery edge produces a truthful Run-level
   failure after `run_started`, with no Goal executor effect and no invented
   Goal-scoped handoff. A deterministic executor failure, missing capability,
   malformed outcome, or blocked dependency after Goal scheduling produces a
   truthful failed or blocked Run with a `goal_failed` or `goal_blocked`
   handoff as appropriate. That Goal-scoped handoff is committed before the
   terminal Run record and before any parent or worker returns. No retry,
   recovery transition, later Goal launch, provider call, or target mutation
   occurs after the stop decision.
8. The approved Graph and its source artifacts remain byte-identical throughout
   the Run. All scheduler state, handoffs, evidence, and RunDetails updates are
   Run-scoped artifacts; no scheduler decision edits the active Gig version.
9. Unit, dependency/joins, deterministic-order, handoff-ordering, failure,
   interruption, tamper, no-target-delta, and installed-wheel scenarios pass on
   the supported Python matrix. The eight packaged schema-resource hashes and
   canonical vectors remain unchanged.

## Verification and evidence

- A linear three-Goal fixture with one entry and one terminal path, including
  `goal_started`/`goal_completed` handoffs for every Goal.
- A two-branch serial fixture with a multi-parent join; tests prove the join
  cannot start after only one predecessor and starts after both exact outcomes.
- A fixture with multiple ready automatic Goals; tests prove canonical-ID
  ordering and repeat-run stability.
- Negative fixtures for tampered Graph bytes, Graph digest mismatch, duplicate
  or missing predecessor conditions, undeclared outcomes, operator gates,
  recovery edges, unsupported failure policies, unsupported parallel capacity,
  missing deterministic capabilities, and malformed evidence. Assertions cover
  the verifying-to-active aggregate mapping, unlisted-outcome blocking, and
  critical-path tie-breaking.
- Failpoint/process-boundary tests proving each Goal's start handoff precedes
  execution and an interrupted worker leaves truthful durable state.
- A fake deterministic executor that attempts network, credentials, shell,
  target writes, undeclared Run writes, and Graph mutation; every attempt fails
  closed.
- Fresh installed-wheel execution from a clean environment with no source
  checkout, provider credentials, or target mutation.
- `docs/development/evidence/phase-3/G14/completion-audit.md` and
  `terminal-handoff.md`, plus a requirement-to-test matrix and sanitized
  before/after Run/target manifests.

## Stop boundary

Stop once one immutable, deterministic Goal Graph can be scheduled serially
with truthful state, ordered handoffs, exact dependency/outcome handling, and
independent evidence. Do not widen an unsupported executor, gate, recovery
edge, tool, provider, target effect, or scheduling policy by inference. If the
existing RunDetails or handoff contract cannot represent a required transition,
stop for an explicit contract amendment before coding.

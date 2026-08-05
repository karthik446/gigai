# G13 — Sealed Deterministic Run Launch

- Status: Approved
- Depends on: G10 (phase gate); consumes G05/G08/G09/G11 surfaces
- Unblocks: later Phase 3 scheduling, executor, gate, and recovery goals

## Outcome

Make `gigai run` an explicit, bounded instruction to execute one already
approved Gig version through one deterministic local capability. Before that
capability can observe execution, GigAI resolves and revalidates the approved
authority, then durably writes and commits a Run Brief, sealed Run manifest,
initial RunDetails, and `run_started` handoff.

The result is a truthful first execution path: one sequential, workpad-only
fixture Run can reach a terminal result and be inspected. It is not yet a
general scheduler, model-runner, target mutator, or autonomous agent.

## In scope

- `gigai run [gig_<id>] [--version <positive-int>] [--wait]` resolving an
  explicit older version only when that version is approved and
  journal-consistent; only a version-less invocation consults the Gig's
  authoritative active-version pointer. Missing, unapproved, mismatched,
  divergent, or ambiguous authority fails before a Run ID or Run directory is
  created.
- Revalidation before launch of the approved Gig version, Goal Graph, goal
  contracts, workpad journal, target observation, declared effects, and the
  deterministic local capability. The approved aggregate budget must remain
  satisfiable. Current target or budget drift, or an invalid proposal graph,
  fails before external execution.
- One bounded deterministic `local_capability` fixture executor suitable for
  tests and installed-wheel scenarios. It may write only declared evidence
  under the Run workpad and has no provider credential, network, shell, or
  target-write access.
- The deterministic path's explicit sealed-runtime representation:
  `resolved_models` and `resolved_tools` are empty because the deterministic
  capability is a Goal executor, not a model or declared tool. `sealed_sources`
  contains a content-addressed artifact for the exact bytes of that built-in
  capability. Aggregate and per-Goal usage set all token counts to `0`,
  `cost` and `currency` to `null`, and `cost_status` to `not_applicable`.
  Remaining budget preserves every non-time policy limit from the approved
  aggregate budget; `max_wall_time_ms` is the nonnegative approved limit minus
  measured deterministic execution time.
- Canonical `run_...` allocation and the exact schema-backed records already
  defined by the packaged contract: `run-brief.md`, `run-manifest.json`,
  `run-details.json`, and a text `run_started` handoff.
- A durable ordering proof: the Brief, sealed manifest, initial RunDetails, and
  committed `run_started` handoff exist before the worker or capability can
  observe its launch instruction.
- One supervised local worker per Run, `gigai run-details <run_id>` as the
  minimal user-facing durable inspection command, and `run --wait` waiting
  only for that explicit Run until it reaches a terminal state.
- Truthful terminal and interruption handling for this deterministic path. A
  missing worker becomes `interrupted` with preserved evidence; it is never
  relaunched automatically. A terminal workpad-only Run records a fresh
  `target_after` observation equal to `target_before`, a non-null terminal
  handoff and workpad commit, and `completion_audit` as
  `{status: "missing", path: null}` until a later goal owns completion audits.
  Its terminal RunDetails has the sole Goal in `goal_sets.complete`, every
  other Goal set empty, one completed Goal record with zero usage and no
  errors, `critical_path` containing that Goal ID, `realized_max_parallel_goals`
  of `1`, empty `tool_errors` and `model_errors`, a nonempty execution summary,
  and empty `next_actions`. A new explicit `run` creates a new Run identity.

## Out of scope

- Deliberative `create`, user-facing example Gigs, templates, or sample
  workpads. Test fixtures are execution evidence, not product examples.
- Multiple ready Goals, parallel scheduling, joins, automatic transitions,
  typed recovery edges, operator gates, `continue`, `stop`, or a long-lived
  daemon.
- Model/API/CLI-provider invocation, external network access, arbitrary
  subprocesses or shell strings, any tool invocation, automatic retries, cost
  spending, or provider fallback.
- Target writes, profile changes, capability discovery, mutable source imports,
  or any executor effect outside the declared Run workpad evidence surface.
- Changes to packaged schemas, canonical vectors, completed goal contracts, or
  the release/distribution lane.

## Acceptance criteria

1. `gigai run` accepts only an approved, journal-consistent Gig version. An
   explicit `--version` selects that exact eligible historical version; only
   the version-less path reads the active pointer. Every failed resolution or
   revalidation path leaves no Run ID, Run directory, handoff, worker,
   provider call, or target delta.
2. A successful preparation produces schema-valid Run Brief front matter,
   sealed Run manifest, initial RunDetails, and `run_started` handoff with one
   matching canonical Run ID, Gig ID, Gig version, graph digest, `invoked_by`,
   `invocation_argv`, and workpad-relative artifact references. The manifest
   records empty `resolved_models` and `resolved_tools`, its exact deterministic
   executor source, and the approved aggregate budget. RunDetails records zero
   aggregate usage with `cost: null`, `currency: null`, and
   `cost_status: "not_applicable"`; it preserves non-time policy limits and
   decrements only `max_wall_time_ms` by measured execution time.
3. The preparation record and its private-journal commit are durable before the
   deterministic capability starts. Failpoints immediately after (a) Brief
   write, (b) manifest seal, (c) initial RunDetails write, and (d) committed
   `run_started` handoff prove that no worker or capability effect occurs
   before the required committed authority exists.
4. A valid sequential approved fixture graph executes exactly its one ready
   deterministic capability, records its typed terminal outcome and declared
   evidence, and reaches truthful terminal RunDetails: its sole Goal is
   complete, its critical path contains only that Goal, realized parallelism is
   `1`, errors are empty, `execution_summary` is nonempty, and `next_actions`
   is empty. Exit code alone is not success; the named proof and outcome must
   validate.
5. The deterministic execution path has no network, credential resolution,
   shell-string construction, arbitrary subprocess, target write, or write
   outside its declared Run workpad surface. Adversarial attempts fail closed.
6. `run --wait` and `gigai run-details <run_id>` report only durable state. A
   terminal workpad-only Run records a `target_after` observation equal to its
   `target_before` observation, a non-null terminal handoff and workpad commit,
   and `completion_audit` as `{status: "missing", path: null}`. An interrupted
   worker is recorded as `interrupted`, retains its evidence and handoff, and
   is never automatically retried or relaunched.
7. Repeating an explicit successful Run creates distinct canonical Run IDs and
   disjoint `runs/run_.../` directories. Both immutable artifact sets remain
   addressable, and the journal retains both ordered, linked `run_started`
   handoffs without overwriting an earlier Run.
8. Unit, interruption/failpoint, process-boundary, target-before/after,
   offline-network-denial, and installed-wheel scenarios pass on the supported
   Python matrix. The eight packaged schema-resource hashes and canonical
   vectors remain unchanged.

## Verification and evidence

- Valid and invalid approved-version resolution cases, including active-pointer
  divergence, explicit historical version selection, target or budget drift,
  malformed Run records, and a missing deterministic capability.
- Failpoint tests at every preparation boundary and a worker-observation test
  proving the journal commit precedes execution.
- A deterministic sequential fixture Run, failed-proof case, interrupted-worker
  recovery case, repeated explicit Run case with two linked handoffs and
  disjoint directories, terminal target-before/after equality, explicit
  zero-usage/not-applicable-cost and elapsed-wall-time accounting, and
  adversarial network/target/subprocess/write-surface attempts.
- Fresh installed-wheel execution of the deterministic fixture with no source
  checkout, provider credentials, or target mutation.
- `docs/development/evidence/phase-3/G13/completion-audit.md` and
  `terminal-handoff.md`, with a requirement-to-evidence matrix and sanitized
  before/after target manifests.

## Stop boundary

Stop once the one deterministic, workpad-only sequential Run path is durable,
schema-valid, and independently evidenced. Do not widen a blocked graph,
unavailable executor, or unsupported execution effect into a scheduler,
provider call, target write, or fallback. If a required Run record or
transition is absent from the packaged contract, stop for an explicit contract
amendment rather than inventing it during implementation.

# G16 — First Review Loop Gig

- Status: Proposed for review
- Depends on: G15 (complete and merged); consumes G14's sequential scheduler
- Consumes: G15 Review Bundle, Review Contract, Finding, Feedback, Adjudication,
  Trace, and Report artifacts
- Unblocks: G18 provider comparison and model handoff

## Outcome

Implement GigAI's first complete Review Loop as a deterministic, local-only
Gig. The loop must make the lifecycle durable and inspectable:

```text
review -> verify -> feedback -> address -> closure -> terminal decision
```

G16 proves the orchestration and artifact parentage using seeded fixtures. It
does not pretend that a deterministic fixture is a useful general reviewer or
addressing model. Provider-backed review and model-to-model handoff remain
G18 work.

The first loop produces both a review report and an addressed artifact when an
address pass succeeds. The addressed artifact is never treated as a target
mutation: it is a new workpad-local, content-addressed artifact with explicit
parent references to the Bundle, Contract, Report, and accepted Findings that
produced it.

## In scope

- Amend the serialized contract before implementation with exactly two
  additive schema resources:
  `review-loop.schema.json` for durable loop state and terminal decisions, and
  `addressed-artifact.schema.json` for the addressed output and its parentage.
  The packaged inventory rises from fifteen to seventeen resources. The
  authoritative pre-amendment digest is `src/gigai/schemas/SHA256SUMS`; copies
  under build or wheel-verification directories are derived. All existing
  fifteen hashes and vectors must remain unchanged.
- Define the loop state machine explicitly. A loop advances through
  `reviewing`, `verifying`, `feedback_pending`, `addressing`, `closing`, and a
  terminal state of `complete`, `blocked`, or `unanswerable`. A clarification,
  unresolved disagreement, unavailable reference, failed address, or cycle
  limit is terminally non-successful and cannot be reported as complete. Loop
  state is a distinct dimension from G14 Goal status: stage Goals remain
  `running` in the aggregate `active` set while their loop stage is
  nonterminal; loop `verifying` does not use the Goal status named
  `verifying`.
- Record this transition table as part of the contract and evidence:

  | Loop state | Allowed next state | Guard |
  | --- | --- | --- |
  | `reviewing` | `verifying` | Report and Trace validate with Bundle evidence |
  | `verifying` | `feedback_pending` | deterministic verification completes |
  | `feedback_pending` | `addressing` or `blocked` | accepted decisions plus required Adjudication, or clarification/unresolved disagreement |
  | `addressing` | `closing` or `blocked` | one address pass succeeds, or partial/cycle-limited address fails |
  | `closing` | `complete`, `blocked`, or `unanswerable` | all accepted Findings resolve; deferred/unresolved items block; only open/deferred items explicitly made unanswerable may produce `unanswerable` |
  | terminal state | none | terminal records are immutable |
- Materialize one approved fixture Gig whose Goal Graph is executed by G14's
  sequential scheduler. Each stage persists ordered Goal handoffs and the
  loop record references the sealed Run, Bundle, Contract, Reports, Findings,
  Feedback, Adjudication, Trace, and addressed artifact records.
- Execute the deterministic G15 evaluator tier for five domain-neutral fixture
  profiles: a research-article pair, a climate-article pair, a pull-request
  diff, a repository snapshot, and a spreadsheet/CSV analysis. The fixtures
  exercise both evidence-backed success and seeded defects.
- Define the first feedback policy. Fixture feedback is supplied as a durable
  Feedback record; `accepted`, `rejected`, and `deferred` decisions retain
  their distinct meanings. `clarification_requested` immediately blocks the
  loop and records the next operator action. Disagreement requires an
  Adjudication before an accepted Finding may enter addressing.
- Define one deterministic address pass. The fixture addresser creates a new
  addressed artifact from the exact source bytes and accepted Finding IDs,
  preserving unsupported claims and reporting partial address explicitly.
  It never writes the user target, invokes a provider, installs a tool, or
  runs an arbitrary subprocess.
- Define closure verification. Every accepted Finding must be individually
  `resolved`; a finding that cannot be answered must transition from `open` or
  `deferred` to `unanswerable` before it can affect terminalization. Rejected
  Findings remain rejected; deferred Findings prevent successful closure.
  Closure revalidates Bundle and Report digests, addressed-artifact parentage,
  and the final loop decision.
- Use `cycle_cap: 1` for the first Gig: one address pass is allowed. A case
  requiring a second pass terminalizes as `blocked` with a durable cycle-limit
  finding and no false success report.
- Keep all artifacts workpad-local, canonical, content-addressed, and
  replayable. A repeated deterministic Run produces equivalent loop artifacts
  after declared variable fields are removed.

## Out of scope

- OpenAI, OpenRouter, Codex CLI, Claude CLI, Anthropic, local-model, or any
  other provider invocation; network access; model handoff; provider fallback;
  usage/cost comparison; or live evaluator execution. G18 owns these effects.
- Proposal-time capability discovery, package installation, activation,
  rollback, credential acquisition, or permission changes. G17 owns capability
  inspection and approved installation.
- Mutation of a user target, patches, commits, deployment effects, or an
  implication that an addressed artifact is ready to apply. G19 owns approved
  target effects.
- A new public `review` command, arbitrary user-supplied loop authoring, or
  replacement of `gigai run`. G16 uses an approved fixture Gig through the
  existing Run path; later creation surfaces may expose it deliberately.
- Recurring schedules, daily/weekly/monthly triggers, background workers, or
  comparative prior-Run scheduling. G21 owns recurrence.
- Universal PII detection, URL sanitization, or domain-specific reviewer
  prompts. G15's explicit redaction boundary remains in force.
- Silent changes to any existing schema, vector, journal authority rule, Run
  state, Goal transition, or accepted default. The two-resource amendment is
  the only permitted contract change; any additional artifact or transition
  stops the goal for a new amendment.

## Acceptance criteria

1. Before runtime implementation, a recorded contract amendment adds exactly
   `review-loop.schema.json` and `addressed-artifact.schema.json`, updates
   SHA256SUMS and the installed verifier, asserts seventeen packaged resources,
   and proves all fifteen prior resource hashes and canonical vectors are
   unchanged.
2. A sealed fixture Run cannot start the loop unless the selected Bundle,
   Contract, and referenced source bytes validate and their digests agree. A
   tampered Bundle, Contract, Report, or addressed artifact fails closed before
   the next Goal starts.
3. The fixture scheduler persists the ordered lifecycle
   `reviewing -> verifying -> feedback_pending -> addressing -> closing` using
   G14's Goal handoffs. No two Goals are active, and the approved Goal Graph is
   byte-identical before and after the Run.
4. Each of the five domain profiles produces a schema-valid Report and Trace
   with real Bundle evidence. Every seeded deterministic defect is found or
   explicitly marked `unanswerable`; an aggregate score without per-Finding
   evidence cannot pass. The corpus manifest fixes source bytes, citation
   ordering, defect IDs, and path/line normalization per profile; only the
   declared Run ID, timestamps, and other listed variable fields may differ
   between replays.
5. Feedback is recorded verbatim with actor, decision, and Finding IDs.
   `clarification_requested` terminalizes as blocked with a durable next action;
   rejected, deferred, and accepted decisions are not conflated.
6. An accepted Finding can enter addressing only after any required
   disagreement has an Adjudication. The addressed artifact records exact
   bytes, digest, media type, source Report/Bundle digests, accepted Finding
   IDs, and parent loop identity.
7. Closure independently replays the source Bundle and Report, verifies the
   addressed artifact, and checks every accepted Finding. Complete is emitted
   only when all accepted Findings are resolved; an accepted Finding may not
   transition directly to `unanswerable`. Findings that cannot be answered
   must be marked `unanswerable` while `open` or `deferred`, before acceptance;
   if no accepted Finding remains and all unresolved items are explicitly
   unanswerable, the loop may terminalize as `unanswerable`. Partial address,
   deferred Findings, unresolved disagreement, or missing evidence cannot
   complete.
8. The cycle-limit fixture consumes the one allowed address pass and then
   terminalizes as blocked without a second address artifact or a successful
   terminal decision. Re-running the same deterministic fixture is stable apart
   from declared timestamps and other variable fields.
9. The loop preserves failure ordering and provenance: each failure or blocked
   handoff is committed before the parent reports terminal state, and no
   original Finding, Feedback, or Adjudication is rewritten to hide history.
10. Negative fixtures cover tampered artifacts, missing references, invented
    citations, unsupported clarification, unresolved disagreement, partial
    address, deferred feedback, cycle exhaustion, malformed loop state, and
    addressed-artifact parent mismatch. Each named rejection has a regression
    assertion and a deterministic finding code.
11. An adversarial fixture attempts network access, credential access, target
    writes, Graph mutation, undeclared subprocess execution, and tool
    installation from the evaluator/addresser path. The existing offline
    process harness enforces network denial, credential-shaped environment
    denial, and target/workpad manifests; static import-graph checks enforce
    the no-subprocess/no-installer boundary. Every attempt is refused or
    recorded as blocked, with no effect outside the workpad.
12. A fresh installed wheel replays all five profiles and the cycle-limit case
    from local bytes without a source checkout, credentials, provider, network,
    or target repository. The supported matrix, seventeen-resource verifier,
    and existing canonical vectors remain green.
13. Completion evidence includes the amended schemas and hashes, loop-state
    transition table, fixture/corpus manifest, requirement-to-test matrix,
    failure and mutation report, sanitized before/after workpad manifests,
    replayable reports and addressed artifacts, completion audit, and terminal
    handoff. The audit explicitly names which G18/G19 behavior remains absent.

## Verification and evidence

- Schema tests prove the two-resource amendment, exact inventory, preserved
  fifteen hashes, and valid positive/negative loop and addressed-artifact
  instances.
- End-to-end fixture tests run each lifecycle stage through the sequential
  scheduler and inspect committed Goal handoffs, loop state, reports, feedback,
  adjudication, traces, and addressed artifacts.
- Domain corpus tests cover research, climate, pull-request, repository, and
  spreadsheet inputs with real citation digests and seeded defects.
- Negative tests cover every semantic rejection class in criterion 10,
  including an explicit conflicting-evaluator case and a partial-address case.
- Replay tests compare canonical machine artifacts and normalized human output,
  excluding only declared variable fields.
- Offline process guards prove no network, credentials, subprocess, target,
  installation, or background activity occurs.
- A fresh-wheel verifier runs all profiles and the cycle limit without importing
  test modules or requiring the source checkout.
- Evidence lives under
  `docs/development/evidence/phase-3/G16/` and includes a completion audit,
  terminal handoff, corpus manifest, transition table, and matrix.

## Stop boundary

Stop before implementation if the loop state, addressed-artifact parentage,
feedback transition, closure rule, cycle behavior, or required schema field is
not precise enough to validate and replay. Do not invent a public command,
provider path, tool effect, target mutation, PII promise, or scheduler policy to
make a fixture pass.

Stop for an explicit amendment if the two new schemas cannot represent the
loop, if an existing Run or Goal transition must change, or if a required
artifact would otherwise be hidden inside a Report or Bundle without durable
parentage. G16 must not begin G18/G19 behavior early, even if a fixture would be
easier to complete by doing so.

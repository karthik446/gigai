# G22 — Deliberative Create and User-Facing Proposal Interview

- Status: Complete — implementation and closeout accepted
- Depends on: accepted S22-01 decision record, accepted additive
  `proposal-interview.schema.json` amendment, and completed G18
  provider comparison/model-handoff boundary; consumes G15 workpad/reference
  authority, G16 review-loop contracts, and G17 proposal-time capability
  decisions
- Unblocks: G19 approved target mutation

## Outcome

Implement the approved S22-01 interaction as the user-facing `gigai create`
flow. An operator supplies a request; the local session collects explicitly
selected local references and then gathers bounded, typed, domain-specific clarification;
the operator chooses the privacy, capability, and effect boundary; and the
system seals a reviewable Gig proposal only after explicit operator approval.

G22 is a proposal-creation boundary, not an execution boundary. A successful
interview creates or revises proposal artifacts and a journal handoff. It does
not create a Run, mutate a target repository, install a capability, or treat a
model response as approval.

## Contract gate

Before runtime implementation, verify the accepted S22-01 decision record, the
accepted `proposal-interview.schema.json` amendment, and G18 completion
evidence are present and citable. Re-read the 22-resource
contract baseline and the existing G08 proposal lifecycle. Prefer existing
`gig-proposal`, Goal Graph, journal, workpad, reference-bundle, and capability
artifacts over new persistence.

If durable question/answer/revision records, new terminal states, or a new
authority boundary cannot be represented by the accepted resources and
existing journal/workpad rules, stop and write a further additive contract
amendment. Do not add fields or reinterpret an existing state by inference.

S16-EVAL is not a blanket G22 dependency. S22-01 owns proposal-question
quality; S16-EVAL owns Review Loop quality. G22 may cite S16-EVAL only if an
implementation path actually invokes or evaluates the Review Loop, and then
must use its fixed bar rather than inventing a second one.

## In scope

- Extend `gigai create` with a short-lived, loopback-only HTMX session while
  preserving an explicit deterministic/non-interactive path if compatibility
  requires one. The selected path and its terminal result must be visible in
  the journal; an old offline path must not silently bypass the new boundary.
- Bind the session to an explicitly resolved target and Gig identity. The
  session may read only references the operator selects by stable ID and exact
  bytes/digest; there is no implicit all-files or whole-workspace prompt.
- Launch the smallest viable local HTTP surface from the CLI, with an
  explicit loopback bind, an unguessable short-lived session token, bounded
  lifetime, one local operator session, and deterministic shutdown at a
  terminal state. No public listener or background service is implied.
- Carry the S22-01 protocol as typed request/response messages: question ID,
  answer type, requiredness, dependencies, rationale, provenance, allowed
  values, answer validation, conditional follow-ups, bounded clarification
  rounds, and terminal `blocked` behavior at the round cap.
- Invoke the shipped deterministic questioner only through G18's model
  port/factory and accepted provider-input boundary. The prompt contains the
  request, approved question context, and selected/redacted reference
  material only. A non-deterministic provider target is refused unless an
  explicit caller supplies network permission; G22 does not claim
  provider-backed question quality. A blocked boundary, unavailable provider,
  cancellation, malformed response, or budget failure remains a deterministic
  non-success outcome.
- Persist short-lived session events in disposable SQLite for browser/session
  recovery and audit, using canonical redacted payloads and monotonic sequence
  numbers. SQLite is a projection/trace, not authority for the Gig, proposal,
  Goal Graph, or approval.
- Persist the authoritative proposal, reference bundle, and approval handoff
  through the existing workpad and journal lifecycle. Every revision has
  explicit parentage; an abandoned or interrupted draft cannot become the
  active proposal by recovery accident.
- Require explicit operator choices for privacy, capability, and effect. The
  only G22 effect choices are `read_local` and `write_workpad`; capability
  choices are inspected and bounded by G17. Approval requires selected
  references and all required boundary answers.
- Seal an approved proposal through the existing proposal validator and
  approval lifecycle. Approval is terminal for the interview and produces no
  Run, target write, provider fallback, package installation, or capability
  execution.
- Prove the question-quality boundary against S22-01's corpus: repository
  feature, resume tailoring, reference synchronization, and tabular/finance
  analysis. Evaluate ambiguity coverage, non-redundancy, domain adaptation,
  privacy/tool awareness, stopping behavior, typed-answer rejection, and
  proposal completeness as applicable to each case.

## Out of scope

- Public, remote, multi-user, authenticated, or internet-facing hosting; a
  daemon, background worker, queue, recurring interview, or long-lived browser
  session.
- Target mutation, patch application, commits, deployment, capability
  installation, arbitrary tool execution, credential acquisition, or starting
  a Run. G17 owns capability inspection; G19 owns approved target effects.
- New provider adapters or provider compatibility claims. G18's accepted
  adapter set and S18-05 input boundary remain authoritative.
- Automatic fallback, hidden retries, provider racing, hidden conversation
  context, or model self-approval. A model may propose questions or a draft;
  only the operator can approve the proposal.
- Making SQLite, browser state, an in-memory session, or model output the
  authority for a Gig. Workpad artifacts and the journal remain authoritative.
- Absorbing S22-01's proposal-question evaluation into S16-EVAL's Review Loop
  methodology, or claiming that question-quality evidence proves Review Loop
  quality.

## State and authority contract

The shipped session must implement the accepted S22-01 states and transitions:

```text
questions_pending -> proposal_ready -> approved
         |                  |
         v                  v
clarification_required   blocked
         |
         +-- round cap --> blocked
```

The following rules are mandatory:

1. Every state-changing request identifies one session, one sequence, and one
   typed event. Unknown questions, stale revisions, invalid answer types,
   disallowed options, and references outside the selected set are rejected
   deterministically.
2. A browser disconnect, process interruption, expired token, or SQLite
   recovery failure never implies approval. Recovery resumes only from the
   last committed non-terminal event or returns an explicit blocked result;
   it cannot duplicate an approval or rewrite an accepted journal entry.
3. `approved` requires all required answers, a non-empty explicit reference
   selection, valid privacy/capability/effect choices, a valid proposal, and an
   operator action. The model cannot emit the approval event.
4. `blocked` records a stable reason and remains terminal for that session.
   It cannot transition to approved, invoking, running, target mutation, or
   automatic retry.
5. The model sees only the request and the current, policy-approved question
   context. Hidden prior context, unselected references, credential values,
   and browser-only state never enter the provider input or durable proposal.

## Acceptance criteria

1. The implementation starts only from accepted S22-01, the accepted
   proposal-interview schema amendment, and G18 evidence. The
   goal's completion audit cites the exact adopted state machine, answer types,
   effect choices, reference-selection rule, G18 provider boundary, and any
   approved contract amendment; no fixture-only behavior is described as
   shipped support.
2. A fresh installed-wheel black-box run of `gigai create` launches the local
   interview, presents the request/reference boundary, and reaches a
   deterministic terminal result. The CLI reports the session/proposal IDs
   and whether the result is `approved`, `blocked`, or an interrupted pending
   draft without creating a Run.
3. Loopback isolation is mechanically proven: the server binds only to the
   loopback interface, rejects requests without the session token, expires or
   closes at the declared lifetime/terminal boundary, and does not expose a
   public listener, background worker, or remote hosting path.
4. Reference selection is exact and explicit. The operator can select a
   subset by stable ID; selected bytes and digests are recorded in the draft
   Bundle; unselected references are absent from the model input; path
   traversal, changed bytes, missing references, and implicit whole-workspace
   selection fail closed.
5. The question protocol validates all four S22-01 answer categories used by
   the implementation (`text`, `choice`, `multiselect`, and `confirmation`),
   dependencies, requiredness, allowed values, and provenance. Invalid,
   stale, duplicate, or out-of-order events have deterministic rejection
   findings and do not advance the session.
6. Clarification is bounded. A fixture requiring follow-up reaches
   `clarification_required`, increments the recorded round, and becomes
   `blocked` at the fixed cap. No cap exhaustion can trigger an automatic
   model retry, fallback, or approval.
7. The shipped deterministic question path passes through G18's model
   port/factory and its selected-input boundary. Tests prove selected-only
   input, credential non-disclosure, and deterministic malformed-response or
   unavailable-provider failures. A non-deterministic provider target is
   refused by default and cannot claim provider-backed question quality until
   a later caller supplies an explicit network permission and boundary
   attestation.
8. SQLite persistence is replayable and subordinate. Tests prove canonical
   redacted payloads, monotonic per-session sequence numbers, duplicate/stale
   event rejection, recovery after interruption, and no approval from an
   uncommitted or corrupted trace. Rebuilding SQLite from the authoritative
   workpad/journal does not change the proposal identity or approval result.
9. Revisions are explicit and durable. A changed request, reference selection,
   answer, boundary choice, or model question creates a traceable revision or
   parent proposal; stale browser drafts cannot overwrite the latest revision,
   and an abandoned draft cannot be approved by a later session accidentally.
10. Boundary choices are enforced at the approval edge. `read_local` and
    `write_workpad` are the complete effect set for G22; missing or invalid
    privacy/capability/effect choices block approval. No target file, Git
    history, package, credential, or external service changes in any fixture.
11. Approval uses the existing proposal validator and journal/workpad
    lifecycle, creates the authoritative proposal handoff, and leaves the
    interview terminal. Tests prove approval is idempotent, does not create a
    Run, and cannot transition into target mutation or capability execution.
12. Interruption and security fixtures cover browser disconnect, process kill,
    expired session token, malformed HTMX payload, cross-session event
    injection, path traversal, changed reference bytes, network denial, and
    credential canaries. Each produces a deterministic non-success outcome
    and leaves no secret or unselected content in durable evidence.
13. The S22-01 question-quality corpus is exercised with a checked-in case
    manifest and per-case expected outcomes. The completion audit reports
    question coverage, non-redundancy, domain adaptation, privacy/tool
    awareness, stopping behavior, and proposal completeness. If the Review
    Loop is also invoked, its evidence separately reports the accepted
    S16-EVAL bar; proposal-question scores do not substitute for it.
14. Completion evidence includes sanitized protocol vectors, loopback HTTP
    integration traces, state-transition and typed-answer fixtures, exact
    reference-selection proofs, SQLite replay/recovery evidence, provider
    boundary evidence, interruption/security negatives, installed-wheel
    replay, target/workpad before/after manifests, a completion audit, and a
    terminal handoff identifying G19 as the next authorized consumer.

## Verification and evidence

- Unit and contract vectors for every state, event, answer type, transition,
  rejection reason, effect choice, and terminal outcome.
- Loopback HTTP tests through the CLI boundary, including token/lifetime,
  malformed requests, concurrent-session isolation, and clean shutdown.
- Fake-model tests through G18's public port/factory with selected-reference,
  redaction, credential, cancellation, unavailable-provider, and budget
  canaries.
- Disposable SQLite crash/replay tests plus workpad/journal authority tests;
  no workstation database or browser cache is committed.
- Installed-wheel/offline black-box scenarios without a source checkout, live
  credentials, network, or target mutation.
- Requirement-to-evidence completion audit and durable terminal handoff.

## Stop boundary

Stop before runtime implementation if S22-01 or G18 evidence is missing,
contradictory, or not sufficient to derive the protocol and provider boundary.
Stop for an additive contract amendment if durable question/answer/revision
records, a new terminal state, or a new authority rule cannot be represented
by the accepted resources and journal/workpad substrate.

Stop implementation if loopback-only binding, session expiry, exact selected
bytes, provider-input redaction, typed-answer validation, bounded
clarification, interruption recovery, or operator-only approval cannot be
proved mechanically. Stop if SQLite becomes authoritative, if an interrupted
session can approve, if a model can approve itself, or if G22 would need
target mutation, capability execution, provider fallback, or background work.

G22 is complete when the local interview and proposal handoff satisfy the
criteria above. G19 may begin only from G22's committed completion audit and
terminal handoff; it must not infer approval or target authority from a draft,
browser state, model output, or SQLite trace.

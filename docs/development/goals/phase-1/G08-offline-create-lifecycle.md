# G08 — Offline Create Lifecycle

- Status: Approved; blocked by G06 and G07
- Depends on: G06, G07
- Unblocks: G09

## Outcome

Implement deterministic offline `create`, proposal review, feedback, revision,
approval, and rejection through the real private workpad, validation, handoff,
and local-commit lifecycle.

## In scope

- Deterministic offline creation fixtures and adapters; no live model or
  network dependency.
- Allocate the Gig ID through the sole G01 identity API, checking both the v2
  registry and configured workpad before persistence. G08 is the first product
  lifecycle goal allowed to generate a Gig ID.
- Invoke the G05 provisioning primitive with that exact ID; never ask G05 to
  generate, replace, or infer one.
- Before any model, research, editor, or proposal effect, invoke G06 to write
  and commit the `creation-started` handoff as sequence 1 in the unborn private
  repository.
- Only after that first durable commit, select the Gig active through the G05
  authority path: authoritative `project.toml` plus derived registry state for
  Git targets, or one authoritative registry transaction for non-Git targets.
- Resume the sole exact managed provisioned-but-unjournaled workpad after
  interruption without allocating a replacement ID. Ambiguous or foreign empty
  workpads fail closed rather than being guessed or deleted.
- Persist the complete proposed workpad artifact set defined by V14 Section
  6.5.
- Validate readable Gig and Goal Markdown plus their machine projections before
  presenting a proposal.
- Open the complete proposal in the configured editor and stop for user review.
- Record feedback verbatim as a text handoff.
- Produce revisions as new validated files and commits linked to the previous
  proposal.
- Approve by validating, sealing one immutable Gig version, tagging the local
  commit, and recording exactly what was approved.
- Reject without creating an executable Gig version.
- Keep proposal, approval, and any future Run as distinct semantic transitions.

## Out of scope

- Live planner, critic, adjudicator, research, or provider adapters.
- Starting a Run, executing a Goal, or materializing generated executable code.
- Self-approval by a model, wrapper, fixture, or `create` command.
- A second parallel proposal format or a second approval transition.
- Generating project IDs, implementing a second Gig-ID allocator, allowing G05
  or G06 to allocate an ID, or activating a Gig before `creation-started` is
  durably committed.

## Acceptance criteria

1. `create` allocates one canonical Gig ID through G01, provisions that exact ID
   through G05, commits `creation-started` as G06 sequence 1, and only then
   makes the Gig active. No earlier step performs a model, research, editor, or
   proposal effect.
2. Interruption after ID allocation, after provisioning, after the first
   handoff replacement, after the first commit, or before active selection
   resumes the same ID or fails closed; it never creates a second workpad or
   guesses success.
3. Static ownership tests prove G08 is the only lifecycle path that invokes Gig
   ID generation and that G05/G06 receive the selected ID unchanged.
4. A deterministic fixture creates the complete proposal artifact set in the
   private workpad and records a proposal-ready handoff and commit.
5. The proposed Markdown and manifest graph pass all G07 validators before
   presentation.
6. `create` opens the proposal and stops; it neither approves nor starts a Run.
7. Feedback is preserved verbatim and linked to the proposal version it
   addresses.
8. Revision produces new validated authority without changing approved or
   historical bytes.
9. Approval seals exactly one immutable Gig version and records its contract,
   graph, sources, decisions, and approval commit.
10. Rejection records a terminal proposal outcome and creates no active Gig
    version. An active Gig ID and an approved active Gig version remain distinct
    states.
11. Interruption at every semantic boundary recovers to a truthful handoff with
   no duplicate automatic transition.
12. All scenarios run without network access or tokens.

## Verification and evidence

- ID-allocation, empty-workpad resume, first-commit, active-selection, and
  failpoint scenarios across Git and explicit non-Git targets.
- End-to-end propose, feedback, revise, approve, and reject scenarios using the
  installed CLI.
- Pre/post workpad Git graphs, handoff sequences, tags, and artifact manifests.
- Interruption matrix across every lifecycle transition.
- Network-denial and secret-canary output.
- Requirement-to-evidence completion audit.

## Stop boundary

Stop after an approved Gig version can exist truthfully and offline. Approval
must start no Run, execute no Goal, and activate no generated capability. G08
owns the orchestration that turns its newly allocated ID into a provisioned,
journaled, active Gig; it must not move ID allocation, first-commit authority,
or workpad resolution into a second implementation.

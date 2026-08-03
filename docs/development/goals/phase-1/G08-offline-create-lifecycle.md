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

## Acceptance criteria

1. A deterministic fixture creates the complete proposal artifact set in the
   private workpad and records a proposal-ready handoff and commit.
2. The proposed Markdown and manifest graph pass all G07 validators before
   presentation.
3. `create` opens the proposal and stops; it neither approves nor starts a Run.
4. Feedback is preserved verbatim and linked to the proposal version it
   addresses.
5. Revision produces new validated authority without changing approved or
   historical bytes.
6. Approval seals exactly one immutable Gig version and records its contract,
   graph, sources, decisions, and approval commit.
7. Rejection records a terminal proposal outcome and creates no active Gig
   version.
8. Interruption at every semantic boundary recovers to a truthful handoff with
   no duplicate automatic transition.
9. All scenarios run without network access or tokens.

## Verification and evidence

- End-to-end propose, feedback, revise, approve, and reject scenarios using the
  installed CLI.
- Pre/post workpad Git graphs, handoff sequences, tags, and artifact manifests.
- Interruption matrix across every lifecycle transition.
- Network-denial and secret-canary output.
- Requirement-to-evidence completion audit.

## Stop boundary

Stop after an approved Gig version can exist truthfully and offline. Approval
must start no Run, execute no Goal, and activate no generated capability.

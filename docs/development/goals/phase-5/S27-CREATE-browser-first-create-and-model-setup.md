# S27-CREATE — Browser-First Create and Model Setup Spike

- Status: Proposed for review; not activated
- Type: Product-flow and contract-design spike; no runtime implementation
- Depends on: G24 UAT findings, G26 builder implementation, G18 model readiness,
  G22 loopback interview, and the G28 readiness goal
- Unblocks: G28 v0.1.5 readiness implementation

## Outcome

S27-CREATE defines the normal operator path:

```text
gigai setup -> choose a truthful model path -> gigai create <gig-name>
  -> local HTMX discovery/build/review flow
```

The target user command is:

```text
gigai create tailor-resume-for-job
```

After normal one-time project setup, it must not require implementation-facing
request, reference, open, or model flags to reach the browser flow.

## Decisions required

The spike must produce an accepted decision record covering:

1. setup's model-choice flow for configured API targets, detected local Codex
   or Claude executables, and deterministic fixture mode;
2. truthful readiness labels and the exact meaning of detected-only versus
   supported/usable;
3. credential-reference-only persistence and privacy/network explanation;
4. target/project initialization expectations and whether any binding may be
   safely inferred or must remain explicit;
5. the first HTMX screen, follow-up question flow, build/research transition,
   review, rejection, revision, and approval boundaries; and
6. CLI, browser, integration, installed-E2E, and behavioral-eval evidence for
   the target command.

## Required boundary

Installed executables are not supported adapters by discovery alone. The setup
flow may display them as detected-only and explain what evidence is missing.
The model choice cannot place credentials, network access, target authority, or
approval into the session.

## Out of scope

- arbitrary provider or CLI support claims;
- automatic target mutation or Run execution;
- a second browser state machine;
- Streamlit or a second frontend authority layer; and
- human UAT itself, which follows the v0.1.5 candidate.

## Acceptance criteria

1. The normal setup/model-choice flow and its readiness states are specified.
2. `gigai create tailor-resume-for-job` reaches HTMX after normal setup without
   implementation-facing flags.
3. The initial screen asks for the Gig definition and explains optional context
   without exposing raw schema terminology.
4. Model, API, credential, network, and local-reference boundaries are
   understandable and fail closed.
5. The flow's required unit/contract, integration, installed-E2E, and
   behavioral-eval evidence is enumerated for G28.

## Stop boundary

Stop if the target command still requires hidden implementation flags, if setup
advertises an unsupported executable as usable, or if a model/browser event can
advance proposal, Run, target, credential, or version authority.

# S22-01 — Local proposal-interview and clarification protocol spike

- Status: Accepted protocol/design prerequisite; no product code
- Depends on: G15 reference substrate, G16 review-loop boundary, and G17
  capability inspection
- Unblocks: G22 proposal-interview implementation after the accepted additive
  `proposal-interview.schema.json` amendment; does not implement `gigai create`

## Decision

Use a short-lived local interview session with structured questions, typed
answers, explicit reference selection, explicit privacy/capability/effect
choices, bounded clarification rounds, and operator approval. The protocol is
local-first and offline by default. A proposal cannot become approved until
the operator has answered the boundary questions and selected only allowed
effects (`read_local` or `write_workpad`).

Ambiguity enters `clarification_required`; after the fixed round cap it becomes
`blocked`. Approval is terminal protocol evidence, not target mutation,
provider invocation, capability execution, background work, or a public HTTP
server.

## State machine

```text
questions_pending -> proposal_ready -> approved
         |                  |
         v                  v
clarification_required   blocked
         |
         +-- round cap --> blocked
```

Every question has an ID, answer type, dependency list, rationale, provenance,
and allowed values. Every answer is validated before it enters the session.
References are selected by ID; there is no implicit all-references behavior.

## Persistence and protocol boundary

The fixture uses a disposable SQLite `interview_events` table with ordered
session-local events and canonical JSON payloads. A future HTMX layer may
transport these protocol messages over loopback, but S22-01 does not start a
server or add routes. Browser-session persistence is short-lived draft state;
the workpad and G15 artifacts remain the authoritative project records later.

## Evaluation corpus

The corpus covers repository feature review, resume tailoring, reference
synchronization, and tabular/finance analysis. Each case requires either a
fully bounded approved proposal or a clarification-blocked outcome. The cases
evaluate question completeness, typed-answer rejection, reference selection,
privacy/effect choices, round caps, and explicit non-effects.

## Contract impact and limitations

No runtime lifecycle, packaged schema, server, provider adapter, capability
execution path, credential flow, or target authority changed. If G22 needs
durable question/answer records beyond the existing workpad/journal substrate,
it must raise a separate additive contract amendment preserving existing
hashes and canonical vectors.

The follow-up [proposal-interview contract amendment](proposal-interview-contract-amendment.md)
is now accepted for G22. It defines the durable interview snapshot without
claiming that the local server, HTMX transport, or `gigai create` has shipped.

This spike does not prove HTMX rendering, browser security, concurrent sessions,
real persistence recovery, proposal quality, provider use, or target mutation.
S22-01 question-quality evaluation remains distinct from S16-EVAL review-loop
quality.

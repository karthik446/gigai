# Phase 2 Development Goals

This directory is the canonical implementation graph for the deliberative
`create` phase. Phase 2 turns an operator's request and explicitly selected
local references into a reviewable, bounded Gig proposal before any approved
Run or target effect exists.

The [V15 roadmap](../../../architecture/v15-roadmap.md) and the accepted
[S22-01 decision record](../../evidence/phase-3/S22-01/decision-record.md)
remain authoritative for the interaction protocol and its boundaries. The
S22-01 record is research evidence, not shipped `create` behavior.

## Goal graph

```text
S22-01 + G18 -> G22 -> G19
```

S22-01 defines the question, answer, clarification, persistence, and approval
protocol. The accepted proposal-interview schema amendment defines its durable
snapshot. G18 supplies the explicit model-invocation and provider boundary.
G22 implements the local user-facing interaction. G19 owns approved target
effects and must not be pulled into G22.

| Goal | Outcome | Depends on | Initial state |
|---|---|---|---|
| [G22](G22-deliberative-create-and-proposal-interview.md) | Local deliberative `create` and approved proposal interview | S22-01, proposal-interview amendment, G18 | Proposed for review |

## Completion rule

G22 stops only after its protocol, loopback server, lifecycle integration,
authority boundary, interruption behavior, and installed black-box evidence
are complete. Its completion audit and terminal handoff must identify the
exact proposal artifacts produced and state that no target mutation, Run, or
unapproved capability effect occurred.

If the implementation needs a new durable schema, state transition, or
authority rule not represented by the accepted 22-resource contract, G22 stops
for a separate additive contract amendment before runtime code continues.

## Evidence layout

```text
docs/development/evidence/phase-2/G22/
  completion-audit.md
  terminal-handoff.md
```

The evidence directory is created when implementation begins. Raw browser
traces, credentials, local paths, and disposable databases do not ship as
durable evidence.

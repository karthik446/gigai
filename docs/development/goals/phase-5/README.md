# Phase 5 Development Goals

This directory is the canonical implementation graph for local improvement
and later recurrence. G20 consumes completed evidence and proposes a new Gig
version; it does not automatically modify approved history or schedule Runs.

## Goal graph

```text
G19 -> G20 -> G21
```

| Goal | Outcome | Depends on | Initial state |
|---|---|---|---|
| [G20](G20-local-improve-and-evaluator-learning.md) | Local evidence-backed improvement proposals and evaluator learning | G16, S16-EVAL, G18, G19, G22 | Proposed for review |
| G21 | Recurring and comparative Gigs | G20 | Planning-only |

G20 must preserve G19's explicit target-effect authority and the existing
workpad journal/active-version authority. G21 must not be activated merely
because G20 can produce a new version; recurrence requires its own Goal
contract and evidence.

## Completion rule

G20 stops only after its additive contract amendment, learning records,
improvement manifest, two proposal gates, authoring recovery, G22 improve path,
stale-base refusal, installed replay, completion audit, and terminal handoff
are accepted. Evidence belongs under
`docs/development/evidence/phase-5/G20/`.

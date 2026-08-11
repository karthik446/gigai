# Phase 5 Development Goals

This directory is the canonical implementation graph for local improvement,
Gig portability, and later recurrence. G20 consumes completed evidence and
proposes a new Gig version; it does not automatically modify approved history
or schedule Runs. G23 is a standalone goal that makes an approved Gig version
self-describing for its declared tools and proposal lineage; it does not
declare an alpha or public release.

## Goal graph

```text
G19 -> G20 -> G21
G20 -> G23
```

| Goal | Outcome | Depends on | Initial state |
|---|---|---|---|
| [G20](G20-local-improve-and-evaluator-learning.md) | Local evidence-backed improvement proposals and evaluator learning | G16, S16-EVAL, G18, G19, G22 | Complete |
| [G21](G21-recurring-and-comparative-gigs.md) | Recurring and comparative Gigs | G13, G14, G20 | Complete |
| [G23](G23-gig-self-containment-and-portability.md) | Capability-manifest reference and proposal-lineage resolution on the active Gig version | G17, G19, G20, G22 | Complete |

G20 must preserve G19's explicit target-effect authority and the existing
workpad journal/active-version authority. G21 must not be activated merely
because G20 can produce a new version; recurrence requires its own Goal
contract and evidence. G23 is independent of G21: it does not require
recurring Runs, and G21 does not require G23's portability field. A later,
separately numbered release-lane goal (following the `docs/development/goals/
release/` G12 pattern) owns any alpha or public-release declaration; neither
G21 nor G23 makes that declaration.

## Completion rule

G20 stopped after its additive contract amendment, learning records,
improvement manifest, two proposal gates, authoring recovery, G22 improve path,
stale-base refusal, installed replay, completion audit, and terminal handoff
were accepted. Evidence belongs under
`docs/development/evidence/phase-5/G20/`.

G23 is complete after its post-closeout repair under the accepted additive
amendment. The repaired refusal-code contract, publication-child ambiguity
guard, implicit manifest revalidation, real Git-history lineage fixtures,
13/13 mutation report, installed replay, completion audit, and terminal handoff
are accepted. Evidence belongs under
`docs/development/evidence/phase-5/G23/`.

G21 is complete after its accepted occurrence/comparison amendment, immutable
reference snapshots, manual occurrence lifecycle, explicit missed-state
handling, deterministic comparisons, mutation evidence, installed replay,
completion audit, and terminal handoff. Evidence belongs under
`docs/development/evidence/phase-5/G21/`.

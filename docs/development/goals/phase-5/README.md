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
G23 -> G24 -> G25
G22 -> G26 + G27-contract -> S27-EVAL/S27-ROLE/S27-CREATE -> G28 -> G27 -> G24 -> G25
```

| Goal | Outcome | Depends on | Initial state |
|---|---|---|---|
| [G20](G20-local-improve-and-evaluator-learning.md) | Local evidence-backed improvement proposals and evaluator learning | G16, S16-EVAL, G18, G19, G22 | Complete |
| [G21](G21-recurring-and-comparative-gigs.md) | Recurring and comparative Gigs | G13, G14, G20 | Complete |
| [G23](G23-gig-self-containment-and-portability.md) | Capability-manifest reference and proposal-lineage resolution on the active Gig version | G17, G19, G20, G22 | Complete |
| [G24](G24-human-uat-and-dogfooding.md) | Human-executed UAT and dogfooding across real GigAI workflows | G18, G19, G20, G21, G22, G23, G26, G27, G28 | Proposed for review |
| [G26](G26-model-facilitated-gig-builder.md) | Model-facilitated Gig definition, adaptive clarification, and proposal research | G18, G22, S18-02, G24 findings | Active — amendment accepted, implementation underway |
| [G28](G28-v0.1.5-readiness.md) | v0.1.5 evaluation, role-registry, and browser-first create readiness | G26, G27 contract, S27-EVAL, S27-ROLE, S27-CREATE | Complete — candidate evidence accepted |
| [G27](G27-adaptive-gig-discovery-and-pre-proposal-research.md) | Adaptive Gig discovery, bounded pre-proposal research, and model-selected direction questions | G20, G22, G26, G28 | Proposed for review |

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

G24 is a human-executed UAT goal. Its session records live outside the
repository under the operator's local GigAI UAT directory; no prompts,
references, credentials, databases, transcripts, or model outputs are
committed as evidence. The goal contract defines the checkpoint-by-checkpoint
review protocol and artifact authority map.

G26 addresses the create-flow finding that the current interview can jump from
one operator answer to approval without a genuine model-backed proposal-build
phase. GigAI remains the facilitator and authority boundary; the selected model
asks domain-specific questions and performs bounded proposal research. G26 must
land before G24's final UAT pass and before G25 alpha-readiness review.

G27 makes the browser interview the actual Gig-definition canvas. It gives the
model a truthful capability inventory, allows a bounded pre-proposal research
plan, and limits each discovery round to five model-selected direction
questions. G27 reuses G20's evidence gates for later improvement and does not
create a second proposal or version authority.

The S27 spikes define the evaluation foundation, central role registry, and
browser-first setup/create contract needed before implementation. G28 owns
their v0.1.5 implementation and candidate evidence; G27 runtime work begins
after G28 is accepted.

Before G24's final human UAT pass, the v0.1.5 readiness gate must close
[TD-0006](../../tech-debt/TD-0006-evaluation-taxonomy-and-behavioral-evals.md),
[TD-0007](../../tech-debt/TD-0007-central-role-registry.md), and
[TD-0008](../../tech-debt/TD-0008-browser-first-create-and-model-setup.md).
These are release-readiness debt items, not new proposal or version authority.

# GigAI v0.1.8 — Backlog

**Status:** Planning for later phases after v0.1.7 Scout; the bounded S11
Phase 1 groundwork packet is implemented, while v0.1.7 remains a release
prerequisite and this backlog does not claim that it has shipped. Spike 6's
initial pilot is complete; narrow local execution/comparison moved into v0.1.7
[RUNTIME-01](../v0.1.7/goals/RUNTIME-01-local-execution-and-backend-comparison.md).
Broader local-model research remains here.

## Release roadmap

[v0.1.8 find-jobs functional roadmap](roadmaps/v0.1.8-find-jobs-functional-roadmap.md)
— recorded 2026-09-22, the active packet plan for the operator-authorized
`acquire → assess → present` graph and local Run workflow UI. The [Scout search-
first roadmap](roadmaps/v0.1.8-scout-search-first-roadmap.md) is obsolete and
kept for history; this new roadmap does not claim v0.1.7 has shipped.

The planned Phase 2 audit/interface-freeze stories are indexed at
[phase-2/tickets](phase-2/tickets/README.md); they are documentation-only and
not started.

## Delivery tasks

| ID | Task | Status |
| --- | --- | --- |
| V018-01 | [Understandable workpads, project/Gig navigation, and readable artifacts](tasks/V018-01-workpad-navigation-and-readable-artifacts.md) | Requested 2026-09-08; readable navigation maps into the first local tracking UI; design and implementation pending |

## Execution directions

- [Scout search-first execution and GigAI local applications](directions/scout-search-first-local-applications.md)
  — recorded 2026-09-20. Immediate job persistence/visibility, independent local
  assessment, requirements matrix, tagged resume revisions, actionable proposals,
  essential local tracking UI, dashboard ranking, portability and readable
  navigation. The first UI is a small local surface for the operator and a few
  friends, reusing existing validated operations; React, FastAPI, hosting and
  multi-tenant expansion are not forced. This is a detailed product-direction
  refinement, not a platform pivot, implemented contract or added v0.1.7 scope.
  Includes proposed execution steps and evidence criteria.

## Research spikes

**Release groundwork and research lanes:** [S11 — behavior-based test
organization](spikes/S11-behavior-based-test-organization.md) is the first
v0.1.8 release foundation; its bounded Phase 1 receipt covers inventory and
measured baseline,
deterministic fixtures, separated unit/integration/CLI/installed/live lanes,
and a bounded migration that preserves coverage and failure semantics. [S07 —
execution modes and cost-aware orchestration](spikes/S07-execution-modes-and-cost-aware-orchestration.md)
remains parallel, non-blocking research rather than a release prerequisite.
S08, S09 and S10 have recorded research evidence with the boundaries stated in
the spike index; S08's small synthetic pilot and S10 caller/installed gaps are
release gates, while S09 does not prove freshness or storage rights. Existing
spike numbers remain stable.

The [existing spike list](spikes/README.md) covers accessibility/onboarding,
effective Gig memory, per-Gig hooks/documentation drift, database versioning,
[schema simplification/redesign](spikes/README.md#spike-5--schema-simplification-and-redesign),
and [local models through Ollama and agent harnesses](spikes/README.md#spike-6--local-models-through-ollama-and-agent-harnesses).
V018-01 is a concrete usability delivery task, not another research-only spike.
Coordinate its design with accessibility Spike 1; do not make it depend on the
unrelated memory, hooks, or Dolt research.

The v0.1.8 slice does not introduce schema, memory, hooks, Dolt, storage
migrations, or a new full Gig. The operator overrode the prior “no new frontend”
line on 2026-09-22 for the local Vite UI in the [find-jobs functional
roadmap](roadmaps/v0.1.8-find-jobs-functional-roadmap.md); this backlog does not
itself change workpad storage or authorize implementation.
The separately recorded RUNTIME-01 scope addition is the operator's explicit
v0.1.7 decision, not an implicit consequence of listing a research spike.

## External tooling follow-ups

- [ORCA-01 — Reliable worker completion delivery](../followups/ORCA-01-worker-completion-delivery.md).
  Deferred Orca issue, not a GigAI v0.1.8 product requirement.

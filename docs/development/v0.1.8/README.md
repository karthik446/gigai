# GigAI v0.1.8 — Backlog

**Status:** Delivery planned for after v0.1.7 Scout. Spike 6's initial pilot is
complete; narrow local execution/comparison moved into v0.1.7
[RUNTIME-01](../v0.1.7/goals/RUNTIME-01-local-execution-and-backend-comparison.md).
Broader local-model research remains here.

## Release roadmap

[v0.1.8 Scout search-first release roadmap](roadmaps/v0.1.8-scout-search-first-roadmap.md)
— recorded 2026-09-21, draft pending refinement. The bounded release plan:
promise, scope, execution days, and controls. Activates only after v0.1.7
ships.

## Delivery tasks

| ID | Task | Status |
| --- | --- | --- |
| V018-01 | [Understandable workpads, project/Gig navigation, and readable artifacts](tasks/V018-01-workpad-navigation-and-readable-artifacts.md) | Requested 2026-09-08; design and implementation pending |

## Execution directions

- [Scout search-first execution and GigAI local applications](directions/scout-search-first-local-applications.md)
  — recorded 2026-09-20. Immediate job persistence/visibility, independent local
  assessment, requirements matrix, tagged resume revisions, actionable proposals,
  dashboard ranking, portability and optional local FastAPI/React UI. A detailed
  product-direction refinement, not a platform pivot, implemented contract or
  added v0.1.7 scope. Includes proposed execution steps and evidence criteria.

## Research spikes

**First research priority:** [S07 — Execution modes and cost-aware orchestration](spikes/S07-execution-modes-and-cost-aware-orchestration.md),
requested 2026-09-10: policy-constrained setup/role selection, risk-based review,
and event-driven coordination without repeated LLM polling. Existing spike
numbers remain stable; S07 runs first after Scout.

The [existing spike list](spikes/README.md) covers accessibility/onboarding,
effective Gig memory, per-Gig hooks/documentation drift, database versioning,
[schema simplification/redesign](spikes/README.md#spike-5--schema-simplification-and-redesign),
and [local models through Ollama and agent harnesses](spikes/README.md#spike-6--local-models-through-ollama-and-agent-harnesses).
V018-01 is a concrete usability delivery task, not another research-only spike.
Coordinate its design with accessibility Spike 1; do not make it depend on the
unrelated memory, hooks, or Dolt research.

This backlog does not itself change workpad storage or authorize implementation.
The separately recorded RUNTIME-01 scope addition is the operator's explicit
v0.1.7 decision, not an implicit consequence of listing a research spike.

## External tooling follow-ups

- [ORCA-01 — Reliable worker completion delivery](../followups/ORCA-01-worker-completion-delivery.md).
  Deferred Orca issue, not a GigAI v0.1.8 product requirement.

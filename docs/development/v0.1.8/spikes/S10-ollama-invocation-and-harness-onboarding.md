# S10 — Ollama invocation and harness onboarding

**Requested:** 2026-09-21.  
**Status:** Research recorded 2026-09-21; implementation and live proof are
not authorized by this brief and S10 is not a v0.1.7 release gate.  
**Scope:** v0.1.8; no v0.1.7 release gate is added by this research record.

**Current evidence pointer:** [S10 invocation and harness onboarding research](evidence/S10-ollama-invocation-and-harness-onboarding-research.md)
supersedes the earlier brief-only index status. It establishes documented and
repo-verified boundaries plus untested caller/harness gaps; it does not prove
the currently installed route, a live caller, durable completion, or private
data safety.

## Question

What is the simplest supported way to invoke an installed local model through
Ollama, directly from GigAI or through an agent harness, and make the actual
execution route clear to the user?

The operator supplied an Ollama menu screenshot showing a “ChatGPT” entry and
“0 requests this session.” Treat those labels as an observation, not proof of
local execution, model identity, compatibility, or request routing. Establish
what that integration does before recommending it.

## Investigate when scheduled

1. **Interactive invocation:** document the minimum first-use path for an
   already-installed model, how to select it, check readiness, and stop a request.
2. **GigAI invocation:** inspect the existing local adapter, configuration and
   public CLI paths before proposing new code. Trace one bounded structured
   request through model selection, invocation, validation and persisted results.
3. **Agent-harness invocation:** establish which locally available harnesses can
   use Ollama and what actually works: structured output, tool calls, file access,
   context limits, cancellation and iteration. A compatible endpoint is not
   proof that a complete coding-agent workflow is supported.
4. **Desktop integration:** identify what the screenshot's ChatGPT entry means,
   how it is configured, and whether requests use a local or remote backend.
   Separate documented behavior from observed behavior and unknowns.
5. **Privacy and routing:** identify endpoint, model tag/digest, runtime version
   and any cloud/remote routing or fallback. Local inference alone does not
   establish that a harness's tools, search or telemetry stay local. Private
   inputs must not silently fall back to a hosted model.
6. **Operational behavior:** explain timeout, cancellation, unavailable-model,
   malformed-output and retry handling; distinguish no per-call inference fee
   from hardware, energy, search and subscription costs.

Use Scout's saved posting plus explicitly selected resume/preferences revisions
to illustrate the intended assessment flow: structured proposal, validation,
local persistence and dashboard visibility. Research examples must use synthetic
candidate data. This is not permission to access actual resumes or preferences.

## Deliverable and finish line

- A short invocation matrix: interactive CLI, GigAI adapter, agent harness and
  the screenshot's desktop integration; prerequisites, supported features,
  limitations and evidence for each.
- Minimal example commands/configuration grounded in verified versions, clearly
  separated from commands actually executed.
- A reuse/gap inventory for existing GigAI work, a recommended first-use path,
  and separately scoped implementation tasks only where a real gap remains.
- A small proposed synthetic smoke test covering exact model selection,
  structured output validation, failure visibility and no hosted fallback.
  Executing it requires separate authorization; do not claim measured results
  from documentation or earlier unrelated experiments.

Stop at the research recommendation. This brief authorizes no research execution,
model/API calls, downloads, daemon startup, configuration changes, credentials,
production edits or new background jobs.

## Relationship to existing work

Research evidence: [S10 Ollama invocation and harness onboarding research](evidence/S10-ollama-invocation-and-harness-onboarding-research.md).

- [Spike 6](README.md#spike-6--local-models-through-ollama-and-agent-harnesses)
  owns the broader capability/harness research. S10 narrows the practical
  invocation and onboarding question; reuse its evidence rather than restart it.
- [S07](S07-execution-modes-and-cost-aware-orchestration.md) owns execution-mode
  and cost-aware routing policy, not this setup walkthrough.
- [S08](S08-cross-model-decision-evaluation-methodology.md) owns fair Qwen/Luna
  comparison methodology; this spike does not establish model equivalence.
- [S09](S09-local-search-retrieval-capability-sourcing.md) owns search sourcing;
  invoking a local model does not by itself provide fresh job discovery.

Before any implementation or authorized proof, reconcile the actually shipped
v0.1.7 adapter and comparison behavior with its evidence and audit the callers
identified above. Preserve the distinction between implemented code, injected
offline tests, installed-package proof and live execution; do not promote the
adapter's local checks into caller or release acceptance.

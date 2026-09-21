# S07 — Execution modes and cost-aware orchestration

**Priority:** First v0.1.8 spike, before the other full research spikes.  
**Requested:** 2026-09-10.  
**Status:** Recorded; research and implementation not started.

## Problem and intended experience

GigAI should make orchestration economical, not just delegate execution. The
Scout development session exposed repeated coordinator waits/checks, expensive
review cycles, and too much active orchestration while workers ran independently.
This overhead belongs in the evaluation of the orchestration goal graph itself.

A user supplies a task/prompt and a mode; the orchestrator discovers eligible
execution setups, assigns implementation/research/review roles, and chooses the
dependency order and role-specific prompts. The orchestrator may itself run
through Claude, Codex, or another supported setup; policy must not depend on which
agent happens to coordinate. With no explicit mode, investigate an explainable
default selected within the user's configured limits.

Illustrative syntax only, **not an existing or frozen CLI contract**:

```text
gigai run t1 "this is the prompt" --mode local-only
gigai run t1 "this is the prompt" --mode quick-review
```

## Investigate

### 1. Separate eligibility from review effort

The suggested `no_$` name mixes cost, locality, and transport. Compare explicit
constraints with convenient presets; settle naming during the spike.

| Candidate policy | Intended constraint / question to resolve |
| --- | --- |
| `local-only` | Inference stays on explicitly allowed local endpoints; no hosted fallback. Local tools/network access need separate rules. |
| `no-metered-api` | Exclude per-use paid inference APIs; potentially permit configured subscription CLI setups as well as local inference. Not a zero-resource-cost promise. |
| `api-only` | API execution interfaces only. Decide explicitly whether local Ollama HTTP endpoints qualify; API does not necessarily mean paid or remote. |
| `cli-only` | CLI execution interfaces only. A local CLI can still send prompts to hosted inference; this is not a privacy/locality guarantee. |
| `quick-review` | A bounded, lower-overhead review strategy inside the eligible setup pool, not permission to bypass routing or correctness constraints. |

Study whether these should be composable axes rather than mutually exclusive
modes. Keep model licensing/open weights separate from inference location and
billing. The user's Luna/Sonnet/Haiku examples are candidate review setups to
measure, not permanent price rankings or an approved model registry.

### 2. Discover, assign, and explain

- Discover installed/configured setups without launching unnecessary sessions.
  Distinguish detected, authenticated, usable, policy-eligible, and selected.
  Missing credentials or unavailable local capacity must have explicit outcomes.
- Build a role/dependency plan from the prompt, input/output contracts, task risk,
  and mode. Run independent stations together; wake dependent stations from
  completion events. Include acceptance criteria, file ownership, and bounded
  role-specific prompts rather than forwarding an entire coordinator transcript.
- Use deterministic checks first; cheap bounded review where sufficient; stronger
  independent review for authority, privacy, storage, or unresolved findings.
  Define escalation and stopping criteria so review is neither skipped silently
  nor repeated indefinitely. Reuse existing review/verify graph contracts.
- Enforce eligibility in dispatch, retries, reviewers, and fallback—not only in
  the orchestrator prompt. No quiet escalation to paid or hosted inference.
  Model selection does not authorize effects, private-data export, or spending.

### 3. Event-driven coordination and honest estimates

- Dispatch, record a rough ETA/window, and yield. Normal background execution
  must not hold an LLM turn open or require repeated status polling.
- Resume on completion, a worker question/failure, or a user prompt. Use one
  scheduled fallback check after the ETA if no notification arrived, with
  cancellation/deduplication when completion arrives first. An overdue result
  triggers bounded inspection and a revised estimate, not a busy-wait loop.
- Investigate durable wakeups across coordinator exit/restart and lost completion
  messages. If self-wakeup is unavailable, report that honestly and give a
  check-back window; do not substitute repeated sleep calls.
- Separate passive scheduler time from model compute. Record coordinator calls,
  tokens, status reads, retries, duplicated checks and reviews alongside worker
  time, API spend, subscription usage limits, local compute and total wall time.

## Evidence and exit criteria

1. A small policy/CLI proposal with precise mode composition, defaults, precedence,
   fallback and refusal behavior; no ambiguous "free" guarantee.
2. A disposable orchestration prototype using existing GigAI execution and
   review/verify seams, plus fake clock/event tests for completion, lost/duplicate
   notifications, overdue jobs and restart. No repeated LLM polling while idle.
3. Same-task comparisons of routing/review policies with pinned prompts, inputs,
   setup versions, risk classes, acceptance criteria and budgets. Measure useful
   completion, missed defects, escalation, latency and total orchestration
   overhead—not model self-reports or a cheaper worker alone.
4. Negative tests proving every dispatched role/retry respects the selected policy,
   and that missing eligible setups cannot silently switch to forbidden ones.
5. An evidence-backed recommendation and bounded implementation goals, including
   where inexpensive review is enough and where stronger review earns its cost.

## Scope and related work

This is GigAI-level orchestration, not Scout-specific routing. Run the spike after
Scout; it does not expand v0.1.7. The already accepted narrow v0.1.7
[RUNTIME-01](../../v0.1.7/goals/RUNTIME-01-local-execution-and-backend-comparison.md)
local execution/setup comparison remains in scope for that release. Reuse its
evidence and coordinate with [Spike 6](README.md#spike-6--local-models-through-ollama-and-agent-harnesses)
rather than duplicate the backend survey. The existing
[Orca completion-delivery follow-up](../../followups/ORCA-01-worker-completion-delivery.md)
is a concrete failure case, not a reason to require Orca as GigAI's scheduler.

Recording this spike does not run experiments, download models, call paid APIs,
start background jobs, or approve a new execution/consent contract.

# GigAI Review Loop Foundation Spike

**Date:** 2026-08-06
**Status:** research spike complete; implementation not authorized
**Question:** What is the smallest credible way to build GigAI's reusable
review → verify → feedback → address loop across articles, repositories,
spreadsheets, and other reference bundles?

This is a design and evidence spike. It does not add a framework dependency,
change a packaged schema, create a goal, or implement a reviewer. The purpose
is to identify the useful ideas in existing systems and reject unnecessary
platform complexity before the first Review Loop Gig is specified.

## Short answer

GigAI should own a small, artifact-neutral evaluation loop, not a collection of
document-review and code-review products:

```text
sync references
  -> verify input integrity and completeness
  -> produce independent review findings
  -> record human/model feedback decisions
  -> address accepted findings
  -> verify closure against the same findings
  -> final review and terminal decision
```

The unit under review is a **reference bundle plus a Gig contract**. The
contract defines the question, reference roles, rubric, required evidence,
output shape, and completion rules. A document, Git repository, pull request,
CSV, or article set is only an input type; it does not require a different
orchestration loop.

The first implementation should be a local, replayable harness with
JSONL-like cases, deterministic graders, captured traces, and explicit human
feedback. Model judges and provider tools can plug into the same interfaces
later. Do not begin with Temporal, LangSmith, or a hosted annotation platform.

## Method and selection rule

I reviewed current primary documentation and public repositories that show a
concrete implementation pattern, not listicles or generic agent-framework
marketing. A source was useful only if it answered at least one of these:

- how inputs and expected outputs are represented;
- how a solver/reviewer is separated from evaluation logic;
- how traces, intermediate steps, and human feedback are retained;
- how multiple evaluators or model versions are compared;
- how a multi-step workflow survives interruption or scheduling.

The sources are evidence for patterns, not dependencies GigAI is committing to.

## Patterns worth borrowing

### 1. Dataset, solver, scorer: Inspect AI

[Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai) separates an
evaluation Task into a dataset, one or more solvers, and scorers. Its
[task documentation](https://inspect.aisi.org.uk/tasks.html) makes the
separation explicit, and its [scoring model](https://inspect.aisi.org.uk/scoring.html)
supports deterministic and model-backed scorers. It also supports tools,
multi-turn dialogue, and human-in-the-loop behavior.

**Borrow:** keep “what is being evaluated,” “how an actor attempts it,” and
“how the result is scored” as separate objects. This maps cleanly to GigAI's
reference bundle, executor Goal, and evaluator Goal.

**Do not borrow yet:** Inspect's broad benchmark/plugin ecosystem. GigAI needs
one first-party loop and a small fixture corpus before it needs a benchmark
registry.

### 2. Traces plus offline/online evaluators: LangSmith

LangSmith's [evaluation concepts](https://docs.langchain.com/langsmith/evaluation-concepts)
distinguish offline datasets with reference outputs from online runs without
reference answers. Its [evaluator binding](https://docs.langchain.com/langsmith/bind-evaluator-to-dataset)
supports code, LLM-as-judge, composite, and human evaluators. Its
[human-feedback alignment flow](https://docs.langchain.com/langsmith/improve-judge-evaluator-feedback)
turns labeled examples into a better judge.

**Borrow:** feedback is structured data attached to a run/finding, not a note
lost in chat. Keep offline regression cases separate from live/reference-free
monitoring. Calibrate model judges against a labeled set.

**Do not borrow yet:** a hosted tracing/control plane. GigAI's existing journal,
RunDetails, evidence references, and private workpad are the local authority.

### 3. Trace-level observability: OpenAI Agents SDK

The [OpenAI Agents SDK tracing model](https://openai.github.io/openai-agents-python/tracing/)
represents a workflow as a trace containing spans for generations, tool calls,
handoffs, guardrails, and custom events. A [trace](https://openai.github.io/openai-agents-python/ref/tracing/traces/)
links related operations with IDs and metadata.

**Borrow:** one Review Loop Run needs a stable trace identity, nested Goal
events, provider/tool metadata when permitted, and a way to exclude or redact
sensitive inputs. The trace is evidence; it is not itself the evaluation.

**Do not borrow yet:** provider-specific trace export as the source of truth.
GigAI must remain inspectable when no provider is configured.

### 4. Declarative assertions and CI: Promptfoo

[Promptfoo](https://github.com/promptfoo/promptfoo) demonstrates a lightweight
CLI/config approach for prompt, agent, and RAG cases, with deterministic
assertions, model-graded assertions, comparisons, red-team probes, and CI
integration. Its [agent-skill guidance](https://www.promptfoo.dev/docs/integrations/agent-skill/)
warns against generic prompts that omit the actual source material and against
using an LLM judge when a cheap deterministic assertion is sufficient.

**Borrow:** cases should be declarative where possible; use exact/structural
checks before paying for a model judge; keep source material explicit in the
grader input; rerun only failed cases during iteration.

**Do not borrow yet:** Promptfoo's config language or Node runtime. GigAI's
case and evidence formats should remain native to its canonical contracts.

### 5. Model-graded evals and meta-evals: OpenAI Evals

[OpenAI Evals](https://github.com/openai/evals) separates an eval definition,
dataset, completion/solver, and metrics. Its [model-graded templates](https://github.com/openai/evals/blob/main/docs/eval-templates.md)
support open-ended answers, and its guidance recommends a meta-eval with
human-provided labels to test whether the judge itself makes the right call.
The API also exposes [string, similarity, and score-model graders](https://platform.openai.com/docs/api-reference/graders).

**Borrow:** version eval cases and rubrics; distinguish the actor from the
grader; test the grader against adjudicated examples; do not pretend an
LLM-as-judge is ground truth.

**Do not borrow yet:** a provider-hosted eval run as the only evidence. Local
deterministic checks and sanitized artifacts must remain runnable offline.

### 6. Durable workflows and schedules: Temporal

[Temporal](https://github.com/temporalio/temporal) uses event-sourced workflow
history, deterministic workflow code, activities for side effects, signals,
timers, and recovery. Its [Python samples](https://github.com/temporalio/samples-python)
include human signals, schedules, replay, and OpenAI-agent integrations.

**Borrow later:** separate deterministic orchestration from side-effecting
activities; model feedback, approvals, and scheduled triggers as durable
signals/events; use replayable history for recovery.

**Do not borrow now:** introducing an external durable-execution service before
GigAI's own Run/Goal/event contracts have stabilized. Phase 3 should first prove
the loop locally; scheduling can initially be an external trigger that starts a
new GigAI Run.

## What the first GigAI loop needs

The following names are conceptual until a contract amendment or goal adopts
them. They describe ownership, not a request to add fields now.

### Reference bundle

An immutable, content-addressed set of inputs with roles and provenance:

- article A / article B;
- repository or pull request snapshot;
- CSV or spreadsheet version;
- prior accepted output, when comparison is part of the Gig.

The bundle records bytes, media type, origin/locator, acquisition time, and
digest. A later Run must be able to prove exactly what it reviewed.

### Review contract

The Gig definition supplies:

- the user question and intended decision;
- reference roles and allowed transformations;
- review criteria and severity levels;
- required evidence/citation shape;
- required output sections or structured fields;
- questions that must be answered before execution;
- closure rules for accepted findings;
- cycle and escalation limits.

This is how agents ask the right questions without inventing a new workflow.
When required context is missing or ambiguous, the agent emits a structured
clarification with the missing fact, why it blocks a criterion, and which
reference or contract clause exposed the problem. The user's answer becomes a
durable feedback/input artifact.

### Finding

A finding is not “the model disliked the answer.” It should carry:

- stable finding ID and severity;
- criterion/rubric reference;
- claim or output location;
- supporting reference spans or evidence artifacts;
- reviewer identity/model and trace ID;
- confidence and disagreement metadata;
- state: open, accepted, rejected, deferred, or resolved.

### Feedback and address pass

Feedback is an explicit decision over findings. It is not the same as a review
and not the same as a revision. The address pass consumes accepted findings,
produces a new output/version, and preserves the parent relationship. A later
verification pass must check each accepted finding individually.

### Evaluator stack

Use the cheapest trustworthy check first:

1. schema/shape/digest and citation existence;
2. deterministic assertions and domain calculations;
3. reference-grounded checks;
4. calibrated model judge;
5. human adjudication for disagreement, high severity, or missing ground truth.

Every score should identify its evaluator version and input trace. A single
aggregate score is not enough to explain why a Gig passed or failed.

## Proposed first Review Loop Gig

The first dogfood Gig should be a generic review loop, not a resume or finance
special case:

```text
sync-refs
  -> verify references and review contract
  -> independent review pass
  -> deterministic evidence/coverage verification
  -> feedback/adjudication
  -> address accepted findings
  -> closure verification
  -> final review and terminal decision
```

Run it against a small matrix:

| Case | References | Seeded challenge |
|---|---|---|
| research-pair | two research articles | conflicting claims and missing citation |
| climate-pair | two climate articles | timeframe/unit ambiguity and unsupported synthesis |
| pull-request | PR diff plus repository snapshot | behavior regression and incomplete test evidence |
| repository-review | full repository plus stated question | scope discovery and high-risk omission |
| spreadsheet-review | current plus prior CSV | schema drift and unexplained metric change |

The loop remains identical. Only the reference adapters, rubric, and
deterministic verifiers vary.

## Evaluation plan for the loop itself

Before provider comparisons, build seeded fixtures that assert:

- every seeded defect is found or explicitly marked unanswerable;
- findings point to real reference bytes, not invented citations;
- duplicate findings are merged without losing provenance;
- reviewer disagreement remains visible and is adjudicated explicitly;
- accepted feedback becomes an addressable revision requirement;
- closure verification catches a partially addressed finding;
- rejected feedback is preserved without being silently reapplied;
- missing context produces a useful blocking question;
- cycle limits stop endless review/repair loops;
- a repeated Run is byte/replay comparable except for explicitly variable fields.

The first eval corpus should be small and curated—roughly 5–10 cases per
critical behavior, with held-out cases for judge calibration. This follows the
practical dataset guidance in [LangSmith's evaluation concepts](https://docs.langchain.com/langsmith/evaluation-concepts)
without importing LangSmith.

## Efficient build recommendation

Build a thin native harness first:

1. repository-local case files and immutable reference bundles;
2. a solver interface that can represent Codex CLI, Claude CLI, an API model,
   or a deterministic fixture;
3. a verifier interface with deterministic and model-backed implementations;
4. journaled findings, feedback decisions, revisions, and closure results;
5. a local runner that emits one machine-readable report and one human review;
6. replay and mutation tests before live provider runs.

Use external projects as adapters or inspiration, not as the authority:

- Inspect is the strongest conceptual model for dataset/solver/scorer;
- OpenAI Evals is useful for model-graded and meta-eval patterns;
- Promptfoo is useful for cheap declarative assertions and CI ergonomics;
- LangSmith is useful evidence for trace-plus-human-feedback workflows;
- Temporal is a later option if local recovery/scheduling becomes inadequate.

Do not make “multi-agent debate” the foundation. Independent reviewers plus a
separate verifier and explicit adjudication are easier to test, cheaper to run,
and less likely to turn agreement into false confidence.

## Decisions this spike recommends

1. Treat the Review Loop as a first-class Gig pattern, not a code-review
   feature.
2. Keep review, verification, feedback, address, and final acceptance as
   distinct durable steps.
3. Make reference bundles and findings content-addressed and replayable.
4. Require deterministic verifiers before model judges.
5. Preserve disagreement and human decisions as evidence.
6. Start with local fixtures and CLI/API solver adapters; defer hosted eval and
   durable workflow infrastructure.
7. Use a small explicit cycle cap for the first Gig; do not allow unbounded
   self-improvement loops.

## Open decisions before a goal is written

- Is the first review output a report, a revised artifact, or both?
- Which finding states and feedback decisions are contractually required?
- Should a clarification pause the Run immediately or be collected until the
  review pass completes?
- What is the initial cycle cap: one address pass, two, or user-selected?
- Which data may be sent to a provider, and what must be redacted first?
- Are recurring schedules external triggers initially, or a later GigAI
  artifact?

## Research URLs

- OpenAI Evals: <https://github.com/openai/evals>
- OpenAI Evals templates: <https://github.com/openai/evals/blob/main/docs/eval-templates.md>
- OpenAI graders API: <https://platform.openai.com/docs/api-reference/graders>
- OpenAI Agents SDK tracing: <https://openai.github.io/openai-agents-python/tracing/>
- OpenAI Cookbook iterative repair loop: <https://github.com/openai/openai-cookbook/blob/main/examples/codex/Build_iterative_repair_loops_with_Codex.ipynb>
- Anthropic agent evaluation guidance: <https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents>
- Inspect AI: <https://github.com/UKGovernmentBEIS/inspect_ai>
- Inspect tasks: <https://inspect.aisi.org.uk/tasks.html>
- Inspect scoring: <https://inspect.aisi.org.uk/scoring.html>
- LangSmith evaluation concepts: <https://docs.langchain.com/langsmith/evaluation-concepts>
- LangSmith evaluator binding: <https://docs.langchain.com/langsmith/bind-evaluator-to-dataset>
- LangSmith human-feedback alignment: <https://docs.langchain.com/langsmith/improve-judge-evaluator-feedback>
- Promptfoo: <https://github.com/promptfoo/promptfoo>
- Promptfoo CI integration: <https://www.promptfoo.dev/docs/integrations/ci-cd/>
- Temporal durable execution: <https://github.com/temporalio/temporal>
- Temporal Python samples: <https://github.com/temporalio/samples-python>
- SWE-bench: <https://arxiv.org/abs/2310.06770>
- Ragas evaluation code: <https://github.com/explodinggradients/ragas>


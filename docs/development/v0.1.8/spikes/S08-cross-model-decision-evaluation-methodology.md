# Spike 8 — Cross-model decision evaluation methodology

**Requested:** 2026-09-20; backlog, not started. Research only; no adoption,
model call, paid API usage, or runtime change is authorized by this brief.

**Question:** How do we run the *same* Scout-shaped decision task fairly across
different execution setups — local Qwen/Ollama, hosted GPT, Luna/Codex, and
Claude Sonnet — and get a comparison that is actually trustworthy, rather than
a demo that favors whichever backend happens to match the grader's own style?

## Motivation and scope

[RUNTIME-01](../../v0.1.7/goals/RUNTIME-01-local-execution-and-backend-comparison.md)
and Spike 6 already own local-model *integration* mechanics (Ollama endpoint,
adapter, tool-calling/structured-output capability). This spike is narrower and
sits one level up: given that a task can run on more than one backend, what
methodology makes the comparison mean something? Today that methodology is
undefined — the operator flagged this explicitly as "loosely defined."

The [Jev structured-decision research](evidence/jev-typesafe-ai-structured-decisions-research.md)
already gives a *decision contract* discipline (typed answer, evidence refs,
abstention, confidence ≠ correctness). This spike reuses that contract as the
output shape being compared; it does not revisit whether to adopt Jev/TypeSafe.

This is comparison-of-setups research, matching the existing pilot's framing:
Qwen/local, GPT, Luna/Codex, and Claude Sonnet are complete configured setups
(model + harness + tools + retries), not interchangeable model weights. A fair
harness records that asymmetry rather than hiding it.

## Investigate

- **Task/case freezing — identical inputs, not identical prompts.** A small
  fixed set (target: 15–30 cases, not hundreds) of representative Scout-shaped
  tasks — grounded extraction, requirement-matrix assessment
  (`supported`/`unclear`/`unsupported`/`not_applicable`), evidence sufficiency,
  question generation. Freeze the *source bytes and task criteria* identically
  across backends: same state, same question/criteria text, same case IDs.
  Do not require a byte-identical prompt string — a documented, harness-specific
  wrapper (system framing, tool-call scaffolding, output-format instructions
  needed to get a given backend to emit the shared decision envelope) is
  allowed and expected to differ per setup. Record each backend's actual
  wrapper text alongside its results so the boundary between "same task" and
  "how each harness asks for it" is inspectable, not implicit.

- **Gold-label sourcing and the circularity risk.** Split cases into two kinds,
  and do not blur them:
  - *Objective cases*: schema validity, "does this evidence ID exist in the
    posting," literal keyword presence. Gold answer is mechanical — no model
    involved in producing it.
  - *Judgment cases*: "does this candidate meet the stated requirement," "is
    this fairly `unclear` vs. `unsupported`." A frontier model (Claude/GPT) may
    *draft* candidate labels for these to save time, but a human must adjudicate
    and can override every judgment-case label before it is frozen as gold —
    especially the deliberately tricky `unclear`/`insufficient`/known-bad cases.
  Skipping human adjudication turns the benchmark into "agreement with
  Claude/GPT," which structurally favors frontier-family models over Qwen for
  reasons unrelated to correctness. Record who drafted each label and who
  adjudicated it; this adjudication step is real spike time, not free.

  Human adjudication reduces circularity; it does not remove all bias. Where
  practical, adjudicate blind to which backend produced a candidate answer
  (adjudicate the *draft label* against the source material, not a specific
  backend's output). Allow the gold set to record genuinely ambiguous cases
  and more than one acceptable answer rather than forcing a single "correct"
  string, and log adjudicator disagreement instead of silently resolving it.
  Treat 15–30 cases as enough for a useful pilot and to shake out the
  methodology — not as a sample size that supports a reliable broad model
  ranking or a calibration study; say so explicitly in the spike's output.

- **Setup-parity boundary.** Decide explicitly what must be held constant
  (task text, case IDs, output contract, grader, decision thresholds) versus
  what is allowed to differ per backend (tool access, prompt adaptation, retry
  behavior) — and log the difference per setup rather than assuming parity.
  Reuse the existing pilot's setup-label table
  (`jev-hosted-decision-api` / `luna-codex-cli` / `qwen38-ollama-local`) as a
  starting point and extend it for GPT and Claude Sonnet setups.

- **Grading mechanism.** Prefer a deterministic grader (schema validity,
  evidence-ID support, enum correctness) wherever the task allows it. Where an
  LLM-judge step is unavoidable, name which model judges, disclose that choice
  as a variable in the report, and check it against the human-adjudicated gold
  set rather than trusting judge/candidate agreement alone.

- **Reported axes stay separate.** Quality, cost, and latency are three
  different numbers, not one blended score. A free-but-worse and a
  paid-but-better setup must both be reportable without collapsing into a
  single "winner." Reuse the pilot's per-case/per-attempt logging fields
  (resolved model identity/digest, retries, cold/warm timing, token usage).

- **Existing harness patterns to mine.** Survey concrete mechanics from:
  - DeepSeek's public evaluation/agent harness material (task framing, judge
    design, retry/logging conventions) — cite specific repos/docs, not general
    reputation.
  - TypeSafe's `system-one-adapter-python` (explicitly built as a comparison
    substitute) for its discrete/probability answer modes, malformed-output
    retry handling, and per-attempt debug history.
  - Any other public open agent-eval harness worth citing (e.g. established
    LLM-eval frameworks), scoped to what's directly reusable for a Scout-shaped
    typed-decision task, not a general literature review.

- **Abstention and confidence handling.** Reuse the Jev-derived abstention
  contract (`refused_or_failed` / `abstain` / `uncertain` / `typed_decision`)
  as the shared output envelope so backends are compared on the same decision
  states, not free-text answers requiring separate parsing per backend.

## Starting points in this repository

- [Jev structured-decision research](evidence/jev-typesafe-ai-structured-decisions-research.md)
  — decision contract, confidence/calibration cautions, proposed typed-decision
  schema and frozen-case/grader outline this spike extends into a live
  cross-model comparison.
- [Qwen3.8 Scout local-model pilot](evidence/qwen38-scout-local-eval.md) and its
  [coordinator review](evidence/qwen38-scout-coordinator-review.md) — existing
  setup-label conventions, logging fields, and named weaknesses (leading
  prompts, weak semantic checks) this spike should not repeat.
- [RUNTIME-01](../../v0.1.7/goals/RUNTIME-01-local-execution-and-backend-comparison.md)
  — the narrow v0.1.7 local-backend/execution-setup comparison this spike's
  methodology should stay compatible with, without expanding its v0.1.7 scope.
- [Scout search-first direction](../directions/scout-search-first-local-applications.md)
  — names this spike's methodology gap directly and lists the source tasks
  (extraction, assessment, questions) the frozen case pack should draw from.

## Expected output

A written methodology: frozen case-pack format, gold-labeling process with
explicit human-adjudication step, setup-parity recording table, grader design
(deterministic-first), and the shared decision-envelope schema. Include a
worked example on 3-5 sample cases to prove the format is usable, not just
specified. End with an evidence-backed recommendation on what a first real
comparison run would need (which setups, how many cases, estimated adjudication
time and any paid-call cost) and open questions. No live cross-backend
comparison, paid API call, or model adoption follows from this brief.

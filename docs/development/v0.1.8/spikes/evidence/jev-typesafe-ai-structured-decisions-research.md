# Jev / TypeSafe AI: structured-decision research

**Date:** 2026-09-20  
**Scope:** v0.1.8 research only; no adoption, activation, SDK installation, paid call, account signup, benchmark run, or model call was performed.  
**Question:** What does Jev document as a typed-decision system, and what can GigAI learn for a future local structured-output comparison?

## Executive summary

Jev is TypeSafe AI's hosted “System One” model. Its public contract is narrow: a caller sends a `state` and named `questions`; the API returns one typed answer per question. The documented primitives are `choice` (one option plus a distribution and confidence), `score` (a probability-weighted position on an ordered rubric plus a distribution and confidence), and `noul` (a yes/no probability from 0 to 1). Questions in one request see the same state and are evaluated independently; the caller composes answers and owns the workflow logic. [Official introduction](https://docs.typesafe.ai/introduction), [API reference](https://docs.typesafe.ai/api), [primitives](https://docs.typesafe.ai/primitives) (accessed 2026-09-20).

The useful GigAI concept is a small, inspectable decision boundary between messy input and deterministic code. The typed response reduces parsing failure; it does not prove that the decision is correct. A probability or `confidence` field is a model-produced uncertainty signal whose calibration must be checked on the task distribution; it is not a correctness certificate, source citation, or hiring probability. The caller still needs a frozen contract, evidence policy, abstention behavior, authorization gate, and independent grader.

The Jev interface is suitable as a conceptual reference for structured local output, but the public material reviewed here does not establish local weights, offline deployment, an open implementation of the model, evidence-reference output, rationales, a Jev-compatible Ollama backend, or accuracy on GigAI tasks. TypeSafe's public GitHub organization does provide SDKs, skills, and a public LLM-backed `system-one-adapter-python`; that adapter is explicitly a comparison substitute, not Jev's model implementation. [TypeSafe GitHub organization](https://github.com/typesafe-ai), [adapter README](https://github.com/typesafe-ai/system-one-adapter-python), [models](https://docs.typesafe.ai/models).

## What is documented versus inferred

| Topic | Documented | Inference / boundary |
| --- | --- | --- |
| Product shape | Hosted Jev/System One endpoint; input `state` plus typed questions; structured answers. | This resembles a typed classifier/decision service, not a general agent or tool executor. |
| Choice | Selects one caller-defined option; returns the selected key, per-option probabilities, and `confidence`. | A `choice` is a contract-constrained label, not evidence that the option is true. |
| Score | Rates an ordered caller-defined rubric; returns a fractional/probability-weighted score, legend, probabilities, and `confidence`. | A score is meaningful only relative to the caller's rubric; “2.9” is not a universal virality scale. |
| Noul | Returns the probability that a yes/no statement is true; no separate confidence field. | A Noul value is a probability-like model output, not a calibrated fact until independently evaluated. |
| Parallel questions | Questions in one request see the same state and are independently evaluated. | The caller should not assume one question's answer is context for another unless it makes a follow-up request. |
| Typed output | Values stay within supplied options/levels and do not require prose parsing. | Schema/type validity is distinct from semantic correctness and grounding. |
| “Calibrated” | TypeSafe describes RLCD as training for decisions and probabilities; its primer explains group calibration as probabilities matching outcome frequencies. | Marketing or documentation claims are hypotheses for GigAI's task-specific validation, not an acceptance result. |
| Evidence | Public API examples contain state, question criteria, answer values, distributions, and usage. | No reviewed Jev response contract emits source IDs, citations, extracted spans, or rationales. A caller must add and verify evidence separately. |
| Workflow action | Docs show code branching on an answer and give confidence-gated routing patterns. | A model route/gate is not authorization to mutate records, spend money, apply for a job, run a tool, or finalize an application. |
| Deployment | Official docs describe `api.typesafe.ai/v1/systemone`, SDKs, and hosted model IDs. | No public local-weight or self-hosting path was found in the reviewed primary sources. |

## Viral-post screenshot: careful interpretation

The operator supplied the following screenshot description: `choice hook_type = bold_claim (52%)`; `bool opens_loop = yes (91%)`; `bool first_line_number = yes (99%)`; `choice evidence = claimed (94%)`; `score virality = 2.9, very strong`; and “real API 325ms”. The independent playground page documents a similar viral-post preset and the three primitives, but its disclaimer says the site is independent of TypeSafe AI and that its playground makes live, rate-limited calls. [Independent Jev playground](https://jevtypesafeai.com/), [independent API guide](https://jevtypesafeai.com/how-to-use) (accessed 2026-09-20).

What the display plausibly means, if its UI maps to the official primitives:

- `hook_type` is a Choice over labels such as `open_loop`, `bold_claim`, `story`, `data`, and `none`; `bold_claim (52%)` is likely the selected option's displayed probability. It is not a measured 52% chance that the post will go viral.
- The screenshot's `bool` labels are not the official API primitive name in the reviewed docs. The closest documented type is `noul`: a yes/no probability. Thus `yes (91%)` and `yes (99%)` should be described as displayed model probabilities, not verified Boolean facts.
- `evidence = claimed (94%)` is a Choice label about how the claim is backed according to the question's rubric. It is not an evidence reference, retrieved source, or model rationale.
- `score virality = 2.9` could be a probability-weighted Score over caller-defined levels; “very strong” is likely a UI rubric label. The number has no meaning without the exact criteria and legend.
- “Real API 325ms” is a demo observation for that request/environment. It is not a reproducible GigAI benchmark, latency SLO, or evidence of semantic quality.

The screenshot's promotional content is not evidence of product accuracy. Do not report those values as validated labels, calibration, or predictive performance without the frozen input, exact question definitions, model identity, repeated runs, grader, and held-out cases.

## Actual documented architecture and dataflow

```text
GigAI caller / SDK / HTTP client
  ├─ state: string | JSON object | array of text
  ├─ model: jev-latest or pinned version (for example jev-1.13.0)
  └─ questions: named Choice / Score / Noul definitions
        │ HTTPS POST + Bearer API key
        ▼
TypeSafe API: POST https://api.typesafe.ai/v1/systemone
        │ hosted Jev/System One evaluation
        ▼
response: resolved model + answers keyed by question IDs + usage
        │
        ▼
GigAI wrapper validates contract, records provenance, applies thresholds,
requests review/abstention, and only then invokes separately authorized effects
```

This is the smallest architecture supported by the public contract. The API says the question ID is used to key the response and is not sent to the underlying model; the question's full `instructions` and criteria are the semantic contract. `state` can be a string, object, or array of text, with images/audio/video unsupported in the current model documentation. [API reference](https://docs.typesafe.ai/api), [state](https://docs.typesafe.ai/concepts/state), [models](https://docs.typesafe.ai/models).

The docs recommend atomic questions and composition in caller code. For example, separate “does the resume state X?” from “does the policy support Y?” rather than asking the model for a broad course of action. This is compatible with GigAI's existing separation between input records, typed output, evidence, and authorized effects, but it does not replace those records or gates. [Atomic questions](https://docs.typesafe.ai/primitives), [confidence guidance](https://docs.typesafe.ai/confidence), [patterns](https://docs.typesafe.ai/patterns).

## Concrete API/schema example (paraphrased)

The official HTTP shape is approximately:

```json
{
  "model": "jev-1.13.0",
  "state": {
    "ticket": "Customer was charged twice and requests a refund.",
    "policy": "Duplicate charges are refundable."
  },
  "questions": {
    "route": {
      "type": "choice",
      "instructions": "Which team should handle this ticket?",
      "criteria": {
        "billing": "Payments, invoices, refunds",
        "technical": "Bugs or integrations",
        "other": "Anything else"
      }
    },
    "policy_supports_refund": {
      "type": "noul",
      "instructions": "Does `policy` support the refund requested in `ticket`?",
      "criteria": {
        "true": "The policy supports the requested refund",
        "false": "The policy does not support it or is insufficient"
      }
    },
    "urgency": {
      "type": "score",
      "instructions": "How urgent is this ticket?",
      "criteria": ["routine", "today", "urgent", "critical"]
    }
  }
}
```

A corresponding response is approximately:

```json
{
  "model": "jev-1.13.0",
  "answers": {
    "route": {
      "type": "choice",
      "choice": "billing",
      "probabilities": {"billing": 0.88, "technical": 0.07, "other": 0.05},
      "confidence": 0.81
    },
    "policy_supports_refund": {"type": "noul", "noul": 0.94},
    "urgency": {
      "type": "score",
      "score": 2.1,
      "legend": {"0": "routine", "1": "today", "2": "urgent", "3": "critical"},
      "probabilities": {"0": 0.0, "1": 0.1, "2": 0.8, "3": 0.1},
      "confidence": 0.72
    }
  },
  "usage": {"input_tokens": 210, "output_tokens": 30}
}
```

This paraphrases the official request and response examples; field names and semantics are documented in the [API reference](https://docs.typesafe.ai/api) and [quick start](https://docs.typesafe.ai/introduction/quickstart). The examples establish an output shape, not correctness. A GigAI wrapper should additionally record schema revision, input digest, question/criteria digest, request attempt, resolved model, wall time, and the policy that interprets the answer.

## Semantics that must not be conflated

### Type enforcement versus correctness

Jev constrains a Choice answer to caller-supplied labels and a Score answer to caller-supplied levels; the API also documents validation errors for malformed requests. This addresses “can the program parse the value?” It does not address “did the model understand the input, use the right source, or apply the rubric correctly?” A perfectly valid `choice: "billing"` can still be wrong.

For local Qwen/Ollama, native structured output or an equivalent parser can enforce the same outer JSON contract. That makes structural comparison possible, not model equivalence. The local adapter must independently reject missing fields, extra/invalid labels where the contract forbids them, truncation, wrong model identity, and invalid probabilities; the grader must still score meaning and evidence.

### Probability, confidence, and calibration

Official docs distinguish the full `probabilities` distribution from `confidence`: Choice and Score confidence is a statistic derived from that distribution; Noul has only its yes-probability. The ML primer describes calibration as a group property (for example, predictions assigned 0.8 should be correct about 80% of the time across many comparable cases), not a guarantee for one answer. [Confidence](https://docs.typesafe.ai/confidence), [AI primer](https://docs.typesafe.ai/introduction/machine-learning-primer).

Therefore:

- Do not call a one-off `0.94` a verified 94% chance of truth.
- Do not treat a peaked distribution as a source-backed rationale.
- Measure reliability by criterion and probability bucket on held-out labeled cases; include abstention and selective-coverage curves where thresholds are used.
- For Qwen, distinguish a probability emitted under a prompt/schema from a calibrated probability. If it only emits a label, record no probability rather than inventing one.

### Evidence references versus rationales

The documented Jev answer fields are typed values, distributions, confidence (Choice/Score), score legends, and usage. They do not include citations or spans. A rationale, even when available from another model, is an explanation artifact—not proof that a claim is supported. For GigAI, source IDs and exact input bytes should be selected and retained by the harness, and an independent grader should verify that each decision's cited source IDs actually support the criterion.

### Routing versus authorized actions

The docs use routing and confidence gates as software patterns. In GigAI, a model output may recommend `route = "review"` or `allow = false`; it must not itself create an application, mutate a user-owned Gig, run a shell command, send a message, or spend money. A separate deterministic policy and explicit user/authority gate must decide whether an effect is authorized. “Candidate fit” or “job fit” must never silently become an invented probability of hiring.

## Confidence and abstention contract for GigAI

Jev's docs recommend high/medium/low confidence paths: automatic handling for low-stakes high-confidence cases, confirmation or review for medium confidence, and no action/fallback for low confidence. Thresholds are domain- and risk-dependent. [Confidence-gated behavior](https://docs.typesafe.ai/confidence).

For a future GigAI decision wrapper, make abstention explicit rather than treating every typed answer as actionable:

```text
if transport/error/timeout/truncation/schema_invalid:
    outcome = refused_or_failed; no decision is published
elif evidence_missing or question_out_of_scope:
    outcome = abstain; route to review or request more evidence
elif confidence/probability below the frozen task threshold:
    outcome = uncertain; route to review
else:
    outcome = typed_decision; apply only a separately authorized deterministic effect
```

The threshold is a policy parameter to evaluate, not a value to infer from marketing. Record the threshold, model output, reason for abstention, and whether a retry occurred. Retrying a malformed response is a transport/schema recovery mechanism; it is not permission to overwrite a low-confidence semantic result.

## Public implementation, deployment, and unknowns

### Publicly visible

- TypeSafe publishes an official Python SDK and an official TypeScript/JavaScript SDK, both MIT repositories in the public organization. The Python quickstart exposes `TypeSafeClient`, typed `Choice`/`Score`/`Noul` questions, sync/async client paths, and `TYPESAFE_API_KEY`. [Python SDK repository](https://github.com/typesafe-ai/typesafe-sdk-python), [Python SDK docs](https://docs.typesafe.ai/sdk/python), [TypeSafe GitHub organization](https://github.com/typesafe-ai).
- TypeSafe publishes `system-one-adapter-python`, a public MIT package that presents a drop-in `system_one`-style API backed by selected LLM providers. Its README explicitly describes it as useful for comparing TypeSafe against an LLM and documents native structured-output mode, discrete/probability answer modes, malformed-output retries, and per-attempt debug history. This is an open comparison adapter, not an open Jev implementation and not evidence that its provider outputs match Jev. [Adapter repository](https://github.com/typesafe-ai/system-one-adapter-python).
- The public TypeSafe organization also lists agent skills and integrations. These are caller tooling; they do not expose Jev weights or make a local model available. [Organization repositories](https://github.com/typesafe-ai).

### Not established by reviewed primary sources

- No Jev model weights, architecture implementation, training data, reproducible benchmark suite, or local/offline runtime was found.
- TypeSafe describes “System One”, a new architecture/sampler, and RLCD at a high level, but does not publish enough implementation detail here to reconstruct or independently verify proprietary internals. [TypeSafe manifesto](https://typesafe.ai/), [AI primer](https://docs.typesafe.ai/introduction/machine-learning-primer).
- No API field for evidence citations, retrieved spans, rationales, abstain/refusal reason, or tool authorization was found in the reviewed request/response reference.
- The model page lists text-only input, hosted model IDs, context/rate-limit details, aliases, and account-level data-handling claims; it does not document self-hosting or a local Ollama backend. Aliases can move, so a production comparison should pin a version and log the response's resolved model. [Models](https://docs.typesafe.ai/models).
- The independent `jevtypesafeai.com` site lists gateways and a hosted proxy, but labels itself unaffiliated; those routes should not be treated as TypeSafe's source of truth for deployment or pricing. [Independent access page](https://jevtypesafeai.com/get-jev).

## GigAI comparison: Jev-shaped local decisions versus complete setups

This is a future evaluation design, not an execution request. It should compare complete, configured setups rather than claim that Luna, Jev, and Qwen are equal models:

| Setup label | Inference path | What must be held comparable | What must remain explicit |
| --- | --- | --- | --- |
| `jev-hosted-decision-api` | TypeSafe's hosted Jev endpoint, pinned model ID, official SDK or direct HTTP | Same frozen synthetic state, question contract, case IDs, grader, decision thresholds, and wall-time boundary | Remote service/network, API version, account/rate limits, retries, usage/cost, resolved model |
| `luna-codex-cli` | Luna/Codex CLI or approved harness, as actually configured later | Same logical cases and outcome contract; prompt/tool adaptation is recorded, not hidden | CLI version, model identity/digest if available, tools/context, permissions, retries, hosted/local destination, observed activity |
| `qwen38-ollama-local` | Installed `qwen3.8:latest` through explicit loopback Ollama harness | Same logical cases, grader, and output contract where feasible | This is only the user's setup label until the exact observed digest is recorded; Ollama version, quantization, context/thinking settings, hardware, warm/cold timing, tool set |

The existing GigAI pilot describes Qwen as a loopback Ollama setup with a recorded digest and synthetic fixtures, and explicitly rejects a Luna-equivalence claim. RUNTIME-01 similarly says to compare execution setups while recording tool asymmetry, prompts, retries, hardware, and identities. This research reuses those boundaries; it does not change v0.1.7 scope. [Pilot evidence](qwen38-scout-local-eval.md), [RUNTIME-01](../../../v0.1.7/goals/RUNTIME-01-local-execution-and-backend-comparison.md), [S07](../S07-execution-modes-and-cost-aware-orchestration.md).

### Proposed independent typed decision contract

Use a small synthetic “posting evidence triage” task, not the promotional viral-post example and not real candidate data. Keep the output contract independent of any one provider:

```json
{
  "schema_version": "gigai.typed_decision.v1",
  "case_id": "posting-eligibility-001",
  "decisions": {
    "requirement_status": "supported|unclear|unsupported|not_applicable",
    "evidence_sufficiency": "sufficient|insufficient",
    "needs_human_review": true
  },
  "evidence_refs": ["posting-001", "resume-001"],
  "abstention": null
}
```

The contract deliberately separates:

- a categorical requirement status from any claim about hiring likelihood;
- evidence sufficiency from a model's explanation;
- a review recommendation from authorization to mutate application state;
- `evidence_refs` from Jev's documented answer fields.

For Jev, ask atomic typed questions and have the wrapper/grader handle the outer envelope and evidence-reference policy. Do not pretend Jev returned source references if it did not. For Qwen or Luna/Codex, native structured output may produce the envelope, but the same independent validator and evidence grader must check it.

### Frozen cases and grader

Before a live evaluation, freeze a small versioned pack containing:

1. Synthetic posting/resume records with stable source IDs and exact bytes.
2. Gold decisions per criterion, including `unclear` and `insufficient` cases.
3. Per-criterion evidence requirements: accepted source IDs, forbidden invented claims, and whether a cited span supports the criterion.
4. Known-bad outputs: wrong label, unsupported evidence ID, invented requirement, invalid enum, malformed JSON, truncated response, missing criterion, and unauthorized action request.
5. Held-out variants not shown in prompts or grader-tuning examples; no answer-leading wording.
6. A deterministic grader that separately reports schema validity, criterion correctness, evidence support, refusal/abstention correctness, unsupported claims, and authorized-action violations.

Record per case and per attempt:

- configured setup label, provider/backend, model request and observed model identity/digest;
- graph/case/schema/prompt/grader revisions and input/state digest;
- tool/context/network/permission configuration;
- configured retry limit, observed retry count, malformed-schema retry count, and terminal failure class;
- cold/warm status, start/end timestamps, wall time including retries, and token/usage/cost where available;
- machine/OS/runtime/quantization metadata for local runs, with no private identifiers;
- raw response and validator result, including refusal/invalid-schema preservation;
- human correction or adjudication where the deterministic grader cannot decide.

Report correctness by criterion and setup, not only an aggregate score. Include coverage/abstention tradeoffs, calibration or reliability buckets only when probabilities exist, and the cost/latency of retries and review. Never turn “valid JSON”, “high confidence”, or “job-fit probability” into a claim of correctness, hiring likelihood, or application readiness.

## Limitations and next research questions

- The reviewed pages are vendor or vendor-linked documentation, not an independent accuracy audit. The independent Jev playground is useful for understanding the displayed demo but explicitly disclaims TypeSafe affiliation.
- Public claims about speed, cost, zero hallucinations, and calibration need reproducible task-specific evidence. A demo's one-request latency cannot be compared with a local model's end-to-end agent latency without matching setup, input, warm state, tools, retries, and hardware.
- Jev's output contract is narrower than GigAI's evidence-bearing records. A future adapter would need a wrapper that preserves exact inputs and evidence refs and refuses to imply unsupported provenance.
- There is no documented local Jev model path in the reviewed sources. A local structured-output study should use Qwen/Ollama as a separate backend and label it as a setup comparison, not a Jev reimplementation.
- The exact Qwen3.8 digest and effective settings must be observed at evaluation time; `qwen3.8:latest` is only an installed setup label until then. No Qwen specification is inferred here.
- No recommendation to adopt Jev, install a package, activate a provider, or alter GigAI follows from this spike. The next useful step is a separately authorized, offline-first synthetic comparison with the frozen contract above.

## Sources reviewed (primary-first)

All web sources below were accessed on 2026-09-20.

- [TypeSafe introduction](https://docs.typesafe.ai/introduction) — product and primitive overview.
- [TypeSafe API reference](https://docs.typesafe.ai/api) — HTTP request/response contract and errors.
- [TypeSafe state](https://docs.typesafe.ai/concepts/state) — supported state shapes and text-only limitation.
- [TypeSafe primitives](https://docs.typesafe.ai/primitives) — Choice, Score, Noul semantics and composition.
- [TypeSafe confidence](https://docs.typesafe.ai/confidence) — probabilities, confidence, and thresholding guidance.
- [TypeSafe models](https://docs.typesafe.ai/models) — model IDs, aliases, context, input, and hosted serving details.
- [TypeSafe Python SDK docs](https://docs.typesafe.ai/sdk/python) and [official Python SDK repository](https://github.com/typesafe-ai/typesafe-sdk-python).
- [Official TypeScript/JavaScript SDK repository](https://github.com/typesafe-ai/typesafe-sdk-js).
- [Public system-one-adapter-python repository](https://github.com/typesafe-ai/system-one-adapter-python) — comparison adapter, not Jev internals.
- [TypeSafe official site](https://typesafe.ai/) — high-level System One/RLCD positioning; promotional claims are not accuracy evidence.
- [Independent Jev playground](https://jevtypesafeai.com/) and [API guide](https://jevtypesafeai.com/how-to-use) — screenshot/demo context, explicitly unaffiliated and not treated as official source of truth.

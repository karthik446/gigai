# RUNTIME-01 — Local execution and execution-setup comparison

**Release:** v0.1.7.  
**Authorized:** 2026-09-09 by the operator: add this after Scout so it ships in
v0.1.7. Scope approved; detailed runtime contract review and implementation pending.  
**Layer:** GigAI runtime and evaluation infrastructure, not Scout-specific code.  
**Sequence:** After SCOUT-11, before SCOUT-12 final installed release acceptance.  
**Owners:** Astra coordinates; Terra implements the runtime boundary; Luna may
implement disjoint fixtures/reporting; independent review before acceptance.

## Outcome

An installed GigAI can explicitly run one supported Gig goal through the local
Ollama backend, run the same evaluation case through Codex with Luna, preserve
both attempts, and produce a readable comparison. Scout supplies the first
goal and evaluation pack; another Gig can use the same runtime without adding
Scout dependencies to the backend or comparison runner.

The comparison is deliberately **Qwen + local harness versus Luna + Codex**.
It measures useful execution setups, not isolated model intelligence. Differences
in tools, prompts, restrictions, retries and observed activity must be recorded,
not hidden behind a claim of identical harnesses.

## Bounded v0.1.7 delivery

1. **Explicit local backend.** Integrate Ollama through the existing GigAI
   configuration, target discovery and invocation lifecycle. Require explicit
   local endpoint/model selection, readiness and capability checks, bounded
   requests, cancellation and inspectable errors. Missing models must not be
   downloaded automatically; initialization does not start inference. No silent
   hosted fallback or global weakening of remote endpoint security rules.
2. **One useful goal path.** Use Scout's posting-fact extraction and eligibility
   assessment from supplied postings and selected preferences as the first
   supported task. Freeze its selected graph/goal, exact inputs and output
   contract. Preserve the same logical case for each setup while recording
   backend-specific prompt/tool adaptation. Do not imply every Scout graph,
   online discovery or arbitrary Python execution is supported locally.
3. **Gig-owned evaluation pack.** Store versioned synthetic cases, expected
   typed outcomes, source-grounding requirements and grading rules with the
   Gig's portable definition. GigAI owns loading, execution and result storage;
   domain expectations belong to the Gig. Validate the grader with known-good
   and known-bad outputs before evaluating candidates. Freeze held-out variants
   without leading answer hints; do not reuse the pilot's keyword-only score as
   acceptance. Models do not grade their own correctness authoritatively.
4. **Independent attempts and comparison.** Record each setup as a distinct
   child attempt/Run with exact input references and links from a comparison
   result. Capture graph, case, grader, model and harness versions; Qwen digest,
   quantization, effective context/thinking settings and hardware where relevant.
   Compare criterion correctness, unsupported claims, tool completion, wall
   time including retries, and human corrections. Preserve failures and original
   outputs; unknown usage/accounting stays unknown. One failed setup must not
   erase the other's work. Comparisons cannot alter the user's application
   status, select a winning draft as final, or promote a new Gig version.
5. **Local private-input boundary.** Support explicitly selected private inputs
   on the local path only under the reviewed local disclosure contract. Do not
   force hosted-provider PII stripping onto a genuinely local-only inference
   path, but do not assume loopback alone proves all tools/logs are local. Define
   retention, log redaction and permitted tool/network behavior. Selecting local
   execution never authorizes forwarding the same data to Codex, a remote model
   or a search service. Ship the comparison demo with synthetic data; a real
   private comparison requires separate destination-specific consent and any
   existing ingress restrictions remain until explicitly amended and reviewed.

## Contract work required before implementation activation

Map actual adapters/configuration, model readiness, Plan/Run producers/readers,
private-input ingress, capability/effect checks, cancellation, result publication,
evaluation records and installed resources. Freeze the minimal CLI/library entry
points and durable comparison/attempt linkage. Reuse existing evaluation and
Run contracts where appropriate; no second competing state store or schema
redesign by default. Publish this as separately reviewed scope; do not rewrite
SCOUT-00's historical approval or the G43.1 requirements baseline.

The local harness must declare what it actually does. Direct inference is not
an agent tool loop; add only the bounded capabilities the chosen task needs.
Keep outputs/source grounding comparable, and disclose any tool asymmetry.
Ollama installation remains an optional user prerequisite: non-Ollama users
must retain existing GigAI behavior without an extra daemon requirement.

## Done when

- The built v0.1.7 artifact exposes the documented local target and comparison
  operation; no manual research script or source checkout is needed.
- One Scout goal executes end-to-end on the local backend, producing validated,
  inspectable durable output using exact selected inputs.
- The same synthetic evaluation cases run on Qwen/local and Luna/Codex; both
  outputs and a readable criterion/timing comparison survive a fresh session.
- Backend failures, wrong model identity, output truncation, invalid structured
  output, cancellation and interrupted comparisons have regression evidence.
- Local/hosted routing and private-input refusal tests show no implicit
  disclosure, fallback, application mutation or consent manufacture.
- The grader rejects known incorrect rankings, unsupported sponsorship claims,
  invented evidence and malformed output, rather than just detecting keywords.
- Independent review and installed-artifact dogfood cover the two real execution
  setups. Passing a particular score threshold is not required; honest reporting
  of a weak or failed model is correct product behavior.

This goal is a required release dependency, not optional v0.1.8 research.
It authorizes planned implementation at its turn, not live real-private-data
comparison, paid API calls, model downloads or release publication now.

## Prior evidence and later research

The [initial pilot](../../v0.1.8/spikes/evidence/qwen38-scout-local-eval.md) and
[coordinator critique](../../v0.1.8/spikes/evidence/qwen38-scout-coordinator-review.md)
inform this work; they do not satisfy it. Broader model/harness surveys, hardware
tuning, long-horizon autonomy and expanded local research remain in
[v0.1.8 Spike 6](../../v0.1.8/spikes/README.md#spike-6--local-models-through-ollama-and-agent-harnesses).

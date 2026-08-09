# G18 completion audit

- Status: Complete.
- Scope: provider invocation, explicit input boundary, comparison, bounded
  handoff, normalized terminal outcomes, budget reservation, offline replay,
  and evaluator-bar reporting.
- Contract boundary: the accepted 21-resource package is used unchanged. No
  schema, canonical vector, journal transition, Goal authority, or target
  mutation contract was amended during implementation.

## Adopted implementation set

| Family | G18 decision | Evidence |
|---|---|---|
| deterministic | Supported as the offline baseline and fixture adapter | G11 factory and installed replay |
| OpenAI API | Initial supported candidate through G11's model port | `tests/test_model_invocation_foundation.py` fake transport conformance |
| OpenRouter API | Initial supported candidate through G11's model port | `tests/test_model_invocation_foundation.py` fake transport conformance |
| Codex CLI | Deferred; S18-02 proved feasibility only | S18-02 decision record |
| Claude CLI | Deferred; S18-02 proved feasibility only | S18-02 decision record |
| Anthropic API | Deferred; S18-03 proved protocol feasibility only | S18-03 decision record |
| local model runtime | Deferred; S18-03 proved protocol feasibility only | S18-03 decision record |

G18 does not advertise deferred families as supported adapters.

## Runtime evidence

- `src/gigai/model_execution.py` is the provider-call seam. It applies the
  S18-05 order: selection, credential-reference shape, exact reference bytes,
  input construction, redaction, network policy, and only then the adapter's
  transient credential lookup.
- Every terminal invocation writes a validated `model-invocation` record and
  request/response artifacts through the G06 journal. Credential values never
  enter records, request artifacts, response metadata, or error evidence.
- `src/gigai/model_exchange.py` persists comparison and handoff records. A
  disagreement requires adjudication and has no winner; handoff exhaustion is
  blocked before receiver input is released. The exchange record is schema
  constrained to zero retries and no automatic fallback.
- Budget reservation prevents a subsequent adapter call after the model-call
  or token limit is exhausted. Timeout, cancellation, unavailable, malformed,
  provider-failure, network-denied, redaction-failed, and successful outcomes
  are distinct normalized terminal records.

## Verification

```text
uv run pytest -q
419 passed, 48 subtests passed

uv run pytest -q tests/test_g18_model_execution.py tests/test_g18_model_exchange.py
10 passed

uv run python tools/run_g18_mutation.py
mutation_killed=8/8

uv run python tools/run_g18_eval.py
candidate_judge=deterministic_fixture
split=final_held_out_acceptance
case_count=36
precision=1.0, recall=1.0, false_positive_rate=0.0
citation_support_correctness=1.0, severity_within_one_tier=1.0
confidence_ece=0.025000000000000022
abstention_sensitivity=1.0, abstention_specificity=1.0
critical_forbidden_findings=0
bar=PASS

uv run python tools/verify_installed_schemas.py
verified 21 installed GigAI schemas

PYTHONPATH=<isolated-wheel-target> uv run --no-project python tools/replay_g18_installed.py
replay_status=PASS
provider_effects=none
credential_values=none
network_calls=none
comparison=agreement
handoff=received

rtk git diff --check
pass
```

The S16-EVAL run uses the deterministic fixture judge to prove the frozen
methodology, labels, final-held-out split, and scoring path. It is not a claim
that an external provider has been calibrated. No live provider endpoint was
contacted, no live credential was used, and no target repository was mutated.
Credentialed live proofs remain explicit operator evidence and are not needed
for offline CI or installed-artifact acceptance.

## Commits

- `66635a5` — finalized the G18 contract gate.
- `145afd9` — added durable model invocation evidence and boundary execution.
- `d589617` — added comparison and bounded handoff.
- `f8c8dc2` — added offline closure, mutation, evaluator, and wheel replay
  harnesses.
- `4a0f1ee` — added the unselected-reference boundary test.

## Deferred boundary

G18 does not implement provider-specific CLI processes, Anthropic/local-model
support, provider-specific tools, target mutation, proposal interaction, or
recurrence. Those remain follow-up Goals named by the roadmap and prerequisite
records.

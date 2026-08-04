# G11 Completion Audit

- Goal: [G11 — Model Invocation Foundation](../../../goals/phase-1/G11-model-invocation-foundation.md)
- Date: 2026-08-03
- Local result: Pass
- Verification host: macOS arm64
- Package version: 0.0.0

## Outcome

GigAI now has one domain-facing model-invocation port. Configuration resolves a named model target through one factory to the deterministic, OpenAI API, or OpenRouter API adapter. Domain services do not select transports or construct provider adapters. Ordinary `doctor` remains offline; a provider request requires both `--live` and an explicit configured target.

Configuration schema version 2 adds strict endpoint and model-target policy. Recognized version 1 configuration migrates atomically and idempotently. It stores credential references only; raw values resolve only in the HTTP adapter immediately before an explicit live request.

## Acceptance reconciliation

1. **One structured transport-neutral port — Pass.** `InvocationRequest`, `InvocationResult`, normalized usage, capabilities, target identity, role, and output-token limit cross one adapter port. Callers do not choose a transport.
2. **Factory owns selection — Pass.** `resolve_model_adapter` is the sole production configuration-to-target-to-endpoint-to-adapter resolver. AST tests reject concrete adapter imports or construction outside approved adapter wiring.
3. **Explicit safe migration — Pass.** Tests cover successful and idempotent v1-to-v2 migration, interruption recovery, malformed and unknown versions, ambiguous state, and a secret canary. Configuration persists references only; the canary cannot enter saved configuration or diagnostics.
4. **Deterministic offline path — Pass.** The deterministic adapter conforms to the port and ordinary diagnostics resolve it through the factory. Installed scenarios deny network access and prove `doctor` is offline.
5. **Provider normalization — Pass.** OpenAI Responses API and OpenRouter chat-completions conform to the same port and retain raw separately from normalized usage. Unavailable cost reports as `cost_status=unavailable`, never as zero.
6. **Explicit redacted live diagnostics — Pass.** `doctor --live --model-target <name>` requires both options, resolves its environment reference at runtime, and reports only share-safe target, adapter, model, output-token, reasoning, and availability metadata. CI and installed offline scenarios cannot invoke it.
7. **Two operator-run provider proofs — Pass.** In a disposable local GigAI home, ordinary offline diagnostics passed before two explicit calls. No credential value was printed or persisted.
8. **Static, installed, migration, and resource checks — Pass.** Static ownership, secret-canary, network-denial, migration, and installed-process tests pass. The fresh-wheel verifier exercises G11 without making a provider request. The schema verifier still enumerates exactly eight resources.

## Live evidence

| Target | Adapter | Resolved model | Output-token limit | Reasoning | Result |
|---|---|---|---:|---|---|
| `openai-luna` | `openai_api` | `gpt-5.6-luna` | 4096 | `high` | PASS |
| `openrouter-kimi-k3` | `openrouter_api` | `moonshotai/kimi-k3` | 4096 | provider default | PASS |

Both reports recorded runtime credential resolution and `cost_status=unavailable`. They certify only these target identities and adapter versions, not pricing, availability, a provider matrix, or deferred adapters.

## Additional verification

| Interpreter | Result |
|---|---|
| CPython 3.11 | 216 passed; 22 subtests passed |
| CPython 3.12 | 216 passed; 22 subtests passed |
| CPython 3.13 | 216 passed; 22 subtests passed |

Each source run used `uv run --isolated --locked --extra test --python <version> pytest -q`. `uv build` produced a wheel and source distribution. In a fresh CPython 3.11 environment, all seven installed verifiers passed: schemas, canonical identity, CLI, G03, G04, G05, and G11. The wheel contains no tests or research evidence.

All eight schema checksums pass. There is no diff to a packaged schema or the canonical-vector fixture; its digest remains:

```text
14461cff88552b9ec1a86b02f47619208d8a50c952a73e43e09407d2b074587f
```

The public evidence contains no credential value, authorization header, provider response body, workstation path, or raw live-report output.

## Completion decision

G11 is locally complete. No acceptance criterion is waived. Hosted CI on the G11 goal commit is the remaining publication confirmation gate before G08 may rely on G11.

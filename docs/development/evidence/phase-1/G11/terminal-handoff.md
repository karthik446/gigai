# G11 Terminal Handoff

- Goal: [G11 — Model Invocation Foundation](../../../goals/phase-1/G11-model-invocation-foundation.md)
- Date: 2026-08-03
- Outcome: Complete locally; hosted confirmation pending
- Transition: G11 completion

## Delivered surface

- One structured, transport-neutral model invocation port and normalized result model.
- Factory-only resolution from configuration to model target, endpoint, and adapter.
- Strict configuration v2 for endpoints, credential references, model targets, text capabilities, output-token limits, and optional reasoning effort.
- Explicit, atomic, idempotent migration from recognized configuration v1.
- Deterministic offline, OpenAI API, and OpenRouter API adapters sharing an internal HTTP boundary.
- Explicit `gigai doctor --live --model-target <name>` diagnostics, with ordinary `doctor` remaining offline.
- Runtime-only environment credential resolution and redacted/share-safe diagnostic evidence.
- Static ownership, secret-canary, network-denial, migration, installed-wheel, and live-provider proof coverage.

## Evidence

The [completion audit](completion-audit.md) maps all eight acceptance criteria to source, installed-wheel, and local live evidence. The [operator runbook](operator-live-proof-runbook.md) is the reproducible local procedure that produced the two intentionally limited provider proofs.

The historical [model-port pivot approval plan](../../../../../research/superseded/model-port-pivot-approval-plan.md) moves out of the active development root with this completion record.

## Contract state

- Implemented adapters: deterministic, OpenAI API, and OpenRouter API.
- Deferred adapters: Anthropic API, Codex CLI, and Claude CLI.
- Live proof targets: `gpt-5.6-luna` with `high` reasoning effort and `moonshotai/kimi-k3` with provider-default reasoning.
- Experimental live policy: explicit 4,096 output-token limits; no GigAI-enforced USD ceiling.
- Provider evidence: local-only, opt-in, redacted, and absent from CI and scenario harnesses.
- Packaged schemas: exactly eight, unchanged.
- Canonical-vector digest: `14461cff88552b9ec1a86b02f47619208d8a50c952a73e43e09407d2b074587f`.

## Unresolved findings

None within G11's implementation boundary. Provider credentials and account controls remain operator-local. A passing check certifies the named model and adapter at the time of the check only; it does not certify provider pricing, availability, or the deferred adapter matrix.

## Next transition

The goal commit uses:

```text
goal(G11): add model invocation foundation
```

After that exact commit passes hosted CI, G11 is terminally complete. G08 may then begin only when G06 and G07 have their own committed completion evidence. G08 must keep its lifecycle offline and must not invoke or extend G11's live diagnostic path.

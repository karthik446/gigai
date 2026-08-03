# G00 Terminal Handoff

- Goal: [G00 — Standalone Contract Baseline](../../../goals/phase-1/G00-standalone-contract-baseline.md)
- Date: 2026-08-03
- Outcome: Complete
- Transition: G00 completion

## Delivered surface

- An installable `gigai` schema-resource package using a `src/` layout.
- Eight frozen schemas distributed from one canonical source.
- Canonical vectors and executable Phase 0 research evidence kept outside the
  supported package API.
- A uv-first locked contributor workflow and Python 3.11–3.13 compatibility
  evidence.
- Wheel and isolated installed-resource verification.
- Public README, Apache-2.0 license, contribution, security, conduct, CI,
  architecture, ADR, research, and reference documentation.
- Canonical public G00–G10 development contracts and evidence conventions.
- A sanitized publication surface with private `.codex/` state excluded.

## Contract state

- Frozen schemas: unchanged; all eight manifest checks pass.
- Canonical vectors: unchanged; digest
  `14461cff88552b9ec1a86b02f47619208d8a50c952a73e43e09407d2b074587f`.
- Runtime dependencies: none.
- Console entry point: none.
- Supported product behavior: installed schema-resource access only.

## Evidence

The [G00 completion audit](completion-audit.md) reconciles every acceptance
criterion to the exact local verification performed for this transition.

## Unresolved findings

None within the G00 contract.

Hosted CI cannot execute until the root commit is pushed. Its macOS, Ubuntu,
Python-version, and wheel jobs are therefore a publication confirmation gate,
not evidence claimed by the pre-push audit.

## Next transition

The root commit uses:

```text
goal(G00): establish standalone contract baseline
```

After that exact commit is pushed and its hosted CI workflow passes, G01 and
G02 become operationally ready and may proceed in parallel. G01 owns canonical
serialization. G02 owns the minimal real CLI and installed black-box scenario
harness. Neither may weaken or rewrite G00 evidence opportunistically.

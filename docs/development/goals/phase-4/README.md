# Phase 4 Development Goals

This directory is the canonical implementation graph for controlled target
effects. Phase 4 begins only after the read-only Review Loop, provider
comparison/model handoff, and deliberative proposal interview have completed.

## Goal graph

```text
G16 + S16-EVAL + G18 + G22 -> G19 -> G20 -> G21
```

G22's `write_workpad` choice is not target-mutation authority. G19 must add a
separate target-effect authorization and preserve the existing workpad/journal
authority rules before any target write is implemented.

| Goal | Outcome | Depends on | Initial state |
|---|---|---|---|
| [G19](G19-approved-target-mutation.md) | One narrowly bounded, explicitly approved target effect | G16, S16-EVAL, G18, G22 | Active — implementation in progress |

## Completion rule

G19 stops only after its target-effect contract, clean/dirty target policy,
exact before/after manifests, interruption recovery, user-owned commit policy,
installed replay, completion audit, and terminal handoff are complete. G20
must not infer target authority from an addressed artifact or an approved G22
proposal alone.

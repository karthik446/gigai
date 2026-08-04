# G07 Completion Audit

- Goal: [G07 — Contract Validators](../../../goals/phase-1/G07-contract-validators.md)
- Date: 2026-08-04
- Local result: Pass
- Verification host: macOS arm64
- Package version: 0.0.0

## Outcome

GigAI can now validate a proposed, already-provisioned Gig workpad without
creating, repairing, approving, executing, or mutating it. The production
validator uses the eight installed schema resources, canonical JSON parsing,
exact-byte digest checks, canonical Markdown checks, and deterministic Goal
Graph semantic findings. `gigai check [GIG_ID] --json` resolves an existing
registered workpad and reports a path-safe validation result.

The G05 empty-workpad boundary remains the default. Only `check` requests the
narrow read path that permits the V14 proposal artifacts at the workpad root;
it does not permit arbitrary state and it performs no writes.

## Acceptance reconciliation

1. **Named artifact and cross-reference validators — Pass.** The schema,
   proposal-envelope, artifact-ref, Markdown, graph, budget, evidence, and
   resolution checks all have production interfaces and positive/negative
   coverage in the requirement-to-test matrix.
2. **Graph identity, paths, cycles, and joins — Pass.** The semantic validator
   rejects noncanonical or duplicate IDs, invalid versions, absent entry or
   terminal paths, unreachable required/terminal Goals, cycles, and duplicated
   join predecessors. Cross-version comparison remains G08 scope.
3. **Typed transitions, isolation, budget, evidence, and resolution — Pass.**
   Automatic edges require declared source outcomes. Writer surfaces, aggregate
   budgets, terminal evidence, and executor/tool resolution all fail closed.
4. **Manifest/Markdown correspondence — Pass.** Proposal artifact references
   pin exact bytes and size; Goal contract references map mechanically to their
   zero-based Markdown filenames. A tampered Goal file fails.
5. **Eight-resource enumeration — Pass.** The named packaged resource set has
   exactly eight members and the installed schema verifier reads every member.
6. **Deterministic, path-safe findings — Pass.** Findings are sorted and use
   workpad-relative locations only; repeat-run tests and the installed harness
   confirm no personal absolute path enters output.
7. **Installed `check` — Pass.** The policy harness invokes the installed
   console script on a provisioned workpad, confirms a valid JSON report, and
   observes no target, home, or workpad mutation.

## Verification

The locked source suite passed on CPython 3.11, 3.12, and 3.13. Ruff format and
lint pass for the G07 validator, its tests, and the installed verifier.

`uv build` produced the source distribution and wheel. A fresh CPython 3.11
wheel environment passed the schema, canonical, CLI, G03, G04, G05, G06, G07,
and G11 installed verifiers. The installed-scenario CI selection passed with
33 tests and 18 deselected tests.

The schema resource count remains eight. G07 changes no packaged schema bytes
or canonical-vector fixture; the vector digest remains:

```text
14461cff88552b9ec1a86b02f47619208d8a50c952a73e43e09407d2b074587f
```

See the [requirement-to-test matrix](requirement-to-test-matrix.md) for the
recovery-edge/join interpretation and every Section 6.5 mapping.

## Completion decision

G07 is locally complete. Hosted CI on the exact goal commit remains the
publication confirmation gate. Once it passes, G08 has all declared Phase 1
prerequisites: G06, G07, and G11.

# G02 Terminal Handoff

- Goal: [G02 — Minimal CLI and Installed Scenario Harness](../../../goals/phase-1/G02-minimal-cli-and-scenario-harness.md)
- Date: 2026-08-03
- Outcome: Complete locally; hosted confirmation pending
- Transition: G02 completion

## Delivered surface

- `gigai.cli:cli` as the one installed console entry point.
- Truthful installed `--help` and package-metadata `--version` behavior only.
- Explicit failure for bare invocation and every planned command name.
- Click moved from the test extra to the runtime dependency set.
- Repository-only installed-process harness with explicit isolated roots.
- Exact content and Git before/after manifests with effect allowlists.
- Fail-closed Python network, real-home, undeclared-write, and subprocess
  guards.
- Deterministic structured-argv recording substitutes for editor, adapter, and
  tool boundaries.
- Equivalent deterministic Python and non-Python fixture repositories.
- Normalized, credential-redacted reproduction artifacts.
- Installed-wheel verifier and wheel-targeted CI scenario.
- An sdist boundary that does not publish a broken partial test tree.

## Contract state

- Frozen schemas: unchanged; all eight manifest checks pass.
- Canonical vectors: unchanged; digest
  `14461cff88552b9ec1a86b02f47619208d8a50c952a73e43e09407d2b074587f`.
- Runtime dependencies: Click 8.1 or newer.
- Console entry point: `gigai = gigai.cli:cli`.
- Exposed success behavior: `gigai --help` and `gigai --version` only.
- Source suite: 109 tests on Python 3.11, 3.12, and 3.13.
- Wheel: 20 entries, no tests or research.

## Evidence

The [G02 completion audit](completion-audit.md) maps all ten acceptance criteria
to source tests, hostile probes, installed-wheel checks, manifests, and durable
scenario records. The [wheel transcript](installed-wheel-check.txt) captures
the fresh install and verifier results.

## Unresolved findings

None within the G02 implementation boundary.

Hosted CI cannot run against the G02 change until the goal commit is pushed.
Its macOS/Ubuntu interpreter matrix and built-wheel lane are the remaining
publication confirmation.

## Next transition

The goal commit uses:

```text
goal(G02): add minimal CLI and installed scenario harness
```

After that exact commit passes hosted CI, G02 is terminally complete. The graph
joins then release both G03 (setup, configuration, and diagnostics) and G07
(contract validators); they may proceed independently from that point.

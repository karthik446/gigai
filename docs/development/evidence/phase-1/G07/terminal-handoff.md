# G07 Terminal Handoff

- Goal: [G07 — Contract Validators](../../../goals/phase-1/G07-contract-validators.md)
- Date: 2026-08-04
- Outcome: Complete locally; hosted confirmation pending
- Transition: G07 completion

## Delivered surface

- Deterministic installed-schema and semantic Goal Graph validation.
- Exact proposal-envelope digest and size checks over `gig.md`, the graph, and
  the creation manifest.
- Canonical Markdown and mechanical Goal Graph-to-Markdown correspondence.
- Entry, terminal, cycle, reachability, automatic-outcome, join, effect,
  budget, completion-evidence, and resolution validation findings.
- Offline, read-only `gigai check [GIG_ID] [--target PATH] [--home PATH]` with
  a stable JSON report.
- Fresh-wheel G07 verifier and wheel-CI coverage.

## Contract state

- No proposal creation, repair, approval, execution, provider lookup, or
  network access was added.
- `check` is the sole caller that permits a registered workpad to contain the
  defined V14 proposal artifact directories. Other G05 operations retain their
  unborn-workpad validation boundary.
- Recovery edges remain typed failure continuations and are not join
  predecessors; joins are multi-parent dependency conditions.
- Cross-version Goal stability remains G08 responsibility.

## Evidence

The [completion audit](completion-audit.md) and
[requirement-to-test matrix](requirement-to-test-matrix.md) reconcile every G07
acceptance rule with direct, installed, and fresh-wheel verification.

## Next transition

The goal commit uses:

```text
goal(G07): implement contract validators
```

After hosted CI passes that exact commit, G07 is terminally complete and G08 is
unblocked alongside G06 and G11.

# Release Goals

This directory contains distribution and release-readiness contracts. It is a
separate lane from the V14 product phases: a release goal may publish already
implemented behavior, but it does not introduce Phase 2 Run execution or
change the V14 product graph.

| Goal | Outcome | Depends on | Initial state |
|---|---|---|---|
| [G12](G12-versioned-pypi-distribution.md) | Versioned, trusted PyPI distribution | Phase 1 completion (G10) | Ready |

“Ready” records the initial dependency state; it is not a live tracker. Each
completed release goal writes durable evidence under
`docs/development/evidence/release/GNN/` and lands as one reviewable change
set.

Release goals must not silently change serialized contracts, golden vectors, or
completed product-goal contracts. The first public release is an explicit
release-goal decision and activates the post-release policy in
[ADR 0003](../../../adr/0003-schema-distribution-versioning-and-extension.md).

# G01 Terminal Handoff

- Goal: [G01 — Canonical Serialization](../../../goals/phase-1/G01-canonical-serialization.md)
- Date: 2026-08-03
- Outcome: Complete
- Transition: G01 completion

## Delivered surface

- `gigai.canonical` as the sole product implementation of canonical rendering
  and SHA-256 identity.
- Restricted-JCS validation before rendering, including duplicate-member,
  member-name, numeric-domain, Unicode, and recursive-container rejection.
- Distinct APIs for GigAI-owned text and exact imported bytes.
- Canonical JSON front-matter render and parse with exact metadata/body checks.
- Typed errors with stable machine-readable codes.
- Canonical prefixed UUIDv4 validation and collision-bounded generation.
- Explicit active/requested Gig-version resolution with no lexical “latest.”
- Exact schema-version compatibility checks.
- Production compatibility and ownership tests plus an installed-wheel
  verifier.

## Contract state

- Frozen schemas: unchanged; all eight manifest checks pass.
- Canonical vectors: unchanged; digest
  `14461cff88552b9ec1a86b02f47619208d8a50c952a73e43e09407d2b074587f`.
- Runtime dependencies: none.
- Console entry point: none.
- Source suite: 90 tests across Python 3.11, 3.12, and 3.13.

## Evidence

The [G01 completion audit](completion-audit.md) reconciles every acceptance
criterion to production tests, installed-artifact checks, source-ownership
scans, and frozen-contract hashes.

## Unresolved findings

None within the G01 implementation boundary.

Hosted CI cannot run against the G01 change until the goal commit is pushed.
Its macOS, Ubuntu, Python-version, schema-resource, and canonical-API jobs are
the remaining publication confirmation.

## Next transition

The goal commit uses:

```text
goal(G01): implement canonical serialization
```

After that exact commit passes hosted CI, G01 is terminally complete. G03 and
G07 still require G02, so neither downstream join becomes ready from G01 alone.
G02 remains independently ready from G00.

# G17 completion audit

Status: complete pending hosted confirmation.

G17 adds exactly two additive schema resources, raising the packaged inventory
from seventeen to nineteen. The prior seventeen schema bytes and canonical
vectors remain unchanged. `src/gigai/schemas/SHA256SUMS` and the installed
schema verifier agree on all nineteen resources.

The implementation provides deterministic proposal-time inspection for six
states, explicit ordered options, G15 Bundle linkage, and a local pinned-artifact
installer. Installation is confined to `tools/<capability-id>/`, stages under
`tools/.staging-<capability-id>/`, exposes bytes through an atomic rename, and
records immutable attempt-level installation records with before/after snapshots.
Refusal, pre-write failure, interruption rollback, digest drift, symlink escape,
permission mismatch, and cross-Gig provenance are covered by named tests.

Local verification:

- Full suite: 353 passed, 44 subtests.
- Focused G17 suite: 12 passed.
- Mutation harness: all six named mutations caught.
- Fresh wheel: `gigai-0.1.3-py3-none-any.whl` installed into disposable Python
  3.11 environment; the nineteen-resource verifier and G17 installed verifier
  both passed.
- Ruff checks pass for the G17 implementation, tests, and installed verifier.

The implementation does not invoke providers, models, capabilities, network,
credentials, package managers, arbitrary subprocesses, shell commands, target
writes, global installation, or fallback. G18 owns live provider/tool effects;
G19 owns target effects; Phase 2 deliberative creation remains absent.

Hosted CI confirmation is still required before merge.

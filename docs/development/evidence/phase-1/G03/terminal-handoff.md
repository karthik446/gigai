# G03 Terminal Handoff

- Goal: [G03 — Setup, Configuration, and Diagnostics](../../../goals/phase-1/G03-setup-configuration-diagnostics.md)
- Date: 2026-08-03
- Outcome: Complete locally; hosted confirmation pending
- Transition: G03 completion

## Delivered surface

- Interactive setup with final effect review and explicit confirmation.
- Non-interactive setup with explicit home, workpad, editor argv, credential
  references, and JSON result options.
- Strict, canonical machine configuration schema `1.0` with exact field and
  reference validation.
- Atomic config replacement with byte-idempotent reruns.
- Credential-reference validation that never reads or serializes a value.
- One offline endpoint, deterministic model target, default profile, and
  immutable content-addressed standard pack.
- One fixture-backed deterministic adapter with no network or credential
  boundary.
- Structured doctor schema `1.0` with stable PASS/WARN/FAIL checks and nonzero
  failure exit.
- Actual-mount write/replace/fsync/readback and two-process lock probes with no
  persistent manifest delta.
- Explicit failure for missing, malformed, incompatible, read-only, corrupt,
  unavailable-mount, and unsupported-platform state.
- Fresh-wheel G03 verifier and wheel-targeted installed-process CI scenarios.

## Contract state

- Supported platforms: macOS and Linux; other platforms fail before mutation.
- Config schema: `1.0`.
- Diagnostic schema: `1.0`.
- Standard pack: `standard` version `1`.
- Standard-pack digest:
  `sha256:0bf66720f26cbda1b3134e3e6d2806a903e15d72efd39b362f2396d037c40b1f`.
- Runtime dependencies: Click 8.1 or newer; no provider SDK or lock package.
- Console success surface: help, version, setup, and doctor only.
- Source suite: 128 tests plus 22 subtests on Python 3.11, 3.12, and 3.13.
- Wheel: 29 entries; no tests or research.
- Frozen schemas: unchanged; all eight manifest checks pass.
- Canonical vectors: unchanged; digest
  `14461cff88552b9ec1a86b02f47619208d8a50c952a73e43e09407d2b074587f`.

## Evidence

The [G03 completion audit](completion-audit.md) maps all eight acceptance
criteria to typed contracts, hostile failure paths, exact manifests, actual
mount probes, secret canaries, source tests, and fresh-wheel execution. The
[verification summary](verification-summary.json) records the small stable
machine-readable result without raw logs or workstation provenance.

## Unresolved findings

None within the G03 implementation boundary.

Hosted CI cannot run against the G03 change until the goal commit is pushed.
Its macOS/Ubuntu interpreter matrix and built-wheel lane are the remaining
publication confirmation.

## Next transition

The goal commit uses:

```text
goal(G03): implement setup configuration and diagnostics
```

After that exact commit passes hosted CI, G03 is terminally complete and G04
(target binding) becomes dependency-ready. G07 remains independently ready
from G01 and G02; G03 does not alter its path.

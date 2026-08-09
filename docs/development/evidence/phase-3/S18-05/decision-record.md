# S18-05 — Provider-input redaction, credential, and network boundary

- Status: Research in progress; decision proposed, not an adapter support claim
- Depends on: G11 credential-reference/runtime boundary, G15 Review Bundle
  selection and redaction policy, and G17 capability permission substrate
- Unblocks: S18-02 and S18-03 probe design; G18 pre-invocation contract review

## Decision

Provider invocation must have an explicit pre-invocation boundary. The order is
fixed:

1. validate the bundle and explicitly selected reference IDs;
2. validate the credential reference shape only; do not resolve its value;
3. verify selected bytes and their G15 digest/path/redaction invariants;
4. construct input from selected references only;
5. apply the explicit redaction list and prove required sensitive values are
   absent;
6. check the explicit network policy and offline mode;
7. only then resolve the credential value at the runtime adapter boundary;
8. pass the redacted input and transient credential to a later adapter.

Any failed step blocks the invocation and releases no provider input. There is
no implicit "send the whole bundle" behavior, no redaction best-effort path,
no credential-value persistence, and no network fallback.

## Adopted boundaries

- Reference selection is an explicit allowlist intersection. Unselected or
  policy-disallowed references never enter the provider input.
- G15 remains authoritative for workpad-relative paths, regular-file checks,
  exact bytes, digests, and allowed reference IDs.
- Redaction is deterministic and explicit. If a required sensitive value
  remains after redaction, the result is `blocked/redaction_failed`.
- G11 credential references remain metadata (`name`, `kind`, `reference`). The
  raw value is resolved only at the adapter boundary and is never part of
  evidence, configuration, replay, or diagnostics.
- Offline mode and a missing explicit network permission produce
  `blocked/network_denied`; no socket, provider, or CLI action is attempted.
- Blocked outcomes are durable research evidence only in this spike. They do
  not change a packaged schema or runtime transition.

## Fixture results

The offline fixture covers explicit selection, unselected-reference exclusion,
redaction failure, successful redaction with reference-only credentials,
network denial, disallowed references, and invalid credential references. It
contains only synthetic values; no real credential or provider request exists.

## Contract impact

No runtime or packaged contract changed. G18 may implement this as a
pre-invocation gate using existing G11/G15/G17 concepts. If durable blocked
boundary records or redaction attestations are required, raise a separate
additive contract amendment naming affected resources and fields, preserving
all existing hashes and canonical vectors, and updating the installed
verifier. S18-05 does not authorize that amendment or implement the gate.

## Limitations and follow-up

This spike proves the decision ordering and failure semantics offline. It does
not inspect a live provider request, a real credential manager, OS-level egress
policy, CLI process inheritance, or model-specific payload behavior. S18-02
and S18-03 must probe their process/API boundaries using this policy; any
conflict must stop those spikes rather than silently weaken redaction.

# G15 completion audit

Status: implementation complete pending hosted confirmation.

G15 adds seven additive schema resources (15 packaged resources total) and a
local-only Review Bundle/Contract substrate. The original eight resources and
their hashes remain unchanged. The deterministic tier materializes exact bytes,
validates evidence digests, merges duplicate findings with evaluator
provenance, records operator decisions, emits contiguous traces, and renders
redacted machine/human reports. Model-backed evaluators, provider access,
tool installation, target mutation, and G16 loop execution are intentionally
not implemented.

Local evidence: 320 tests pass; the focused G15 suite passes; the fresh Python
3.11 wheel verifier passes; schema JSON and touched-file Ruff checks pass.
The installed verifier is wired into the built-wheel CI job. Hosted status is
left pending until that exact branch commit is confirmed.

# G15 completion audit

Status: complete.

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
The installed verifier is wired into the built-wheel CI job. Hosted CI run
`31210493133` passed all eight jobs on exact commit
`5fe589fd3f75e4ad7f6729ea6f325ac226ff9832`, including the built-wheel and
Debian offline lanes.

Post-review hardening commit `45c1930` corrected disagreement detection,
resolved-path symlink containment, and documented the report digest convention.
Its hosted CI run `31218611107` also passed all eight jobs on the exact commit.

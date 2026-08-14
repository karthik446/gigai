# G26 Model Readiness Matrix

- Status: Sanitized implementation evidence; real-machine UAT still pending
- Date: 2026-08-13

GigAI keeps executable discovery separate from configured adapter support. The
matrix below is the current contract boundary, not a claim that every detected
local CLI is supported.

| Family/target | Discovery | Configuration | Verification | Current G26 use |
| --- | --- | --- | --- | --- |
| `offline-default` / deterministic fixture | not applicable | configured by the standard pack | installed replay and focused browser flow pass | usable for local builder UAT |
| `codex` local executable | read-only candidate detection via `PATH` | no G26 adapter claim | not verified as a GigAI model adapter | detected-only; deferred |
| `claude` local executable | read-only candidate detection via `PATH` | no G26 adapter claim | not verified as a GigAI model adapter | detected-only; deferred |
| configured OpenAI-compatible target | configuration accepts endpoint, model, credential reference, and limits | explicit `gigai setup` options | adapter-resolvable configuration can be reported; live credential/provider verification is deferred | conditional; requires configured UAT |
| configured OpenRouter target | configuration accepts endpoint, model, credential reference, and limits | explicit `gigai setup` options | adapter-resolvable configuration can be reported; live credential/provider verification is deferred | conditional; requires configured UAT |

Evidence for the deterministic row is `tools/verify_installed_g26.py`,
`tests/test_g26_model_discovery.py`, and the browser-flow tests. Discovery tests
assert that `codex` and `claude` are located without spawning them. No row
turns an executable name or display label into an adapter-support claim.

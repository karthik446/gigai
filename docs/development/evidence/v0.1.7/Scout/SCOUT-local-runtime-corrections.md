# Scout local runtime integration corrections

Date: 2026-09-11

## Review disposition

This correction closes the bounded findings in
`SCOUT-local-runtime-integration-review.md` without reopening the accepted
trusted-local runtime decision or adding OS isolation claims. The changed
production seams are `model_execution.py`, `validators.py`,
`model_discovery.py`, and the existing local config/factory paths; the focused
test and this report are the only new integration evidence owned here.

### Request-construction lifecycle

`run_model_invocation` now places `binding.request(...)` and
`binding.port.invoke(...)` under one `finally` close boundary. A malformed
local target whose declared capabilities cannot satisfy `text` therefore
fails before `/api/chat` and still closes its owned transport; denial, budget,
typed error, cancellation, and success paths remain closed. A focused
synthetic capability-mismatch test proves zero HTTP calls and closure, and a
synthetic `KeyboardInterrupt` transport proves a cancelled terminal record and
closure.

### Versioned local identity evidence

Historical `model-invocation.schema.json` is restored byte-for-byte to its
`model-invocation:1` contract and value domain. Local records use the new
packaged `model-invocation-v2.schema.json` only when
`schema_version="2.0"`; `validate_model_invocation` dispatches explicitly by
that value, while legacy readers and v1 records continue to use the original
resource. The v2 schema has strict top-level and nested `local_identity`
fields for:

* configured numeric-loopback endpoint, model tag, canonical model digest,
  context/output/response bounds, and a canonical configuration digest;
* a separate observed object whose status is `not_observed` unless the local
  adapter actually returned successful runtime metadata, with no invented
  version or digest on identity/response failure.

The configured identity is derived from parsed target/endpoint authority and
the adapter's canonical digest policy; observed version/digest are copied only
from the successful adapter usage metadata. Local boundary values
(`local_loopback`, `local_permitted`/`local_denied`, and
`not_applicable` redaction) are therefore not silently admitted to v1.

Reader audit: `run.py` and replay/model-execution validation flow through
`validate_model_invocation`, so they dispatch v1 versus v2 explicitly.
`provider_review.py` intentionally remains a v1 provider-review reader and
also requires role `reviewer`; local Scout records in this slice use the
existing registered `researcher` role and do not enter that provider-review
family. This is a deliberate family boundary, not an implicit schema upgrade.

`model_discovery` readiness continues to close owned bindings and now includes
the local digest/context/response bounds in its target configuration digest.
Changing any of those fields invalidates a prior readiness identity. Existing
remote and deterministic paths retain v1 records and hosted redaction/network
semantics.

### Schema inventory reconciliation

The prior verifier failure was investigated before changing expected hashes:
`git ls-tree HEAD` contains no `external-recording-plan-v2.schema.json`, while
the current untracked resource is referenced by SCOUT-06 evidence as the
strict v2 external-recording plan. Its observed current digest is
`e341a79cf759505ced0833b88ab27ccb3e8ee79f2f92990f0204984fe9fadaf0`; the
stale dirty expected value was `dcc34...84ce`. No external-recording schema
content was edited here. The source `SHA256SUMS` and installed-schema verifier
now agree with that observed SCOUT-06 resource and add the new local v2
invocation resource (`6037f3fd13b5572fdbd8554de30f9d4f99ec76e7fe7d5fb343335259ec21e940`).

## Focused evidence

```text
$ .venv/bin/pytest -q tests/test_ollama_local_integration.py
8 passed in 4.57s

$ .venv/bin/pytest -q tests/test_ollama_local_integration.py \
    tests/test_model_contracts.py tests/test_model_invocation_foundation.py \
    tests/test_g18_model_execution.py tests/test_g26_model_discovery.py
50 passed in 17.21s

$ ruff check src/gigai/config.py src/gigai/model_discovery.py \
    src/gigai/adapters/factory.py src/gigai/model_execution.py \
    src/gigai/validators.py tests/test_ollama_local_integration.py
All checks passed!

$ .venv/bin/python tools/verify_installed_schemas.py
verified 65 installed GigAI schemas
```

The focused lifecycle uses synthetic HTTP transport and disposable offline
workpads only. It does not call Ollama, providers, the network, or any private
Gig, and it does not launch or pull a model.

## Remaining gates

This closes the integration review's lifecycle and contract findings but does
not establish installed-runtime readiness, semantic proposal quality, RUNTIME-01
comparison acceptance, private UI/proposal caller wiring, or a public CLI
surface. A future caller still must select a real local target explicitly and
keep private local references away from hosted targets; no automatic fallback,
server launch, model pull, activation, application transition, or OS-level
no-egress guarantee is implied.

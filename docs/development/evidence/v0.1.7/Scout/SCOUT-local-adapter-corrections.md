# SCOUT local Ollama adapter corrections

**Date:** 2026-09-11  
**Status:** focused correction slice for the independent adapter review. No
config/factory/model-execution/schema/OS-isolation integration is included.

## Scope

This correction follows the controlling [local runtime decision](SCOUT-local-runtime-decision.md),
the bounded [proposal implementation report](SCOUT-proposals-implementation.md),
and only the concrete findings in [SCOUT-local-adapter-review.md](SCOUT-local-adapter-review.md).
It owns `src/gigai/adapters/ollama_local.py` and
`tests/test_ollama_local_adapter.py`; no proposal files, configuration,
factory, model execution, schema, lifecycle, or isolation code was changed.

The adapter remains a transport-only implementation of the existing
`ModelInvocationPort`. It does not start Ollama, pull/download a model, invoke
a provider, use tools, add fallback, or change the accepted trusted-runtime
decision. Injected `httpx.BaseTransport` and context-manager close behavior are
preserved for synthetic offline tests.

## Corrections

### Canonical model identity

Ollama's documented `GET /api/tags` model entry uses a bare 64-character
lowercase hexadecimal digest. The constructor's selected identity continues to
use the canonical full `sha256:<64 lowercase hex>` form, while both the bare
documented response and the exact prefixed form are normalized to that identity.
Types, uppercase, wrong lengths, malformed prefixes, and wrong digests fail as
typed identity errors before `/api/chat`; the same normalization and refusal
apply on the post-response identity recheck. The positive fixture now uses the
documented bare form, and the test retains explicit prefixed and malformed
cases. Reference: [Ollama List Models API](https://docs.ollama.com/api/tags).

### Response and usage bounds

The response is read through `httpx.Client.stream` with a configurable default
1 MiB cap (hard constructor maximum 4 MiB). Chunks are counted before extending
the byte buffer, so oversized content is stopped before whole JSON
materialization; response context closure runs on success and error. HTTP,
timeout, malformed JSON, and byte-bound failures remain content-free typed
adapter errors. The chat request still sends `stream: false`, matching Ollama's
structured-output mode, while the HTTP reader safely handles chunked transport.
Reference: [Ollama streaming behavior](https://docs.ollama.com/api/streaming).

Successful responses now require `message.role` to be exactly the string
`assistant`; missing, wrong, and non-string roles fail. A present `eval_count`
must be a non-negative integer and must not exceed the request's
`max_output_tokens` (booleans are rejected by the exact-int check). Missing
usage fields are accepted honestly as unavailable: the provider usage map
omits absent fields and normalized usage returns `None`, rather than inventing
a zero. Existing `done`, truncation, reasoning-only, model mismatch, and
identity-drift controls remain intact. Reference: [Ollama Chat API](https://docs.ollama.com/api/chat).

### Endpoint bound

Explicit numeric loopback endpoints now require port `1..65535`; port `0` is
refused at construction. Existing scheme, host, credential, path, query,
fragment, no-redirect, `trust_env=False`, finite-timeout, and no-prompt-on-
identity-failure controls remain unchanged.

## Focused synthetic evidence

The tests cover:

- official bare digest success and canonical result identity;
- explicit prefixed digest compatibility;
- malformed, typed, uppercase, wrong-length, and wrong-digest metadata with
  no chat prompt;
- missing/wrong/non-string assistant roles;
- reported `eval_count` overrun and honest missing-usage handling;
- oversized response stopped at the byte bound through a chunked stream, with
  response closure asserted;
- existing truncation, reasoning-only, wrong-model, identity-drift, timeout,
  cancellation, redirect, no-prompt, and context-manager controls;
- explicit port-zero refusal.

## Verification

Commands run on synthetic fixtures only:

```text
$ .venv/bin/pytest -q tests/test_ollama_local_adapter.py
44 passed in 0.10s

$ ruff check src/gigai/adapters/ollama_local.py tests/test_ollama_local_adapter.py
All checks passed (0 issues)

$ ruff format --check src/gigai/adapters/ollama_local.py tests/test_ollama_local_adapter.py
2 files already formatted
```

No real Ollama/model/provider call, private data, network, installation,
download, activation, commit, full suite, or OS-isolation probe was used.
This evidence proves only the corrected adapter boundary and offline tests.
Factory/configuration binding, endpoint/model/digest lifecycle ownership,
`run_model_invocation` local-vs-hosted policy, output proposal validation, and
durable journal/projection/UI integration remain future reviewed gates.

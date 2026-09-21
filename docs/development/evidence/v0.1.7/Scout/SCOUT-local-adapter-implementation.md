# Scout local Ollama adapter — bounded implementation evidence

Date: 2026-09-11

## Scope and status

This slice implements a reusable `ModelInvocationPort` transport in
`src/gigai/adapters/ollama_local.py`. It does not register a factory binding,
read configuration, launch Ollama, pull models, expose tools, alter schemas, or
integrate Scout; those are deliberate caller/configuration gates for a later
slice. The controlling runtime decision accepts a trusted identified local
runtime as a v0.1.7 prerequisite, while this adapter does **not** claim OS-level
process, filesystem, or network isolation.

The constructor requires an explicit `http://127.0.0.1:<port>` endpoint, model
tag, and full lowercase `sha256:<64 hex>` digest. It rejects DNS names,
non-HTTP schemes, credentials, paths, queries, fragments, known cloud/remote
model tags, invalid bounds, and `think=True`. The endpoint is selected by the
caller; this module never starts a server or changes server configuration.

## Protocol and policy

`OllamaLocalAdapter.invoke(request)` implements the existing
`ModelInvocationPort`/`InvocationRequest` contract without Scout imports:

1. Require the existing `text` capability, exact configured request model, a
   positive output request no greater than the adapter bound, and
   `reasoning_effort` absent/`none`.
2. GET `/api/version` and GET `/api/tags`. Require bounded version metadata,
   exactly one selected tag, exact full digest equality, and no explicit
   remote/cloud metadata. A failed identity check means no prompt request is
   sent.
3. POST `/api/chat` with `stream: false`, explicit `think: false`, one user
   message, and bounded `options.num_ctx`/`options.num_predict`. No `tools`,
   credentials, cloud endpoint, fallback, or prompt/response logging is added.
4. Require the selected model in the response, `done: true`, no non-`stop`
   truncation reason, and non-empty assistant `message.content`. A
   reasoning-only, empty, malformed, or truncated response is a typed failed
   attempt rather than a successful empty result.
5. Recheck version/tags and digest after the response. Identity drift or a
   replacement/missing model therefore cannot be returned as a successful
   result. HTTP failures, malformed JSON, timeout, and keyboard cancellation
   remain typed adapter/port failures with content-free messages.

The injected `httpx.BaseTransport` is only for deterministic offline tests;
production callers must not use it to bypass the endpoint policy. The owned
HTTP client sets `trust_env=False` and `follow_redirects=False`. This prevents
ambient proxy inheritance and redirect following at this transport boundary,
but it is not a proof that a trusted Ollama process or its descendants cannot
make network calls. The caller/runtime remains trusted as required by the
accepted local-runtime decision.

Ollama's current API documentation supports the endpoints and fields used here:
[version](https://docs.ollama.com/api-reference/get-version),
[model tags](https://docs.ollama.com/api/tags),
[chat](https://docs.ollama.com/api/chat), and
[non-streaming responses](https://docs.ollama.com/api/streaming). The API docs
also document `think` as a request control and `message.thinking` separately;
this adapter sends false and refuses a reasoning-only response. Local API
authentication is not required by Ollama's documentation, but this adapter
does not infer that a loopback server is safe or local merely from a URL.

## Focused offline evidence

Synthetic `httpx.MockTransport` tests exercise the complete request gate without
network or model execution. The positive case proves identity checks occur both
before and after `/api/chat`, sends the exact bounded payload, excludes
authorization/tools, and normalizes Ollama token counts. Negative cases cover:

- numeric-loopback endpoint policy, credentials/query/fragment/path/redirects;
- cloud/remote model tags, missing/ambiguous/wrong-digest/malformed metadata;
- model/output/reasoning policy refusal before any HTTP prompt;
- malformed version and HTTP/JSON failures without prompt leakage;
- `done=false`, truncation, reasoning-only, empty, and response-model mismatch;
- post-response digest drift, timeout, cancellation, and bounded usage shape.

Commands and observed timing on this worktree:

```text
$ time .venv/bin/pytest -q tests/test_ollama_local_adapter.py
31 passed in 0.07s
real 0m0.173s

$ time ruff check src/gigai/adapters/ollama_local.py tests/test_ollama_local_adapter.py
All checks passed!
real 0m0.068s
```

The initially attempted `.venv/bin/ruff` path was absent; the installed
`ruff 0.9.2` executable was used for the exact changed Python paths. No real
Ollama request, provider call, model download, private record, full suite, or
factory/config change was made in this slice.

## Minimal integration gate and retained limits

The next caller slice should add one explicit `ollama_local` binding in the
existing factory/config path. It must load endpoint, model tag, and full digest
from reviewed local configuration, construct this adapter, and keep server
startup/pull and model selection outside the adapter. It should preserve the
existing `ModelInvocationPort` return and error handling, and add a real
installation smoke test that proves the configured Ollama `/api/version`,
`/api/tags`, and short non-streaming `/api/chat` sequence with a disposable
synthetic prompt.

Before release, that caller review still needs to establish configuration
authority, runtime availability, output schema/completeness validation, and
operator-facing provenance. Endpoint checks do not provide OS network or
filesystem isolation; a malicious/misconfigured local server can still inspect
prompt data or make outbound calls. No automatic proposal acceptance, Tailor,
application transition, hosted fallback, or provider execution is implied.

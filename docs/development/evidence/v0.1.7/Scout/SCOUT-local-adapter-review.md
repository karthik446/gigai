# SCOUT local Ollama adapter — independent review

Date: 2026-09-11  
Scope: read-only review of `src/gigai/adapters/ollama_local.py`,
`tests/test_ollama_local_adapter.py`, `SCOUT-local-adapter-implementation.md`,
and the controlling `SCOUT-local-runtime-decision.md`.

## Verdict

**Not ready for the installed-runtime caller until the digest representation
defect below is fixed.** This is an adapter correctness issue, not a request to
reopen the accepted OS-isolation decision. The controlling decision is applied
as written: the installed local runtime is trusted, but the adapter still has
to prove selected loopback model identity and fail safely on malformed data.

No source or test files were changed. This review made two offline synthetic
`httpx.MockTransport` probes only; it made no real Ollama, provider, model,
private-data, installation, activation, or suite call.

## Findings

### P0 — documented `/api/tags` digest can never match the required digest

`_checked_digest` admits only `sha256:<64 lowercase hex>`
([adapter:104-107](../../../../../src/gigai/adapters/ollama_local.py#L104-L107)),
but `_verify_identity` compares that value literally to `entry["digest"]`
([adapter:211-219](../../../../../src/gigai/adapters/ollama_local.py#L211-L219)).
Ollama's current official List Models example returns a **bare** 64-hex digest,
not a `sha256:`-prefixed digest ([Ollama List Models](https://docs.ollama.com/api/tags)).

Reproducer (offline, no socket): construct with selected digest `sha256:` + 64
`a`s and return the official-shaped tag `{"name":"gemma4", "digest": "a" *
64}`. Invocation stops before `/api/chat` with `OllamaLocalIdentityError`.
The purported successful test fixture instead returns the non-documentary
prefixed representation ([tests:46-47](../../../../../tests/test_ollama_local_adapter.py#L46-L47)),
so its identity-before/after assertion ([tests:83-110](../../../../../tests/test_ollama_local_adapter.py#L83-L110))
does not establish real-API compatibility.

Smallest fix: preserve one canonical selected identity, strictly normalize a
tag digest that is either exactly 64 lowercase hex or exactly `sha256:` plus
that value, then compare canonical forms. Reject every other type/form; add
offline success coverage for the official bare form and, if wanted, the
prefixed form, plus malformed/uppercase/wrong-digest negative cases.

### P1 — successful response is neither role-validated nor client-bounded

The chat contract documents `message.role` and `message.content`, and token
counts in `eval_count` ([Ollama Chat](https://docs.ollama.com/api/chat)). The
adapter only checks that `message` is a dictionary and that `content` is
non-blank ([adapter:280-287](../../../../../src/gigai/adapters/ollama_local.py#L280-L287));
it does not require `role == "assistant"`. It also transmits a `num_predict`
bound ([adapter:175-185](../../../../../src/gigai/adapters/ollama_local.py#L175-L185)),
but accepts arbitrary-size content and any non-negative `eval_count`
([adapter:289-303](../../../../../src/gigai/adapters/ollama_local.py#L289-L303)).

Second offline probe used valid prefixed fixture identity, request maximum 1,
and a complete chat response with `message.role: "user"`, 10,000 synthetic
characters, and `eval_count: 999`. It returned `success 10000 999`. Thus the
generation limit is requested but not enforced against an erroneous local HTTP
peer, and a non-assistant message can become a successful proposal input.

Smallest fix: require an exact assistant role; reject reported `eval_count`
greater than request `max_output_tokens`. Separately establish a response-byte
or content cap before materializing JSON/text, or document and test the
trusted-runtime-only resource limit if hard client-side capping is excluded.
Add wrong/missing-role, count-overrun, and oversized-response fixtures. Existing
tests cover incomplete and wrong-model responses
([tests:216-231](../../../../../tests/test_ollama_local_adapter.py#L216-L231)),
not these cases.

### P3 — port zero is accepted as an endpoint

The endpoint policy correctly restricts scheme, numeric host, credentials,
path, query, fragment, and redirect behavior
([adapter:65-88](../../../../../src/gigai/adapters/ollama_local.py#L65-L88)),
but `http://127.0.0.1:0` passes because only `port is None` is rejected. Port
zero is not a usable configured destination. Smallest fix: require
`1 <= port <= 65535` and add the constructor-negative test.

## Confirmed controls and evidence limits

- Request model equality, capabilities, output request upper bound, and
  reasoning restriction occur before identity requests
  ([adapter:162-171](../../../../../src/gigai/adapters/ollama_local.py#L162-L171)).
  Identity runs before prompt submission and again after it; changed version or
  digest is not returned as success
  ([adapter:173-197](../../../../../src/gigai/adapters/ollama_local.py#L173-L197)).
- The client disables environment proxy use and redirect following, and uses a
  finite 300-second-maximum timeout
  ([adapter:127-149](../../../../../src/gigai/adapters/ollama_local.py#L127-L149)).
  Non-200, JSON/type, timeout, and transport errors use content-free typed
  messages ([adapter:241-264](../../../../../src/gigai/adapters/ollama_local.py#L241-L264));
  `KeyboardInterrupt` maps to existing cancellation. There is no adapter
  logging path, so inspected source does not expose raw prompt/answer text in
  adapter errors or logs.
- Known `-cloud`/`-remote` tag forms and explicit remote/cloud-looking tag
  metadata are refused
  ([adapter:91-101](../../../../../src/gigai/adapters/ollama_local.py#L91-L101),
  [adapter:221-239](../../../../../src/gigai/adapters/ollama_local.py#L221-L239)).
  The documented tags example does not include these metadata fields, so tests
  prove adapter policy only, not that Ollama emits such markers.
- `close()` and context-manager support close the owned `httpx.Client`
  ([adapter:151-160](../../../../../src/gigai/adapters/ollama_local.py#L151-L160)).
  MockTransport/context-manager tests establish deterministic request sequence,
  not real socket/proxy behavior or a production caller's eventual close.

## Caller integration implications — deferred, not current defects

The implementation note accurately says this pure adapter is not yet a
config/factory/Scout integration ([implementation](SCOUT-local-adapter-implementation.md)).
It should remain that way for this review; missing integration is not scored as
an adapter defect.

When authorized, the next slice must add the explicit `ollama_local` branch in
the sole concrete-adapter factory and pass reviewed endpoint, model, digest, and
bounds; today it accepts deterministic, CLI, OpenAI, and OpenRouter adapters
([factory:61-90](../../../../../src/gigai/adapters/factory.py#L61-L90)). It must
also give lifecycle ownership a real `close()` path because `ModelInvocationPort`
contains only `invoke`.

Most importantly, `run_model_invocation` currently treats every non-
`deterministic` adapter as networked and denies it when `network_allowed` is
false ([model_execution:141-199](../../../../../src/gigai/model_execution.py#L141-L199)).
The caller must make an explicit reviewed distinction between permitted
numeric-loopback local transport and hosted network access, retaining no hosted
fallback and all selection, redaction, journal, output-validation, and caller
authority gates. No current test or this adapter slice proves that integration.

## Test status

The implementation artifact records **31 passed in 0.07s** and Ruff for these
paths, but neither was rerun in this review. Its fixtures are useful offline
checks; their prefixed tags cannot be accepted as real-API compatibility
evidence for the P0 path.


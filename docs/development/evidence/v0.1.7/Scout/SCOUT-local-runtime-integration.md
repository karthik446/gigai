# Scout local runtime integration — bounded core slice

Date: 2026-09-11

## Status and scope

This slice connects the accepted trusted-local-runtime decision to the normal
GigAI configuration, sole model-adapter factory, and durable G18 invocation
path. It is implementation evidence for configuration and caller wiring, not
OS-level isolation, Ollama installation/readiness, model-quality acceptance, or
Scout proposal integration. The local runtime remains a trusted prerequisite;
the endpoint and model identity checks do not prove that an Ollama daemon or
its process tree is incapable of reading other files or making network calls.

## Configuration and factory contract

`config.toml` may now declare the additive endpoint adapter
`ollama_local`:

```toml
[[endpoints]]
name = "ollama-loopback"
adapter = "ollama_local"
base_url = "http://127.0.0.1:11434"

[[model_targets]]
name = "qwen-local"
endpoint = "ollama-loopback"
model = "qwen3.8:8b"
capabilities = ["text"]
max_output_tokens = 512
model_digest = "sha256:<64 lowercase hex digits>"
context_tokens = 4096
max_response_bytes = 1048576
```

The endpoint has no credential and must use an explicit numeric
`http://127.0.0.1:<1..65535>` URL without credentials, path, query, or
fragment. `model_digest` is required for this adapter and accepts the same
full lowercase digest form as the transport (with optional `sha256:` prefix);
the context and response bounds are additive local-only fields with bounded
defaults when omitted. Remote/deterministic targets reject these local-only
fields, and all existing configurations render byte-identically because the
new fields are omitted unless configured.

`resolve_model_adapter` is still the only production binding point. Its local
branch constructs `OllamaLocalAdapter` with the configured endpoint, model,
digest, output bound, and local bounds; it does not pull a model, start a
server, read credentials, expose tools, or fall back to a hosted adapter. A
test-only `transport_overrides` mapping is injectable at this factory seam and
is not part of configuration authority.

## Invocation boundary and durable evidence

`InvocationPolicy.local_allowed` is distinct from hosted
`network_allowed`. A local target is blocked unless the caller explicitly
sets `local_allowed=True`; a remote target never treats that bit as hosted
network consent. The local caller must use a registered model-invocation role
(the focused lifecycle uses existing `researcher`); no new Scout role is
registered here.

For an explicitly allowed local target, selected reference bytes are passed to
the trusted local model without claiming that hosted redaction ran. The
durable invocation boundary records `redaction.result=not_applicable`,
`network.policy=local_loopback`, and `network.result=local_permitted`.
Denied local calls record `local_runtime_denied` and `local_denied` without
sending an identity or prompt request. Hosted paths retain the existing
redaction and network-consent checks. The factory binding closes an owned
transport on success, typed failure, cancellation, budget denial, and other
pre-invocation local blocks; remote adapters retain their existing behavior.

The adapter still verifies `/api/version` and `/api/tags` before `/api/chat`,
then rechecks identity after the response. Identity mismatch means no prompt
is sent (or a failed terminal attempt if the mismatch is discovered before a
response), and malformed/truncated/reasoning-only output is not success.
Invocation records remain private journal artifacts and contain no credentials
or raw error payloads.

## Focused lifecycle evidence

`tests/test_ollama_local_integration.py` uses a disposable offline lifecycle
fixture (`create_offline`/`approve_offline`), normal config parsing, the sole
factory, injected synthetic HTTP transport, and `run_model_invocation`.
It proves:

* complete local identity → chat → post-response identity succeeds with an
  existing `researcher` role and a contract-valid durable record;
* selected synthetic posting bytes reach only the local request path and the
  owned transport is closed;
* wrong digest fails before `/api/chat`, persists a failed terminal record, and
  closes the client;
* explicit local permission denial creates no HTTP requests and persists a
  blocked, contract-valid record without inventing a redaction pass;
* missing/malformed identity and out-of-bound local configuration are refused
  while remote/local legacy configuration behavior remains covered by the
  existing focused tests.

The existing read-only model-readiness seam now closes an owned binding after
configuration inspection or an explicit probe, and its target-configuration
digest includes the local model digest/context/response bounds. This keeps a
recorded readiness proof from surviving a local identity or bound change; the
existing fake readiness bindings remain compatible through an optional close
method.

Commands run in this worktree:

```text
$ .venv/bin/pytest -q tests/test_ollama_local_integration.py
6 passed in 2.56s

$ .venv/bin/pytest -q tests/test_ollama_local_integration.py \
    tests/test_g18_model_execution.py \
    tests/test_setup_configuration_diagnostics.py \
    -k 'not product_subprocesses_are_literal_argv_with_shell_disabled'
23 passed, 1 deselected in 11.34s

$ ruff check src/gigai/config.py src/gigai/adapters/factory.py \
    src/gigai/model_execution.py tests/test_ollama_local_integration.py
All checks passed!

$ .venv/bin/pytest -q tests/test_ollama_local_integration.py \
    tests/test_g26_model_discovery.py tests/test_model_contracts.py \
    tests/test_model_invocation_foundation.py tests/test_g18_model_execution.py
50 passed in 16.64s

$ ruff check src/gigai/config.py src/gigai/model_discovery.py \
    src/gigai/adapters/factory.py src/gigai/model_execution.py \
    tests/test_ollama_local_integration.py
All checks passed!
```

The excluded subprocess AST test currently fails on the pre-existing
`src/gigai/scout_materialization.py` call lacking an explicit `shell=False`;
that file is outside this slice. The initial implementation run of schema
verification stopped on the dirty `external-recording-plan-v2.schema.json`
expectation (`dcc34...84ce` versus observed `e341...adaf`). The correction
slice traced that resource to the SCOUT-06 strict v2 schema, preserved the
historical model-invocation:1 bytes, added the explicit local
`model-invocation-v2` resource, and reconciled the inventory; the current
verifier result is recorded in `SCOUT-local-runtime-corrections.md`.

## Integration gate and retained limits

No public CLI command is advertised by this slice: the current CLI does not
expose a local model invocation command. A future caller must bind readiness to
an operator-owned numeric endpoint and exact digest, keep local private
references out of hosted paths, and add bounded proposal-output completeness/
semantic checks before exposing any command. RUNTIME-01 comparison and
goal-specific acceptance, real installed-runtime dogfood, cancellation timing,
and Scout proposal/UI integration remain open. No provider call, model pull,
server launch, private record, activation, or application transition occurred
in this slice.

# SCOUT local runtime integration — independent review

Date: 2026-09-11  
Scope: read-only review of the trusted-local decision and the bounded core
integration in `config.py`, `model_discovery.py`, `adapters/factory.py`,
`model_execution.py`, the invocation schema/inventory, and
`tests/test_ollama_local_integration.py`. No source, schema, inventory, test,
configuration, journal, UI, proposal, or runtime file was changed by this
review.

## Verdict

The focused local path is correctly separated from hosted consent in the
reviewed source, and its synthetic lifecycle proves numeric-loopback
configuration, model identity checks, no prompt on identity failure, and
transport closure on the tested success/denial/identity-failure paths. It is
not release-ready as a durable integration because a request-construction
failure can leak an owned local binding, invocation evidence does not pin the
exact endpoint/model digest checked, and the current schema inventory has
unresolved hash/compatibility failures. These are bounded integration and
contract findings, not a request to reopen the accepted OS-isolation decision.

## Findings

### P1 — `run_model_invocation` can leak a local binding when request construction fails

`src/gigai/model_execution.py:251-260` constructs
`request = binding.request(...)` before entering the inner `try/finally` that
calls `binding.close()`. `ModelAdapterBinding.request` creates an
`InvocationRequest`, whose capability check can raise `ValueError`
(`src/gigai/adapters/port.py:58-66`) before `binding.port.invoke` is reached.
An `ollama_local` target whose configured capabilities omit `text` is accepted
by the general configuration capability syntax, so this is a reachable
malformed-target path: the outer `ValueError` handler at
`model_execution.py:283-285` records failure but `selection_reason` remains
`None`, and the later denied-path close at `:287-296` is skipped. The local
adapter's owned `httpx.Client` is therefore not closed on every error path.

Smallest correction: put request construction and invocation under one
binding-owned `try/finally`, or close immediately if request construction
fails, while preserving the existing remote adapter behavior. Add one
synthetic local capability-mismatch test that asserts no `/api/chat` and an
owned transport close.

### P1 — durable invocation identity does not pin the checked local endpoint and digest

The factory passes configured endpoint, model, digest, and bounds into
`OllamaLocalAdapter` (`src/gigai/adapters/factory.py:80-103`), and the adapter
checks `/api/version` and `/api/tags` before and after `/api/chat`. However,
`run_model_invocation` records only `configured_selector=model_target` and
`endpoint_identity=endpoint.name` (`src/gigai/model_execution.py:312-325`),
while `InvocationResult` has no runtime identity fields. The durable
`model-invocation` schema likewise has no target-configuration digest or
selected model digest field (`src/gigai/schemas/model-invocation.schema.json:30-65`).
The separate readiness digest includes local digest/context/response bounds
(`src/gigai/model_discovery.py:275-305`), but invocation does not require or
reference a readiness proof.

Thus the transient adapter check is real, but a later journal reader cannot
prove which numeric endpoint URL and exact model digest were checked for a
given invocation from the invocation record alone. This is an evidence
pinning gap, not evidence that the adapter used a remote target. The next
caller/schema owner should bind a target-configuration identity artifact or
explicit digest to the invocation record, with a versioned compatibility plan;
do not silently add fields to the historical schema.

### P0 — schema identity and inventory are not currently coherent

The checked worktree has directly observable conflicting authorities:

* `model-invocation.schema.json` is modified in place, expanding redaction and
  network enums (`git diff`, schema lines 104-140), while its `$id` remains
  `urn:gigai:schema:model-invocation:1` and its common `schema_version` remains
  `1.0` (schema lines 2-4 and `common.schema.json:6-9`). The repository's own
  compatibility rule requires exact supported versions and says additive
  changes create a new minor schema version (`src/gigai/schemas/README.md:169-173`).
  This changes the accepted historical value domain without a versioned reader
  boundary.
* The current source schema hashes to
  `751a485785bffbf23ba16cf338b742557caebf27ef5527d821ad549b63bbcb42`, and
  `src/gigai/schemas/SHA256SUMS:21` records that value, but the independent
  installed-schema verifier still expects the historical
  `756ca9eb7a746e3f0b6700b028c4807ed98050e15df29d182aeed73335e51bd6`
  (`tools/verify_installed_schemas.py:49`). The focused integration test's
  `validate_model_invocation` success therefore does not establish historical
  schema compatibility.
* The verifier was expanded in the current diff to 64 entries, matching the
  current `validators.SCHEMA_NAMES` count, but the verification command stops
  first on `external-recording-plan-v2.schema.json`: expected
  `dcc34e3834bfcd7688193ffb4d564fa7003b0ef1bbfef91044c17e3bac2845ce`, observed
  `e341a79cf759505ced0833b88ab27ccb3e8ee79f2f92990f0204984fe9fadaf0`. That
  schema is untracked in this worktree while its expected hash is newly listed
  in both the dirty checksum inventory and verifier. Its provenance cannot be
  called pre-existing from the available evidence; the mismatch must be
  attributed and corrected before claiming installed-schema verification.

Smallest safe disposition: stop treating this inventory as release evidence;
have the schema owner reconcile the untracked artifact and checksum, then
choose an explicit model-invocation schema version/reader compatibility path
for the local boundary. Updating only a checksum would hide the historical
contract change.

## Controls that hold in this slice

Configuration rejects non-numeric/non-loopback local endpoints, credentials,
paths, queries, fragments, and port 0 (`src/gigai/config.py:569-590`), requires
a local model digest and bounded local options (`config.py:832-863`), and
rejects local-only fields on remote targets. The sole factory branch selects
`OllamaLocalAdapter` with the configured digest and has no hosted fallback
(`src/gigai/adapters/factory.py:70-124`). Adapter construction preserves
`trust_env=False`, no redirects, finite timeout, identity-before/after checks,
and exact digest normalization (`src/gigai/adapters/ollama_local.py:146-181`,
`:208-237`).

The local/hosted consent split is explicit in
`src/gigai/model_execution.py:149-223`: local targets require
`policy.local_allowed`; remote targets ignore that bit and require
`network_allowed` (or remain blocked offline). Local denial skips input
materialization, redaction, and adapter HTTP calls. `_select_references` and
`_build_provider_input` use only the exact caller-selected IDs and verify
bytes/digests before constructing provider input (`model_execution.py:381-406`),
with no fallback branch. This is source evidence of the boundary, not a claim
that a trusted Ollama daemon's entire process tree is offline.

`model_discovery.resolve_target_readiness` and the explicit probe close their
owned binding in `finally` (`src/gigai/model_discovery.py:431-469` and
`:485-505`), and the adapter's stream context closes response resources on
HTTP, timeout, malformed-response, and cancellation paths. The missing
`binding.request` close path above remains the integration exception.

The focused integration tests prove a synthetic complete local lifecycle,
wrong-digest refusal before `/api/chat`, local permission denial with no HTTP
calls, closure on those paths, and bounded local configuration negatives. They
do not prove private-data behavior with real records, installed-runtime
readiness, durable endpoint/digest pinning, cancellation timing through the
core caller, or the local-vs-hosted matrix in one test.

## Evidence and limits

Focused synthetic command run in this worktree:

```text
$ rtk .venv/bin/pytest -q tests/test_ollama_local_integration.py
6 passed in 2.68s
```

Schema inventory probe (read-only) failed:

```text
$ rtk .venv/bin/python tools/verify_installed_schemas.py
schema digest mismatch for external-recording-plan-v2.schema.json:
expected dcc34e3834bfcd7688193ffb4d564fa7003b0ef1bbfef91044c17e3bac2845ce,
got e341a79cf759505ced0833b88ab27ccb3e8ee79f2f92990f0204984fe9fadaf0
```

No provider/model/network call, model download, installation, activation,
real private data, broad suite, or source change was performed. The
integration report's sample `gigai model invoke --target ... --local-allowed`
command (`SCOUT-local-runtime-integration.md:142-153`) is explicitly
nonexistent; remove it or label it as an unimplemented future CLI sketch,
rather than presenting it as a supported dogfood command. Config/factory/
model-execution correction, schema migration/inventory reconciliation, and
CLI/proposal/journal/projection/UI integration remain separate owner gates.

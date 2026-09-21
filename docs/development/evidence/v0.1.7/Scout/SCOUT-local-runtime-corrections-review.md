# SCOUT local runtime corrections — independent re-review

Date: 2026-09-11  
Scope: bounded, read-only re-review of `SCOUT-local-runtime-corrections.md`
against the trusted-local decision. I inspected the corrected
`config.py`, `model_discovery.py`, `adapters/factory.py`,
`adapters/ollama_local.py`, `model_execution.py`, `validators.py`, `run.py`,
the invocation schemas/inventory, Scout's proposal request helper, and the
focused synthetic integration fixtures. No source, schema, test, config,
journal, activation, provider, local model, or network state was changed.

## Verdict

The correction addresses the previously reported request-construction close
path and adds a distinct strict v2 local invocation shape with configured and
observed identity fields. The local-versus-hosted consent split, numeric
loopback checks, exact selected bytes, no fallback, v1/v2 dispatch in the
validator, and reconciled 65-schema inventory are bounded and credible in the
inspected slice. The actual Scout-proposal caller remains a legitimate next
integration seam, not a regression in this runtime slice; this review does not
claim whole-Scout acceptance or reopen OS-isolation scope.

## Findings

### Accepted — request construction is now inside the binding close boundary

`model_execution.py` now places both `binding.request(...)` and
`binding.port.invoke(...)` in the same `try/finally` (`:250-264`). A
capability mismatch raised by `ModelAdapterBinding.request` therefore closes
the factory-owned local binding before the outer typed-error handling. The
existing denied/budget/error/cancellation path also closes a binding when
`selection_reason` is set. This fixes the prior reachable leak where request
construction happened before the inner `finally`.

The correction's synthetic capability-mismatch and `KeyboardInterrupt`
fixtures are useful bounded controls: they expect no chat call and assert
closure. They do not establish every possible future adapter's ownership
contract or end-to-end cancellation timing, so those remain ordinary future
integration checks.

### Accepted, with evidence limit — local consent cannot become hosted consent

The execution branch classifies `endpoint.adapter == "ollama_local"` as a
local runtime and requires `InvocationPolicy.local_allowed` before selected
bytes are materialized or any adapter call occurs (`model_execution.py:149-223`).
For remote adapters, the branch remains governed by `network_allowed` and
`offline`; `local_allowed` is not consulted as hosted permission. The factory
selects only the configured local adapter, and no fallback branch is present.
Configuration rejects non-numeric/non-loopback local endpoint forms,
credentials, paths, queries, fragments, and port zero, while requiring the
configured local model digest and bounded options. These facts support the
trusted-local decision's consent boundary; they do not prove process-wide
network isolation or private-data behavior with real records.

### Accepted for the bounded slice — configured/observed identity is recorded

The trusted-local decision does not require a separate readiness artifact. A
self-contained host-resolved configured endpoint/model/digest and its
configuration digest persisted in the journal can satisfy bounded identity
pinning, provided the host resolves and validates those values before
invocation. I therefore do not find a violated accepted requirement here.

The v2 record now carries `local_identity` with configured endpoint, model,
canonical digest, context/output/response bounds, a configuration digest, and
an observed object. `_local_identity` only marks observed status passed when
the adapter result supplies a nonempty runtime version and a valid canonical
digest; otherwise it preserves `not_observed`/null rather than inventing
metadata. The adapter performs identity checks before and after chat, and the
readiness calculation includes local identity/bounds in its configuration
digest.

The record's self-contained identity remains intentionally distinct from an
external authority ID: a later reader can validate the persisted endpoint,
model, digest, bounds, and configuration digest without requiring a new
readiness-resource contract. A future journal integration may additionally
cross-link host resolution or readiness evidence for audit convenience, but
that is future work rather than a blocker or a new requirement imposed by this
review. Any such linkage must preserve exact request and identity bytes and
must not add unversioned fields to model-invocation:1.

### Accepted runtime boundary; next integration — strict v2 dispatch and the
Scout proposal reader/caller

`validators.validate_model_invocation` explicitly dispatches canonical bytes
with `schema_version == "2.0"` to `model-invocation-v2.schema.json`, otherwise
to the historical v1 schema. `run._provider_invocation_records` reads
`record.json` bytes and calls that validator before accepting records, so the
generic Run reader does not blindly downgrade a v2 record. The external
recording readers similarly use `_recorded_dispatch` to select v1 versus v2
by exact envelope version and reject unsupported versions; focused external
domain tests cover unknown-version refusal and strict v2 artifact fields.

That is not universal Scout routing proof. `scout_proposals.build_invocation_request`
constructs the existing port request with the registered `model_invocation`
role `reviewer`, while `tests/test_ollama_local_integration.py` exercises
`run_model_invocation` with role `researcher`. The proposal helper is pure and
does not call `run_model_invocation`, write a v2 record, or pass through
`run._provider_invocation_records`; the integration fixture therefore proves
the researcher route only. `roles.py` registers both names, so merely seeing a
registered researcher is insufficient evidence that Scout's reviewer request
has an approved caller/reader path.

Required next seam: an owner must wire the host-selected proposal request to
the existing execution/journal path, explicitly use the reviewer role, emit
the strict v2 record, and have the actual reader validate it. That caller must
also own exact request-envelope/output binding and target/readiness lineage;
the model response cannot supply or override those identities. Until then,
proposal construction and local integration remain separate bounded helpers,
not end-to-end Scout acceptance. This is the planned next integration, not a
regression or a release blocker for the already-scoped local runtime slice.

### Accepted — historical model-invocation:1 bytes are not silently upgraded

The corrected v2 schema has a distinct `$id`
`urn:gigai:schema:model-invocation:2` and requires `schema_version: "2.0"`
plus `record_version: 2`. The historical
`model-invocation.schema.json` remains the v1 resource and the validator
chooses v2 only from an explicit `2.0` value. `run.py` and the existing
provider-review reader continue to validate v1 bytes through their v1 paths;
the provider-review path intentionally requires v1 reviewer records. This is
the right compatibility direction.

The claim is limited to source-level dispatch and focused fixtures. A release
must still preserve and replay a committed historical v1 `record.json` byte
for byte through the real journal reader, alongside a v2 record, before
claiming historical compatibility.

### Correction note — schema and inventory conflicts from the prior draft are
resolved

The prior review text copied stale observations from an earlier worktree state:
it reported a modified historical model-invocation schema and conflicting
hashes. Fresh inspection shows an empty diff for
`src/gigai/schemas/model-invocation.schema.json`, and the corrected v2 resource
is separately dispatched by explicit `schema_version: "2.0"`. The repository
README's compatibility rule is therefore satisfied by the observed current
v1/v2 separation; this report does not repeat the old P0 finding.

The current inventory probe passes all 65 packaged schemas. The earlier plan-v2
hash mismatch and old 64-resource count were stale evidence, not current
blockers. The corrected inventory is now evidence of packaging consistency,
while it still does not by itself prove a live installed Ollama runtime or
whole-Scout readiness.

## Evidence and limits

Fresh read-only commands run during this correction review:

```text
$ rtk .venv/bin/python tools/verify_installed_schemas.py
verified 65 installed GigAI schemas

$ rtk .venv/bin/pytest -q tests/test_ollama_local_integration.py \
    research/scout-qwen-extraction/test_run_extraction.py
13 passed in 4.85s

$ ruff check src/gigai/config.py src/gigai/model_discovery.py \
    src/gigai/adapters/factory.py src/gigai/adapters/ollama_local.py \
    src/gigai/model_execution.py src/gigai/validators.py src/gigai/run.py \
    tests/test_ollama_local_integration.py \
    research/scout-qwen-extraction/test_run_extraction.py
All checks passed!

$ git diff -- src/gigai/schemas/model-invocation.schema.json
[no output; empty diff]
```

The coordinator's broader reported totals are `50 focused`, `8 integration`,
and `65 schema`; this review ran only the exact bounded commands above, not the
full suite. No provider/model/network call, model download, activation,
private-data test, or broad test suite was run. The nonexistent sample command
in the integration report (`SCOUT-local-runtime-integration.md:142-153`)
should be removed or clearly labeled a future CLI sketch. Deferred gates
include installed-runtime readiness, proposal caller/journal/projection
wiring, semantic proposal quality, and UI/CLI integration; no automatic
Tailor/application action is implied.

# SCOUT R7 fresh installed live-comparison proof — 2026-09-21

## Verdict

This bounded proof is **blocked before comparison execution**. The exact
installed wheel, public local setup syntax, disposable Gig lifecycle, public
run-input import, and one bounded readiness probe per configured backend all
worked, but the supported public Graph Set path cannot produce the immutable
runtime_comparison.output_contract_ref required by the corrected reader:
graph-set propose allocates its proposal ID after it has already sealed the
evaluation-contract bytes. No provider comparison, six-call run, durable Run,
or model-quality claim was made after this blocker, and no private journal
injection or hand-edit bypass was used.

The repository has no AGENTS.md at the workspace or parent workspace paths;
the available /Users/kar/.codex/RTK.md was read. Existing product/source,
historical workpads, the prior comparison, shared virtual environments, and
global configuration were not modified. All artifacts below are disposable
under /private/tmp/gigai-r7-fresh-live-20260921/.

## Exact installed boundary

    rtk shasum -a 256 /private/tmp/gigai-r7-contract-coherence/dist/gigai-0.1.7-py3-none-any.whl
    0371662823de1b963455f50a967d226c95cfe2fadbefe03557630b3f844ca5b7  gigai-0.1.7-py3-none-any.whl

    rtk uv venv /private/tmp/gigai-r7-fresh-live-20260921/venv
    rtk uv pip install --python /private/tmp/gigai-r7-fresh-live-20260921/venv/bin/python /private/tmp/gigai-r7-contract-coherence/dist/gigai-0.1.7-py3-none-any.whl
    rtk /private/tmp/gigai-r7-fresh-live-20260921/venv/bin/python -I -c 'import gigai, gigai.runtime_comparison as m; print(gigai.__file__); print(m.OUTPUT_CONTRACT_VERSION)'

The wheel installed with 17 dependencies. The isolated import resolved to
/private/tmp/gigai-r7-fresh-live-20260921/venv/lib/python3.13/site-packages/gigai/__init__.py
outside the checkout and printed r7-output-contract:1.

## Public setup and lifecycle

The successful local setup used the shipped endpoint/digest syntax:

    gigai setup --non-interactive --home .../home --workpad-root .../workpads \
      --editor /usr/bin/true --no-open-with-target \
      --endpoint 'ollama_r7=ollama_local:http://127.0.0.1:11434' \
      --model-target 'qwen_r7=ollama_r7:qwen3.8:latest@sha256:22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643' \
      --create-model-target qwen_r7 --json

It returned config_changed:true, schema version 2.0, and passed atomic
replace/interprocess-lock checks. A second public setup update added the
existing subscription target without storing credentials:

    gigai setup --non-interactive --home .../home --workpad-root .../workpads \
      --editor /usr/bin/true --no-open-with-target \
      --model-target 'luna_r7=codex:gpt-5.6-luna' --create-model-target qwen_r7 --json

Public init, create, approve, layout migrate, and run-input add then completed
in the disposable scope. IDs:

- project: project_1390a50d-d68a-4185-af2b-82d0589834b7
- Gig: gig_12c34a1c-356c-4de3-a9b5-542cff69ab74, approved v1/tag gig-v000001
- initial proposal: gp_0192fb78-a2b2-4f5e-a212-185ce3267b4a
- imported pack: input_479921c3-7592-472b-add5-796720668120
- pack raw SHA-256: sha256:dce130e9d7f659d9b6c82510fd79646ebc1851a286d4070b6249c1ad2904e53d, 5374 bytes

The initial attempt to pass codex=codex_cli to non-interactive setup was
rejected by the public parser; Codex was then discovered from the local
subscription CLI runtime, and luna_r7=codex:gpt-5.6-luna was added using the
public model-target syntax. No secret or credential value was printed.

## Bounded readiness probes

Exactly one explicit readiness probe was run for each backend, with no repeat,
fallback, download, or daemon startup:

    gigai models --probe qwen_r7 --home .../home --json
    probe: adapter=ollama_local, endpoint=ollama_r7, model=qwen3.8:latest,
           target=qwen_r7, readiness=usable,
           states=[configured,compatible,authenticated,verified,usable,selected]

    gigai models --probe codex-default --home .../home --json
    probe: adapter=codex_cli, endpoint=codex, model=default,
           target=codex-default, readiness=usable,
           states=[detected,configured,compatible,authenticated,verified,usable]

The Ollama target used the required loopback URL and pinned digest
sha256:22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643.
The Codex probe verified the authenticated CLI runtime (codex-cli 0.155.1);
the separately configured luna_r7 target was not probed again, preserving the
one-probe-per-backend bound.

## Exact public-path blocker

The disposable pack contains all three shipped synthetic cases and
selected_graph.output_contract_version = r7-output-contract:1. Its sealed
evaluation contract necessarily used this placeholder because the public
command allocates the Graph Set proposal ID internally:

    {"runtime_comparison":{"output_contract_version":"r7-output-contract:1","output_contract_ref":{"path":"PLACEHOLDER/output_contract.json","content_sha256":"sha256:6ccf4cce4f97aabd2cbedeecbe85b5122cf541f5e1d13a9007d8e86c23478a21","media_type":"application/json","size_bytes":129}}}

The supported command accepted the definition and allocated
gp_f50d674a-0370-45c6-a887-2a35cb8c5a9b:

    gigai graph-set propose --definition .../definition/graph/definition.json \
      --gig gig_12c34a1c-356c-4de3-a9b5-542cff69ab74 --target .../target --home .../home --json

Its staged descriptor now has the immutable ref:

    {"path":"manifests/graph-sets/gp_f50d674a-0370-45c6-a887-2a35cb8c5a9b/r7-fresh/output_contract.json","content_sha256":"sha256:6ccf4cce4f97aabd2cbedeecbe85b5122cf541f5e1d13a9007d8e86c23478a21","media_type":"application/json","size_bytes":129}

The evaluation contract was already sealed, so it cannot be updated to that
path through the same public proposal. Approve was not attempted because it
would create an authority the corrected reader must reject. A public reject
attempt exposed an additional disposable-workpad lifecycle defect:

    Error: proposal is not pending and valid: schema_invalid, schema_invalid

    gigai check ... --json
    {"findings":[
     {"code":"schema_invalid","location":"$","message":"'goal_graph' is a required property"},
     {"code":"schema_invalid","location":"$","message":"Additional properties are not allowed ('graph_set' was unexpected)"}
    ],"valid":false}

The pending proposal remains confined to the disposable workpad. No approved
Graph Set, comparison, Run, result, show/status record, provider prompt, or
provider output was created; public status retained active version 1 with the
proposal pending. This is the exact supported-path gap requested by the task:
creating the pinned binding needs a public operation that knows the proposal ID
before sealing the evaluation contract, or an implementation change. Manual
journal injection or rewriting would bypass the authority boundary and was
not done.

## Disposable hashes, logs, and limits

    52dd2d34c43e54c622f25efa6fee373e524903499d7c776b02d3e731232165e8  make_pack.py
    3226171a5845eb72a7f477d14e86d718becd69f56ecda823297b8e3bdc30667f  make_graph_definition.py
    b79901665e912685f69ef1dec3e29087ee9ca695418c84956afa3ed27842f7be  invocation.json
    dce130e9d7f659d9b6c82510fd79646ebc1851a286d4070b6249c1ad2904e53d  definition/evaluation-pack.txt
    a5851f0e9819a8ec3982b77eb47751dc80139b41265961b7872a240ae33a23f0  definition/graph/definition.json
    ea42bd1f61ceb35900288a06439812e94689ead24d5714bfa9dd27883022bdc5  definition/graph/evaluation.json
    6ccf4cce4f97aabd2cbedeecbe85b5122cf541f5e1d13a9007d8e86c23478a21  definition/graph/output.json
    c2585d8d7e0ebcbad6ac32503b0ef1584cb6fedb1c4a59a225dbf727b412cb52  logs/models-after-setup.json
    c870d3309b34c9d2598346bc90d7b78fbad30210995422d84898a9d96eab498f  logs/gig-status.json
    6978b5f58ad3e7bebc03675c0781015700ce209924d2c9b46b0023167375be76  logs/proposals.json
    e5660a18dde53dfe4425adf41d794d01145320bf245c45aec2607e587c884f0c  logs/check.json

No comparison calls were made, so there are no transmitted model prompts,
outputs, latency records, retries, Run IDs, result refs, or quality scores.
The proof establishes installed setup/readiness only, not live execution or
model quality, and makes no claim over the three synthetic cases.

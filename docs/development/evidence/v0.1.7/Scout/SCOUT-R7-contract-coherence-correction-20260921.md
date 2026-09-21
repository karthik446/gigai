# SCOUT R7 contract-coherence correction — 2026-09-21

## Scope and result

This correction addresses the two bounded findings in
`SCOUT-R7-live-corrections-review-20260921.md`. The existing capability-refusal
correction and all unrelated dirty work were left untouched.

P1 is corrected in the production comparison loader without adding a second
authority path. For a fresh pack that declares `output_contract`, every case
must declare the same supported `r7-output-contract:1` version as
`selected_graph.output_contract_version`. The authenticated evaluation
contract now also carries the smallest binding needed by the existing
Graph Set → evaluation contract → pack-ref chain:

```json
"runtime_comparison": {
  "output_contract_version": "r7-output-contract:1",
  "output_contract_ref": {
    "path": "<approved Graph Set>/output_contract.json",
    "content_sha256": "<approved bytes>",
    "media_type": "application/json",
    "size_bytes": 125
  }
}
```

The loader compares this immutable binding with the selected approved Graph
Set output-contract reference before creating comparison intent, runs, or
invocations. An explicit contradiction in the selected graph version, case
version, or evaluation binding is rejected. Packs with no case-level
`output_contract` retain the historical path: their stored bytes remain
readable and their original prompts are returned unchanged.

P2 is corrected in `validate_evaluation_pack`: public criterion IDs are
required to be strings, unique, and exactly equal to the keys of that case's
`expected.criteria` mapping (set equality; order is not significant). Missing,
extra, duplicate, or renamed IDs are rejected before the comparison path can
invoke a provider. `_comparison_prompt` exposes only the public JSON shape and
criterion IDs/descriptions; expected statuses, evidence, and gold answers are
not copied into the prompt.

## Production-path regression evidence

`tests/test_runtime_comparison.py` uses the production lifecycle services and
production `run_comparison`, `comparison_status`, and `show_comparison` paths.
The fixture creates an approved disposable Graph Set and evaluation contract,
uses an injected offline invocation transport for valid execution, and keeps
the journaled pack pretty-printed while authenticating its canonical digest.
The four incoherence cases (selected graph version, renamed ID, extra ID, and
missing IDs) each use an invocation spy; all were refused before invocation,
with no new `runs` or `comparisons` entries. Public prompt capture in the
existing valid-path test confirms the contract shape and descriptors are
present while private expected/gold fields are absent.

Historical and byte-identity coverage remains in the focused lane: bundled,
pretty, and whitespace pack reloads retain one canonical digest; the existing
changed-byte, forged-reference, foreign-authority, nested-result, and later
publication-head negatives remain green; a pack without `output_contract` and
its old `r6-output:1` selected-graph marker validates and retains its original
prompt.

## Commands and results

All commands were run from the workspace unless noted.

```text
rtk .venv/bin/pytest -q tests/test_runtime_comparison.py
17 passed in 43.46s

rtk ruff check src/gigai/runtime_comparison.py tests/test_runtime_comparison.py
[]

rtk .venv/bin/python -m compileall -q src/gigai/runtime_comparison.py tests/test_runtime_comparison.py
success (no output)

rtk .venv/bin/python tools/verify_installed_schemas.py
verified 82 installed GigAI schemas
```

No schema or bundled-pack bytes were changed in this correction; their current
hashes are recorded for inventory coherence:

```text
c43f43f8c1536c37d8bfcc6124709621a2e0c069da445c595cb0f12e7febe87c  src/gigai/schemas/runtime-evaluation-pack.schema.json
3624398d5aeaff93f640c60aef88aa1ece26a0d8319e47f916dfce6429a304bd  src/gigai/data/runtime-comparison-pack-v1.json
```

The dependency-complete disposable wheel proof used a fresh directory
`/private/tmp/gigai-r7-contract-coherence/`:

```text
rtk uv build --out-dir /private/tmp/gigai-r7-contract-coherence/dist
rtk uv venv /private/tmp/gigai-r7-contract-coherence/venv
rtk uv pip install --python /private/tmp/gigai-r7-contract-coherence/venv/bin/python \
  /private/tmp/gigai-r7-contract-coherence/dist/gigai-0.1.7-py3-none-any.whl
rtk /private/tmp/gigai-r7-contract-coherence/venv/bin/python -c \
  'import gigai, gigai.runtime_comparison as m; print(gigai.__file__); print(m.OUTPUT_CONTRACT_VERSION)'
```

The import resolved outside the checkout to
`.../venv/lib/python3.13/site-packages/gigai/__init__.py` and printed
`r7-output-contract:1`. The wheel installed with 17 dependencies and hashes
are:

```text
0371662823de1b963455f50a967d226c95cfe2fadbefe03557630b3f844ca5b7  gigai-0.1.7-py3-none-any.whl
11fb92ba696cc1ffc67808930857dee6c0c7f5f91f1972e5cc5f867ec638a9a5  gigai-0.1.7.tar.gz
```

Using the installed CLI against the disposable copy of the existing synthetic
comparison (`/private/tmp/gigai-r7-live-corrections-0F5F5u/read-workpads`, with
the previously prepared registry locator and read-only source workpad copy),
both public reads succeeded:

```text
gigai comparison show comparison_1455227f-fc9d-4893-87d4-cc86cb368422 \
  --gig gig_a5ed1db7-715c-4329-86f4-987477806008 \
  --home /private/tmp/gigai-r7-live-corrections-0F5F5u/read-home \
  --target /private/tmp/gigai-r7-live-comparison-20260921/synthetic-target --json
gigai comparison status comparison_1455227f-fc9d-4893-87d4-cc86cb368422 \
  --gig gig_a5ed1db7-715c-4329-86f4-987477806008 \
  --home /private/tmp/gigai-r7-live-corrections-0F5F5u/read-home \
  --target /private/tmp/gigai-r7-live-comparison-20260921/synthetic-target --json
```

Both returned comparison ID
`comparison_1455227f-fc9d-4893-87d4-cc86cb368422`; `show` retained the old
pack digest
`sha256:fe708376c8f2b4b1f8045f295574e8a7b02e1d7138769e77492e1b5a6c4ecc29`
and `status` returned both historical attempts. These reads did not open the
original workpad for writes.

Current source hashes for this correction are:

```text
4ceb7644f82822bb96adad01b0df0872721c5b3687b7b9eff2a43796d2ab4158  src/gigai/runtime_comparison.py
2faa0863f3b228ae49ad9a8fadb616df380d1af14d58dffc71d9d77e9fbee747  tests/test_runtime_comparison.py
```

## Limits and unproven gates

No live model/provider/network inference, download, daemon startup, private or
real user data, shared virtual-environment mutation, global configuration
change, publication, commit, tag, or push was performed. The valid fresh
Graph Set flow proves production load → approve → injected offline run → fresh
show/status in focused tests; it does not prove a live provider/model, and the
installed-wheel CLI proof intentionally reads the pre-existing synthetic
comparison rather than creating new historical artifacts. The report does
not claim whole-suite acceptance.

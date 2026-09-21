# SCOUT R7 public binding final closure — 2026-09-21

## Scope and boundary

This closure was implemented and exercised only in the disposable scratch copy
`/private/tmp/gigai-r7-public-binding-correction-20260921`, whose source/test
inputs were copied from the current dirty candidate (not from git HEAD). The
main checkout remains source-frozen; this report is the only main-checkout
artifact written by this task. No schema, resource, build metadata, historical
workpad, private data, global configuration, provider/model call, commit, tag,
push, publication, or shared virtual-environment change was made.

Input manifest SHA256:

```
9cf5fa3194a8d11e991b44068af08e64422cee4c6a723991327cbe60f52e4d89
```

The transferable scratch patch is
`/private/tmp/gigai-r7-public-binding-correction-20260921/SCOUT-R7-public-binding-correction-transfer.patch`
(494 lines, SHA256
`9b4b59af4a437403d49921f7968a55d35c8f95602b7feca9c939b653a2a56f25`). It
contains only the scratch lifecycle/CLI implementation and focused regression
test changes; it was not applied to the main checkout.

## Corrections

`reject_offline` and the public `reject` command now accept an optional explicit
Gig identifier (`--gig` in the CLI). Omission preserves the legacy resolution
path, while an explicit selection resolves exactly that Gig and requires both
the pending proposal's `gig_id` and `proposal_id` to match. There is no active
Gig auto-selection, broad scan, fallback to another Gig, active-version
overwrite, or mutation of an unrelated instance; active-version rejection
continues to refuse the proposal. Focused v2 and historical v1 tests cover
successful fresh rejection, wrong Gig/proposal refusal, untouched other state,
active-version refusal, and omitted/explicit legacy behavior.

Optional `canonical_sha256` is now retained on both staged artifact and
rewritten evaluation references. When supplied, staging recomputes the digest
from the actual JSON output bytes using the existing canonical JSON digest
semantics, requires the JSON media type, and rejects a false or malformed
assertion before publication; it never copies an unverified descriptor value.
The existing exact reference equality check remains in force, so the resulting
Graph Set output descriptor and evaluation binding carry one authenticated
representation. No schema redesign or new authority object was introduced.

Scratch file SHA256 values after the final changes:

```
src/gigai/lifecycle.py                       f3e29a6713e7877c93129e67dbbd33de8894f91f35d142d8ca6996b611f4320c
src/gigai/cli.py                             297751e44bb9fc1776100410ebebe66d19a180767734639ceb1f77f326952de8
tests/test_runtime_comparison.py             04bc9963ba76b30102e83392f88d4b110fac9f1989dc7de2d8d18bfed268ae8f
research/.../probe.py                        895d230a0bcfcd7e7489feb5d69ce0473b5875903a86e1629bc23a6edeb4815c
```

## Verification

All commands below were run against the scratch source unless noted otherwise.
The focused production regression suites completed as follows:

```
.venv/bin/pytest -q tests/test_runtime_comparison.py
28 passed in 148.03s (0:02:28)

.venv/bin/pytest -q tests/test_scout02_graph_set_flow.py tests/test_scout05_first_proposal.py
16 passed in 51.88s

.venv/bin/pytest -q tests/test_g08_offline_create_lifecycle.py -k 'reject'
1 passed, 9 deselected in 1.23s
```

The final installed targeted regression run used the rebuilt wheel in a fresh
dependency-complete environment and production imports:

```
PYTHONPATH=/private/tmp/gigai-r7-public-binding-correction-20260921 \
  /private/tmp/gigai-r7-public-binding-installed-closure-20260921/bin/pytest -q \
  tests/test_runtime_comparison.py -k 'first_graph_proposal or reject_preserves_active or optional_output_canonical or false_optional or historical_v1_pending'
5 passed, 23 deselected in 21.09s
```

The rebuilt wheel is
`/private/tmp/gigai-r7-public-binding-correction-20260921/dist/gigai-0.1.7-py3-none-any.whl`,
SHA256
`bd82e0adba492dfdb93212c616a7af06e1ebca4ff7aefce214ea3d37398328cb`.
The installed CLI reported `gigai 0.1.7`; it was imported outside the checkout
from `/private/tmp/gigai-r7-public-binding-installed-closure-20260921/`.

The installed public-path offline probe used a fresh synthetic scope, public
creation/proposal/check/approval paths, injected offline invocation, and a
fresh-process comparison read. It reported `approved_version:2`, a durable
comparison ID ending in `0014`, six prompt calls, `prompt_contract_shape true`,
`prompt_has_private_gold false`, and `mismatch_invocations 0`; a deliberately
incoherent version was refused with
`RuntimeComparisonError: evaluation pack output contract version is not
coherent`, and `mismatch_state_unchanged true`. Public subprocess commands
then read the new comparison using `comparison show --json` (12,560 bytes) and
`comparison status` (725 bytes) without changing the pre-existing history.

The same installed targeted tests exercised the fresh v2 reject lifecycle and
the optional canonical digest path. The public smoke summary included
`check valid true`, a fresh synthetic Gig ID, and an approved v2 proposal;
canonical output references were equal across the output descriptor and
evaluation binding, while a false digest was rejected before publication.

Static checks on the changed surface:

```
.venv/bin/ruff check src/gigai/lifecycle.py src/gigai/cli.py tests/test_runtime_comparison.py --select F401,F821
All checks passed
.venv/bin/python -m compileall -q src/gigai tests/test_runtime_comparison.py
passed
.venv/bin/ruff check research/scout-r7-public-binding-correction/probe.py
All checks passed
```

## Limits and handoff

This is bounded synthetic/offline evidence, not provider quality or whole-suite
acceptance: no live model/provider/network inference was run, and no full test
suite was run. The focused suites and installed smoke prove the public
construction, authenticated binding, rejection boundaries, and fresh read
path, but do not establish general comparison quality beyond the synthetic
cases. The coordinator may review and selectively integrate the transferable
patch after the frozen lane settles; this task intentionally left all main
source/test/tool/schema/build bytes unchanged.

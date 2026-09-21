# SCOUT R5/R6 final correction review

Date: 2026-09-20 (America/Denver)  
Reviewer: independent bounded review  
Status: all five requested correction findings are closed in the inspected
synthetic implementation slice. This is not installed-wheel, live-provider,
publication, or R7 acceptance.

## Scope and evidence boundary

I read the local RTK/instruction files, the prior correction review and both
final-correction handoffs, then inspected the current source and focused tests
independently. I used disposable synthetic workpads/journals only; no private
user data, real `.gigai`, activation, publication, commit, reset, live network,
or live model/provider call was used. The only probe file created for this
review was removed after the probe completed.

## Verification

The one requested combined suite was run once:

```text
rtk proxy .venv/bin/pytest -q \
  tests/test_scout_r5_interview_transfer.py \
  tests/test_scout_r5_transfer_corrections.py \
  tests/test_runtime_comparison.py
26 passed, 1 warning in 95.25s (0:01:35)
```

The warning is ZipFile's expected duplicate-name warning from the negative
duplicate-member fixture; the test passed.

Additional bounded verification:

```text
rtk proxy .venv/bin/python tools/verify_installed_schemas.py
verified 82 installed GigAI schemas

rtk proxy ruff check src/gigai/runtime_comparison.py \
  src/gigai/scout_interview_records.py src/gigai/private_transfer.py \
  src/gigai/cli.py tests/test_runtime_comparison.py \
  tests/test_scout_r5_interview_transfer.py \
  tests/test_scout_r5_transfer_corrections.py \
  tools/verify_installed_schemas.py
All checks passed!
```

I also ran a disposable two-process probe: process 1 created an interrupted
comparison after one saved case and exited; process 2 resumed it and completed
the comparison with the same comparison ID and persisted Run IDs. A separate
disposable subprocess held `.git/comparison-<run>.lock`; the competing process
returned `RuntimeComparisonError: comparison attempt is already running`
before entering attempt execution.

## Finding dispositions

### 1. Interrupted comparison resume, checkpoints, same IDs, concurrent refusal

Disposition: **closed for the bounded synthetic implementation**.

`run_comparison` seals `comparisons/<id>/intent.json` before attempts and
allocates the two Run IDs into that intent (`src/gigai/runtime_comparison.py:335-354`).
`resume_comparison` authenticates the sealed scope, reuses each planned Run ID,
and refuses changed targets/settings (`src/gigai/runtime_comparison.py:427-462`).
The attempt loader skips terminal checkpoints, preserves prior errors, and
checkpoints each result through the journal (`src/gigai/runtime_comparison.py:868-935`).
An OS-level nonblocking per-Run lock refuses concurrent execution
(`src/gigai/runtime_comparison.py:785-804`). The supported CLI exposes explicit
`comparison resume` with confirmation (`src/gigai/cli.py:796-827`).

The focused regression asserts an interrupted case, status checkpoint, same
Run IDs after resume, and no rerun of the first terminal case
(`tests/test_runtime_comparison.py:190-231`). The fresh-process probe described
above additionally exercised restart/session separation. This is still injected
transport evidence, not process-kill recovery under a live socket/provider.

### 2. Approval-input head versus output-publication head

Disposition: **closed**.

The comparison reader authenticates approved Graph Set/Goal Graph/selected
Graph/pack references at the recorded approval head
(`src/gigai/runtime_comparison.py:658-684`). It separately authenticates Run
manifests and case-result references at the comparison artifact's publication
head (`src/gigai/runtime_comparison.py:685-714`). Thus output artifacts are not
incorrectly demanded at the earlier approval commit. The later nested-result
replacement regression replaces a case result after publication and verifies
that `show_comparison` still reads the publication snapshot
(`tests/test_runtime_comparison.py:234-250`).

### 3. Real observed adapter/model/digest enforcement versus synthetic injection

Disposition: **closed for implementation and bounded negative evidence**.

The comparison identity gate treats missing transport identity as
`unknown/synthetic_injection` only when the caller explicitly supplied the
injected seam; otherwise it compares observed adapter, endpoint, and model,
and requires a passed matching Ollama digest
(`src/gigai/runtime_comparison.py:748-782`). The normal model-execution path
records provider family, endpoint, resolved model, adapter identity, and local
identity (`src/gigai/model_execution.py:337-367`); the local identity payload
retains configured and observed digest state (`src/gigai/model_execution.py:572-615`).
The focused negative test supplies a wrong adapter and verifies a structured
`observed_adapter_mismatch` terminal error rather than grading success
(`tests/test_runtime_comparison.py:253-265`). Synthetic fixtures remain
explicitly identity-unknown at `runtime_comparison.py:756-758`; no live adapter,
socket, model, or digest observation was claimed.

### 4. Actual feedback delta versus copied cumulative feedback

Disposition: **closed**.

An interview revision is admitted as `kind: feedback` only when it has a same-
record parent, a nonempty feedback list, and a strict prefix-preserving list
extension (`src/gigai/scout_interview_records.py:480-506`); source
authentication invokes this check before retaining the committed reference
(`src/gigai/scout_interview_records.py:283-292`). The regression rejects an
initial preparation relabel and an ordinary preparation revision that copies
the cumulative feedback list, while accepting a real `save_interview_feedback`
delta (`tests/test_scout_r5_interview_transfer.py:158-197`). Actual saved
research and discovery posting inputs are also exercised through their journal
readers (`tests/test_scout_r5_interview_transfer.py:200-238`).

### 5. Anchored archive extraction/cleanup and nested symlink race

Disposition: **closed for the bounded supported-platform implementation**.

Extraction refuses platforms without directory-FD and no-follow primitives and
opens the existing ancestor chain descriptor-anchored
(`src/gigai/private_transfer.py:540-578`). New destination directories and
nested parents are created/opened through already-open directory FDs, and files
use exclusive no-follow creation (`src/gigai/private_transfer.py:591-678`).
Failure cleanup checks device/inode identity through those FDs before unlinking
or removing anything (`src/gigai/private_transfer.py:679-701`). The deterministic
swap regression replaces a nested `tools` directory with a symlink during
reservation, verifies typed refusal, and verifies no outside payload or
unrelated sentinel deletion (`tests/test_scout_r5_transfer_corrections.py:67-98`).
This is a deterministic adversarial swap probe, not a probabilistic race proof
for every filesystem; unsupported-platform refusal was not exercised.

## Release classification

No genuine missing product implementation remains among these five findings in
the inspected bounded slice. Evidence remains synthetic and focused: the
normal model path has enforcement code, but no live adapter/model/digest call
was authorized or performed. Per the execution graph, R7 is the separate exact-
wheel/full-matrix/release-proof stage before authorized publication and user
install/UAT (`SCOUT-release-execution-graph.md:66-76,85-94`); those deferred
checks must not be represented as completed by this review.

No production, test, schema, roadmap, or unrelated dirty file was edited.

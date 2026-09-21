# SCOUT-R7 public binding regressions — 2026-09-21

## Scope

Follow-up acceptance work was completed only in `/private/tmp/gigai-r7-public-binding-correction-20260921/`, copied from the current dirty candidate rather than git HEAD. The main checkout received only this report; no main source, tests, schemas, tools, build metadata, historical workpad, shared environment, commit, tag, publication, provider, or model state was changed.

Scratch input manifest remains `input-digests.sha256`, SHA256 `9cf5fa3194a8d11e991b44068af08e64422cee4c6a723991327cbe60f52e4d89`.

## Transferable scratch changes

The prior scratch source correction now rejects malformed local output bindings without normalizing them into trusted authority. The accepted source-local reference shape is the existing artifact-reference shape: required `path`, `content_sha256`, `media_type`, and `size_bytes`, with only the existing optional `canonical_sha256`; unknown keys, missing keys, set mismatches, and any identity mismatch are rejected before Graph Set publication. A supplied optional canonical digest is preserved into the rewritten staged reference rather than silently dropped.

The scratch `tests/test_runtime_comparison.py` fixture now uses explicit v2 workpad migration and production `import_run_input` for the evaluation pack, declares the pack path as `run-inputs/<input_id>/source.txt`, declares the source-local output reference, updates every graph descriptor's evaluation reference, and never predicts a proposal ID or injects a journal transition for Graph Set construction. It adds production regressions for path, digest, size, media-type, unknown-key, and missing-key binding failures; version and criterion mismatch refusal with zero invocation and unchanged `runs/`/`comparisons/`; fresh subprocess CLI `show`/`status`; historical v1 pending-check readability; and v2 first-proposal check behavior.

Unified transferable diff (source + CLI dispatch + durable tests):

```text
/private/tmp/gigai-r7-public-binding-correction-20260921/SCOUT-R7-public-binding-correction-transfer.patch
SHA256 2f42e84c12ae75091aad5fdca75a42322ba80e6e224e1f9a50c401527ae62a1a
305 lines
```

Scratch final file SHA256 values:

```text
src/gigai/lifecycle.py                  e3f89e0c0d33b589648b25e071240500f0fa911b4d5a1c05b4fe237a6b1be0fc
src/gigai/cli.py                        5bd33f209a9b2a0796af6305bc660d9274b3ad5fad9e5426a92c66f0f00590ca
tests/test_runtime_comparison.py        9da36b28bffd4218e9b5669d8ff16fb5503166adb4a0690e4d0a3351a40a579a
research/scout-r7-public-binding-correction/probe.py
                                        895d230a0bcfcd7e7489feb5d69ce0473b5875903a86e1629bc23a6edeb4815c
```

## Verification

Commands and results:

```text
.venv/bin/pytest -q tests/test_runtime_comparison.py
25 passed in 120.59s

.venv/bin/pytest -q tests/test_runtime_comparison.py tests/test_scout02_graph_set_flow.py tests/test_scout05_first_proposal.py
41 passed in 174.32s

.venv/bin/ruff check src/gigai/lifecycle.py src/gigai/cli.py tests/test_runtime_comparison.py --select F401,F821
All checks passed

.venv/bin/python -m compileall -q src/gigai tests/test_runtime_comparison.py research/scout-r7-public-binding-correction
.venv/bin/ruff check research/scout-r7-public-binding-correction/probe.py
All checks passed
```

The complete runtime-comparison file is now green; its 12 prior failures were fixture failures caused by guessed staged IDs and private journal materialization, and are corrected through supported source-local import/proposal setup. Broad Ruff still reports unrelated pre-existing style/import findings in the large source files; focused F401/F821 checks are clean. No schema/resource inventory changed, so no schema hash update was required.

## Installed-wheel proof

Final scratch wheel:

```text
/private/tmp/gigai-r7-public-binding-correction-20260921/dist/gigai-0.1.7-py3-none-any.whl
SHA256 6af9a615327d936c5afceda90f01d261178d6e55a24ecfbf826762373c1b7083
```

Installed with dependencies into fresh `/private/tmp/gigai-r7-public-binding-installed-final-20260921/`, outside the checkout. Installed import resolved to that environment's `site-packages/gigai/__init__.py`; `gigai --version` returned `gigai 0.1.7`. The production public-path offline smoke then passed setup/config, target initialization, create, v2 migration, run-input import, Graph Set propose/check/approve, injected six-call comparison, version mismatch refusal, prompt capture, and durable show/status.

Smoke result: `check.valid=true`, six prompts, public output shape present, private `expected`/`criterion_evidence` absent, zero mismatch invocations, mismatch runs/comparisons unchanged, local digest `sha256:2222…2222`, and comparison status succeeded. Fresh installed subprocess `comparison show` and `comparison status` returned 12,560 and 725 bytes respectively for the new synthetic comparison.

## Remaining bounded blocker

The fresh provisioned v2 first-Graph-Set proposal passes production `check` through the new Graph Set validator. The supported public `reject` command still has no `--gig`/proposal-target selection and resolves only the target's active Gig; a fresh provisioned instance intentionally has no active Gig, so `gigai reject <proposal>` returns `no_active_gig` before `_pending_proposal` and cannot demonstrate successful first-Graph-Set rejection. The regression records this exact blocker and confirms no active version was created; fixing it would require a separate narrowly scoped CLI/lifecycle API change outside this fixture correction, while active-version amendment rejection behavior remains preserved.

No live Ollama/Codex readiness or quality inference was attempted, and no claim is made beyond synthetic injected execution and installed local CLI durability.

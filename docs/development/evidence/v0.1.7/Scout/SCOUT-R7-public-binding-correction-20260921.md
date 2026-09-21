# SCOUT-R7 public binding correction — 2026-09-21

## Scope and input identity

This is a transferable scratch-copy correction only. No product bytes, historical workpads, shared virtual environments, version metadata, commits, tags, publication, or provider/model calls were changed in the main checkout.

- Source checkout: `/Users/kar/orca/workspaces/gigai/gigai-v0.1.7` (current dirty candidate, not git HEAD).
- Scratch copy: `/private/tmp/gigai-r7-public-binding-correction-20260921/`.
- Excluded from scratch: `.git`, `.gigai`, `.venv`, credentials, and private state.
- Input manifest: `input-digests.sha256`; SHA256 `9cf5fa3194a8d11e991b44068af08e64422cee4c6a723991327cbe60f52e4d89`.
- Scratch patch files: `src/gigai/lifecycle.py` and `src/gigai/cli.py`; disposable helper `research/scout-r7-public-binding-correction/probe.py`.
- Scratch source SHA256 after correction: lifecycle `548b4e488dc452626258d7a7d7896116ad95bb1fb11deecbc34f69c8731f45b7`; CLI `5bd33f209a9b2a0796af6305bc660d9274b3ad5fad9e5426a92c66f0f00590ca`.

## Correction

The public `propose_graph_set_offline` path allocated a proposal ID, rewrote output-contract paths under that ID, and then copied an evaluation contract whose runtime binding still named a caller-guessed staged path. The scratch patch stages `output_contract` first, accepts a runtime evaluation binding only when its complete `(path, content_sha256, media_type, size_bytes)` identity exactly matches the source-local output reference, and rewrites the authenticated evaluation binding to the newly staged immutable reference (lifecycle lines 1687–1729, 1763–1779). Malformed, missing, foreign, or digest/size/media/path-mismatched local bindings are rejected before staging/publication; no proposal ID is guessed and no digest/path/version check is weakened.

The scratch patch also adds schema-aware pending-workpad dispatch: v2 Graph Set proposals use `validate_graph_set_proposal_workpad`, while historical v1 proposals retain `validate_proposal_workpad` (lifecycle lines 2581–2600 and 2910–2921; CLI `check` line 3804). This same dispatcher is used by the v2 reject path through `_pending_proposal`; no new authority object or schema redesign is introduced.

## Public-path evidence

Command (source scratch):

```text
PYTHONPATH=. .venv/bin/python research/scout-r7-public-binding-correction/probe.py
```

The helper used production setup/config objects, `initialize_target`, `create_offline`, v2 layout migration, public `run-input` import service, public Graph Set proposal and approval, public CLI `check`, production `run_comparison` with an injected offline invocation seam, and production `show_comparison`/`comparison_status`. Synthetic result: Gig `gig_00000000-0000-4000-8000-000000000002`, coherent proposal `gp_00000000-0000-4000-8000-00000000000f`, approved version 2, comparison `comparison_00000000-0000-4000-8000-000000000014`.

Observed output:

```text
check: {"findings": [], "valid": true}
prompt_count: 6 (3 shipped synthetic cases × 2 complete setups)
prompt_contract_shape: true
prompt_has_private_gold: false
local_digest: sha256:2222222222222222222222222222222222222222222222222222222222222222
```

The injected transport captured every actual production prompt; public criterion IDs/descriptors and the required output shape were present, while `expected` and `criterion_evidence` were absent. The comparison completed durably with both setup attempts and no winner/application-state mutation; quality claims are intentionally not made from these three synthetic cases.

Negative proof in the same public lifecycle created and approved a second synthetic Graph Set whose pack selected `r6-output:1` while the bound public contract remained `r7-output-contract:1`. Production comparison refused with `RuntimeComparisonError: evaluation pack output contract version is not coherent`; invocation spy calls were `0`, and `runs/` and `comparisons/` inventories were byte/state unchanged.

## Installed-wheel proof

Built scratch wheel:

```text
/private/tmp/gigai-r7-public-binding-correction-20260921/dist/gigai-0.1.7-py3-none-any.whl
SHA256 77a23d54789f560f374e0dcb6627f8bb41cb0ec771fb9e442f7020966dbf6438
```

Installed into fresh `/private/tmp/gigai-r7-public-binding-installed-20260921/` with dependencies. Outside the checkout, installed import resolved to `.../site-packages/gigai/__init__.py`, `gigai --version` returned `0.1.7`, and the same public-path/offline helper passed with the installed package. Fresh subprocess commands against the persisted synthetic result passed:

```text
gigai comparison show comparison_00000000-0000-4000-8000-000000000014 --gig gig_00000000-0000-4000-8000-000000000002 --home <smoke-root>/home --target <smoke-root>/target --json
gigai comparison status comparison_00000000-0000-4000-8000-000000000014 --gig gig_00000000-0000-4000-8000-000000000002 --home <smoke-root>/home --target <smoke-root>/target --json
```

The installed source hashes exactly matched the scratch source hashes: lifecycle `548b4e488dc452626258d7a7d7896116ad95bb1fb11deecbc34f69c8731f45b7`; CLI `5bd33f209a9b2a0796af6305bc660d9274b3ad5fad9e5426a92c66f0f00590ca`. Show/status outputs were 12,560 and 725 bytes respectively.

## Verification commands and limits

Passed:

- `.venv/bin/pytest -q tests/test_scout02_graph_set_flow.py tests/test_scout05_first_proposal.py` — `16 passed`.
- `.venv/bin/pytest -q tests/test_runtime_comparison.py -k 'pack_and_grader or pack_reload or historical_pack or setup_roundtrip'` — `4 passed`.
- `.venv/bin/python -m compileall -q src/gigai research/scout-r7-public-binding-correction`.
- `.venv/bin/ruff check research/scout-r7-public-binding-correction/probe.py` — clean.
- `.venv/bin/ruff check src/gigai/lifecycle.py src/gigai/cli.py --select F401` — clean.

The existing full `tests/test_runtime_comparison.py` focused file was not accepted as green: `5 passed, 12 failed`; all failures occur during the old fixture's construction because it pre-guesses `manifests/graph-sets/<proposal-id>/...` in the source evaluation binding, which this correction deliberately refuses. Those fixtures must be updated to declare source-local output references and use public run-input import, not restored by weakening the correction. Broad Ruff on the two pre-existing large files reports unrelated baseline import/style findings; no schema/resource inventory changed.

Not proven here: live Ollama readiness or model quality, Codex provider execution, first-Graph-Set rejection from a clean provisioned v2 workpad, criterion/ref/digest negative variants beyond the exercised version mismatch, or coordinator integration into the frozen main lane. The v2 check/reject dispatcher is source-compiled and wired through both callers, but the disposable public comparison smoke used an already-approved Gig because the normal amendment rejection guard correctly refuses replacement once an active version exists.

## Coordinator integration

Review and transplant only the narrow scratch diff for `src/gigai/lifecycle.py` and `src/gigai/cli.py` after the frozen lane settles. Do not transplant the scratch `.venv`, wheel, generated smoke state, or helper as production artifacts; retain this report as the evidence boundary.

# SCOUT R7 live installed comparison — 2026-09-21

Status: **comparison executed; release acceptance blocked by an installed durability-reader defect.** This is real installed-wheel evidence, not a synthetic/injected transport result and not a model-quality pass.

## Artifact and installed boundary

- Wheel: `/private/tmp/gigai-r7-rebuilt-candidate-verification-9TxZsp/dist/gigai-0.1.7-py3-none-any.whl`
- Required SHA-256: `ed3b3336d8f4d0bd49f9175c1e216ccafcc7e20583ed94d1ccb550679b0a59d3` (confirmed).
- Disposable environment: `/private/tmp/gigai-r7-live-comparison-20260921/venv311`.
- Isolated `python -I` import origin: `/private/tmp/gigai-r7-live-comparison-20260921/venv311/lib/python3.11/site-packages/gigai/__init__.py`.
- Installed metadata version: `0.1.7`; no checkout import shadowing was observed.
- Comparison data/workpad: `/private/tmp/gigai-r7-live-comparison-20260921/workpads/`.
- No repository source, tests, shared exact-wheel venv, candidate artifact, global configuration, MCP, daemon, or user Gig state was changed.

The installed CLI exposed `comparison start`, `comparison run` (alias), `comparison status`, `comparison resume`, and `comparison show`. The public `setup` preparation parser accepts only `openai_api`/`openrouter_api` endpoint specifications even though the installed config parser/factory accepts `ollama_local`; therefore this test used a disposable schema-validated config containing only the observed local endpoint and the existing Codex CLI target. That preparation-surface gap remains a product issue; no production fix was made.

## Real setup identity and readiness

One readiness probe was run per backend, with no repeated doctor/probe calls.

| Setup | Configured target | Observed identity | Readiness |
|---|---|---|---|
| Qwen/Ollama | `qwen-r7` → `ollama-r7` → `qwen3.8:latest`; endpoint `http://127.0.0.1:11434`; digest `sha256:22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643` | `ollama_local`, endpoint `ollama-r7`, model `qwen3.8:latest`, same digest | usable |
| Luna/Codex | `luna-r7` → `codex` → `gpt-5.6-luna` | `codex_cli`, endpoint `codex`, model `gpt-5.6-luna`; digest not applicable | usable |

Local Ollama metadata came from `http://127.0.0.1:11434/api/tags`; the installed model was `qwen3.8:latest`, parent `qwen3.8:27b-q4_K_M`, 27.3B Q4_K_M, digest `22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643`. The detected Codex executable reported `codex-cli 0.155.1`. No download or daemon start was performed.

## Public lifecycle authority

The synthetic Gig was bootstrapped through installed public lifecycle commands: explicit agent invocation validation, `create`, `approve`, `layout migrate`, `run-input add` for the synthetic pack, `graph-set propose`, and `approve`. The comparison selected the approved graph with `--graph source-grounded-review` and used the journaled Gig-owned evaluation contract; no pack or selection authority was patched by hand.

- Synthetic Gig: `gig_a5ed1db7-715c-4329-86f4-987477806008`.
- Project: `project_82f7c9d3-5225-42c7-bb6d-a77e4a7bb5ca`.
- Approved version: `2`, tag `gig-v000002`.
- Graph Set proposal: `gp_e564b8ad-8e47-4e8c-86b9-0161cd90c956`.
- Approved journal commit: `c794711c2f285aab57a48435a8bec3da84a7df8c`.
- Selected graph selector/UUID: `source-grounded-review` / `graph_00000000-0000-4000-8000-000000000601`.
- Selected goal: `goal_00000000-0000-4000-8000-000000000602`.
- Grader: `gigai.source-grounded-comparison-grader@1.0`; validation vectors passed (known-good pass and known-bad fail).
- Pack: `gigai-synthetic-runtime-comparison@1.0`, three cases (`posting_supported`, `unsupported_duration`, `finalized_not_applied`).
- Pack source ref: `run-inputs/input_bede015d-8d5e-4e21-9f4b-28a070a5bbb2/source.txt`, raw bytes SHA-256 `sha256:d958f818b7cc0274c11a53b0e5b8be9ee1149d6fbdf27d393b59674185483f16`, 3031 bytes.

## One bounded comparison

The exact installed invocation was:

```text
gigai comparison start --gig gig_a5ed1db7-715c-4329-86f4-987477806008 \
  --local-target qwen-r7 --luna-target luna-r7 \
  --graph source-grounded-review \
  --pack /private/tmp/gigai-r7-live-comparison-20260921/synthetic-pack.md \
  --target /private/tmp/gigai-r7-live-comparison-20260921/synthetic-target \
  --home /private/tmp/gigai-r7-live-comparison-20260921/home --confirm --json
```

Comparison ID: `comparison_1455227f-fc9d-4893-87d4-cc86cb368422`. `max_retries=0`; six provider case executions occurred exactly once, with no automatic repeat, fallback, or alternative backend. `application_state_changed` was `false` and `selected_winner` was `null`.

| Setup / Run ID | Case | Invocation ID | Grade | Wall ms | Retries | Usage |
|---|---|---|---|---:|---:|---|
| `qwen_ollama` / `run_0e38f7da-787b-436b-9c7d-fab821a0d379` | `posting_supported` | `inv_ff56e2de-c177-4181-8e33-4e7720ffe140` | `invalid_output` | 9023 | 0 | 160 in / 36 out / 196 total |
| same | `unsupported_duration` | `inv_f4a1abab-92bd-4d00-9e57-a129a209456a` | `invalid_output` | 18384 | 0 | 184 in / 145 out / 329 total |
| same | `finalized_not_applied` | `inv_43d850b1-912e-4834-a360-9a2bdcd65ff3` | `invalid_output` | 8846 | 0 | 194 in / 60 out / 254 total |
| `luna_codex` / `run_aedcbea1-e5da-4e68-8596-31b8a4503138` | `posting_supported` | `inv_d61a8d21-6a1f-4db9-846a-c65b9fb8019e` | `invalid_output` | 6993 | 0 | 17,504 in / 87 out / total unknown |
| same | `unsupported_duration` | `inv_7da0f73d-3dec-4bb1-bb0b-d078afe1aa4b` | `invalid_output` | 8351 | 0 | 17430 in / 100 out / total unknown |
| same | `finalized_not_applied` | `inv_38068802-b009-48ec-a748-275ac5e561f6` | `invalid_output` | 9184 | 0 | 17442 in / 96 out / total unknown |

The Luna input-token values above are provider-reported values from the durable invocation records; total-token accounting remained unknown. Total wall time across case records was approximately 60.8 seconds. Provider outputs and invocation records remain inspectable under each `runs/<run-id>/invocations/` directory; they contain only synthetic case material.

Both models produced readable answers but did not satisfy the strict grader shape: the required `criteria` array of `{criterion_id,status,evidence_ids}` was replaced by prose or differently shaped objects. All six case failures are therefore preserved as `invalid_output`; no model-quality success is claimed.

## Fresh-process durability result

The comparison artifact and all six result references were written durably. A fresh installed CLI process was then used for both:

```text
gigai comparison show comparison_1455227f-fc9d-4893-87d4-cc86cb368422 ... --json
gigai comparison status comparison_1455227f-fc9d-4893-87d4-cc86cb368422 ... --json
```

Both returned:

```text
runtime_comparison_invalid: comparison pack digest is not authenticated
```

The durable JSON shows `pack.content_sha256 = sha256:fe708376c8f2b4b1f8045f295574e8a7b02e1d7138769e77492e1b5a6c4ecc29` (the canonical evaluation-pack digest), while its authenticated `pack.source_ref` points to raw journaled bytes with digest `sha256:d958f818b7cc0274c11a53b0e5b8be9ee1149d6fbdf27d393b59674185483f16`. The installed fresh-reader rejects this mismatch as “pack digest is not authenticated,” so supported `show`/`status` cannot reload the otherwise durable comparison. Direct read-only evidence confirms both run manifests match their stored refs and all six result refs match their bytes, but this is not a substitute for the failed public reader acceptance.

## Verdict and remaining blocker

- **Provider/harness execution:** bounded real installed execution completed for both setups; observed identities and six independent attempt records are present, with no retries or winner/application mutation.
- **Model quality:** all six cases failed strict schema grading as `invalid_output`; no local goal success is claimed.
- **Durability acceptance:** **blocked/failed** for release evidence because a fresh installed `comparison show` and `comparison status` cannot authenticate the published pack source digest.
- **Required next action:** product owner must correct the installed comparison pack source/content digest binding and rerun a separately authorized comparison if new provider evidence is desired. This wave made no fix and performed no rerun.

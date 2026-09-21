# SCOUT R7 live corrections: independent bounded review

Date: 2026-09-21  
Review type: read-only source/diff review with bounded offline probes  
Verdict: **CHANGES REQUESTED for a fresh supported R7 comparison**

## Scope and provenance

This review covers the comparison/Ollama correction wave, not the separately accepted capability-refusal correction.  The comparison was made against the previous candidate wheel at `/private/tmp/gigai-r7-rebuilt-candidate-verification-9TxZsp/dist/gigai-0.1.7-py3-none-any.whl` (SHA-256 `ed3b3336d8f4d0bd49f9175c1e216ccafcc7e20583ed94d1ccb550679b0a59d3`), rather than Git HEAD, which does not contain the untracked Scout implementation.  The corrected wheel at `/private/tmp/gigai-r7-live-corrections-0F5F5u/dist/gigai-0.1.7-py3-none-any.whl` was confirmed as SHA-256 `fbca831bc46c6992ee813d9c37ce90802a83073440777897a2c7b84ffc8e2fe9` and imported from its isolated 0.1.7 virtual environment.

The bounded diff covered `src/gigai/runtime_comparison.py`, `src/gigai/cli.py`, the runtime evaluation-pack schema/data, and the installed-schema inventory.  The Ollama adapter itself is byte-identical between the two wheels (`4acbf0be39894f0bae83c5b6c3ecadf62234c1e24f64695a2f1d3e43e7ed31ae`); this review therefore found no new adapter implementation to assess.

No provider/model call, full matrix, source edit, historical workpad edit, activation, publication, or release operation was performed.  Disposable extraction/probe material was kept under `/private/tmp/gigai-r7-live-corrections-review-rZqitf` and `/private/tmp/gigai-r7-review-setup-MeAtED`.

## Changes requested

### P1 — Fresh graph/pack output-contract version is not authenticated

`src/gigai/runtime_comparison.py:678-684` authenticates the pack owner, graph ID/version, and approved goal, but does not bind `selected_graph.output_contract_version` (or an equivalent graph/evaluation-contract reference) to the approved Graph Set.  `src/gigai/runtime_comparison.py:227-255` accepts a per-case `r7-output-contract:1` and composes an R7 prompt, while the pack-level `selected_graph` value is not checked for coherence.  The new bundled pack reports `r7-output-contract:1`, but the fresh Graph Set fixture path in `tests/test_scout02_graph_set_flow.py:30-67` stages only a generic `run_output_contract`/`evaluation_contract`; it contains no R7 runtime-contract binding.

Bounded offline reproduction (current source, disposable copy; no public/provider execution) changed only the pack's selected-graph output-contract version from `r7-output-contract:1` to `r6-output:1`.  `load_evaluation_pack` still loaded the pack and `_comparison_prompt` still emitted an R7 contract prompt:

```
loaded r6-output:1 case_contract r7-output-contract:1 prompt_contract True coherence_refused False
```

This is a real authority/coherence gap, not a claim that the refused operation wrote source.  A newly approved graph could therefore advertise one output contract while the authenticated pack instructs the model under another; the current reader would accept the identity/goal binding and proceed.  The correction should add an explicit runtime-comparison contract binding in the approved graph/evaluation contract and reject mismatched pack-level/per-case versions (while retaining historical packs with no `output_contract`), or otherwise prove an equivalent immutable authority relation.

The current tests do cover graph/goal/consent/case and raw/canonical digest mutations, but `tests/test_runtime_comparison.py:84-103` updates only the fixture pack's graph ID/version and does not exercise this version mismatch.  The focused tests use an injected `invocation_service` and synthetic journal fixture; they are useful authority-path evidence but do not close this fresh public Graph Set coherence gap.

### P2 — Per-case output criteria are not bound to the deterministic grader criteria

`src/gigai/runtime_comparison.py:233-245` checks that output-contract criterion IDs are strings and unique, but does not compare that set to the case's deterministic `expected.criteria` keys.  The schema permits the two lists to diverge, and the prompt can then request evidence for criteria which the grader never evaluates (or omit a criterion the grader does evaluate).  This is a concrete pack integrity/quality gap for a fresh R7 pack; the bundled pack is internally coherent by inspection.  Add an exact criterion-set check when the R7 contract is present, preserving the historical no-contract compatibility branch at lines 227-230, and add a public-path negative test.

A second bounded disposable-pack probe renamed the first case's contract criterion to `not_the_grader_key`; `load_evaluation_pack` accepted it and `_comparison_prompt` requested it while the grader's expected set remained `['location']`:

```
loaded not_the_grader_key expected_ids ['location'] prompt_requests_mismatch True
```

## Accepted bounded evidence

* **Dual raw/canonical authentication and authority heads:** The new reader preserves the approval head for raw source, graph, goal, selected descriptor, and consent, while reading published attempt/result refs from the comparison head (`src/gigai/runtime_comparison.py:698-739`, with the head selection in `_authenticate_comparison`).  Source inspection and the existing focused production tests show rejection of changed raw bytes, forged canonical digest, foreign graph/goal, foreign consent, and mismatched case identity.  The old installed comparison's `show` and `status` were read successfully from the corrected environment, and its historical workpad before/after byte listings were unchanged; that is historical-read proof, not a fresh comparison proof.
* **Historical compatibility:** `_comparison_prompt` explicitly returns the original prompt when a historical case has no `output_contract` (`runtime_comparison.py:227-230`).  The old comparison records `r6-output:1` and its six historical invalid outputs; preserving that record is correct.  It must not be used as evidence that a new R7 graph/pack approval is coherent.
* **Public setup and parser compatibility:** From the exact corrected wheel's isolated environment, public `gigai setup --non-interactive --json` accepted `local=ollama_local:http://127.0.0.1:11434` and `qwen=local:qwen3.8:latest@sha256:<64 lowercase hex>` and wrote the expected disposable config.  Bounded parser probes retained the old remote forms (`openai_api:credential`, `openrouter_api:credential:https://...`) and accepted the new local form; existing config validation still constrains local Ollama to loopback HTTP, a valid port, a canonical digest, and the output limit.  No Ollama endpoint was contacted, so readiness, model availability, digest correctness against `/api/tags`, and live invocation remain unproven.
* **Prompt safety:** The new prompt composition keeps expected answers/gold out of the public prompt and includes explicit top-level output fields, criterion IDs, supported statuses, and evidence-ID rules.  Existing focused tests exercise that composed shape through an injected transport; they do not constitute provider or installed-live proof.
* **Schema/inventory:** The correction is additive: the evaluation-pack schema adds the optional R7 case `output_contract`, while historical cases remain readable, and the current source inventory/claimed verifier count is 82 schemas.  This review did not rerun the verifier; schema JSON and the corrected inventory were inspected in the bounded diff.

## Public-path and release limits

The implementation report's 12 focused comparison tests are offline, injected-transport tests.  They do not demonstrate a fresh installed `setup -> Graph Set approval -> comparison` path with the new pack, and no provider/model calls were made here.  The only installed comparison evidence available for this review is read-only `show/status` of the prior historical record, which is valuable for raw/canonical replay but cannot establish the new graph-definition/output-contract gate.

Therefore the exact scope is **not accepted pending the two coherence corrections (P1 required; P2 required for a clean R7 contract)** and corresponding public-path negatives.  Independently unproven gates remain the corrected live comparison/provider output quality, local Ollama readiness and model digest availability, the original full source matrix (reported externally as 1564 passed/1 failed/1 skipped and not rerun), cross-platform/Docker/tag CI, publication, and UAT/release approval.  No broad redesign is requested, and no claim is made that any denied/refused operation mutated source.

## Files and probes

Owned durable artifact: this report only.  Disposable review extraction: `/private/tmp/gigai-r7-live-corrections-review-rZqitf`; disposable installed setup/parser probe: `/private/tmp/gigai-r7-review-setup-MeAtED`.  No product, schema, fixture, or test file was edited.

# Qwen3.8 local rotation review trial

Run directory: `/Users/kar/orca/workspaces/gigai/gigai-v0.1.7/research/local_model_eval/qwen38_rotation/runs/20260910T195214Z`

## Frozen trial

Three synthetic review cases and their expected defects/rubric were frozen before any candidate call in `rubric.json`: one clean supported review, one unsupported duration/ownership claim derived from posting text, and one finalized resume that must not be represented as applied. Each candidate would receive one serial local request only; no retries or prompt tuning are performed.

## Endpoint and safety

Endpoint is explicitly `http://127.0.0.1:11434` with proxy disabled, redirects refused, no cloud fallback, and no model download. Expected model `qwen3.8:latest` with digest `22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643`; identity is recorded in `/Users/kar/orca/workspaces/gigai/gigai-v0.1.7/research/local_model_eval/qwen38_rotation/runs/20260910T195214Z/identity.json`. Hardware metadata (no serials or user identifiers) is recorded in `hardware.json`: `{"cpu_count": 14, "machine": "arm64", "memory_bytes": 68719476736, "python": "3.12.8", "release": "25.5.0", "system": "Darwin"}`.

## Result

Identity check failed before candidate calls: `transport` — `local_request_failed:URLError:<urlopen error [Errno 1] Operation not permitted>`. Candidate sample size is n=0; no Qwen answer, usefulness, false-positive rate, or token/timing claim is made.

Luna assessment is recorded in `luna_assessment.json`: no candidate findings were available. This trial therefore provides no support for low-risk review assistance in this environment, and is insufficient evidence for authority/storage changes.

## Limits and next test

The failed identity path may reflect sandbox loopback policy rather than the user's Ollama server state; this is not an OS/network audit. No private records/resumes/preferences/secrets, providers, hosted inference, generated-code execution, product code, or old pilot evidence were used. A future authorized run should repeat these unchanged fixtures when loopback is available and preserve this original failure rather than overwrite it.

## Dated access diagnosis and corrected rerun — 2026-09-10 UTC

The worker execution context ran a bounded direct Python probe equivalent to
`LocalClient().request('/api/tags', None, 30)` and the corrected stdlib harness
also failed at `http://127.0.0.1:11434/api/tags` with
`URLError: [Errno 1] Operation not permitted`, classified as `transport`.
This errno is a local sandbox access denial, not evidence that Ollama is down.
The coordinator's already-allowed context ran:

```text
curl --noproxy '*' --max-time 5 -sS http://127.0.0.1:11434/api/tags
```

and received the expected installed `qwen3.8:latest` digest, so the most
supported explanation is worker-context loopback restriction. One bounded
`orca orchestration check` in this worker also failed with “Could not connect
to the running Orca app”; this shares the worker's restricted IPC context but
does not prove a causal relationship. No restart, kill, global permission
change, config change, or repeated notification retry was attempted.

The corrected harness rerun is in
`research/local_model_eval/qwen38_rotation/runs/20260910T195214Z/` and again
stopped before candidate calls (`sample_size: 0`). It now enforces the overall
monotonic deadline for identity and candidate requests, resets candidate state
per case, distinguishes valid answers from request attempts, performs strict
nested output-type validation, and uses structural evidence-bound grading
(`grader_version: rotation-review-v2-structural`) rather than substring defect
matching. The frozen case/rubric fixtures are unchanged; the prior run
directories remain intact. `python -m py_compile
research/local_model_eval/qwen38_rotation/harness.py` and `ruff check
research/local_model_eval/qwen38_rotation/harness.py` both pass.

## Actionable follow-up

Run this exact command once from the coordinator's already-permitted local
execution context (it performs identity verification and, only on matching
tag/digest, the three unchanged candidate calls):

```text
python research/local_model_eval/qwen38_rotation/harness.py
```

The coordinator should preserve the resulting new run directory and append
its raw candidate requests/responses, per-case scores, and an actual Luna
assessment after independently reading the candidate answers. Until that
permitted run exists, this trial supports only the diagnosis that the current
worker cannot access loopback; it provides no model-quality or low-risk-review
usefulness claim and remains insufficient evidence for authority/storage
changes.

## Coordinator permitted-context attempt — 2026-09-10 20:01 UTC

Started the corrected harness in the coordinator's permitted context, using
the unchanged fixtures and a separate `QWEN-rotation-permitted-rerun.md` report.
This is a background execution attempt; candidate success is not yet asserted.

Important evaluation limitation found while inspecting the frozen fixtures:
`candidate_prompt` includes the entire fixture, including `expected_defects`.
Those fields leak expected answers. The finalized-not-applied artifact is also
already correct (`applied=false`), not the requested erroneous applied-state
case. Consequently this unchanged rerun is only a connectivity/structured-output
smoke test, not a valid independent quality benchmark. Preserve its evidence;
do not claim routing suitability from a high score. Any subsequent quality
trial must version corrected fixtures, omit answer metadata from prompts, and
freeze a genuinely defective lifecycle case before inference.

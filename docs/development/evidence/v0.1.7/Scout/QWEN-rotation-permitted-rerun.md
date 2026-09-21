# Qwen3.8 local rotation review trial

Run directory: `/Users/kar/orca/workspaces/gigai/gigai-v0.1.7/research/local_model_eval/qwen38_rotation/runs/20260910T200130Z`

## Frozen trial

Three synthetic review cases and their expected defects/rubric were frozen before any candidate call in `rubric.json`: one clean supported review, one unsupported duration/ownership claim derived from posting text, and one finalized resume that must not be represented as applied. Each candidate would receive one serial local request only; no retries or prompt tuning are performed.

## Endpoint and safety

Endpoint is explicitly `http://127.0.0.1:11434` with proxy disabled, redirects refused, no cloud fallback, and no model download. Expected model `qwen3.8:latest` with digest `22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643`; identity is recorded in `/Users/kar/orca/workspaces/gigai/gigai-v0.1.7/research/local_model_eval/qwen38_rotation/runs/20260910T200130Z/identity.json`. Hardware metadata (no serials or user identifiers) is recorded in `hardware.json`: `{"cpu_count": 14, "machine": "arm64", "memory_bytes": 68719476736, "python": "3.13.1", "release": "25.5.0", "system": "Darwin"}`.

## Result

Candidate sample size is n=3 with one request per case; raw requests/responses and machine-readable scores are retained under `/Users/kar/orca/workspaces/gigai/gigai-v0.1.7/research/local_model_eval/qwen38_rotation/runs/20260910T200130Z`. Scores are structural/evidence-based and do not establish semantic completeness or parity to Luna. `luna_assessment.json` is `pending_review`; no Luna assessment is manufactured by this harness.

## Limits and next test

The failed identity path may reflect sandbox loopback policy rather than the user's Ollama server state; this is not an OS/network audit. No private records/resumes/preferences/secrets, providers, hosted inference, generated-code execution, product code, or old pilot evidence were used. A future authorized run should repeat these unchanged fixtures when loopback is available and preserve this original failure rather than overwrite it.

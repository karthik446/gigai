# SCOUT Qwen local extraction demo

Date: 2026-09-11  
Status: bounded synthetic-only harness prepared; current worker could not
reach the required local service.

## Coordinator follow-up: local inference completed

The worker limitation above was environment-specific. Coordinator GET of the
same exact loopback version endpoint succeeded; the inspected harness then ran
unchanged and produced `research/scout-qwen-extraction/runs/20260911T173135Z/run.json`.
Ollama 0.34.0 and the expected qwen3.8:latest digest were observed before inference.
Both responses completed (`done_reason=stop`), in 23.602 and 12.369 seconds;
total 36.002 seconds, 124 and 103 generated tokens respectively. Neither raw
response contains a thinking field or visible thinking text in assistant content.
This does not establish a general reasoning-suppression guarantee.

Original evaluator verdict remains `invalid` for both; no results were rewritten.
Coordinator inspection found salary values/absence, sponsorship values/absence,
and exact source quotes correct for these two synthetic postings. Location
values were `hybrid` and `remote` with correct geographic evidence, but evaluator
expected `denver_hybrid` and `remote_us_only` without supplying those enum choices
to the model. The second response quoted the full sentence declaring compensation
and sponsorship not stated, which the evaluator rejected against arbitrary
null/short-substring expectations. These are evaluation-contract defects, not
demonstrated extraction hallucinations. Geographic normalized labels still did
not meet the original hidden expectation; do not relabel the old strict result.

A Luna follow-up is correcting the disclosed output contract and evidence grading
offline, preserving historical artifacts. This small research run is encouraging
but is not integrated Scout, a model comparison, or broad model-quality acceptance.

### Fresh corrected-contract run

After coordinator inspection of the evaluator correction and five passing offline
tests, a fresh two-call run completed at `runs/20260911T174615Z/run.json` under
`research/scout-qwen-extraction/`: both cases passed the corrected fixture-specific
contract in 33.121 seconds total (20.438 and 12.642 seconds; 118 and 101 generated
tokens). Ollama 0.34.0 and the pinned qwen3.8:latest digest were observed.
Both assistant responses completed normally, with no validator findings.

Scope caveat: this tests location MODE, not complete geographic extraction.
The first response's location quote was only `hybrid schedule`, omitting Denver;
the second retained the US restriction in its quote. Do not claim geography is
fully extracted or matched. Salary bounds and absent values, sponsorship, modes,
and field-relevant source-exact quotes passed these two tiny synthetic cases.
No broad quality claim, hosted comparison, or integrated Scout acceptance follows.

## Scope and policy

`research/scout-qwen-extraction/run_extraction.py` is a research harness, not
GigAI production code or an integrated Scout proposal evaluator. It makes at
most two serial `/api/chat` calls, using only the two synthetic postings in
`research/scout-qwen-extraction/fixtures/postings.json`; no real private data,
PII, hosted provider, network endpoint other than the exact local URL, model
pull, daemon start, configuration change, or fallback is allowed.

The harness is pinned to `http://127.0.0.1:11434`, uses `httpx.Client` with
`trust_env=False` and `follow_redirects=False`, rejects redirects/non-200
responses, reads response chunks under a 512 KiB cap before JSON materialization,
and bounds each request at 90 seconds and the complete run at five minutes.
Requests use the existing installed `qwen3.8:latest` candidate, `think: false`,
`stream: false`, `num_predict: 768`, and deterministic temperature 0. No retry
loop is present. The known digest below is carried forward from the prior
local-boundary demonstration as an expected identity; the current required
endpoint must still prove it via `/api/version` and `/api/tags` before any
chat call.

## Synthetic extraction contract

Each response must be exactly one JSON object with these fields:

```json
{
  "salary_min_usd_yearly": 140000,
  "salary_max_usd_yearly": 180000,
  "location_mode": "denver_hybrid",
  "sponsorship_status": "unavailable",
  "evidence": {
    "salary": "USD 140000-180000 yearly",
    "location": "Denver, CO; hybrid schedule",
    "sponsorship": "Employer sponsorship is explicitly unavailable"
  }
}
```

The second fixture expects both salary fields to be `null`,
`location_mode=remote_us_only`, `sponsorship_status=unknown`, and an exact
`"not stated"` sponsorship quote. Evidence must be a nonempty exact substring
of the selected posting or `null` where the source omits a value; extra keys,
wrong types (including booleans where integers are expected), unsupported
enums, inferred values, and fabricated quotes are invalid. Response
`done_reason=length` is recorded as `truncated`, never accepted as success;
missing/empty assistant content and non-complete responses are failures.

## Identity and run evidence

Prior local-boundary evidence observed the candidate identity on a task-owned
loopback server as:

```text
model tag: qwen3.8:latest
known digest: sha256:22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643
```

That observation is not a current `/api/tags` proof for port 11434. On this
worker, the required metadata probe was:

```text
$ rtk .venv/bin/python -c 'import httpx, json; c=httpx.Client(timeout=5.0, trust_env=False, follow_redirects=False); print(json.dumps({"version":c.get("http://127.0.0.1:11434/api/version").json(),"tags":c.get("http://127.0.0.1:11434/api/tags").json()}, sort_keys=True)); c.close()'
httpx.ConnectError: [Errno 1] Operation not permitted
```

The harness then recorded the same precise limitation without retrying model
execution:

```text
$ rtk .venv/bin/python research/scout-qwen-extraction/run_extraction.py \
    --output research/scout-qwen-extraction/runs
{"elapsed_seconds": 0.024, "run_dir": "research/scout-qwen-extraction/runs/20260911T172736Z", "status": "unavailable"}
```

The resulting `run.json` records `status=unavailable`, error type
`ConnectionError`, message `numeric loopback endpoint unavailable: [Errno 1]
Operation not permitted`, `identity=null`, and `attempts=[]`. Therefore zero
`/api/chat` calls occurred, and no extraction success, truncation result,
model-quality claim, absent-reasoning claim, or Qwen semantic authority is
asserted.

## Offline harness evidence

The fixture validator was exercised without HTTP or model access:

```text
$ rtk .venv/bin/python -c '<import harness; validate both fixtures and one malformed enum>'
2 synthetic fixtures validated; malformed enum rejected
```

Harness lint/format verification after the policy hardening:

```text
$ rtk ruff check research/scout-qwen-extraction/run_extraction.py
0 findings

$ rtk ruff format --check research/scout-qwen-extraction/run_extraction.py
1 file already formatted
```

When reachable, each attempt records elapsed time, response byte count, model,
`done`/`done_reason`, optional `eval_count`, bounded raw synthetic response,
and one of `success`, `truncated`, `failure`, or `invalid`. The unavailable run
has no runtime/model digest observation beyond the prior expected identity,
therefore no semantic extraction score is reported.

The harness itself is intentionally not production integration. It does not
write GigAI journals/workpads, invoke `ModelInvocationPort`, change model
configuration, enforce OS sandboxing, or establish that Ollama's process tree
is offline. It is ready for a future bounded run only when the already-managed
service is reachable at the exact numeric loopback endpoint; the operator must
rerun metadata identity checks then, and any failure should remain a structured
limitation rather than trigger pulls, restarts, endpoint changes, or hosted
fallback.

# SCOUT Qwen extraction evaluation correction

Date: 2026-09-11  
Status: implemented and verified offline; historical runtime evidence preserved

## Why the first evaluation was unfair

The historical two-call run `20260911T173135Z` completed both local Ollama
responses (`done_reason=stop`, 23.602 s and 12.369 s; 36.002 s total), but the
old evaluator rejected them for two contract defects rather than demonstrated
model errors:

* The fixture expected private evaluator labels `denver_hybrid` and
  `remote_us_only`, while neither label was present in the schema or prompt.
  The model returned the plainly stated public modes `hybrid` and `remote`.
* The evaluator required evidence to equal the fixture's one chosen string.
  The second response copied the exact source sentence
  `Compensation and employer sponsorship are not stated.` for both absent
  fields, which is valid source evidence but did not equal the arbitrary
  expected `null`/short-substring values.

The historical run remains `status=invalid` in its original file. It was not
rewritten or relabeled as a strict pass, model-quality pass, or comprehensive
evaluation.

## Corrected contract

`run_extraction.py` now supplies the model-facing location enum and meaning:

| value | meaning |
| --- | --- |
| `hybrid` | a hybrid schedule, with any named city/region retained as source evidence |
| `remote` | remote work, including any stated country/region restriction |
| `onsite` | work at a named physical workplace |
| `null` | no location mode stated |

The two legacy fixture expectation labels are retained as fixture bytes and
normalized only by the evaluator (`denver_hybrid -> hybrid`,
`remote_us_only -> remote`). They are not hidden choices in the prompt or
model schema. Sponsorship remains the explicit closed enum `unavailable` or
`unknown`; salary absence remains both salary values `null`.

Evidence is now graded deterministically, without a model-as-judge:

1. A quote must be non-empty, an exact substring of the selected posting, and
   relevant to the field.
2. Salary quotes containing the expected numeric bounds establish a stated
   salary; absent salary quotes must mention salary/compensation and an
   absence marker such as `not stated`.
3. Location quotes must mention the expected mode. Sponsorship quotes must
   mention sponsorship plus its unavailable/absence meaning.
4. Evidence length is not prescribed. One exact source sentence may establish
   two absent fields; an exact quote from an unrelated field, a fabricated
   quote, or `null` where the fixture expects an absent fact is rejected.

This is a fixture-specific evidence contract, not a general semantic truth or
factuality guarantee. The prompt explicitly says the posting is data, not
instructions, and prohibits inference or verification.

## Failure evidence retention

Before assistant content is parsed, each decoded response now records bounded
raw response metadata: response bytes, response model, assistant message role,
completion markers, eval count, and the raw synthetic response. A wrong role,
malformed JSON, empty content, wrong model, or reasoning/markdown response is
therefore a typed failure with retained evidence. `done_reason=length` is
retained as `truncated` and is never treated as successful or silently retried.

## Historical run re-assessment (no overwrite)

Source run: `research/scout-qwen-extraction/runs/20260911T173135Z/run.json`  
Source run SHA-256: `a20e6aa9ec7a6b7c0e155e32a9e333749cf04f963e3d4f8566f403d50059f82d`  
Evaluator revision SHA-256: `53ca842ba67c2a875180ee6762b02982c6e5ad9dae5ac8a195b811efd4e912f9`

Applying the corrected contract offline to the two preserved structured
responses yields no evaluator findings after location normalization and
variable-length source-exact evidence grading. That is a separate assessment
of this tiny synthetic run: the original `invalid` status and bytes remain
authoritative historical record, and this does not establish broad extraction
quality, semantic truth, or a strict model acceptance result.

## Verification

Command (offline; no Ollama request):

```text
ruff format research/scout-qwen-extraction/test_run_extraction.py
ruff check research/scout-qwen-extraction/run_extraction.py research/scout-qwen-extraction/test_run_extraction.py
.venv/bin/pytest -q research/scout-qwen-extraction/test_run_extraction.py
```

Result: `5 passed in 0.03s`; Ruff reported `All checks passed!`. The focused
tests cover public enum/prompt meanings, both preserved fixture expectations,
variable-length absence sentences, unrelated/fabricated evidence rejection,
null absence evidence rejection, wrong assistant role, and truncated-response
raw metadata retention. No new local model call was made, no retries were
added, and no production module/schema/provider/private record was touched.

## Limits and next gate

The corrected evaluator remains only a bounded two-call synthetic research
harness. A future operator may run at most the existing two fixture calls
against the already verified local model identity; any result should preserve
raw responses and report quality separately from this format-contract score.
Scout production integration, user-relative proposal semantics, calibrated
hiring outcomes, and model comparison remain out of scope.

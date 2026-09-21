# Qwen3.8 pilot — coordinator interpretation

Reviewed the worker report, harness, fixture/rubric definitions, and saved
coding, tailoring and tool-episode answers on 2026-09-09. No model calls or
generated-code execution were repeated for this review.

## What the pilot establishes

The [worker report](qwen38-scout-local-eval.md) records ten synthetic tasks and
three unchanged repeats, all passing the repaired keyword rubric. The counted
rerun took 340.74 seconds, with individual cases roughly 14–40 seconds.
Requests explicitly set `think:false`, temperature zero and seed 42. This is
not a measurement of the thinking-enabled mode demonstrated by the operator.
The fixture-tool episode really dispatched allowlisted functions and supplied
their results to subsequent model calls. Saved outputs support a useful initial
smoke test for instruction following, simple extraction and tool protocol use.

## Why 13/13 is not a capability acceptance score

- Prompts disclose much of the intended answer: the missing sponsorship
  statement, Rust gap, successor requirement and injected instruction are all
  explained to the model. The tool prompt names both functions and exact IDs,
  and the fixture data is already present in the initial prompt. This does not
  test independent tool selection or whether retrieval was necessary.
- Scoring is keyword/source-ID matching, not full semantic evaluation. The
  ranking check requires an ID to appear but does not check its rank. Follow-up
  relevance is reduced to nonempty text. A negation anywhere in a 48-character
  prefix can suppress a forbidden-term finding. These checks can false-pass.
- The coding answer uses `value.strip()`, which strips Unicode whitespace too,
  despite the ASCII-whitespace wording. The rubric only looks for `strip`,
  `lower`, `none` and a few forbidden strings; it does not establish exact
  edge behavior, syntax validity or execution correctness.
- The tailoring answer asserts alignment with distributed systems without
  supplied distributed-systems evidence. That inference deserves human review;
  the all-pass score does not establish that every claim is grounded.
- The harness parses structured JSON but does not independently validate the
  full requested output schema. It also does not reject a model digest mismatch
  before calls, or inspect generation `done_reason` for token truncation.
  These are limitations of harness reuse, not evidence that the recorded model
  identity was wrong or that a particular saved answer was truncated.
- The worker repaired scoring after seeing initial results and performed a
  live rerun. Original artifacts are preserved, but these are no longer an
  untouched held-out evaluation. Deterministic same-seed repeats add little
  evidence about robustness across task variants.

## Disposition

Promising enough to justify a harder evaluation for local background Scout
work. Not evidence of Luna-equivalent coding, autonomous online discovery,
general injection resistance, or validated private-data handling. Local
inference is compatible with keeping raw PII local if the surrounding tools,
logging and routing also maintain that boundary; this synthetic pilot did not
audit those systems.

Before a next live evaluation, fix offline grader/protocol checks, use unseen
task variants without answer-leading hints, hide tool results until retrieval,
and evaluate typed outcomes and actual source support. Add a separately
authorized matched baseline if a Luna comparison is wanted. Preserve original
results and do not retroactively relabel the keyword score as semantic success.

No runtime integration or additional model run is started by this review.

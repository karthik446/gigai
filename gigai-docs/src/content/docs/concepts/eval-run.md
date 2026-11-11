---
title: Running evals
description: How `gigai <gig> eval run` calls, records and scores an eval step, how replay and rescoring work without new calls, and how to write a scorer or postprocess.
---

A Gig that declares an `[evals]` table gets an `eval` command group. `gigai <gig> eval list` shows
what the table declares. `gigai <gig> eval run` runs one step on one suite's dataset: it calls the
model, records every call, and scores every answer. `eval run --from-traces` and `eval rescore`
score answers you already have, without calling a model again.

Everything lands in the Gig's eval store, `evals.sqlite` in the gig folder (`<home>/gigs/<gig>/`):
one `runs` row per run, one `traces` row per model call, and one `scores` row per answer and
scorer.

## Run a step

```sh
gigai my-gig eval run --step answer --suite ci
```

The run, in order:

1. Resolves the model target of the step and of each judge it scores with, and applies the call
   cap rule for `release` and `upgrade` runs (below).
2. Reads the suite's dataset (`ci`, `release` or `upgrade`) from the step's `[[evals.steps]]`
   entry and checks it against its pinned SHA-256. A `ci` dataset ships inside the Gig's package.
   A private `release` or `upgrade` dataset that is not there yet is refused with the path to put
   it at.
3. Fills the prompt for every row, checks every request setting against the target, and checks
   every judge scorer (below).
4. Calls the step's model `k` times per row with the filled prompt. `--k` sets `k`; without it,
   the gate's `k` applies, else 1. Each call is recorded with its repeat number (0 to `k` − 1) and
   the step's postprocess output.
5. Runs each of the step's scorers on each recorded answer and writes the scores.

A refusal in steps 1 to 3 comes before any call and leaves no run recorded.

It prints the run id, the call count, and each scorer's mean:

```text
run run_01J…: step answer, suite ci, 6 items x 1, 12 calls
  my_gig.scorers:exact_match: 0.6667 over 6 traces
  step:judge: 0.6667 over 6 traces
```

`--tag T` stores a label on the run. `--temperature`, `--seed` and `--output-mode` go with every
call. A Claude or Codex CLI target can't carry `--temperature` or `--seed`, so a run that sets
them on one is refused before any call. Another target whose adapter has no such setting records
it as unsupported.

The run never prints dataset rows or model answers. An error names the input by its SHA-256. In
an error message, GigAI replaces with `<redacted>` any run of 8 or more characters copied
verbatim from the row, prompt or answer (including their quoted and JSON-escaped forms), and any
shorter value of the row or answer that stands as a whole word. Text a scorer or postprocess
changes before raising, such as an upper-cased, sliced-to-a-few-characters or reformatted copy,
is not recognised and can still show, so keep item text out of the errors your own code raises.

### Call caps

A cap is checked before each call. A run that would go over a cap stops before that call and exits
with status 2 and `cap N reached before call N+1`. The calls already made stay recorded, so
`--max-calls 3` leaves exactly 3 traces.

- `--max-calls N` caps every call in the run, judge calls included.
- The `max_calls` of the step's gate for the suite caps calls to targets that cost money. Calls to
  the offline deterministic adapter don't count against it.
- A budget the Gig context carries caps every call too.
- A `release` or `upgrade` run that would call a target that costs money needs one of these caps.
  Without any, it is refused before any call and the message tells you to pass `--max-calls N`.

### Judge scorers need a validation

A `step:<id>` scorer asks the judge step `<id>` to grade each answer with Inspect AI's
model-graded template. GigAI only trusts a judge it has checked against human labels. The judge
step's `validation` sets the bar (`min_kappa`, `min_n`). The latest `judge_validations` row for
that judge model and judge prompt must meet the bar, or the run is refused before any call:

```text
gigai my-gig eval: scorer step:judge refused: judge … with prompt sha256:… needs a validation of kappa >= 0.6 over >= 3 items (latest: none); validate the judge first
```

A newer failing validation replaces an older passing one. Replay and rescoring check the same bar.

## Score again without calling

```sh
gigai my-gig eval run --step answer --from-traces run_01J…
gigai my-gig eval rescore run_01J… --scorer includes
```

- `eval run --from-traces RUN` makes a new run that records RUN as its source. It copies RUN's
  answers, runs the postprocess on them again, and scores them again. It makes no calls, so
  `--suite`, `--k`, `--max-calls` and the sampling flags are refused with it. A judge scorer reads
  the judge's stored grades instead of asking again. Use it to check that a scorer or postprocess
  change gives the scores you expect.
- `eval rescore RUN` adds new score rows to RUN's own answers. It uses the step's scorers, or the
  ones you name with `--scorer` (repeatable). It makes no new run, trace or call.

Replay only works if the postprocess is pure: the same answer must always give the same output.

## Scorers

A step's `scorers` list takes:

- `gigai.evals.contracts:match` (or `match` with `rescore --scorer`): the target at the end of the
  answer, compared as numbers when every target is a number, so `So the total is $1,234.00`
  matches `1234`.
- `gigai.evals.contracts:includes` (or `includes`): the target anywhere in the answer, ignoring
  case.
- `step:<id>`: a judge step, as above.
- `module:attr`: your own function `(trace, reference) -> Score`.

`match`, `includes` and `pattern` run Inspect AI's scorer code, vendored unchanged apart from
imports. An empty answer scores `I` with reason `no_response`. `pattern(regex)` compares the captured
group as plain text, so `pattern(r"ANSWER:\s*(\d+)")` grades `ANSWER: 1,234` wrong where `match`
grades it right.

A custom scorer gets a `ScoredTrace` (`input`, `output`, `parsed`, `parse_status`, `item_id`,
`repeat_index`) and the row's `expected` value, and returns a `Score`:

```python
from gigai.sdk import CORRECT, INCORRECT, Score, pattern

answer_marker = pattern(r"ANSWER:\s*(\d+)")


def exact_match(trace, reference):
    found = (trace.parsed or {}).get("answer")
    return Score(CORRECT if str(found) == str(reference) else INCORRECT, answer=found)
```

GigAI stores `Score.value` as a number: `C` is 1, `P` is 0.5, `I` and `N` are 0, `yes`/`true` is
1 and `no`/`false` is 0, and numbers stay as they are. NaN, infinity, other strings, or a return
value that is not a `Score` stop the run with an error.

## Postprocess

A step's `postprocess` is `module:attr`, a function `(raw_text) -> (parsed, status)`. `status` is
`ok` or names why parsing failed. It must be pure: no files, no network, no clock, no randomness.
Replay runs it again on stored answers and expects the same result.

## A worked example

The repository's eval test Gig runs a `solve` step on 10 rows of tinyGSM8k (`ci` suite) with the
offline deterministic adapter. Its canned answers are right on 8 rows (3 say `ANSWER: N`, 5 say
`So the total is $N.00`) and wrong on 2, which add one to the answer. `match` scores **0.800**,
while `pattern(r"ANSWER:\s*(\d+)")` scores 0.300. The 0.800 is committed as the expected result, and
a replay from those traces gives the same per-item scores with no calls.

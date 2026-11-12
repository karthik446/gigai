---
title: Base run
description: How `gigai <gig> eval base` runs the public eval sets across a ladder of models, and the five checks that say whether the evals can be trusted.
---

Before a Gig's own eval numbers mean anything, the evals themselves need checking. Do stronger
models score higher? Is the score stable when you ask again? Does it fall when you break the
prompt on purpose? `gigai <gig> eval base` answers these. It runs the public eval sets across a
ladder of models, from a strong paid model down to the offline deterministic adapter. Then it
computes five validation gates, named a to e.

```sh
gigai my-gig eval base --plan                                  # what a run would call and cost
gigai my-gig eval base --deterministic                         # offline, 0 calls (source checkout)
gigai my-gig eval base --rungs claude-haiku,gemini-flash-lite,gemma,deterministic --max-calls 2080 --report base.md
```

## When to run it

| Cadence | Command | Calls |
|---|---|---|
| Every commit | `eval base --deterministic` | 0 |
| Every release | `eval base --tier gate` on the rungs you can reach, with a cap | 560 per rung, plus noise and canaries: 2,080 model calls, about $24, for `claude-haiku`, `gemini-flash-lite` and `gemma` |
| On request | `eval base --tier full` or `--tier judge` | see `--plan` |

`--deterministic` answers every selected rung from the canned completions of the committed ci
slices, so every rung scores the same. It checks the wiring: every set is scored, every gate is
computed, the replay matches, and the report renders. It cannot check the model order. The ci
slices ship only in a gigai source checkout. An installed gigai refuses `--deterministic` with
exit 2 and says to run it from a checkout.

Without `--deterministic`, the run reads each set's release slice from the gig folder
(`<home>/gigs/<gig>/datasets/<set>-release.jsonl`). If a slice is missing, it fetches it first.

## The ladder

`src/gigai/evals/ladder.toml` lists the rungs. Each rung records:

- its adapter and model
- its price per million tokens, with the page the price came from
- its rank in the published order, with a source URL and a date
- the sampling knobs it accepts (`temperature`, `seed`), or why it accepts none

The ladder runs from a strong paid model down to Gemma, which runs free through Ollama, and ends
with the offline deterministic adapter. `--rungs R,R` picks rungs by name. A rung is skipped, and
the report gives the reason, when:

- its model target is missing from `config.toml`, or uses another adapter
- its model target runs a different model from the one the ladder pins
- the adapter's binary or API key is missing
- for Gemma, no Ollama server answers at the endpoint's `http://127.0.0.1:<port>` URL, or the
  server has not pulled the model. This check runs only when the run will make calls, so
  `--plan` and a refused run never touch the network.

The Gemma rung pins the tagged model `gemma3:4b`, because Ollama names pulled models
`name:tag`. Pull it once with `ollama pull gemma3:4b`, and give its model target in
`config.toml` the same `model = "gemma3:4b"`.

The Claude rungs pin the `claude` CLI aliases `sonnet` and `haiku`. These aliases move to the
newest Sonnet and Haiku, so a rank is true for the model the alias named on the rank's date,
which the ladder records.

The ladder file is data, and its SHA-256 is part of every report. A Gig can register the ladder
it was evaluated against in a step's release gate:

```toml
[[evals.steps]]
# ...
gates.release = { suite = "release", prereg = { registered_at = "2026-11-09", ladder_sha256 = "<sha256 of ladder.toml>" } }
```

If the shipped ladder no longer matches that SHA-256, the run is refused with exit 2. To accept
a changed ladder, register it again.

## Tiers

`--tier` picks how many items of each set to run:

- `gate` (the default) runs 560 items per rung: 60 from ContractNLI and 100 each from RAGTruth,
  QuALITY, IFEval, JSONSchemaBench and RewardBench 2.
- `judge` runs 100 items from JudgeBench and 150 from MT-Bench human judgments.
- `full` runs both, plus 200 prompt-injection items.

IFBench is not available, so IFEval stands in for it, and the report says so. The IFEval scorer
is approximate. It checks 15 of the 25 instruction types; an item with any other type is left
unscored. It skips language detection for the all-capitals and all-lowercase checks, and it
counts sentences with a simple pattern rather than a full sentence splitter.

A JSONSchemaBench item is asked in schema mode: the call carries the item's own JSON Schema, so
the model sees the schema its answer must match. An answer that is not JSON scores 0, and so
does a provider that refuses the schema with HTTP 400.

A set where the best rung scores 97 points or more is marked saturated. A saturated set is
reported but left out of a rung's suite score and out of the gates.

## The five gates

**a. Order.** This gate compares the measured order of the rungs with their published order. It
uses Kendall's tau-b, which runs from +1 (the two orders agree on every pair of rungs) through 0
(no relation) to −1 (exact reverse). It passes when tau-b is at least 0.6. With fewer than three
measured rungs, or with every rung tied, the gate is `not_validated`. The deterministic run ties
every rung, so its gate a is always `not_validated`.

**b. Noise floor.** One rung answers up to 100 items again, with the items spread evenly across
the sets. Its main answers count as the first repeat, so with three repeats it answers those
items twice more. The rung is `claude-haiku`, or the strongest other selected rung. The limits
are a suite score that varies by at most 2.0 points (sample standard deviation) and at most 10%
of items that flip between right and wrong.

Gate b is pass/fail when the rung can fix a seed or a temperature. It is also pass/fail for the
deterministic rung, whose repeats cannot differ. A `--deterministic` run, the check on every
commit, keeps gate b pass/fail too: every rung answers from canned data there, so its repeats
cannot differ either. Otherwise gate b is `measured`, not pass/fail, when the rung can fix
neither. `claude-haiku` runs through the claude CLI, which takes no seed and no temperature, so
its repeats differ by design. The limits are unchanged. The report shows them next to the
numbers and says which ones the numbers exceed, as a fact and not as a verdict.

A measured gate b also reports the smallest difference the data can tell apart: 1.96 times the
square root of 2 times the noise sd, about 2.77 times the sd, in points. In plain words, two
rungs' suite scores can be told apart only if they differ by more than that. The 1.96 is for a
95% interval. The square root of 2 is there because both scores are noisy. It assumes every rung
is as noisy as the noise rung, and the sd comes from only three repeats, so it is rough. With an
sd of 3 points, for example, two rungs must differ by more than about 8.3 points.

**c. Canaries.** Three deliberate sabotages must each pull the score down:

| Canary | What changes | Must fall by |
|---|---|---|
| `broken_prompt` | the prompt's sentences (or words) shuffled | 10 points |
| `truncated_context` | only the first quarter of the prompt kept | 15 points |
| `wrong_label` | the main answers scored against wrong gold labels (no calls) | 30 points |

Each canary must also pass an exact McNemar test on the items that flipped, with p below 0.01.
The two canaries that call re-ask 100 items each, spread evenly across their sets, on
`gemini-flash-lite`, or the strongest other selected rung. `truncated_context` is priced at a
quarter of the prompt.

**d. Judge agreement.** This gate checks the judge against human labels. The base run does not
check it, so it is always `not_validated` here.

**e. Replay.** The run copies one main run's stored answers into a replay run, without calling
the model, and scores them again. The gate passes when the scores differ by less than 0.0005
and the replay made 0 calls. Because it scores the same stored text again, it catches a scorer
that is not repeatable and answers lost in the copy. It cannot catch a model that answers
differently on a second call; gate b measures that.

Exit code 0 means the run completed. Read each gate's status, `pass`, `fail`, `measured` or
`not_validated`, in the report. Exit code 0 does not mean the gates passed.

## Cost and caps

`--plan` prints, for each rung, its sets, the item counts, the calls, and the dollars implied by
its price and each set's token estimate. It also prints the noise and canary calls. It builds
nothing and calls nothing.

A run that makes model calls needs a cap. A model call is a call to any rung but the
deterministic one, so a free local rung such as Gemma counts too. The cap is `--max-calls N` or
a release gate's `max_calls`, and the smaller one wins. The run checks the plan against the cap
before it reads any slice, probes any endpoint or builds any adapter. A run with no cap, or one
whose plan is over the cap, is refused with exit 2:

```text
gigai my-gig eval: cap 0 reached before call 1 (the plan needs 2080 model calls; see --plan)
```

The cap is checked again before every call.

## Failed calls

A model call can fail for one item: the local Ollama server answers HTTP 500 or times out, an
API returns an error, or a CLI exits with an error. The run records that call as a trace with an
empty answer, outcome `failed` and the error class (with the HTTP status, never the error text),
gives it no score and goes on. The call still counts toward the cap and toward the rung's calls.
The suite and set scores are over the scored items only, and each run row shows its items and
how many were scored.

The report counts failed calls per rung: `failed_calls` and `failures` (error class to count) in
`--json`, and after the rung's calls in the text and markdown reports, only when some failed:

```text
  gemma: rank 5, ran, 560 calls (3 failed: OllamaLocalAdapterError 3), suite 41.2
```

A rung may lose at most 10% of its planned model calls (its main, noise and canary calls in
`--plan`). The failure that passes that limit stops the run with exit 1:

```text
gigai my-gig eval: rung gemma: 57 of 560 calls failed (limit 10%); last error OllamaLocalAdapterError
```

Some failures stop the run at the first one, because every later call would fail the same way:
missing authentication, an API answering 401, 402 or 403, a local model whose identity changed,
a request field or capability the adapter refuses, a cancelled call, and any error gigai does not
recognize. Two schema-mode outcomes are not failed calls but the model's answer, scored 0: a
reply that is not JSON, and an HTTP 400 (the provider cannot answer under that schema). An HTTP
400 to any other call is a failed call for that item.

## Output and exit codes

- The default output is a short text summary. `--json` prints the full report, and
  `--report PATH` writes it as markdown in the layout of `docs/evals/base-run.md`.
- Every rung and set run is a normal row in the eval store (`evals.sqlite` in the gig folder).
  That covers the main runs and the noise, canary and replay runs.
- Errors and reports name rungs, sets, run ids and input SHA-256s. They never show item text.

| Exit | Meaning |
|---|---|
| 0 | The run completed. The gate verdicts are in the report. |
| 1 | A run-level model-call error, more than 10% of a rung's calls failed, or the eval store failed. Rows written before the stop stay recorded. |
| 2 | Refused before any call: bad options, a malformed ladder, a prereg mismatch, a missing cap, a plan over the cap, or `--deterministic` outside a source checkout. |

---
title: Validating a judge
description: How `gigai <gig> eval validate-judge` measures a judge step's agreement with people's labels or a public set, what the pass rule is, and what it unlocks.
---

A judge is an eval step that grades another step's answers: `[[evals.steps]]` with a `grades`
entry and a `validation` table (see [Running evals](../../concepts/eval-run/)). `eval run` only
scores with a judge whose agreement with people has been measured and recorded. `gigai <gig> eval
validate-judge` is the command that measures it.

```bash
gigai <gig> eval validate-judge --step judge --against labels.jsonl
gigai <gig> eval validate-judge --step judge --against labels --swap
gigai <gig> eval validate-judge --step judge --against judgebench --swap --json
```

`--step` is the judge step. `--against` is what the judge is checked against: a labels file, the
word `labels` for the labels already in the gig's eval store, or a public set (`judgebench`,
`mtbench_human`). `--swap` adds a position-swap check. `--json` prints the report as one JSON
object.

The command calls the judge once per unit (twice more per pair with `--swap`), traces every call
under a run tagged `validate-judge:<against>`, and records the result in the eval store's
`judge_validations` table. No input or output text is printed; the report shows ids, counts and
numbers only.

## Against labels

This is the check that unlocks the judge for `eval run`.

Give it labels of the graded step's traces, either a file in the
[`eval label` export format](../../concepts/eval-label/) (one JSON object per line with `run_id`,
`item_id`, `step_id`, `label`, `critique`, `rubric`, `who`, `at`, `round`) or `labels` to read
every label of that step from the store. Each labelled trace is one unit:

- The unit's human verdict is the majority of its `met` / `not met` labels. `unknown` labels are
  skipped; a tie is contested and the unit is dropped. Both counts are reported.
- The judge is asked the gig's own scoring prompt for that trace: the item's question, the stored
  answer and the suite row's `expected` value, exactly as `eval run` would ask it. Its grade is
  parsed the same way (`GRADE: C` is "met"). A reply that does not parse is counted as `unparsed`
  and scored as "not met", so it costs agreement on every unit people marked met.

The report gives, over the units:

- **kappa** with a 95 % interval (Cohen's κ, the Fleiss-Cohen-Everitt standard error; see
  [Eval statistics](../../concepts/eval-statistics/)), and the raw observed agreement,
- **tpr** and **tnr**: the share of human "met" units the judge also marks met, and the share of
  human "not met" units it also marks not met.

Because the units are the gig's own scoring prompt, the recorded row carries that prompt's
digest and the judge's model target, which is what `eval run` looks for.

### Position swap

`--swap` asks whether the judge prefers an answer for what it says, not for where it sits. For
each item, the labelled "met" traces are paired with the labelled "not met" traces of that item,
and each pair goes to the judge twice through the vendored autoevals **Battle** template: once
with the met answer first, once with it second. A pair is consistent when the same answer wins
both times, whichever answer that is. **swap consistency** is the consistent share of the pairs:
order invariance. A judge that always prefers the first answer scores 0; a judge that follows the
content scores 1 even when it prefers the wrong answer. Whether it picked the met answer both
times is reported separately as the pair accuracy (`swap_pair_accuracy` in the details), which is
not part of the pass rule: kappa already measures whether the judge is right.

## Against a public set

`--against judgebench` or `--against mtbench_human` runs the judge over gigai's committed slice of
that public pairwise set (the slices [`eval` datasets](../../concepts/eval-datasets/) ship). Every
item is a prompt with two responses and a gold preference; the judge's reply is read for its
verdict (`A>B` / `B>A` for JudgeBench, the `winner` for MT-Bench), and agreement, kappa, the rates
and, with `--swap`, the consistency after exchanging the two responses in the prompt (consistent
when the same response wins both times, the same definition as above) are reported
as above, plus the accuracy per source of the set. A judge on a deterministic target answers from
the slice's canned completions, so the check never costs a call.

This validates **the model**, not your scoring prompt: the recorded row carries the set's own
prompt digest, so it does not unlock the judge for `eval run`. Use it to compare candidate judge
models before you collect labels.

## The pass rule

```text
kappa >= 0.6 and n >= 126 and swap_consistency >= 0.9 when measured
```

`n` is the number of units that reached the judge. 126 is the number of labels gigai's gates have
always asked for: at 126 balanced units a kappa near 0.6 carries a 95 % interval of about ±0.14
(at 63 it is ±0.20, wide enough to hide a judge that is really at 0.4). A check over fewer units
is recorded with its numbers but never passes. Without `--swap` the rule uses kappa and `n` only.

Two things leave a metric without a value. Kappa is undefined when the labels and the judge
between them use one category only (every unit met, say): there is no chance agreement to correct
for. Swap consistency has no value when `--swap` finds no pair, that is no item has both a met and
a not-met trace (or, for a public set, no unit reached the judge). Each is a reason of its own in
the report (`kappa undefined`, `no swap pairs`), fails the rule, and writes no row for that metric;
the report's `recorded:` line names the rows that were written.

The command exits 0 when the rule is met, 1 when it is not (a clean fail: the rows are recorded,
the last line reads `pass: no (n < 126)` and names every reason), and 2 when it could not run at
all: an unknown step, a step that grades nothing, a bad labels file, a label of a trace the store
does not hold, or a judge target the configuration cannot resolve. Nothing is recorded on exit 2.

## What is recorded

Each check writes one `judge_validations` row with `metric = 'kappa'` and, with `--swap`, a second
one with `metric = 'swap_consistency'`, both in one transaction (a check that cannot write both
writes neither) and only for a metric that has a value. Both carry `n_items`, the judge's model
target (`judge_model`), the prompt digest (`judge_prompt_sha256`), the label set's name and digest,
and a `details` JSON object: `pass` (true or false), `reasons`, `rule`, the interval, `tpr`, `tnr`,
`observed_agreement`, the label counts (`labels`, `skipped_unknown`, `contested`, `unparsed`),
`swap_consistency`, `swap_pair_accuracy` and `swap_pairs` (`null` without `--swap`), the
per-source accuracy of a public set, `unlocks_scorer`, and the id of the run that traced the
judge's calls.

A check that fails the rule is still recorded with `pass` false. `eval run` reads the newest row
of the judge and its prompt and refuses to score when it fails the step's own `validation` bar
(`min_kappa`, `min_n`) or carries `pass` false, naming the reasons:

```text
scorer step:judge refused: judge deterministic-b with prompt sha256:… needs a validation of
kappa >= 0.6 over >= 3 items (latest: kappa 1.000 over 24 items), which fails: n < 126;
validate the judge first
```

`gigai <gig> eval list` shows each judge's latest check with a `validated yes` or `validated no`
suffix (JSON: `last_validation.validated`), so you can see at a glance which judges `eval run`
will accept.

## Vendored templates

The Battle template of the swap check and the other grader templates come from
[autoevals](https://github.com/braintrustdata/autoevals) (MIT), and two reference prompts from
[openevals](https://github.com/langchain-ai/openevals) (MIT). They are copied unchanged under
`gigai/evals/_vendor/autoevals/templates/` and `gigai/evals/_vendor/openevals/prompts/`, with each
project's LICENSE and a `DIFF.md` that records the upstream commit and the sha256 of every copied
file; a test recomputes those hashes. gigai reads the YAML templates with its own small parser, so
no YAML library is needed. The templates (`battle`, `closed_q_a`, `factuality`, `humor`,
`possible`, `security`, `sql`, `summary`, `translation`) and the openevals `correctness` and
`hallucination` prompts are available to a gig's own judge code through
`gigai.evals.judge.load_template`, `render_template` and `openevals_prompt`.

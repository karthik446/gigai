---
title: Running an ablation
description: How `gigai <gig> eval ablate` runs a pre-registered ablation over one or more knobs of an eval step, one run per design cell, and reports the level comparisons with one Holm correction.
---

An ablation asks what one knob of a step does to its primary metric: run the step's suite once per
level, then compare the levels. `gigai <gig> eval ablate` does that for the knobs a run can move,
under a plan you write down before the first run.

```bash
gigai <gig> eval ablate --axis temperature --prereg ablations/sampling.toml
gigai <gig> eval ablate --axis temperature,top_p,output_mode,prompt --design l9 --prereg ablations/sampling.toml
gigai <gig> eval ablate --axis model --prereg ablations/models.toml --max-calls 200
gigai <gig> eval ablate --axis temperature --prereg ablations/sampling.toml --json
```

Every run it makes is an ordinary [eval run](../../concepts/eval-run/) (same store, same traces and
scores, `eval report` and `eval compare` read it), tagged `ablate:<id>:<design>:<cell>` and carrying
the plan in its `prereg` block. The comparisons use the same tests, intervals and MDE as
[eval compare](../../concepts/eval-compare/).

## The pre-registration

`--prereg FILE` is a TOML file written before the first run. It is the whole plan: the step, the
suite, the primary metric, the number of items, the levels of each axis and which pairs of levels
you intend to compare.

```toml
id = "solve-sampling"           # names the ablation; every run of it carries this id
registered_at = "2026-11-10"
step = "solve"
suite = "ci"                    # ci | release | upgrade
n_planned = 10                  # items per run; must equal the suite's size
k = 1                           # repeats per item (optional, default 1)
primary_metric = "match"        # a scorer of the step, by its metric name
alpha = 0.05                    # optional, default 0.05
direction = "two-sided"         # two-sided | greater | less (optional)

[axes.temperature]
levels = [0.0, 0.5, 1.0]

[axes.output_mode]
levels = ["text", "json", "schema"]
schema = { type = "object", properties = { answer = { type = "string" } } }   # sent with the schema level

[axes.prompt]
levels = ["prompts/solve.txt", "prompts/solve-terse.txt", "prompts/solve-steps.txt"]

[axes.model]
levels = ["deterministic-a", "deterministic-b"]   # targets of config.toml; the first is the baseline

[[comparisons]]
axis = "temperature"
levels = [0.0, 1.0]
```

Unknown keys are refused. An axis without an `[axes.<id>]` table runs on its default levels
(below); `prompt`, `few_shot`, `context`, `model` and `judge_model` have none and must be registered.
The comparisons listed are the **registered** family: they are Holm-corrected together at `alpha`.
Every other level pair of every axis is still measured and printed, marked `exploratory`, with its
raw p-value only.

The file is hashed byte for byte (`sha256`) and the digest is stored in every run's prereg block.
A later `eval ablate` under the same `id` from a file that no longer hashes the same is refused
(exit 2, `pre-registration changed`) before any run: a plan that changed after looking at data is a
new study, so give it a new `id`. Whitespace and comments count; so does a line ending.

## Axes

An axis is one knob. A run can move a request setting, the prompt template, or the target a step
is bound to for that one run; what it cannot move yet is refused up front, before any run row, with
the reason.

| axis | what moves | default levels |
| --- | --- | --- |
| `temperature` | request setting `temperature` (0 to 2) | `0.0, 0.5, 1.0` |
| `top_p` | request setting `top_p` (0 to 2) | `0.1, 0.5, 1.0` |
| `output_mode` | request setting `output_mode`; the `schema` level sends `[axes.output_mode].schema` | `text, json, schema` |
| `retry_policy` | request setting `retry_policy`, as attempts (1 to 10) | `1, 2, 3` |
| `prompt` | the step's prompt template: each level is a prompt file of the gig package | none, register them |
| `few_shot` | the prompt template with the examples baked in (prompt files) | none, register them |
| `context` | the prompt template with the context placed differently (prompt files) | none, register them |
| `model` | the step's target: each level is a target name of `config.toml` | none, register them |
| `judge_model` | the judge step's target, for the step's one `step:<judge>` scorer | none, register them |
| `reasoning_effort` | not yet: waits for a core request field a run can carry | |
| `max_output_tokens` | not yet: same | |
| `timeout` | not yet: same | |

`model` and `judge_model` bind the target per run, the way `gigai <gig> steps set` would, without
writing `steps.toml`: the gig's binding is what it was when the ablation ends. Every level must be a
target of `config.toml` (an unknown one is refused before any run), and every `judge_model` level
must have passed [judge validation](../judges/) against the current judge prompt, each checked
before the first run. `judge_model` needs a step with exactly one `step:<judge>` scorer; a step
without one, or with two, is refused.

A request-setting axis is also checked against the step's target, or against every target of the
`model` axis when both move: a claude or codex CLI target carries only `output_mode`, `schema` and
`system`, so `--axis temperature` on a step bound to one is refused
(`axis unsupported by adapter claude_cli`) before any run. `prompt`, `few_shot` and `context` all
set the template, so one ablation moves at most one of them.

## Designs

`--design ofat` (the default) moves one factor at a time: every axis at its first (baseline) level is
one cell, then each axis moves through its other levels alone. One axis of three levels is three
runs; two axes of three levels are five. An arm is one cell, so a comparison is a plain paired test
between two runs.

`--design l9` is the L9 orthogonal array: two to four axes of exactly three levels each in nine runs,
where every level of one axis meets every level of every other axis exactly once. An arm is the three
cells that share a level; the comparison pools them, three traces per item, so the test is on the
item means. L9 estimates main effects only; interactions between axes are folded into the noise.

The cost is the run count times one run's calls, judge calls included: nine runs over a 100-item
suite with a judge is 1,800 calls. `--max-calls N` caps the **paid** calls over the whole ablation:
calls on a deterministic target are free and not counted, every other target's call is. A run that
reaches the cap is cut short, the cells after it are recorded with zero calls, the table is still
printed, and the command exits 2; a cell whose first paid call would already be over the cap makes
none. So `--max-calls 0` runs an all-offline plan in full (the fixture gig's
`eval ablate --axis temperature --design ofat --prereg tests/fixtures/evals/prereg.toml --max-calls 0`
writes its three runs and prints the Holm table), and on a paid target it refuses before the first
call, which is how to check a registration before paying for it. The step's `cap_usd` applies to
every run as it does to `eval run` ([USD caps](../../concepts/usd-caps/)).

## What it prints

```
ablate solve-sampling: design ofat, step solve, suite ci, 10 items x 1, 3 runs, prereg sha256:… registered 2026-11-10, alpha 0.05 two-sided
  run run_…: temperature=0.0, 10 calls
  run run_…: temperature=0.5, 10 calls
  run run_…: temperature=1.0, 10 calls
  temperature 0.0 -> 0.5 (registered): match 0.8000 -> 0.8000, difference +0.0000, 95% CI +0.0000 to +0.0000, McNemar p=1.0000, holm p=1.0000, no effect detected, MDE none at n=10
  temperature 0.0 -> 1.0 (registered): match 0.8000 -> 0.0000, difference -0.8000, 95% CI -1.0000 to -0.6000, McNemar p=0.0078, holm p=0.0234, rejected, MDE 0.8000 at n=10
```

One line per comparison: the primary metric's means, the difference (B minus A), the interval when
the items span enough clusters, the test and its p-value, the Holm-adjusted p-value for registered
pairs, the verdict, and always the MDE at that n so a null result says what it could have seen
([eval statistics](../../concepts/eval-statistics/)). A pair whose arms scored nothing (a capped run)
is `not measured`. `--json` prints the same as one object: `runs` (cell, run id, levels, calls),
`comparisons` and `capped`.

Exit codes: 0 done; 1 a run failed (an adapter or scorer error) or the store could not be opened;
2 refused before any run (plan, axis, design, adapter or changed registration) or the call cap
was reached.

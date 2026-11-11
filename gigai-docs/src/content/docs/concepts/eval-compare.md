---
title: Comparing runs and gates
description: How `gigai <gig> eval compare` pairs two stored runs item by item, which runs it refuses to compare, and how `gigai <gig> eval gate` turns a step's declared gate into exit 0, 1 or 2.
---

`gigai <gig> eval compare A B` reads two runs from the Gig's eval store (`evals.sqlite` in the gig
folder, `<home>/gigs/<gig>/`) and says whether B differs from A. `gigai <gig> eval gate --suite S`
applies the gate a step declares for suite `S` and exits 0 (pass), 1 (fail) or 2 (cannot compare).
Neither command calls a model, and neither writes to the store.

The statistics behind both are described in [Eval statistics](../eval-statistics/).

## Compare two runs

```sh
gigai my-gig eval compare run_01JA… run_01JB…
```

Both runs must be runs of the same step. Compare pairs them by item id: for each metric, every item
gets one value per run, the mean of its repeats. Repeats are averaged per item, not paired by
repeat index, so both runs must repeat every item the same number of times. If an item was retried,
its last attempt counts. If an answer was rescored, its latest score counts. Items are grouped by
the dataset row's `cluster_id`, or by item id when the row has none.

For each metric it prints one line:

```text
compare run_01JA… -> run_01JB…: step solve, 12 items, 10 clusters, dataset sha256:b0a2…
  match: 0.2500 -> 0.7500, difference +0.5000, 95% CI +0.0909 to +1.0000, McNemar b=3 c=9 p=0.1460, holm p=0.1460, MDE 0.… at n=12
  answer_marker: 0.5000 -> 0.5750, difference +0.0750, 95% CI …, Wilcoxon p=0.0039, holm p=0.0078, MDE 0.… at n=12
```

- **Difference and interval.** The difference is B − A. The 95% interval comes from a paired
  cluster bootstrap with a fixed seed, so the same two runs always print the same interval. A gate
  slice whose items span fewer than 10 clusters prints `no interval under 10 clusters` instead.
- **Test.** A metric whose values are all 0 or 1 gets the exact McNemar test. `b` counts the items
  A got right and B got wrong, and `c` counts the reverse. Any other metric gets the Wilcoxon
  signed-rank test on the paired differences.
- **MDE.** Every line prints the minimum detectable difference at this number of items (80% power,
  two-sided α = 0.05). A difference smaller than the MDE is one these runs could not have
  detected reliably.
- **Noise bound.** When the runs have `k` ≥ 2, the line ends with `noise bound` (`noise_bound`
  in the JSON): the upper end of the 95% interval around the mean paired difference. A
  difference under it is within run-to-run noise.
- **Correction.** Every preregistered primary and guard metric that both runs scored is corrected
  together with Holm, or with Benjamini-Hochberg under `--correction bh`. This holds under
  `--metric` too: a preregistered metric that is not shown still counts in the correction. Other
  metrics keep their raw p-value.

Options:

- `--metric M` (repeatable) compares only these metrics. `M` is a scorer id or the part after its
  last `:`. Without it, every metric both runs scored is compared.
- `--slice S` keeps the items whose dataset row has `slice` equal to `S` or `S` in its `tags`.
- `--json` prints the whole comparison as one JSON object.

### What compare refuses

Two runs that measured different things are not compared. Compare exits 2 with one line on stderr
when:

- the runs are of different steps;
- the runs read datasets with different SHA-256s;
- a compared or preregistered scorer has a different version in the two runs;
- a compared or preregistered scorer scored a different set of items in each run;
- a run repeated its items unequally, or the runs repeated them a different number of times (`k`);
- a run holds two traces for the same item, repeat and attempt;
- an item sits in a different cluster in each run (the cluster key differs);
- the items span fewer than 10 clusters, or the slice does;
- a named run, or a named metric in either run, does not exist;
- the Gig has no eval store yet.

## Apply a gate

```sh
gigai my-gig eval gate --suite ci
gigai my-gig eval gate --suite release --step solve
```

A gate is the `gates.<suite>` table of a `[[evals.steps]]` entry. Without `--step`, every step that
declares a gate for the suite is checked. The last line is `gate <suite>: PASS` or
`gate <suite>: FAIL`. On a fail, stderr gets one line listing each failed check.

### ci: replay a snapshot

The `ci` gate checks that the step's code still scores the recorded answers the way it did when the
snapshot was made. It makes no model call.

```toml
[[evals.steps]]
id = "answer"
datasets.ci = { path = "data/ci.jsonl", source = "…", licence = "CC0-1.0", sha256 = "…" }
gates.ci = { suite = "ci", snapshot = "snapshots/answer-ci.json" }
```

The snapshot is a JSON file inside the Gig's package. It holds:

- `dataset_sha256`: the ci dataset's SHA-256 when the snapshot was made;
- one row per item with:
  - `id`;
  - `output`: the model's recorded answer;
  - `judge`: each judge step's recorded reply;
  - `scores`: each scorer's value.

The gate runs the step's postprocess and every code scorer on the recorded answer. It scores each
judge scorer from its recorded reply. Then it compares every value with the snapshot. One value off
fails the gate and names the row:

```text
gigai my-gig eval: gate ci failed: step answer row q3: my_gig.scorers:exact_match scores 1.0000, snapshot 0.0000
```

The gate exits 2 when:

- the snapshot is missing, is not JSON, or lies outside the package;
- the ci dataset changed since the snapshot was made;
- the snapshot's rows do not match the dataset's items.

To fix that, record the snapshot again from a fresh `eval run --suite ci`.

### release and upgrade: pair two runs

```toml
gates.release = { suite = "release", k = 1, paired_to = "last-release", slices = ["easy", "hard"], primary = { metric = "match", min_ci_lower = 0.0 }, guard = { metric = "answer_marker", no_worse = -0.10 } }
gates.upgrade = { suite = "upgrade", k = 2, paired_to = "previous-model", primary = { metric = "match", no_worse = -0.05 } }
```

The gate takes the newest finished run of the step on the suite and pairs it, like `eval compare`,
with the run `paired_to` names:

- `last-release` (the default for `release`) means the newest earlier `release` run.
- `previous-model` (the default for `upgrade`) means the newest earlier run of the suite on a
  different model.

The candidate must have read the suite's dataset as it is now.

Two rules decide the gate:

- `primary` (required) and `guard` each name a metric: a scorer id, or the part after its last
  `:`.
- `min_ci_lower` fails the rule when the lower end of the 95% interval of B − A is below it.
- `no_worse` fails it when the difference B − A is below it.

Each rule needs at least one of the two thresholds, and both are numbers. A rule without a metric
or a threshold, with a threshold that is not a number, or with any other key exits 2 before any
run is read.

The rules apply to all items and to each slice. `slices` lists them; without it, every `slice` and
tag the rows name is used. A listed slice that none of the runs' items belongs to exits 2, so a
misspelt slice is never skipped silently. A slice whose items span fewer than 10 clusters is
reported but not gated. The `upgrade` gate also prints each run's `k` and, when it is 2 or more,
the variance components of its repeats (within-item, between-item, and the standard error of the
mean).

### Held-out share

`require = { private_share = 0.30 }` on a release or upgrade gate requires that at least 30% of the
suite's items are held out. An item is held out when its row's `licence`, or else the dataset's
`licence`, is `private`. The gate checks this only when it is set. If the share is lower, the gate
exits 2 before reading any run:

```text
gigai my-gig eval: held-out share 0.00 < 0.30
```

`eval gate` checks no other `require` key. A gate that sets one, or sets a `private_share` that is
not a number, exits 2 rather than pass unchecked.

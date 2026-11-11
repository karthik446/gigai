---
title: Reporting runs
description: How `gigai <gig> eval report` summarises the stored eval runs (means with intervals, slices, gate verdicts, label counts), what its JSON feed leaves out, and how `--export inspect` writes Inspect log files.
---

`gigai <gig> eval report` reads the Gig's eval store (`evals.sqlite` in the gig folder,
`<home>/gigs/<gig>/`) and summarises every eval run in it, oldest first. It calls no model and
does not write to the store.

```sh
gigai my-gig eval report
gigai my-gig eval report --run run_01JB… --json
gigai my-gig eval report --export inspect
```

## What it prints

For each run:

```text
run run_01JA…: step solve, suite release, k 1, 12 items, 10 clusters, 12 calls, models deterministic-a
  answer_marker: mean 0.5000 over 12 items, 10 clusters, CI [0.5000, 0.5000]
  match: mean 0.2500 over 12 items, 10 clusters, CI [0.0000, 0.4545]
  slice easy: 6 items, 6 clusters
    match: mean 0.5000 over 6 items, 6 clusters, no CI (fewer than 10 clusters)
  …
  gate release: PASS
  labels: met 9, not met 2, unknown 1 (1 relabels)
```

- **Means.** Each metric is named by the part of its scorer id after the last `:`, or by the
  whole scorer id when two scorers share that part. Its mean is the mean of the item means: an
  item's repeats are averaged first, so an item repeated more often does not weigh more. If an
  item was retried, its last attempt counts. If an answer was rescored, its latest score counts.
- **Interval.** The 95% interval of the mean comes from a cluster bootstrap with a fixed seed
  (items grouped by the dataset row's `cluster_id`), so the same run always prints the same
  interval. Under 10 clusters there is none.
- **Slices.** Each `slice` and tag the dataset rows name gets the same lines over its own items.
  When the dataset changed since the run, or cannot be read, the report says so instead; a path
  under the gigai home is written `<home>/…`, so the line does not name your user folder.
- **Gate.** The verdict of the gate the run's step declares for the run's suite, worked out the
  way [`eval gate`](/concepts/eval-compare/#apply-a-gate) works it out: a `release` or `upgrade`
  gate judges the newest run of the suite, so every run of that step and suite shows the same
  verdict. A step without such a gate prints `no gate`. A gate that cannot be checked, for
  example the first release run with nothing to pair it with, prints `cannot check` and why; the
  report itself still exits 0.
- **Labels.** Counts of the first-round labels [`eval label`](/concepts/eval-label/) gave the
  run's traces, by value; relabels are counted apart. A run without labels prints no label line.

`--run R` reports only run `R`.

## The JSON feed

`--json` prints the same report as one JSON object:
`{"gig": …, "include_inputs": false, "runs": [...]}`. Each run lists its metrics (each with its
`metric` name and full `scorer` id), and its items by id, cluster, repeat, trace id, model, and
the SHA-256 of the input and of the raw output, with each scorer's value. It holds no prompt,
answer or expected value, no key named `input`, no path under the gigai home (written `<home>`),
and no string longer than 200 characters (a longer one, such as a long gate line, is cut to 200
and ends with `…`). That keeps it safe to paste into a ticket or feed to another tool.

`--include-inputs` adds each item's `input` (the prompt), `output` (the raw answer) and `expected`
(the dataset row's reference), uncut, and prints a warning on stderr. These are private: share
the result only where the gig folder could be shared.

## Export to Inspect

`--export inspect` writes one [Inspect](https://inspect.aisi.org.uk/) log per reported run, so
`inspect view` lists the runs and opens each one:

```sh
pip install 'gigai[inspect]'
gigai my-gig eval report --export inspect
inspect view start --log-dir ~/.gigai/gigs/my-gig/inspect
```

The `inspect` extra installs the Inspect release the logs are shaped for (inspect-ai 0.3.288),
with `agent-client-protocol` below 1.0, which that release needs. Without the extra, the export
exits 2 with that install line before reading anything.

The logs go to `inspect/` in the gig folder (`<home>/gigs/<gig>/inspect/`, readable only by you),
one JSON file per run named `<UTC time of the run>_<gig>-<step>_<run id>.json`; every character
of the gig, step or run id other than a letter, digit, `.` or `-` becomes `-`. The line
`wrote <path>` names each file (on stderr under `--json`, so stdout stays JSON).

A file is only replaced by a new export of the same run. The export exits 2, before writing any
file, when two of the runs it writes would get the same name (run ids that differ only in the
characters that become `-`, with the same time), or when the name is already taken by a file
that is not a log of that run.

Each log has one sample per trace: its id is the item id and its epoch the repeat, counted from 1.
Its scores are keyed by metric name, as the report names them.
Without `--include-inputs`, a sample's input is the SHA-256 of the prompt, its target is empty and
its output message is the SHA-256 of the raw answer. With it, they hold the prompt, the expected
value and the raw answer.

## What report refuses

Report exits 2 with one line on stderr, and prints nothing on stdout, when:

- the Gig has no eval store yet, or the store has no eval run;
- the run `--run` names does not exist, or was not made by `eval run`;
- a run holds two traces for the same item, repeat and attempt;
- a run stores a score that is not a finite number;
- `--export inspect` is asked for without the `inspect` extra, the `inspect` folder in the gig
  folder is a symlink, or a log's file name clashes (see above).

No refusal prints a prompt, an answer or any other item text.

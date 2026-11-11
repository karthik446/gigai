---
title: Labelling eval rows
description: How `gigai <gig> eval label` asks a person met, not met or unknown about each traced row of a step without showing which run or model gave it, and how labels move in and out as JSONL.
---

`gigai <gig> eval label --step S` is a page in the terminal. It shows the traced rows of step `S`
from the Gig's eval store (`evals.sqlite` in the gig folder, `<home>/gigs/<gig>/`) one at a time
and asks whether each output meets the step's task. Each answer is stored as soon as it is given,
in the store's `labels` table. The page calls no model.

```sh
gigai my-gig eval label --step solve
gigai my-gig eval label --step solve --select contested
gigai my-gig eval label --step solve --relabel 0.2
```

## The page

A row is one run's answer to one dataset item. For each row the page prints the input the step
sent and the output it got back (every repeat's last attempt). It never prints which run, arm,
model or target gave the row: no run id, tag, model name or endpoint. Rows from different runs are
shuffled together in a fixed order, so the order gives nothing away either. A label made on this
page is stored as blind.

For each row, answer:

- `m` or `met`: the output does what the step asks for this input;
- `n` or `not met`: it does not;
- `u` or `unknown`: you cannot tell;
- `q`: stop. The labels given so far are kept.

Then type a one-line critique (at most 200 characters; it may be empty). Any other answer asks
again. When input ends, the page stops as if you typed `q`.

Each label records who gave it (`$USER`, else the OS login), when (UTC), the rubric
(`gigai-met-1`, the three answers above) and the critique.

### Resume

Run the page again and it starts at the first row you have not labelled. Rows are tracked per
person: a second person sees every row.

### Pick rows with `--select`

- `random:N` shows the first `N` rows of the page's fixed order. The order is seeded from the Gig
  and the step, so the same store always gives the same `N` rows, and `random:5` is the start of
  `random:10`.
- `below:met` shows the rows the step's scorers did not fully meet. A row meets the bar when every
  scorer's value on it is at least 1.0 (full marks), averaged over its repeats, using each
  answer's latest score. A row with any scorer below 1.0 is below met.
- `contested` shows the rows where the scorers disagree: one scorer meets the bar on a row and
  another does not, or one scorer meets it on an item in one run and not in another. In that last
  case the item's rows in every run are shown.

Rows with no scores are never below met or contested.

### Give labels again with `--relabel`

`--relabel 0.2` shows 20% of the rows you have already labelled (rounded, and at least one), to
check how consistent your labels are. The rows are picked with a fixed seed, so running it again
resumes the same pick. Your earlier answer is not shown. The new label points at the one it
repeats and carries the next round number (2 for a first relabel). The fraction must be above 0
and at most 1.

## Labels as JSONL

```sh
gigai my-gig eval label --step solve --export labels.jsonl
gigai my-gig eval label --step solve --from labels.jsonl
```

`--export F` writes the step's labels to a new file `F` (it refuses an existing one), one JSON
object per line, sorted by run, item, step, person, time and round. `--from F` imports such a file.
Each row has exactly these keys:

| key | holds |
| --- | --- |
| `run_id` | the run whose answer was labelled |
| `item_id` | the dataset item |
| `step_id` | the step |
| `label` | `met`, `not met` or `unknown` |
| `critique` | the one-line critique (may be empty) |
| `rubric` | the rubric applied |
| `who` | who labelled it |
| `at` | when, in UTC (`2026-01-31T09:30:00Z`) |
| `round` | 1, or 2 and up for a relabel (at most 1000) |

A row never carries the input or output text: the store holds those. Every string is one line of
at most 200 characters.

`--from` checks the whole file before it writes anything. It exits 2 with one line on stderr, and
imports nothing, when the file holds no rows, or when any row:

- has a key outside the nine (an `input` key, say; the error names it) or lacks one;
- has a label other than the three answers, a `round` that is not a whole number from 1 to 1000,
  an `at` that is not a UTC time, or a string that is too long or spans lines;
- is of another step than `--step`;
- names a run, step and item the store holds no answer for;
- repeats the run, item, step, person, time and round of another row, or of a stored label, with a
  different label.

Errors name the line, and the key when one key is at fault, never a value from the file. A row
already in the store with the same content is skipped, so importing a file twice changes nothing.
Imported labels are stored as blind.

## What label refuses

`eval label` exits 2 with one line on stderr when:

- no `[[evals.steps]]` entry has the `--step` id;
- the Gig has no eval store yet (run `eval run` first); the page creates none;
- `--select` is not one of the three modes, or `--relabel` is not a fraction above 0 and at most 1;
- `--export` and `--from` are given together, or either is given with `--select` or `--relabel`;
- `--export` names a file that exists, or the step has no labels to export;
- the `--from` file is missing, is not UTF-8, or is refused as above;
- it cannot tell who you are (`$USER` is unset and the OS has no login for you).

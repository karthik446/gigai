# The labels JSONL format

`gigai <gig> eval label --export F` writes it and `gigai <gig> eval label --from F` reads it. It carries the
labels a person gave to a step's traced rows. It never carries the row's input or output text: a label points at
the row by its run, item and step ids, and the gig's `evals.sqlite` holds the text.

One JSON object per line, UTF-8. Blank lines are ignored. Every object has exactly these nine keys:

| key | type | meaning |
| --- | --- | --- |
| `run_id` | string | The `eval run` run whose trace was labelled. |
| `item_id` | string | The dataset item the trace answered. |
| `step_id` | string | The `[[evals.steps]]` entry of the trace. |
| `label` | string | `met`, `not met` or `unknown`. |
| `critique` | string | The labeller's one-line reason; may be empty. |
| `rubric` | string | The rubric the labeller applied (the page's is `gigai-met-1`). |
| `who` | string | The labeller's login (`$USER`, else the OS login). |
| `at` | string | When the labeller answered: UTC, `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`. |
| `round` | integer | `1` for a first label; a relabel is its original's round + 1. At most `1000`. |

`run_id`, `item_id`, `step_id`, `rubric` and `who` are non-empty. Every string is one line of at most 200
characters. `NaN` and `Infinity` are not JSON and a key may not appear twice in one object.

`--export` writes the rows sorted by `(run_id, item_id, step_id, who, at, round)`, keys sorted, one row per line.

## The rules `--from` applies

- A file with no rows is refused (exit 2). One validator (`gigai.evals.label.validate_batch`) checks the whole
  batch before anything is written. Any key outside the nine (an `input` key, say), a missing key, a wrong type or
  a bad value refuses the whole batch (exit 2); the error names the line, and the key when one key is at fault,
  never a value. Nothing is imported.
- Every row must be of the `--step` step and name a run, step and item that has a trace in `evals.sqlite`.
- A label is identified by `(run_id, item_id, step_id, who, at, round)`. A row whose identity is stored already
  with the same `label`, `critique` and `rubric` is skipped, so importing a batch twice changes nothing. The same
  identity with a different label refuses the batch, as do two such rows inside one batch.
- An imported row is stored with `blind = 1`, `source = 'human'` and the batch's `rubric`. A row of round 2 or
  more points `relabel_of` at the same person's label on the same row one round earlier, when it is stored.

## Where it lands

Each row becomes one row of `labels` in `evals.sqlite`: `label` holds the JSON string (`"met"`), `dataset_id`
comes from the run and `item_version` from the trace, and `recorded_at` is when gigai stored it.

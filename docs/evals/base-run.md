# Base run report template

`gigai <gig> eval base --report PATH` writes this layout from the run's `--json` output
(`gigai.evals.base.render_markdown`). Every cell comes from the JSON; the report names rungs,
sets, run ids and numbers only, never item text. Angle brackets mark the filled values.

```markdown
# Base run: <gig>

- Tier: <gate|judge|full>, <ci|release> slices[ (deterministic)]
- Ladder sha256: `<64-hex sha256 of src/gigai/evals/ladder.toml>`
- Calls made: <model calls: calls to every rung but the deterministic one>

## Rungs

| Rung | Adapter | Model | Price in/out ($/M) | Rank (source, date) | Knobs | Status | Calls | Suite |
|---|---|---|---|---|---|---|---|---|
| <name> | <adapter> | <model name> | <in> / <out> | <rank> (<source URL>, <date>) | <knobs, or why none> | <ran, or skipped: reason> | <calls>[ (<n> failed: <error class> <count>, ...)] | <suite points, or n/a> |

## Sets

| Set | K-score | Items | Best | Saturated |
|---|---|---|---|---|
| <set>[ (approximate IFEval: 15 of 25 instruction types; others unscored; IFBench: not available)] | <0-2> | <items> | <best rung's points> | <yes, not gated / no> |

## Runs

| Rung | Set | Run | Items | Scored | Score | Calls |
|---|---|---|---|---|---|---|
| <rung> | <set> | `<run id>` | <items> | <scored items> | <points> | <calls> |

## Gates

| Gate | Measure | Status |
|---|---|---|
| a order | tau-b <tau> (min <min>) | <pass/fail/not_validated>[: reason] |
| b noise | sd <points> points, flip rate <percent>%, n=<items>[, smallest tell-apart difference <points> points] | <pass/fail/measured/not_validated> |
| c broken_prompt | drop <points> points, p <p> | <status> |
| c truncated_context | drop <points> points, p <p> | <status> |
| c wrong_label | drop <points> points, p <p> | <status> |
| d judge | <reason> | not_validated |
| e replay | max abs delta <delta>, <calls> calls | <status> |

[Gate b is measured, not pass/fail: <reason>. Limits sd <points> points and flip rate <percent>%, over: <sd, flip rate, or none>. Smallest tell-apart difference: <formula sentence>.]
```

## Reading it

- **Calls** is the rung's model calls, failed ones included. When some failed, it is followed by
  their count and their error classes, for example `560 (3 failed: OllamaLocalAdapterError 3)`;
  the JSON has the same per rung as `failed_calls` and `failures` (error class to count, `{}`
  when none failed). A failed call is stored as a trace with an empty answer, parse status
  `not_parsed`, outcome `failed` and its error class (and HTTP status, never the error text),
  and it gets no score. In a run row, **Scored** next to **Items** counts the answers that got a
  score; the suite and set scores are over scored items only. A schema-mode call answered with
  non-JSON or with HTTP 400 is not a failed call: it is stored as an empty `not_json` answer and
  scores 0.
- **Suite** is a rung's mean over the gated sets of its per-set score, in points (0-100). A set
  where the best rung scores at or above `order.saturated_at` (0.97) is flagged and left out of
  the suite and the gates.
- **Gate a** passes when Kendall tau-b between the suite scores and the published ranks is at
  least `order.min_tau` (0.6); fewer than `order.min_rungs` (3) measured rungs, or every rung
  tied, is `not_validated`.
- **Gate b** re-asks `noise.items` (100) items on the noise rung (`noise.rung`, else the
  strongest selected rung). Its main answers are repeat 0, so `noise.k` (3) means two more
  passes. The limits are a sample SD of the suite score of at most 2.0 points and at most 10%
  of items that flip. When the noise rung can fix a seed or a temperature, is the deterministic
  rung, or the run is `--deterministic` (the every-commit check, where every rung answers from
  canned data, so repeats cannot differ), the gate passes within both limits and fails
  otherwise; the JSON `gates.b` has `rule` `limit` and `limits`. When it can fix neither (today
  the `claude_cli` and `codex_cli` rungs), its repeats differ by design, so the status is
  `measured`, not pass/fail,
  and `rule` is `measured`. The JSON then has `limits`, `reason`, `over_limits` (which limit the
  numbers exceed, as facts), `min_detectable_difference` and
  `min_detectable_difference_formula`. The smallest tell-apart
  difference is 1.96 x sqrt(2) x SD points (about 2.77 SD): two rungs' suite scores can be told
  apart only if they differ by more than that. It assumes every rung is as noisy as the noise
  rung and the SD comes from only k repeats, so it is rough. Too few scored items stays
  `not_validated`.
- **Gate c** passes a canary when its score falls by at least its `min_drop` (10 / 15 / 30
  points) and the exact McNemar p of the flips is below 0.01. The two calling canaries re-ask
  their `items` (100 each), spread across their sets, on the canary rung (`canaries.rung`, else
  the strongest selected rung).
- **Gate d** (judge agreement with human labels) is not checked by the base run.
- **Gate e** copies one main run's traces into a replay run with no call, scores them again and
  passes when the means differ by less than 0.0005 and the replay made 0 calls. It re-scores the
  same text, so it catches scorer nondeterminism and trace-copy loss, not model variance.
- **IFEval** is scored approximately: 15 of its 25 instruction types are checked, and an item
  with any other type is left unscored.

The per-gig eval report (`gigai <gig> eval report`) has its own layout; this template covers the
base run only.

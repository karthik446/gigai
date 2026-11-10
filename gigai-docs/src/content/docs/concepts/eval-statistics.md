---
title: Eval statistics
description: The statistics GigAI uses to say whether one eval arm really beat another, and how sure it is.
---

An eval score is a measurement, so it comes with noise. `gigai.evals.stats` holds the
statistics GigAI uses to tell a real difference between two arms (a model, a prompt, a Gig
version) from noise. It uses only the Python standard library. Two metrics, Wilson intervals
and Krippendorff's alpha, call code vendored from Inspect AI under
`gigai.evals._vendor.inspect_ai`. Every function returns plain data (dicts of numbers, booleans,
strings and `None`), so results can be written straight to JSON.

## Two rules every function enforces

- **No NaN or infinite scores.** A NaN or infinite score, p-value or parameter raises
  `NonFiniteScoreError`. Drop or fix the item first. A NaN would otherwise pass silently through
  the vendored metrics.
- **At least 10 clusters.** A clustered estimate on fewer than 10 clusters (for example, items
  from fewer than 10 source documents) raises `TooFewClustersError`. With 2 clusters, a
  clustered interval came out narrower than a plain Wilson interval, which is backwards. The
  cut-off of 10 is a judgment call, not a published threshold.

## Is one arm's accuracy uncertain?

- **Wilson interval** (`wilson_interval(successes, n)`): a confidence interval for one accuracy
  that stays sensible near 0 % and 100 %. At 51 of 60 correct (85 %), the 95 % interval is
  about ±9 points. Source: [Brown, Cai & DasGupta 2001](https://doi.org/10.1214/ss/1009213286).
- **Noise bound** (`noise_bound(mean, sd, n)`): upper = mean + 1.96 × sd / √n. A measured
  difference below `upper` is within run-to-run noise. Source:
  [Miller 2024, "Adding Error Bars to Evals"](https://arxiv.org/abs/2411.00640).

## Did arm B beat arm A on the same items?

- **Exact McNemar** (`mcnemar_exact(b, c)`): for pass/fail scores. `b` counts items A got right
  and B got wrong; `c` counts the reverse. p = 2 × P(X ≤ min(b, c)) with X ~ Binomial(b + c, ½).
  b = 3, c = 9 gives p = 0.146, so not significant. The mid-p is reported too, but the verdict
  uses the exact p. Sources: [McNemar 1947](https://doi.org/10.1007/BF02295996),
  [Fagerland, Lydersen & Laake 2013](https://doi.org/10.1186/1471-2288-13-91).
- **Wilcoxon signed-rank** (`wilcoxon_signed_rank(a, b)`): for continuous paired scores. It
  ranks the differences, so a few large swings cannot dominate. Zero differences are dropped.
  The p-value is exact for up to 20 non-zero differences and uses the normal approximation, with tie and
  continuity corrections, above that. Sources:
  [Wilcoxon 1945](https://doi.org/10.2307/3001968), [Demšar 2006](https://jmlr.org/papers/v7/demsar06a.html).
- **Paired cluster bootstrap** (`paired_cluster_bootstrap(a, b, clusters)`): a confidence
  interval for the mean difference B − A when items come in groups that are not independent
  (several questions from one document). It resamples whole clusters, never single items. It
  is seeded (`seed=0` by default), so a rerun gives the same interval. Resamples are capped at
  10,000. Source: [Miller 2024](https://arxiv.org/abs/2411.00640), Appendix A.

## Is B shown to be no worse than A?

- **TOST** (`tost(difference, stderr, margin)`): two one-sided tests that show the difference
  lies inside ±margin. "Not significantly different" does not mean "the same"; TOST is the test
  for "no regression". With difference 0 and margin 0.05, a standard error of 0.01 passes and a
  standard error of 0.05 does not. Sources: [Schuirmann 1987](https://doi.org/10.1007/BF01068419),
  [Lakens 2017](https://pmc.ncbi.nlm.nih.gov/articles/PMC5502906/).

## Many comparisons at once

- **Holm** (`holm(p_values)`): use for the few comparisons you planned in advance. It keeps the
  chance of any false win at alpha. Source: [Holm 1979](https://www.jstor.org/stable/4615733).
- **Benjamini-Hochberg** (`benjamini_hochberg(p_values)`): use for exploratory sweeps. It keeps
  the expected share of false wins among the reported wins at alpha. Source:
  [Benjamini & Hochberg 1995](https://www.jstor.org/stable/2346101).

## Can the judge be trusted?

- **Cohen's kappa** (`cohen_kappa(table)`): agreement between a model judge and a human beyond
  chance, with the Fleiss-Cohen-Everitt standard error and interval. Sources:
  [Cohen 1960](https://doi.org/10.1177/001316446002000104),
  [Fleiss, Cohen & Everitt 1969](https://doi.org/10.1037/h0028106).
- **PABAK** (returned by `cohen_kappa`): 2 × agreement − 1 for two categories. Kappa falls when
  one answer is common even at the same raw agreement. At 90 % agreement, kappa is 0.80 with
  balanced labels and 0.61 with 85 % of labels on one side; PABAK is 0.80 in both. Source:
  [Byrt, Bishop & Carlin 1993](https://doi.org/10.1016/0895-4356(93)90018-V).
- **Krippendorff's alpha** (`krippendorff_alpha(units)`): agreement among several raters when
  some ratings are missing (`None`). Source:
  [Krippendorff 2011](https://repository.upenn.edu/items/034a6030-c584-4d14-9d3d-7b7e8d16df20).
- **AUC with Hanley-McNeil SE** (`auc_hanley_mcneil(positives, negatives)`): how well a score
  ranks good outputs above bad ones, with a standard error. Source:
  [Hanley & McNeil 1982](https://pubmed.ncbi.nlm.nih.gov/7063747/).

## How many items does a suite need?

- **Paired sample size** (`mde_sample_size(discordance, delta)`): the items needed to detect a
  true accuracy gap `delta` at 95 % confidence and 80 % power. `discordance` is the share of
  items the two arms disagree on. The formula is
  n = ⌈(1.96 √p_d + 0.84 √(p_d − δ²))² / δ²⌉. Source:
  [Connor 1987](https://doi.org/10.2307/2531961).

  | discordance | gap 0.05 | gap 0.10 | gap 0.20 |
  |---|---|---|---|
  | 0.10 | 312 | 77 | — |
  | 0.20 | 626 | 155 | 37 |
  | 0.30 | 940 | 234 | 57 |

  A dash means the gap is larger than the discordance, which is impossible: B cannot win more
  items than the arms disagree on. `mde_table` builds the whole table, and
  `minimum_detectable_difference(n, discordance)` answers the reverse question for a suite you
  already have.
- **Continuous MDE** (`mde_continuous(n, sd)`): the smallest mean difference `n` paired items can
  detect, (1.96 + 0.84) × sd / √n. Source: [Miller 2024](https://arxiv.org/abs/2411.00640).

## Should I re-run items or add new ones?

- **Variance components** (`variance_components(scores)`): splits the spread of a score into
  "which items were asked" (between-item) and "re-running the same item" (within-item). Pass
  `cost_ratio` = how much one new item costs compared with re-running one (10 means a new item
  costs as much as ten re-runs), and it returns how many repeats per item are worth paying for:
  √(cost_ratio × within-item / between-item). With within-item 0.0605, between-item 0.0906 and
  cost_ratio 10, that is about 2.6, so 3 repeats. Sources: [Miller 2024](https://arxiv.org/abs/2411.00640),
  [Bouthillier et al. 2021](https://arxiv.org/abs/2103.03098).

"""Paired tests, corrections, agreement and power for gigai evals (stdlib only).

Written by gigai (PLAN V3 part b). Wilson intervals and Krippendorff's alpha are not
re-implemented here: `wilson_interval` and `krippendorff_alpha` call the numpy-free lift of
inspect_ai in `gigai.evals._vendor.inspect_ai` (see its DIFF.md). Every public function returns
plain data (dicts of floats, ints, bools, strings or None, lists of those) so callers can write
JSON directly.

Non-finite input is refused: the vendored metrics give silently wrong answers on NaN or inf
(DIFF.md, "Behaviour differences from upstream"), so every function here raises
`NonFiniteScoreError` on a NaN or infinite value before computing anything.
"""

from __future__ import annotations

import math
import random
from collections.abc import Hashable, Sequence
from statistics import NormalDist, pstdev

from gigai.evals._vendor.inspect_ai._metric import SampleScore, Score
from gigai.evals._vendor.inspect_ai.krippendorff import krippendorff_alpha as _vendored_krippendorff_alpha
from gigai.evals._vendor.inspect_ai.std import _quantile
from gigai.evals._vendor.inspect_ai.std import ci_wilson as _vendored_ci_wilson

__all__ = [
    "MAX_BOOTSTRAP_RESAMPLES",
    "MIN_CLUSTERS",
    "WILCOXON_EXACT_MAX_N",
    "NonFiniteScoreError",
    "TooFewClustersError",
    "auc_hanley_mcneil",
    "benjamini_hochberg",
    "cohen_kappa",
    "holm",
    "krippendorff_alpha",
    "mcnemar_exact",
    "mde_continuous",
    "mde_sample_size",
    "mde_table",
    "minimum_detectable_difference",
    "noise_bound",
    "paired_cluster_bootstrap",
    "require_min_clusters",
    "tost",
    "variance_components",
    "wilcoxon_signed_rank",
    "wilson_interval",
]

MIN_CLUSTERS = 10
"""Fewest clusters a clustered estimate accepts (SPIKE §6 / D §3.4; a carried guess, `[INFERENCE]`)."""

MAX_BOOTSTRAP_RESAMPLES = 10_000
"""Upper bound on bootstrap resamples (PLAN V3 risk 2: pure-Python speed)."""

WILCOXON_EXACT_MAX_N = 20
"""Wilcoxon uses the exact null up to this many non-zero differences, the normal approximation above."""

_NORMAL = NormalDist()


class NonFiniteScoreError(ValueError):
    """A score, p-value or summary statistic was NaN or infinite; drop or reject it before calling."""


class TooFewClustersError(ValueError):
    """A clustered estimate was asked for with fewer than `MIN_CLUSTERS` clusters."""


def _finite(values: Sequence[float], name: str) -> list[float]:
    out: list[float] = []
    for index, value in enumerate(values):
        number = float(value)
        if not math.isfinite(number):
            raise NonFiniteScoreError(f"{name}[{index}] is {number!r}; drop or reject non-finite values first")
        out.append(number)
    return out


def _finite_scalar(value: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise NonFiniteScoreError(f"{name} is {number!r}; drop or reject non-finite values first")
    return number


def _two_sided_z(level: float) -> float:
    level = _finite_scalar(level, "level")
    if not 0.0 < level < 1.0:
        raise ValueError(f"level must be in (0, 1), got {level}")
    return -_NORMAL.inv_cdf((1.0 - level) / 2.0)


def _probability(value: float, name: str, *, open_interval: bool = True) -> float:
    number = _finite_scalar(value, name)
    inside = 0.0 < number < 1.0 if open_interval else 0.0 <= number <= 1.0
    if not inside:
        bounds = "(0, 1)" if open_interval else "[0, 1]"
        raise ValueError(f"{name} must be in {bounds}, got {number}")
    return number


def _paired(a: Sequence[float], b: Sequence[float]) -> list[float]:
    left, right = _finite(a, "a"), _finite(b, "b")
    if len(left) != len(right):
        raise ValueError(f"a and b must pair item for item, got {len(left)} and {len(right)} values")
    return [y - x for x, y in zip(left, right)]


def require_min_clusters(n_clusters: int) -> None:
    """Refuse a clustered estimate on fewer than `MIN_CLUSTERS` clusters, where its CI is too narrow to trust.

    With 2 clusters the clustered interval came out narrower than Wilson (B-landscape H1, SPIKE §6);
    Miller 2024, "Adding Error Bars to Evals", https://arxiv.org/abs/2411.00640 (fetched 2026-11-09),
    Appendix A. The threshold 10 is `[INFERENCE]`. Raises `TooFewClustersError` below the threshold,
    `NonFiniteScoreError` on NaN or inf, and `TypeError` on anything that is not a whole number.
    """

    if isinstance(n_clusters, float):
        _finite_scalar(n_clusters, "n_clusters")
        if not n_clusters.is_integer():
            raise TypeError(f"n_clusters must be a whole number, got {n_clusters!r}")
    elif isinstance(n_clusters, bool) or not isinstance(n_clusters, int):
        raise TypeError(f"n_clusters must be an integer, got {n_clusters!r}")
    if n_clusters < MIN_CLUSTERS:
        raise TooFewClustersError(f"{n_clusters} clusters; clustered estimates need at least {MIN_CLUSTERS}")


def wilson_interval(successes: int, n: int, level: float = 0.95) -> dict:
    """How uncertain one arm's accuracy is, with bounds that stay sensible near 0 % and 100 %.

    Calls the vendored `ci_wilson` (Wilson 1927; Brown, Cai & DasGupta 2001,
    https://doi.org/10.1214/ss/1009213286, fetched 2026-11-09). `half_width` is half the interval length.
    """

    if isinstance(successes, bool) or isinstance(n, bool) or not isinstance(successes, int) or not isinstance(n, int):
        raise TypeError("successes and n must be integers")
    if n < 1 or not 0 <= successes <= n:
        raise ValueError(f"need n >= 1 and 0 <= successes <= n, got successes={successes}, n={n}")
    _two_sided_z(level)
    scores = [SampleScore(score=Score(value=1.0 if i < successes else 0.0), sample_id=i) for i in range(n)]
    interval = _vendored_ci_wilson(level=level)(scores)
    lower, upper = float(interval["lower"]), float(interval["upper"])
    return {
        "successes": successes,
        "n": n,
        "proportion": successes / n,
        "lower": lower,
        "upper": upper,
        "half_width": (upper - lower) / 2.0,
        "level": level,
    }


def krippendorff_alpha(units: Sequence[Sequence[object]], level: str = "nominal") -> dict:
    """How far several raters agree beyond chance, allowing missing ratings (None) per unit.

    Calls the vendored `krippendorff_alpha` (Krippendorff 2011, "Computing Krippendorff's Alpha-Reliability",
    https://repository.upenn.edu/items/034a6030-c584-4d14-9d3d-7b7e8d16df20, fetched 2026-11-09). Units with
    fewer than two non-missing ratings do not count; `alpha` is None when no unit counts.
    """

    rated: list[list[object]] = []
    for unit_index, unit in enumerate(units):
        ratings = [rating for rating in unit if rating is not None]
        for rating in ratings:
            if isinstance(rating, float) and not math.isfinite(rating):
                raise NonFiniteScoreError(f"units[{unit_index}] holds {rating!r}; drop or reject non-finite ratings first")
        if len(ratings) >= 2:
            rated.append(ratings)
    if not rated:
        return {"alpha": None, "units": 0, "level": level}
    scores = [SampleScore(score=Score(value=ratings), sample_id=i) for i, ratings in enumerate(rated)]
    alpha = float(_vendored_krippendorff_alpha(level=level)(scores))  # type: ignore[arg-type]
    return {"alpha": alpha, "units": len(rated), "level": level}


def mcnemar_exact(b: int, c: int, alpha: float = 0.05) -> dict:
    """Whether the items that flipped between two arms flipped one way more often than chance.

    `b` = pairs where arm A was right and arm B wrong, `c` = A wrong and B right. Exact two-sided
    p = min(1, 2 * P(X <= min(b, c))) with X ~ Binomial(b + c, 1/2) (McNemar 1947,
    https://doi.org/10.1007/BF02295996); mid-p = min(1, 2 * (P(X <= min) - P(X = min) / 2))
    (Fagerland, Lydersen & Laake 2013, https://doi.org/10.1186/1471-2288-13-91; both fetched
    2026-11-09). `significant` gates on the exact p only; the mid-p is reported, not gated on.
    """

    for name, value in (("b", b), ("c", c)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer, got {value!r}")
    alpha = _probability(alpha, "alpha")
    discordant = b + c
    smaller = min(b, c)
    if discordant == 0:
        p_exact = p_mid = 1.0
    else:
        total = 2**discordant
        tail = sum(math.comb(discordant, i) for i in range(smaller + 1))
        at_smaller = math.comb(discordant, smaller)
        p_exact = min(1.0, 2 * tail / total)
        p_mid = min(1.0, (2 * tail - at_smaller) / total)
    return {
        "b": b,
        "c": c,
        "discordant": discordant,
        "p_exact": p_exact,
        "p_mid": p_mid,
        "alpha": alpha,
        "significant": p_exact <= alpha,
    }


def paired_cluster_bootstrap(
    a: Sequence[float],
    b: Sequence[float],
    clusters: Sequence[Hashable],
    *,
    resamples: int = 2000,
    seed: int = 0,
    level: float = 0.95,
) -> dict:
    """How uncertain the mean difference B - A is when items come in clusters (documents, postings).

    Resamples whole clusters with replacement, never single items, and recomputes the item-weighted
    mean of d = b - a; the interval is the percentile interval, `stderr` the population SD of the
    resampled means. Seeded `random.Random(seed)` (`seed` must be an int; NaN or inf raises
    `NonFiniteScoreError`, other non-ints `TypeError`); `resamples` capped at `MAX_BOOTSTRAP_RESAMPLES`;
    refuses fewer than `MIN_CLUSTERS` clusters. Efron & Tibshirani 1993 (bootstrap); cluster
    resampling: Miller 2024, https://arxiv.org/abs/2411.00640; resampling methods: Ernst 2004,
    https://doi.org/10.1214/088342304000000396 (both fetched 2026-11-09). Ernst 2004 is about
    permutation tests; using it for the bootstrap rests on the same resample-the-exchangeable-units
    logic `[INFERENCE]`.
    """

    differences = _paired(a, b)
    if len(clusters) != len(differences):
        raise ValueError(f"clusters must label every item, got {len(clusters)} labels for {len(differences)} items")
    if isinstance(seed, float):
        _finite_scalar(seed, "seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError(f"seed must be an integer, got {seed!r}")
    if isinstance(resamples, bool) or not isinstance(resamples, int) or not 1 <= resamples <= MAX_BOOTSTRAP_RESAMPLES:
        raise ValueError(f"resamples must be an integer in [1, {MAX_BOOTSTRAP_RESAMPLES}], got {resamples!r}")
    tail = (1.0 - _probability(level, "level")) / 2.0

    index: dict[Hashable, int] = {}
    sums: list[float] = []
    sizes: list[int] = []
    for label, difference in zip(clusters, differences):
        slot = index.setdefault(label, len(sums))
        if slot == len(sums):
            sums.append(0.0)
            sizes.append(0)
        sums[slot] += difference
        sizes[slot] += 1
    require_min_clusters(len(sums))

    rng = random.Random(seed)
    cluster_ids = range(len(sums))
    means: list[float] = []
    for _ in range(resamples):
        picks = rng.choices(cluster_ids, k=len(sums))
        means.append(sum(sums[i] for i in picks) / sum(sizes[i] for i in picks))
    return {
        "difference": math.fsum(differences) / len(differences),
        "lower": _quantile(means, tail),
        "upper": _quantile(means, 1.0 - tail),
        "stderr": pstdev(means),
        "level": level,
        "resamples": resamples,
        "seed": seed,
        "items": len(differences),
        "clusters": len(sums),
    }


def wilcoxon_signed_rank(a: Sequence[float], b: Sequence[float]) -> dict:
    """Whether paired continuous scores moved one way, using ranks so a few large swings cannot dominate.

    d = b - a; zero differences are dropped (Wilcoxon's rule) and tied |d| share their mean rank.
    Rule: with n <= `WILCOXON_EXACT_MAX_N` non-zero differences the p-value is exact (all 2^n sign
    assignments of the observed ranks, ties included); above that it uses the normal approximation
    with mean n(n+1)/4, variance n(n+1)(2n+1)/24 - sum(t^3 - t)/48 over tie groups of size t, and a
    0.5 continuity correction. Two-sided. Wilcoxon 1945, https://doi.org/10.2307/3001968; Demšar
    2006, https://jmlr.org/papers/v7/demsar06a.html (both fetched 2026-11-09).
    """

    differences = _paired(a, b)
    nonzero = [d for d in differences if d != 0.0]
    n = len(nonzero)
    zeros = len(differences) - n
    if n == 0:
        return {"n": 0, "zeros": zeros, "w_plus": 0.0, "w_minus": 0.0, "statistic": 0.0, "p_value": 1.0, "method": "exact", "z": None}

    order = sorted(range(n), key=lambda i: abs(nonzero[i]))
    doubled = [0] * n  # 2 x mid-rank: always an integer
    tie_sizes: list[int] = []
    start = 0
    while start < n:
        end = start
        while end + 1 < n and abs(nonzero[order[end + 1]]) == abs(nonzero[order[start]]):
            end += 1
        for position in range(start, end + 1):
            doubled[order[position]] = (start + 1) + (end + 1)
        tie_sizes.append(end - start + 1)
        start = end + 1

    plus2 = sum(r for r, d in zip(doubled, nonzero) if d > 0)
    total2 = n * (n + 1)
    w_plus, w_minus = plus2 / 2.0, (total2 - plus2) / 2.0

    if n <= WILCOXON_EXACT_MAX_N:
        counts = [0] * (total2 + 1)
        counts[0] = 1
        for rank2 in doubled:
            for s in range(total2, rank2 - 1, -1):
                counts[s] += counts[s - rank2]
        low = min(plus2, total2 - plus2)
        p_value = min(1.0, 2 * sum(counts[: low + 1]) / 2**n)
        method, z = "exact", None
    else:
        mean = n * (n + 1) / 4.0
        variance = n * (n + 1) * (2 * n + 1) / 24.0 - sum(t**3 - t for t in tie_sizes) / 48.0
        gap = w_plus - mean
        z = math.copysign(max(abs(gap) - 0.5, 0.0), gap) / math.sqrt(variance)
        p_value = min(1.0, 2.0 * _NORMAL.cdf(-abs(z)))
        method = "normal"
    return {
        "n": n,
        "zeros": zeros,
        "w_plus": w_plus,
        "w_minus": w_minus,
        "statistic": min(w_plus, w_minus),
        "p_value": p_value,
        "method": method,
        "z": z,
    }


def _checked_p_values(p_values: Sequence[float], alpha: float) -> tuple[list[float], float]:
    values = _finite(p_values, "p_values")
    for index, value in enumerate(values):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"p_values[{index}] = {value} is not a probability")
    return values, _probability(alpha, "alpha")


def holm(p_values: Sequence[float], alpha: float = 0.05) -> dict:
    """Which of several confirmatory tests still count as wins once the number of tests is paid for.

    Holm step-down (controls the family-wise error rate under any dependence): sort p ascending,
    reject p_(j) while p_(j) <= alpha / (m - j + 1), stop at the first failure. Adjusted p_(j) =
    max over i <= j of min(1, (m - i + 1) p_(i)). Results are in input order. Holm 1979,
    https://www.jstor.org/stable/4615733 (fetched 2026-11-09).
    """

    values, alpha = _checked_p_values(p_values, alpha)
    m = len(values)
    order = sorted(range(m), key=lambda i: values[i])
    reject = [False] * m
    adjusted = [0.0] * m
    running = 0.0
    stopped = False
    for j, i in enumerate(order):
        running = max(running, min(1.0, (m - j) * values[i]))
        adjusted[i] = running
        if not stopped and values[i] <= alpha / (m - j):
            reject[i] = True
        else:
            stopped = True
    return {"reject": reject, "adjusted": adjusted, "alpha": alpha}


def benjamini_hochberg(p_values: Sequence[float], alpha: float = 0.05) -> dict:
    """Which exploratory results to follow up while keeping the expected share of false wins at alpha.

    Benjamini-Hochberg step-up (controls the false discovery rate for independent or positively
    dependent tests): reject every p_(j) with j <= max{j : p_(j) <= j alpha / m}. Adjusted p_(j) =
    min over i >= j of min(1, m p_(i) / i). Results are in input order. Benjamini & Hochberg 1995,
    https://www.jstor.org/stable/2346101 (fetched 2026-11-09).
    """

    values, alpha = _checked_p_values(p_values, alpha)
    m = len(values)
    order = sorted(range(m), key=lambda i: values[i])
    cutoff = 0
    for j, i in enumerate(order, start=1):
        if values[i] <= j * alpha / m:
            cutoff = j
    reject = [False] * m
    adjusted = [0.0] * m
    running = 1.0
    for j in range(m, 0, -1):
        i = order[j - 1]
        running = min(running, m * values[i] / j)
        adjusted[i] = running
        reject[i] = j <= cutoff
    return {"reject": reject, "adjusted": adjusted, "alpha": alpha}


def tost(difference: float, stderr: float, margin: float, alpha: float = 0.05) -> dict:
    """Whether two arms are shown to differ by less than `margin` ("no regression"), not merely "not shown different".

    Two one-sided z tests: p_lower = P(Z >= (d + margin) / se), p_upper = P(Z <= (d - margin) / se);
    equivalent when max(p_lower, p_upper) <= alpha, i.e. the (1 - 2 alpha) interval d -/+ z se lies
    inside (-margin, margin). Normal theory: pass a paired-difference SE from a large enough sample.
    Schuirmann 1987, https://doi.org/10.1007/BF01068419; Lakens 2017,
    https://pmc.ncbi.nlm.nih.gov/articles/PMC5502906/ (both fetched 2026-11-09).
    """

    difference = _finite_scalar(difference, "difference")
    stderr = _finite_scalar(stderr, "stderr")
    margin = _finite_scalar(margin, "margin")
    alpha = _probability(alpha, "alpha")
    if stderr <= 0.0 or margin <= 0.0:
        raise ValueError(f"stderr and margin must be positive, got stderr={stderr}, margin={margin}")
    if alpha >= 0.5:
        raise ValueError(f"alpha must be below 0.5, got {alpha}")
    p_lower = _NORMAL.cdf(-(difference + margin) / stderr)
    p_upper = _NORMAL.cdf((difference - margin) / stderr)
    critical = _NORMAL.inv_cdf(1.0 - alpha)
    p_value = max(p_lower, p_upper)
    return {
        "difference": difference,
        "stderr": stderr,
        "margin": margin,
        "alpha": alpha,
        "p_lower": p_lower,
        "p_upper": p_upper,
        "p_value": p_value,
        "lower": difference - critical * stderr,
        "upper": difference + critical * stderr,
        "equivalent": p_value <= alpha,
    }


def cohen_kappa(table: Sequence[Sequence[int]], level: float = 0.95) -> dict:
    """How far a judge agrees with a human beyond chance, with an interval and the prevalence-free PABAK.

    `table[i][j]` counts items rater A put in category i and rater B in category j (k x k, k >= 2).
    kappa = (p_o - p_e) / (1 - p_e) with p_e = sum_i r_i c_i (Cohen 1960,
    https://doi.org/10.1177/001316446002000104). `se` is the Fleiss-Cohen-Everitt large-sample SE
    for kappa != 0 (Fleiss, Cohen & Everitt 1969, https://doi.org/10.1037/h0028106):
    var = [sum_i p_ii (1 - (r_i + c_i)(1 - kappa))^2 + (1 - kappa)^2 sum_{i != j} p_ij (c_i + r_j)^2
    - (kappa - p_e (1 - kappa))^2] / (n (1 - p_e)^2); the interval is kappa -/+ z se clipped to [-1, 1].
    `se_null` is their SE under kappa = 0: [p_e + p_e^2 - sum_i r_i c_i (r_i + c_i)] / (n (1 - p_e)^2).
    PABAK = 2 p_o - 1 for 2 x 2 (Byrt, Bishop & Carlin 1993, https://doi.org/10.1016/0895-4356(93)90018-V,
    fetched 2026-11-09); for k categories (k p_o - 1) / (k - 1) `[INFERENCE]` (the same idea: chance = 1/k).
    """

    k = len(table)
    if k < 2 or any(len(row) != k for row in table):
        raise ValueError("table must be square with at least 2 categories")
    for row in table:
        for count in row:
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError(f"table cells must be non-negative integers, got {count!r}")
    n = sum(sum(row) for row in table)
    if n == 0:
        raise ValueError("table is empty")
    z = _two_sided_z(level)
    p = [[count / n for count in row] for row in table]
    rows = [sum(p[i]) for i in range(k)]
    cols = [sum(p[i][j] for i in range(k)) for j in range(k)]
    p_o = sum(p[i][i] for i in range(k))
    p_e = sum(rows[i] * cols[i] for i in range(k))
    if p_e >= 1.0:
        raise ValueError("chance agreement is 1 (both raters used one category); kappa is undefined")
    kappa = (p_o - p_e) / (1.0 - p_e)
    spread = 1.0 - kappa
    diagonal = sum(p[i][i] * (1.0 - (rows[i] + cols[i]) * spread) ** 2 for i in range(k))
    off = spread**2 * sum(p[i][j] * (cols[i] + rows[j]) ** 2 for i in range(k) for j in range(k) if i != j)
    variance = (diagonal + off - (kappa - p_e * spread) ** 2) / (n * (1.0 - p_e) ** 2)
    se = math.sqrt(max(variance, 0.0))
    null_variance = (p_e + p_e**2 - sum(rows[i] * cols[i] * (rows[i] + cols[i]) for i in range(k))) / (n * (1.0 - p_e) ** 2)
    return {
        "kappa": kappa,
        "se": se,
        "lower": max(kappa - z * se, -1.0),
        "upper": min(kappa + z * se, 1.0),
        "se_null": math.sqrt(max(null_variance, 0.0)),
        "observed_agreement": p_o,
        "chance_agreement": p_e,
        "pabak": (k * p_o - 1.0) / (k - 1),
        "n": n,
        "level": level,
    }


def auc_hanley_mcneil(positive_scores: Sequence[float], negative_scores: Sequence[float], level: float = 0.95) -> dict:
    """How well a score ranks positives above negatives (AUC), with the Hanley-McNeil standard error.

    AUC = share of (positive, negative) pairs ranked right, ties counting 1/2 (Mann-Whitney).
    SE^2 = [A(1 - A) + (n1 - 1)(Q1 - A^2) + (n0 - 1)(Q2 - A^2)] / (n1 n0), Q1 = A / (2 - A),
    Q2 = 2 A^2 / (1 + A); interval A -/+ z SE clipped to [0, 1]. Hanley & McNeil 1982,
    https://pubmed.ncbi.nlm.nih.gov/7063747/ (fetched 2026-11-09).
    """

    positives = _finite(positive_scores, "positive_scores")
    negatives = _finite(negative_scores, "negative_scores")
    if not positives or not negatives:
        raise ValueError("need at least one positive and one negative score")
    z = _two_sided_z(level)
    n1, n0 = len(positives), len(negatives)
    wins = sum(1.0 if s > t else 0.5 if s == t else 0.0 for s in positives for t in negatives)
    auc = wins / (n1 * n0)
    q1 = auc / (2.0 - auc)
    q2 = 2.0 * auc * auc / (1.0 + auc)
    variance = (auc * (1.0 - auc) + (n1 - 1) * (q1 - auc * auc) + (n0 - 1) * (q2 - auc * auc)) / (n1 * n0)
    se = math.sqrt(max(variance, 0.0))
    return {
        "auc": auc,
        "se": se,
        "lower": max(auc - z * se, 0.0),
        "upper": min(auc + z * se, 1.0),
        "n_positive": n1,
        "n_negative": n0,
        "level": level,
    }


def _power_z(alpha: float, power: float) -> tuple[float, float]:
    alpha = _probability(alpha, "alpha")
    power = _probability(power, "power")
    return _NORMAL.inv_cdf(1.0 - alpha / 2.0), _NORMAL.inv_cdf(power)


def _connor_n(discordance: float, delta: float, z_alpha: float, z_beta: float) -> float:
    return (z_alpha * math.sqrt(discordance) + z_beta * math.sqrt(discordance - delta * delta)) ** 2 / (delta * delta)


def mde_sample_size(discordance: float, delta: float, alpha: float = 0.05, power: float = 0.80) -> int:
    """How many paired items a run needs to detect a true accuracy gap `delta` at the given discordance.

    Connor 1987 paired-proportion sample size, n = ceil((z_{alpha/2} sqrt(p_d) + z_beta
    sqrt(p_d - delta^2))^2 / delta^2), two-sided; needs 0 < delta <= p_d (Connor 1987,
    https://doi.org/10.2307/2531961, fetched 2026-11-09; D §3.8a).
    """

    discordance = _probability(discordance, "discordance", open_interval=False)
    delta = _finite_scalar(delta, "delta")
    if not 0.0 < delta <= discordance:
        raise ValueError(f"need 0 < delta <= discordance, got delta={delta}, discordance={discordance}")
    z_alpha, z_beta = _power_z(alpha, power)
    return math.ceil(_connor_n(discordance, delta, z_alpha, z_beta))


def minimum_detectable_difference(n: int, discordance: float, alpha: float = 0.05, power: float = 0.80) -> dict:
    """The smallest true accuracy gap a paired run of `n` items can detect at the given discordance.

    Inverts Connor 1987 (as `mde_sample_size`, without the ceiling) by bisection over
    0 < delta <= p_d; `mde` is None when even delta = p_d needs more than `n` items
    (https://doi.org/10.2307/2531961, fetched 2026-11-09; D §3.8).
    """

    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"n must be a positive integer, got {n!r}")
    discordance = _probability(discordance, "discordance", open_interval=False)
    if discordance == 0.0:
        raise ValueError("discordance must be positive")
    z_alpha, z_beta = _power_z(alpha, power)
    result = {"n": n, "discordance": discordance, "alpha": alpha, "power": power, "mde": None}
    if _connor_n(discordance, discordance, z_alpha, z_beta) > n:
        return result
    low, high = 0.0, discordance  # needed n(low) > n >= needed n(high); n(delta) falls as delta grows
    for _ in range(200):
        middle = (low + high) / 2.0
        if middle in (low, high):
            break
        if _connor_n(discordance, middle, z_alpha, z_beta) > n:
            low = middle
        else:
            high = middle
    result["mde"] = high
    return result


def mde_continuous(n: int, sd: float, alpha: float = 0.05, power: float = 0.80) -> dict:
    """The smallest true mean difference a paired run of `n` items can detect, given the SD of the differences.

    MDE = (z_{alpha/2} + z_beta) sd / sqrt(n), two-sided (D §3.8; Miller 2024 §5,
    https://arxiv.org/abs/2411.00640, fetched 2026-11-09).
    """

    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"n must be a positive integer, got {n!r}")
    sd = _finite_scalar(sd, "sd")
    if sd < 0.0:
        raise ValueError(f"sd must be non-negative, got {sd}")
    z_alpha, z_beta = _power_z(alpha, power)
    return {"n": n, "sd": sd, "alpha": alpha, "power": power, "mde": (z_alpha + z_beta) * sd / math.sqrt(n)}


def mde_table(
    discordances: Sequence[float], deltas: Sequence[float], alpha: float = 0.05, power: float = 0.80
) -> list[dict]:
    """The items needed for every (discordance, gap) pair, so a suite's size can be checked before running.

    One row per pair from `mde_sample_size`; `n` is None where delta > p_d (B cannot win more pairs
    than are discordant). `alpha`, `power` and every value are checked up front, even when a list is
    empty (empty input gives `[]`): discordances must lie in [0, 1], deltas must be positive.
    Connor 1987, https://doi.org/10.2307/2531961 (fetched 2026-11-09); D §3.8a.
    """

    _power_z(alpha, power)
    checked_discordances = [
        _probability(value, f"discordances[{index}]", open_interval=False)
        for index, value in enumerate(_finite(discordances, "discordances"))
    ]
    checked_deltas = _finite(deltas, "deltas")
    for index, delta in enumerate(checked_deltas):
        if delta <= 0.0:
            raise ValueError(f"deltas[{index}] must be positive, got {delta}")
    rows: list[dict] = []
    for discordance in checked_discordances:
        for delta in checked_deltas:
            n = mde_sample_size(discordance, delta, alpha, power) if delta <= discordance else None
            rows.append({"discordance": discordance, "delta": delta, "n": n})
    return rows


def variance_components(scores: Sequence[Sequence[float]], cost_ratio: float | None = None) -> dict:
    """How much of a score's spread comes from which items were asked versus from re-running the same item.

    `scores[i][r]` = item i, repeat r (every item the same k >= 2 repeats, n >= 2 items). One-way
    random effects: within_item = sum_i sum_r (y_ir - mean_i)^2 / (n (k - 1)); between_item =
    max(0, var_i(mean_i) - within_item / k) (sample variance over items; truncated at 0);
    var_mean = (between_item + within_item / k) / n. With `cost_ratio` c = the cost of one NEW item in
    units of one repeat (c = 10: a new item costs as much as ten re-runs), optimal_repeats =
    sqrt(c within_item / between_item), the repeats per item that minimise var_mean for a fixed
    budget; None when between_item = 0 (D-measurement §3.5: c ≈ 10 there gives k* ≈ 2.6).
    Miller 2024, https://arxiv.org/abs/2411.00640; Bouthillier et al. 2021,
    https://arxiv.org/abs/2103.03098 (both fetched 2026-11-09).
    """

    matrix = [_finite(row, f"scores[{i}]") for i, row in enumerate(scores)]
    n = len(matrix)
    if n < 2:
        raise ValueError("need at least 2 items")
    k = len(matrix[0])
    if k < 2 or any(len(row) != k for row in matrix):
        raise ValueError("every item needs the same number (>= 2) of repeats")
    if cost_ratio is not None:
        cost_ratio = _finite_scalar(cost_ratio, "cost_ratio")
        if cost_ratio <= 0.0:
            raise ValueError(f"cost_ratio must be positive, got {cost_ratio}")
    item_means = [math.fsum(row) / k for row in matrix]
    grand = math.fsum(item_means) / n
    within = math.fsum((y - mean) ** 2 for row, mean in zip(matrix, item_means) for y in row) / (n * (k - 1))
    between_raw = math.fsum((mean - grand) ** 2 for mean in item_means) / (n - 1) - within / k
    between = max(between_raw, 0.0)
    var_mean = (between + within / k) / n
    optimal = math.sqrt(cost_ratio * within / between) if cost_ratio is not None and between > 0.0 else None
    return {
        "items": n,
        "repeats": k,
        "mean": grand,
        "within_item": within,
        "between_item": between,
        "var_mean": var_mean,
        "stderr_mean": math.sqrt(var_mean),
        "optimal_repeats": optimal,
    }


def noise_bound(mean: float, sd: float, n: int, level: float = 0.95) -> dict:
    """How large a mean difference must be before it stands out from run-to-run noise.

    upper = mean + z sd / sqrt(n) (z = 1.96 at 95 %); a measured difference below `upper` is within
    noise. F-read `:93` (0.167 + 1.96 x 0.595 / sqrt(48) = 0.335); D §3.5, Miller 2024,
    https://arxiv.org/abs/2411.00640 (fetched 2026-11-09).
    """

    mean = _finite_scalar(mean, "mean")
    sd = _finite_scalar(sd, "sd")
    if sd < 0.0:
        raise ValueError(f"sd must be non-negative, got {sd}")
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"n must be a positive integer, got {n!r}")
    half_width = _two_sided_z(level) * sd / math.sqrt(n)
    return {"mean": mean, "sd": sd, "n": n, "half_width": half_width, "lower": mean - half_width, "upper": mean + half_width, "level": level}

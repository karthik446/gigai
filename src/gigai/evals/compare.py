"""``gigai <gig> eval compare A B``: two stored runs of one step, item by item, with the statistics to read them.

Runs are paired by ``item_id``. An item's value is the mean of its repeats (the last attempt of each repeat, the
latest score row of each scorer): repeats are averaged per item, not paired by repeat index, so both runs must
repeat every item the same number of times. Items are clustered by the trace's ``cluster_id`` (an item without one
is its own cluster). Per metric the comparison prints the means, the difference B - A with a paired cluster
bootstrap interval, a test (exact McNemar when every item value is 0 or 1, Wilcoxon signed-rank otherwise), the
minimum detectable difference at the run's n, and, when the runs repeated their items (k >= 2), the noise bound of
the paired differences. Every metric a run pre-registered (``runs.prereg``: primary and guard) that both runs scored
is corrected together, Holm by default, Benjamini-Hochberg with ``--correction bh``, whatever ``--metric`` prints;
other metrics keep their raw p.

The runs must measure the same thing or nothing is printed (exit 2): the same step, the same dataset bytes, the
same scorer version per measured metric, the same items with the same clusters and the same repeats, one trace per
(item, repeat, attempt), and at least ``stats.MIN_CLUSTERS`` clusters. Every statistic comes from
``gigai.evals.stats``. Nothing from the dataset rows or the model outputs is printed.
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import click

from ..sdk.context import GigContext
from . import records, stats
from .run import RunRefused, _entry, _run_row, guard, load_suite

#: The bootstrap's seed: the same two runs always print the same interval.
BOOTSTRAP_SEED = 0
CORRECTIONS: dict[str, Callable[[Sequence[float], float], dict]] = {"holm": stats.holm, "bh": stats.benjamini_hochberg}


@dataclass(frozen=True)
class Pairing:
    """Two runs that passed every fingerprint check, their item values per scorer and the items' clusters."""

    a: Mapping[str, Any]
    b: Mapping[str, Any]
    #: scorer id -> item id -> the repeat values, for run A and run B: the shown metrics and the family.
    values_a: Mapping[str, Mapping[str, list[float]]]
    values_b: Mapping[str, Mapping[str, list[float]]]
    clusters: Mapping[str, str]
    #: Every pre-registered metric both runs scored (the correction's family, measured even when not shown).
    family: tuple[str, ...]
    #: The metrics to report (``--metric``, else every metric both runs scored).
    shown: tuple[str, ...]


@click.command("compare")
@click.argument("run_a", metavar="A")
@click.argument("run_b", metavar="B")
@click.option("--metric", "metrics", multiple=True, help="A scorer id or metric name to compare (repeatable; default: all).")
@click.option("--slice", "slice_name", help="Only the items whose dataset row has this slice (or tag).")
@click.option(
    "--correction",
    type=click.Choice(sorted(CORRECTIONS)),
    default="holm",
    show_default=True,
    help="How the pre-registered metrics' p-values are corrected together.",
)
@click.option("--json", "as_json", is_flag=True, help="Print the comparison as JSON.")
@click.pass_context
def compare_command(
    ctx: click.Context, run_a: str, run_b: str, metrics: tuple[str, ...], slice_name: str | None, correction: str, as_json: bool
) -> None:
    """Compare two runs of one step item by item: difference, interval, test and MDE."""

    gig: GigContext = ctx.obj

    def action() -> list[str]:
        result = compare(gig, run_a, run_b, metrics, slice_name=slice_name, correction=correction)
        return [json.dumps(result, indent=2, sort_keys=True)] if as_json else report(result)

    for line in guard(gig, action):
        click.echo(line)


def compare(
    gig: GigContext,
    run_a: str,
    run_b: str,
    metrics: Sequence[str] = (),
    *,
    slice_name: str | None = None,
    correction: str = "holm",
) -> dict[str, Any]:
    """The comparison of run ``run_b`` against ``run_a`` (B - A), or ``RunRefused`` when they cannot be compared."""

    connection = open_store(gig)
    try:
        pairing = pair(connection, run_a, run_b, metrics)
    finally:
        connection.close()
    items: Collection[str] = pairing.clusters.keys()
    if slice_name is not None:
        entry = _entry(gig, str(pairing.b["step_id"]))
        rows = load_suite(gig, entry, str(pairing.b["suite"]), expect_sha256=str(pairing.b["dataset_sha256"])).rows
        items = [item for item in pairing.clusters if item in rows and in_slice(rows[item], slice_name)]
        clusters = len({pairing.clusters[item] for item in items})
        if clusters < stats.MIN_CLUSTERS:
            raise RunRefused(f"slice {slice_name!r} has {clusters} clusters, fewer than the {stats.MIN_CLUSTERS} a comparison needs")
    return {
        "a": pairing.a["run_id"],
        "b": pairing.b["run_id"],
        "step": pairing.b["step_id"],
        "dataset_sha256": pairing.b["dataset_sha256"],
        "slice": slice_name,
        "items": len(items),
        "clusters": len({pairing.clusters[item] for item in items}),
        "correction": correction,
        "family": list(pairing.family),
        "metrics": measure_all(pairing, items, correction),
    }


def open_store(gig: GigContext) -> sqlite3.Connection:
    """The gig's eval store, read-only; none yet is a refusal (there is nothing to compare)."""

    if not records.evals_db_path(gig.name, gig.home).is_file():
        raise RunRefused(f"gig {gig.name!r} has no eval store yet: make runs with eval run first")
    return records._open_existing(gig.name, gig.home)


def pair(connection: sqlite3.Connection, run_a: str, run_b: str, metrics: Sequence[str] = ()) -> Pairing:
    """Runs ``run_a`` and ``run_b`` paired by item, after every fingerprint check (each failure a ``RunRefused``)."""

    a, b = _run_row(connection, run_a, None), _run_row(connection, run_b, None)
    if a["step_id"] != b["step_id"]:
        raise RunRefused(f"cannot compare: the runs are of different steps (A {a['step_id']}, B {b['step_id']})")
    if a["dataset_sha256"] != b["dataset_sha256"]:
        raise RunRefused(f"cannot compare: dataset sha256 differs (A {a['dataset_sha256']}, B {b['dataset_sha256']})")
    values_a, versions_a, clusters_a = _scores(connection, a)
    values_b, versions_b, clusters_b = _scores(connection, b)
    common = [s for s in values_b if s in values_a]
    shown = [_scorer(name, values_a, values_b) for name in metrics] if metrics else common
    if not shown:
        raise RunRefused(f"cannot compare: runs {run_a} and {run_b} share no scored metric")
    registered = _prereg_metrics(b) or _prereg_metrics(a)
    family = [scorer for scorer in common if scorer in registered or _metric(scorer) in registered]
    measured = list(dict.fromkeys([*shown, *family]))
    for scorer in measured:
        if versions_a[scorer] != versions_b[scorer]:
            raise RunRefused(
                f"cannot compare: scorer {scorer} version differs"
                f" (A {', '.join(sorted(versions_a[scorer]))}, B {', '.join(sorted(versions_b[scorer]))})"
            )
        if values_a[scorer].keys() != values_b[scorer].keys():
            raise RunRefused(
                f"cannot compare: the runs scored different items with {scorer}"
                f" (A {len(values_a[scorer])}, B {len(values_b[scorer])})"
            )
        repeats_a, repeats_b = _repeats(values_a[scorer]), _repeats(values_b[scorer])
        if len(repeats_a) > 1 or len(repeats_b) > 1:
            run, counts = (run_a, repeats_a) if len(repeats_a) > 1 else (run_b, repeats_b)
            raise RunRefused(
                f"cannot compare: run {run} repeats its items unequally with {scorer} ({min(counts)} to {max(counts)})"
            )
        if repeats_a != repeats_b:
            raise RunRefused(f"cannot compare: k differs (A {min(repeats_a)}, B {min(repeats_b)})")
    items = sorted(values_a[measured[0]])
    if any(values_a[scorer].keys() != values_a[measured[0]].keys() for scorer in measured):
        raise RunRefused("cannot compare: the metrics were scored on different items")
    moved = sum(clusters_a[item] != clusters_b[item] for item in items)
    if moved:
        raise RunRefused(f"cannot compare: the cluster key differs ({moved} item(s) in a different cluster)")
    clusters = {item: clusters_b[item] for item in items}
    count = len(set(clusters.values()))
    if count < stats.MIN_CLUSTERS:
        raise RunRefused(f"cannot compare: {count} clusters, fewer than the {stats.MIN_CLUSTERS} a paired comparison needs")
    return Pairing(
        a,
        b,
        {scorer: values_a[scorer] for scorer in measured},
        {scorer: values_b[scorer] for scorer in measured},
        clusters,
        tuple(family),
        tuple(shown),
    )


def measure_all(pairing: Pairing, items: Collection[str], correction: str = "holm") -> list[dict[str, Any]]:
    """The shown metrics of ``pairing`` measured on ``items``; the whole family's p-values (shown or not) are
    corrected together with ``correction``."""

    measured = {scorer: measure(pairing, scorer, items) for scorer in pairing.values_a}
    family = [measured[scorer] for scorer in pairing.family]
    if family:
        corrected = CORRECTIONS[correction]([result["p"] for result in family], 0.05)
        for result, adjusted, reject in zip(family, corrected["adjusted"], corrected["reject"], strict=True):
            result["p_adjusted"], result["reject"] = adjusted, reject
    return [measured[scorer] for scorer in pairing.shown]


def measure(pairing: Pairing, scorer: str, items: Collection[str]) -> dict[str, Any]:
    """One metric on ``items``: means, difference, test and MDE always; the interval when the items span at least
    ``stats.MIN_CLUSTERS`` clusters (``ci`` is ``None`` otherwise); the noise bound when either run has k >= 2."""

    order = sorted(items)
    a = [statistics.fmean(pairing.values_a[scorer][item]) for item in order]
    b = [statistics.fmean(pairing.values_b[scorer][item]) for item in order]
    clusters = [pairing.clusters[item] for item in order]
    differences = [right - left for left, right in zip(a, b, strict=True)]
    n = len(order)
    sd = statistics.stdev(differences) if n > 1 else 0.0
    result: dict[str, Any] = {
        "metric": _metric(scorer),
        "scorer": scorer,
        "items": n,
        "clusters": len(set(clusters)),
        "mean_a": statistics.fmean(a),
        "mean_b": statistics.fmean(b),
        "difference": statistics.fmean(differences),
        "ci": None,
        "p_adjusted": None,
        "reject": None,
        "noise_bound": None,
        "noise": None,
    }
    if len(set(clusters)) >= stats.MIN_CLUSTERS:
        interval = stats.paired_cluster_bootstrap(a, b, clusters, seed=BOOTSTRAP_SEED)
        result["ci"] = {name: interval[name] for name in ("lower", "upper", "level", "stderr", "resamples")}
    if all(value in (0.0, 1.0) for value in a + b):
        b_count = sum(left == 1.0 and right == 0.0 for left, right in zip(a, b, strict=True))
        c_count = sum(left == 0.0 and right == 1.0 for left, right in zip(a, b, strict=True))
        test = stats.mcnemar_exact(b_count, c_count)
        result["test"], result["p"] = "mcnemar", test["p_exact"]
        result["mcnemar"] = {"b": b_count, "c": c_count, "p": test["p_exact"], "p_mid": test["p_mid"]}
        discordance = (b_count + c_count) / n
        result["mde"] = (
            stats.minimum_detectable_difference(n, discordance)
            if discordance > 0
            else {"n": n, "discordance": 0.0, "mde": None}
        )
    else:
        ranked = stats.wilcoxon_signed_rank(a, b)
        result["test"], result["p"] = "wilcoxon", ranked["p_value"]
        result["wilcoxon"] = {name: ranked[name] for name in ("n", "zeros", "statistic", "p_value", "method")}
        result["mde"] = stats.mde_continuous(n, sd)
    if max(int(pairing.a["k"] or 1), int(pairing.b["k"] or 1)) >= 2:
        noise = stats.noise_bound(result["difference"], sd, n)
        result["noise"], result["noise_bound"] = noise, noise["upper"]
    return result


def report(result: Mapping[str, Any]) -> list[str]:
    """The comparison as text: a head line, then one line per metric."""

    where = f", slice {result['slice']}" if result["slice"] else ""
    head = (
        f"compare {result['a']} -> {result['b']}: step {result['step']}{where}, {result['items']} items,"
        f" {result['clusters']} clusters, dataset {result['dataset_sha256']}"
    )
    return [head, *(metric_line(metric, result["correction"]) for metric in result["metrics"])]


def metric_line(m: Mapping[str, Any], correction: str = "holm") -> str:
    """One measured metric on one line (shared by ``eval gate``)."""

    parts = [f"  {m['metric']}: {m['mean_a']:.4f} -> {m['mean_b']:.4f}, difference {m['difference']:+.4f}"]
    if m["ci"] is not None:
        parts.append(f"{m['ci']['level']:.0%} CI {m['ci']['lower']:+.4f} to {m['ci']['upper']:+.4f}")
    else:
        parts.append(f"no interval under {stats.MIN_CLUSTERS} clusters")
    if m["test"] == "mcnemar":
        parts.append(f"McNemar b={m['mcnemar']['b']} c={m['mcnemar']['c']} p={m['p']:.4f}")
    else:
        parts.append(f"Wilcoxon p={m['p']:.4f}")
    if m["p_adjusted"] is not None:
        parts.append(f"{correction} p={m['p_adjusted']:.4f}")
    mde = m["mde"]["mde"]
    parts.append(f"MDE {'none' if mde is None else f'{mde:.4f}'} at n={m['mde']['n']}")
    if m["noise_bound"] is not None:
        parts.append(f"noise bound {m['noise_bound']:+.4f}")
    return ", ".join(parts)


def in_slice(row: Mapping[str, Any], name: str) -> bool:
    """Whether a dataset row is in slice ``name``: its ``slice`` field is ``name`` or its ``tags`` list holds it."""

    tags = row.get("tags")
    return row.get("slice") == name or (isinstance(tags, list) and name in tags)


# --- internals -------------------------------------------------------------------------------------------------


def _scores(
    connection: sqlite3.Connection, run: Mapping[str, Any]
) -> tuple[dict[str, dict[str, list[float]]], dict[str, set[str]], dict[str, str]]:
    """Run ``run``'s values per scorer and item (one per repeat), the scorer versions, and each item's cluster; a run
    with two traces for one (item, repeat, attempt) is refused (the latest score could come from either)."""

    duplicate = connection.execute(
        "SELECT item_id, repeat_index, attempt_index, count(*) FROM traces WHERE run_id = ? AND step_id = ?"
        " GROUP BY item_id, repeat_index, attempt_index HAVING count(*) > 1 ORDER BY 1, 2, 3 LIMIT 1",
        (run["run_id"], run["step_id"]),
    ).fetchone()
    if duplicate is not None:
        item, repeat, attempt, count = duplicate
        raise RunRefused(
            f"cannot compare: run {run['run_id']} records {count} traces for item {item} repeat {repeat} attempt {attempt}"
        )
    latest: dict[tuple[str, int, str], tuple[str, float]] = {}
    clusters: dict[str, str] = {}
    for item, cluster, repeat, scorer, version, value in connection.execute(
        "SELECT traces.item_id, traces.cluster_id, traces.repeat_index, scores.scorer_id, scores.scorer_version,"
        " scores.value FROM traces JOIN scores USING (trace_id) WHERE traces.run_id = ? AND traces.step_id = ?"
        " ORDER BY traces.item_id, traces.repeat_index, traces.attempt_index, scores.recorded_at, scores.rowid",
        (run["run_id"], run["step_id"]),
    ):
        latest[(item, repeat, scorer)] = (version, float(value))  # a later attempt or score row replaces the earlier
        clusters[item] = item if cluster is None else str(cluster)
    values: dict[str, dict[str, list[float]]] = {}
    versions: dict[str, set[str]] = {}
    for (item, _repeat, scorer), (version, value) in latest.items():
        values.setdefault(scorer, {}).setdefault(item, []).append(value)
        versions.setdefault(scorer, set()).add(version)
    for scorer, by_item in values.items():
        if any(not math.isfinite(value) for repeats in by_item.values() for value in repeats):
            raise RunRefused(f"cannot compare: run {run['run_id']} stores a non-finite {scorer} score")
    return values, versions, clusters


def _repeats(by_item: Mapping[str, Sequence[float]]) -> set[int]:
    """The distinct repeat counts of a scorer's items (one count when every item was repeated alike)."""

    return {len(repeats) for repeats in by_item.values()}


def _scorer(name: str, values_a: Mapping[str, object], values_b: Mapping[str, object]) -> str:
    """The scorer id ``name`` stands for in both runs: the id itself, or the one scorer whose metric name it is."""

    found = [scorer for scorer in values_b if scorer == name] or [scorer for scorer in values_b if _metric(scorer) == name]
    if len(found) != 1 or found[0] not in values_a:
        why = "is ambiguous" if len(found) > 1 else "is not scored in both runs"
        raise RunRefused(f"cannot compare: metric {name!r} {why}")
    return found[0]


def _metric(scorer: str) -> str:
    return scorer.rpartition(":")[2]


def _prereg_metrics(run: Mapping[str, Any]) -> set[str]:
    try:
        prereg = json.loads(run["prereg"]) if run["prereg"] else None
    except (TypeError, ValueError):
        prereg = None
    if not isinstance(prereg, dict):
        return set()
    return {str(prereg[key]) for key in ("primary_metric", "guard_metric") if prereg.get(key)}

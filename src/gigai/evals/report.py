"""``gigai <gig> eval report [--run R] [--json] [--include-inputs] [--export inspect]``: what the stored runs measured.

For each ``eval run`` run (or only ``--run R``), oldest first: its items, clusters, adapter calls and models; per
metric the mean over items (an item's repeats averaged first) with a 95% cluster-bootstrap interval
(``stats.paired_cluster_bootstrap`` against zero, the interval of the run's own mean; none under
``stats.MIN_CLUSTERS`` clusters); the same per slice (a dataset row's ``slice`` or one of its ``tags``) when the
run's dataset can still be read at the run's SHA-256; the verdict of the gate the run's step declares for its suite
(``eval gate`` applied in-process: the ci gate replays its snapshot, the release and upgrade gates check the newest
run of the suite), or ``no gate``; and the counts of the first-round ``labels`` rows of the run's traces.

``--json`` prints the same as one JSON object (the feed another gig reads). It carries no item text: items are named
by id and SHA-256, no key is called ``input``, and any string longer than ``MAX_FEED_STRING`` characters (a long gate
line) is cut there. ``--include-inputs`` adds each trace's ``input`` and ``output`` and its dataset row's ``expected``
to the JSON and to the export, uncut, and says so on stderr. ``--export inspect`` also writes each run as an Inspect
log file (``gigai.evals.export_inspect``; needs the ``inspect`` extra). Every refusal (no eval store, an unknown
run, a non-finite score, the extra missing) exits 2 with one line on stderr and nothing written.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import click

from ..sdk.context import GigContext
from . import compare, gates, stats
from .run import RunRefused, _entry, _run_row, guard, load_suite

#: Longest string the ``--json`` feed carries without ``--include-inputs``; a longer one is cut to this length.
MAX_FEED_STRING = 200
#: The label values ``eval label`` records; any other stored value is counted as ``other``.
LABEL_VALUES = ("met", "not met", "unknown")
INCLUDE_INPUTS_WARNING = "warning: --include-inputs puts the items' input, output and expected text in this report"


@click.command("report")
@click.option("--run", "run_id", metavar="RUN", help="Report only this run (default: every eval run).")
@click.option("--json", "as_json", is_flag=True, help="Print the report as JSON (no item text).")
@click.option("--include-inputs", is_flag=True, help="Put each trace's input and output text in --json and the export.")
@click.option("--export", "export", type=click.Choice(["inspect"]), help="Also write each run as an Inspect log file.")
@click.pass_context
def report_command(ctx: click.Context, run_id: str | None, as_json: bool, include_inputs: bool, export: str | None) -> None:
    """Report the stored eval runs: means with intervals, slices, gate verdicts and label counts."""

    gig: GigContext = ctx.obj

    def action() -> list[str]:
        if export is not None:
            from . import export_inspect

            export_inspect.require_inspect()
        result = build(gig, run_id, include_inputs=include_inputs)
        if include_inputs:
            click.echo(INCLUDE_INPUTS_WARNING, err=True)
        if export is not None:
            for path in export_inspect.write_logs(gig, result, include_inputs=include_inputs):
                click.echo(f"wrote {path}", err=as_json)
        if as_json:
            return [json.dumps(result if include_inputs else clip(result), indent=2, sort_keys=True)]
        return text(result)

    for line in guard(gig, action):
        click.echo(line)


def build(gig: GigContext, run_id: str | None = None, *, include_inputs: bool = False) -> dict[str, Any]:
    """The report as plain data: one entry per run (``run_id`` only, else every ``eval run`` run, oldest first)."""

    connection = compare.open_store(gig)
    try:
        if run_id is not None:
            rows = [_run_row(connection, run_id, None)]
        else:
            ids = [row[0] for row in connection.execute(
                "SELECT run_id FROM runs WHERE step_id IS NOT NULL AND suite IS NOT NULL ORDER BY created_at, run_id"
            )]
            if not ids:
                raise RunRefused(f"gig {gig.name!r} has no eval run yet: make one with eval run first")
            rows = [_run_row(connection, found, None) for found in ids]
        verdicts: dict[tuple[str, str], dict[str, Any]] = {}
        runs = [_run(gig, connection, row, verdicts, include_inputs) for row in rows]
    finally:
        connection.close()
    return {"gig": gig.name, "include_inputs": include_inputs, "runs": runs}


def summarise(values: Mapping[str, Sequence[float]], clusters: Mapping[str, str]) -> dict[str, Any]:
    """One metric over items: ``n``, ``clusters``, the mean of the item means and its cluster-bootstrap interval
    (``ci`` is ``None`` under ``stats.MIN_CLUSTERS`` clusters). A NaN or infinite value is refused."""

    if any(not math.isfinite(value) for repeats in values.values() for value in repeats):
        raise RunRefused("cannot report: a non-finite score")
    items = sorted(values)
    means = [math.fsum(values[item]) / len(values[item]) for item in items]
    labels = [clusters[item] for item in items]
    count = len(set(labels))
    result: dict[str, Any] = {"n": len(items), "clusters": count, "mean": math.fsum(means) / len(means) if means else None, "ci": None}
    if count >= stats.MIN_CLUSTERS:
        interval = stats.paired_cluster_bootstrap([0.0] * len(means), means, labels, seed=compare.BOOTSTRAP_SEED)
        result["ci"] = {key: interval[key] for key in ("lower", "upper", "level", "stderr", "resamples", "seed")}
    return result


def clip(value: Any) -> Any:
    """``value`` with every string longer than ``MAX_FEED_STRING`` characters cut to that length (ending in ``…``)."""

    if isinstance(value, str):
        return value if len(value) <= MAX_FEED_STRING else value[: MAX_FEED_STRING - 1] + "…"
    if isinstance(value, dict):
        return {key: clip(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clip(item) for item in value]
    return value


def metric_names(scorers: Iterable[str]) -> dict[str, str]:
    """Each scorer id's metric name: the part after its last ``:``, or the whole id when another scorer of ``scorers``
    has the same part."""

    scorers = set(scorers)
    taken = Counter(compare._metric(scorer) for scorer in scorers)
    return {scorer: compare._metric(scorer) if taken[compare._metric(scorer)] == 1 else scorer for scorer in scorers}


def text(result: Mapping[str, Any]) -> list[str]:
    """The report as text: a head line per run, then its metrics, slices, gate and labels."""

    lines: list[str] = []
    for run in result["runs"]:
        calls = "?" if run["calls"] is None else run["calls"]
        models = ", ".join(run["models"]) or "none"
        lines.append(
            f"run {run['run']}: step {run['step']}, suite {run['suite']}, k {run['k']}, {run['n']} items,"
            f" {run['clusters']} clusters, {calls} calls, models {models}"
        )
        lines += [f"  {line}" for line in _metric_lines(run["metrics"])]
        if run["slices"] is None:
            lines.append(f"  slices: not read ({run['slices_note']})")
        for part in run["slices"] or ():
            lines.append(f"  slice {part['slice']}: {part['n']} items, {part['clusters']} clusters")
            lines += [f"    {line}" for line in _metric_lines(part["metrics"])]
        gate = run["gate"]
        if gate["verdict"] == "no gate":
            lines.append("  no gate")
        elif gate["verdict"] == "cannot check":
            lines.append(f"  gate {gate['suite']}: cannot check: {gate['reason']}")
        else:
            lines += [f"  {line}" for line in gate["lines"]]
        labels = run["labels"]
        if labels is not None:
            counts = ", ".join(f"{value} {labels[value]}" for value in (*LABEL_VALUES, "other") if labels[value] or value != "other")
            lines.append(f"  labels: {counts} ({labels['relabels']} relabels)")
    return lines


# --- internals -------------------------------------------------------------------------------------------------


def _run(
    gig: GigContext, connection: sqlite3.Connection, row: Mapping[str, Any], verdicts: dict[tuple[str, str], dict[str, Any]], include_inputs: bool
) -> dict[str, Any]:
    run_id, step_id, suite = row["run_id"], row["step_id"], row["suite"]
    traces = _traces(connection, run_id, step_id)
    values, scores = _values(connection, run_id, step_id)
    for scorer, by_item in values.items():
        if any(not math.isfinite(value) for repeats in by_item.values() for value in repeats):
            raise RunRefused(f"cannot report: run {run_id} stores a non-finite {scorer} score")
    clusters = {trace["item"]: trace["cluster"] for trace in traces}
    rows, note = _dataset_rows(gig, row)
    if (step_id, suite) not in verdicts:
        verdicts[(step_id, suite)] = _verdict(gig, step_id, suite)
    return {
        "run": run_id,
        "step": step_id,
        "suite": suite,
        "tag": row["tag"],
        "k": row["k"],
        "replay_of": row["replay_of"],
        "created_at": row["created_at"],
        "dataset": {"id": row["dataset_id"], "sha256": row["dataset_sha256"]},
        "calls": row["calls_made"],
        "models": sorted(
            model for (model,) in connection.execute("SELECT DISTINCT model_id FROM traces WHERE run_id = ? AND model_id IS NOT NULL", (run_id,))
        ),
        "n": len(clusters),
        "clusters": len(set(clusters.values())),
        "metrics": _metrics(values, clusters, None),
        "slices": None if rows is None else _slices(values, clusters, rows),
        "slices_note": note,
        "gate": verdicts[(step_id, suite)],
        "labels": _labels(connection, run_id, step_id),
        "items": [_item(trace, scores, rows, include_inputs) for trace in traces],
    }


def _traces(connection: sqlite3.Connection, run_id: str, step_id: str) -> list[dict[str, Any]]:
    """The run's step traces, the latest attempt of each (item, repeat), ordered by item and repeat."""

    duplicate = connection.execute(
        "SELECT item_id, repeat_index, attempt_index, count(*) FROM traces WHERE run_id = ? AND step_id = ?"
        " GROUP BY item_id, repeat_index, attempt_index HAVING count(*) > 1 ORDER BY 1, 2, 3 LIMIT 1",
        (run_id, step_id),
    ).fetchone()
    if duplicate is not None:
        item, repeat, attempt, count = duplicate
        raise RunRefused(f"cannot report: run {run_id} records {count} traces for item {item} repeat {repeat} attempt {attempt}")
    latest: dict[tuple[str, int], dict[str, Any]] = {}
    for trace_id, item, cluster, repeat, text_in, sha_in, text_out, sha_out, model in connection.execute(
        "SELECT trace_id, item_id, cluster_id, repeat_index, input, input_sha256, output_raw_text, output_sha256, model_id"
        " FROM traces WHERE run_id = ? AND step_id = ? ORDER BY item_id, repeat_index, attempt_index",
        (run_id, step_id),
    ):
        latest[(item, repeat)] = {
            "trace": trace_id, "item": item, "cluster": item if cluster is None else str(cluster), "repeat": repeat,
            "input": text_in, "input_sha256": sha_in, "output": text_out, "output_sha256": sha_out, "model": model,
        }
    return list(latest.values())


def _values(connection: sqlite3.Connection, run_id: str, step_id: str) -> tuple[dict[str, dict[str, list[float]]], dict[tuple[str, int], dict[str, float]]]:
    """The run's values per scorer and item (one per repeat, repeat order) and per (item, repeat) and scorer; the
    latest attempt and the latest score row win, as in ``eval compare``."""

    latest: dict[tuple[str, int], dict[str, float]] = {}
    for item, repeat, scorer, value in connection.execute(
        "SELECT traces.item_id, traces.repeat_index, scores.scorer_id, scores.value"
        " FROM traces JOIN scores USING (trace_id) WHERE traces.run_id = ? AND traces.step_id = ?"
        " ORDER BY traces.item_id, traces.repeat_index, traces.attempt_index, scores.recorded_at, scores.rowid",
        (run_id, step_id),
    ):
        latest.setdefault((item, repeat), {})[scorer] = float(value)  # a later attempt or score row replaces the earlier
    values: dict[str, dict[str, list[float]]] = {}
    for (item, _repeat), by_scorer in sorted(latest.items()):
        for scorer, value in by_scorer.items():
            values.setdefault(scorer, {}).setdefault(item, []).append(value)
    return values, latest


def _metrics(values: Mapping[str, Mapping[str, Sequence[float]]], clusters: Mapping[str, str], items: set[str] | None) -> list[dict[str, Any]]:
    measured: list[dict[str, Any]] = []
    for scorer in sorted(values):
        by_item = {item: repeats for item, repeats in values[scorer].items() if items is None or item in items}
        if by_item:
            measured.append({"metric": compare._metric(scorer), "scorer": scorer, **summarise(by_item, clusters)})
    return measured


def _slices(values: Mapping[str, Mapping[str, Sequence[float]]], clusters: Mapping[str, str], rows: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    names: set[str] = set()
    for item in clusters:
        row = rows.get(item, {})
        if isinstance(row.get("slice"), str):
            names.add(row["slice"])
        if isinstance(row.get("tags"), list):
            names.update(tag for tag in row["tags"] if isinstance(tag, str))
    found: list[dict[str, Any]] = []
    for name in sorted(names):
        items = {item for item in clusters if item in rows and compare.in_slice(rows[item], name)}
        found.append({"slice": name, "n": len(items), "clusters": len({clusters[item] for item in items}), "metrics": _metrics(values, clusters, items)})
    return found


def _dataset_rows(gig: GigContext, row: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]] | None, str | None]:
    """The run's dataset rows by item id, or ``None`` and why they cannot be read at the run's SHA-256."""

    try:
        data = load_suite(gig, _entry(gig, row["step_id"]), row["suite"])
    except RunRefused as exc:
        return None, _home_free(gig, str(exc))
    if data.record.sha256 != row["dataset_sha256"]:
        return None, f"the {row['suite']} dataset changed since run {row['run_id']} ({row['dataset_sha256']}, now {data.record.sha256})"
    return data.rows, None


def _verdict(gig: GigContext, step_id: str, suite: str) -> dict[str, Any]:
    """The ``suite`` gate of ``step_id`` applied as ``eval gate`` applies it: pass, fail, cannot check or no gate."""

    verdict: dict[str, Any] = {"suite": suite, "verdict": "no gate", "reason": None, "lines": [], "failures": []}
    try:
        if gates._gate(_entry(gig, step_id), suite) is None:
            return verdict
        failures: list[str] = []
        lines = gates.apply(gig, suite, step_id, failures)
    except RunRefused as exc:
        return {**verdict, "verdict": "cannot check", "reason": _home_free(gig, str(exc))}
    return {
        **verdict,
        "verdict": "fail" if failures else "pass",
        "lines": [_home_free(gig, line) for line in lines],
        "failures": [_home_free(gig, failure) for failure in failures],
    }


def _home_free(gig: GigContext, message: str) -> str:
    """``message`` with the gigai home's path written ``<home>``: the report is meant to be shared, the path names
    the user."""

    home = Path(gig.home)
    for prefix in sorted({str(home), str(home.resolve())}, key=len, reverse=True):
        message = message.replace(prefix, "<home>")
    return message


def _labels(connection: sqlite3.Connection, run_id: str, step_id: str) -> dict[str, int] | None:
    """Counts of the run's first-round labels by value, and how many relabels there are; ``None`` when none."""

    columns = {column[1] for column in connection.execute("PRAGMA table_info(labels)")}
    if not {"run_id", "step_id", "round"} <= columns:
        return None
    found = connection.execute(
        "SELECT CASE WHEN json_valid(label) THEN json_extract(label, '$') END, round, count(*) FROM labels"
        " WHERE run_id = ? AND step_id = ? GROUP BY 1, 2",
        (run_id, step_id),
    ).fetchall()
    if not found:
        return None
    counts = dict.fromkeys((*LABEL_VALUES, "other", "relabels"), 0)
    for value, round_, count in found:
        key = "relabels" if isinstance(round_, int) and round_ > 1 else value if value in LABEL_VALUES else "other"
        counts[key] += count
    return counts


def _item(
    trace: Mapping[str, Any], scores: Mapping[tuple[str, int], Mapping[str, float]], rows: Mapping[str, Mapping[str, Any]] | None, include_inputs: bool
) -> dict[str, Any]:
    item: dict[str, Any] = {key: trace[key] for key in ("item", "cluster", "repeat", "trace", "model", "input_sha256", "output_sha256")}
    item["scores"] = dict(scores.get((trace["item"], trace["repeat"]), {}))
    if include_inputs:
        row = (rows or {}).get(trace["item"], {})
        item.update(input=trace["input"], output=trace["output"], expected=row.get("expected"))
    return item


def _metric_lines(metrics: Sequence[Mapping[str, Any]]) -> list[str]:
    names = metric_names(m["scorer"] for m in metrics)
    lines: list[str] = []
    for m in metrics:
        ci = m["ci"]
        where = f"CI [{ci['lower']:.4f}, {ci['upper']:.4f}]" if ci else f"no CI (fewer than {stats.MIN_CLUSTERS} clusters)"
        lines.append(f"{names[m['scorer']]}: mean {m['mean']:.4f} over {m['n']} items, {m['clusters']} clusters, {where}")
    return lines

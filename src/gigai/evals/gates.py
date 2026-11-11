"""``gigai <gig> eval gate --suite ci|release|upgrade [--step S]``: pass (exit 0), fail (exit 1) or cannot compare (2).

The gate runs in-process and never calls a model:

- ``ci``: the step's committed replay snapshot (``gates.ci.snapshot``, a file in the gig package) holds each ci item's
  recorded raw output, the stored replies of its judge steps and the score of every scorer. The gate parses each
  output with the step's postprocess and scores it again (a judge scorer reads its stored reply); every score must
  equal the snapshot's. A differing value fails the gate naming the row id and the scorer.
- ``release`` and ``upgrade``: the newest run of the step on the suite, paired against ``paired_to``
  (``last-release``: the newest earlier release run; ``previous-model``: the newest earlier run of the suite on
  another model; defaults ``last-release`` for release, ``previous-model`` for upgrade) as ``eval compare`` pairs
  runs. ``primary`` and ``guard`` rules (``min_ci_lower``: lowest allowed lower bound of the difference's interval;
  ``no_worse``: lowest allowed point difference; a rule needs at least one, as numbers) apply to the whole dataset
  and to each slice (``slices``, else every slice the rows name); a slice under ``stats.MIN_CLUSTERS`` clusters is
  reported, not gated, and a declared slice the runs have no item of is refused (exit 2), as is a malformed rule.
  ``require = { private_share = X }``, only when set, refuses (exit 2) a dataset whose held-out (licence
  ``private``) share is under X. ``upgrade`` also reports each run's k and, for k >= 2, its variance components.

Errors and verdicts name rows by id and runs by run id; no dataset text or model output is printed.
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

import click

from ..gigs.eval_manifest import SUITES, eval_steps
from ..sdk.context import GigContext
from . import compare, contracts, stats
from . import run as eval_run
from .run import (
    RunRefused,
    Suite,
    _entry,
    _guarded,
    _import,
    _render,
    _scored,
    _template,
    _value,
    guard,
    load_suite,
    resolve_scorer,
)

#: ``paired_to`` per suite when the gate does not say.
DEFAULT_PAIRED_TO = {"release": "last-release", "upgrade": "previous-model"}
#: Scores equal to the snapshot's within this (they are stored as SQLite REAL / JSON numbers).
TOLERANCE = 1e-9


@click.command("gate")
@click.option("--suite", required=True, type=click.Choice(SUITES), help="The gate to apply: ci, release or upgrade.")
@click.option("--step", "step_id", help="Only this [[evals.steps]] entry (default: every step with a gate for the suite).")
@click.pass_context
def gate_command(ctx: click.Context, suite: str, step_id: str | None) -> None:
    """Apply a suite's gate: exit 0 pass, 1 fail, 2 cannot compare."""

    gig: GigContext = ctx.obj
    failures: list[str] = []
    for line in guard(gig, lambda: apply(gig, suite, step_id, failures)):
        click.echo(line)
    if failures:
        click.echo(f"gigai {gig.name} eval: gate {suite} failed: {'; '.join(failures)}", err=True)
        raise click.exceptions.Exit(1)


def apply(gig: GigContext, suite: str, step_id: str | None, failures: list[str]) -> list[str]:
    """Apply the ``suite`` gate of ``step_id`` (or every step that has one); returns the report lines and appends
    one entry per failed check to ``failures``. A gate that cannot be checked raises ``RunRefused`` first."""

    entries = [_entry(gig, step_id)] if step_id is not None else eval_steps(gig.manifest)
    gated = [(entry, gate) for entry in entries if (gate := _gate(entry, suite)) is not None]
    if not gated:
        who = "no step declares a" if step_id is None else f"step {step_id!r} declares no"
        raise RunRefused(f"{who} {suite} gate")
    lines: list[str] = []
    for entry, gate in gated:
        lines += _ci(gig, entry, gate, failures) if suite == "ci" else _paired(gig, entry, gate, suite, failures)
    lines.append(f"gate {suite}: {'FAIL' if failures else 'PASS'}")
    return lines


def held_out_share(data: Suite) -> float:
    """The share of the dataset's items under licence ``private`` (a row's own ``licence``, else the dataset's)."""

    def private(row: Mapping[str, Any]) -> bool:
        licence = row.get("licence") if isinstance(row.get("licence"), str) else data.declared.licence
        return licence == "private"

    return sum(private(row) for row in data.rows.values()) / len(data.rows) if data.rows else 0.0


# --- ci --------------------------------------------------------------------------------------------------------


def _ci(gig: GigContext, entry: Mapping[str, Any], gate: Mapping[str, Any], failures: list[str]) -> list[str]:
    step_id = str(entry["id"])
    snapshot, name = _snapshot(gig, step_id, gate)
    data = load_suite(gig, entry, "ci")
    if data.record.sha256 != snapshot["dataset_sha256"]:
        raise RunRefused(
            f"the ci dataset of step {step_id!r} changed since snapshot {name}"
            f" ({snapshot['dataset_sha256']}, now {data.record.sha256}): record the snapshot again"
        )
    rows = {row["id"]: row for row in snapshot["rows"]}
    if rows.keys() != data.rows.keys():
        raise RunRefused(f"snapshot {name} has {len(rows)} rows for the {len(data.rows)} items of the ci dataset")
    scorers = [resolve_scorer(scorer) for scorer in entry.get("scorers") or ()]
    postprocess = _import(entry["postprocess"], "postprocess") if entry.get("postprocess") else None
    template = _template(gig, entry)
    for item_id in sorted(rows):
        for spec in scorers:
            if spec.id not in rows[item_id]["scores"]:
                raise RunRefused(f"snapshot {name} row {item_id} has no {spec.id} score")
            if spec.judge_step is not None and spec.judge_step not in rows[item_id].get("judge", {}):
                raise RunRefused(f"snapshot {name} row {item_id} has no stored reply of judge step {spec.judge_step!r}")
    means: dict[str, list[float]] = {spec.id: [] for spec in scorers}
    before = len(failures)
    for item_id in sorted(rows):
        stored, row = rows[item_id], data.rows[item_id]
        prompt = _render(template, row, step_id)
        output = stored["output"]
        private = (prompt, output, row)
        parsed, status = (None, "not_parsed")
        guarded = _guarded(postprocess, prompt, row)
        if guarded is not None:
            parsed, status = guarded(output)
            parsed = None if parsed is None else json.loads(json.dumps(parsed))  # as a stored trace returns it
        trace = contracts.ScoredTrace(f"snapshot:{item_id}", step_id, item_id, 0, prompt, output, parsed, status)
        for spec in scorers:
            if spec.judge_step is None:
                score = _scored(spec, trace, row.get("expected"), private)
            else:
                score = contracts.judge_score(stored["judge"][spec.judge_step])
            value = _value(spec, trace, score, private)
            means[spec.id].append(value)
            expected = float(stored["scores"][spec.id])
            if not math.isclose(value, expected, rel_tol=0.0, abs_tol=TOLERANCE):
                failures.append(f"step {step_id} row {item_id}: {spec.id} scores {value:.4f}, snapshot {expected:.4f}")
    lines = [
        f"step {step_id}: ci snapshot {name}, {len(rows)} rows x {len(scorers)} scorer(s), 0 calls,"
        f" {len(failures) - before} mismatch(es)"
    ]
    lines += [f"  {scorer}: {statistics.fmean(values):.4f}" for scorer, values in means.items() if values]
    return lines


def _snapshot(gig: GigContext, step_id: str, gate: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """Step ``step_id``'s ci snapshot, its shape checked; a missing, escaping or malformed file is a refusal."""

    name = gate.get("snapshot")
    if not isinstance(name, str) or not name:
        raise RunRefused(f"the ci gate of step {step_id!r} names no snapshot (gates.ci.snapshot)")
    package = eval_run._package_dir(gig.name)
    if package is None:
        raise RunRefused(f"cannot locate the package of gig {gig.name!r} (is it installed?)")
    path = (Path(package) / name).resolve()
    if not path.is_relative_to(Path(package).resolve()):
        raise RunRefused(f"snapshot {name} leaves the gig package")
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RunRefused(f"snapshot {name} of step {step_id!r} is not in the gig package") from exc
    except (OSError, ValueError) as exc:
        raise RunRefused(f"snapshot {name} of step {step_id!r} is not readable JSON") from exc
    if not _snapshot_shape(loaded):
        raise RunRefused(
            f"snapshot {name} of step {step_id!r} is not a snapshot: it needs dataset_sha256 and rows of"
            " {id, output, scores, judge?} with one row per id"
        )
    return loaded, name


def _snapshot_shape(loaded: object) -> bool:
    if not isinstance(loaded, dict) or not isinstance(loaded.get("dataset_sha256"), str) or not isinstance(loaded.get("rows"), list):
        return False
    ids = set()
    for row in loaded["rows"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not isinstance(row.get("output"), str) or row["id"] in ids:
            return False
        scores, judge = row.get("scores"), row.get("judge", {})
        if not isinstance(scores, dict) or any(isinstance(v, bool) or not isinstance(v, int | float) for v in scores.values()):
            return False
        if not isinstance(judge, dict) or any(not isinstance(v, str) for v in judge.values()):
            return False
        ids.add(row["id"])
    return True


# --- release / upgrade -----------------------------------------------------------------------------------------


def _paired(gig: GigContext, entry: Mapping[str, Any], gate: Mapping[str, Any], suite: str, failures: list[str]) -> list[str]:
    step_id = str(entry["id"])
    where = f"the {suite} gate of step {step_id!r}"
    rules = _rules(gate, where)
    declared = gate.get("slices")
    if declared is not None and not (isinstance(declared, list) and all(isinstance(n, str) and n for n in declared)):
        raise RunRefused(f"{where}: slices must be a list of slice or tag names")
    data = load_suite(gig, entry, suite)
    _check_require(gate, where, data)
    paired_to = str(gate.get("paired_to") or DEFAULT_PAIRED_TO[suite])
    connection = compare.open_store(gig)
    try:
        candidate, baseline = _runs(connection, step_id, suite, paired_to)
        if candidate["dataset_sha256"] != data.record.sha256:
            raise RunRefused(
                f"run {candidate['run_id']} measured {candidate['dataset_sha256']}, not the {suite} dataset now"
                f" ({data.record.sha256})"
            )
        pairing = compare.pair(connection, baseline["run_id"], candidate["run_id"], [str(rule["metric"]) for _, rule in rules])
    finally:
        connection.close()
    names = list(declared) if declared is not None else sorted(_slice_names(data.rows.values()))
    slices = {
        name: [item for item in pairing.clusters if item in data.rows and compare.in_slice(data.rows[item], name)]
        for name in names
    }
    for name in names:
        if not slices[name]:
            raise RunRefused(f"slice {name!r} of {where} names no item the runs scored")
    lines = [
        f"step {step_id}: {suite} gate, run {candidate['run_id']} paired to {paired_to} run {baseline['run_id']},"
        f" {len(pairing.clusters)} items, {len(set(pairing.clusters.values()))} clusters"
    ]
    lines += _judged("all items", pairing, pairing.clusters.keys(), rules, step_id, failures)
    for name, items in slices.items():
        clusters = len({pairing.clusters[item] for item in items})
        if clusters < stats.MIN_CLUSTERS:
            lines.append(f"  slice {name}: {len(items)} items, {clusters} clusters < {stats.MIN_CLUSTERS}: reported, not gated")
            lines += ["  " + compare.metric_line(m) for m in compare.measure_all(pairing, items)]
        else:
            lines += _judged(f"slice {name}", pairing, items, rules, step_id, failures)
    if suite == "upgrade":
        lines += _upgrade_report(pairing, paired_to)
    return lines


def _rules(gate: Mapping[str, Any], where: str) -> list[tuple[str, Mapping[str, Any]]]:
    """The gate's primary rule and its guard rule (when declared), each a metric name with numeric thresholds."""

    rules: list[tuple[str, Mapping[str, Any]]] = []
    for role in ("primary", "guard"):
        rule = gate.get(role)
        if rule is None:
            if role == "primary":
                raise RunRefused(f"{where} declares no primary rule")
            continue
        if (
            not isinstance(rule, dict)
            or not isinstance(rule.get("metric"), str)
            or not rule["metric"]
            or set(rule) - {"metric", "min_ci_lower", "no_worse"}
        ):
            raise RunRefused(f"{where} has a malformed {role} rule: it takes a metric name, min_ci_lower and no_worse")
        thresholds = [key for key in ("min_ci_lower", "no_worse") if key in rule]
        if not thresholds:
            raise RunRefused(f"{where}: the {role} rule sets no threshold (min_ci_lower or no_worse)")
        for key in thresholds:
            if not _number(rule[key]):
                raise RunRefused(f"{where}: the {role} rule's {key} is not a number")
        rules.append((role, rule))
    return rules


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _judged(
    label: str,
    pairing: compare.Pairing,
    items: Collection[str],
    rules: list[tuple[str, Mapping[str, Any]]],
    step_id: str,
    failures: list[str],
) -> list[str]:
    """``items`` measured, with every rule applied; a broken rule appends to ``failures``."""

    measured = {m["scorer"]: m for m in compare.measure_all(pairing, items)}
    lines = [f"  {label}:"]
    for role, rule in rules:
        metric = next(m for m in measured.values() if str(rule["metric"]) in (m["scorer"], m["metric"]))
        broken = []
        if "min_ci_lower" in rule:
            lower = metric["ci"]["lower"]
            if lower < float(rule["min_ci_lower"]):
                broken.append(f"CI lower {lower:+.4f} < min_ci_lower {float(rule['min_ci_lower']):+.4f}")
        if "no_worse" in rule and metric["difference"] < float(rule["no_worse"]):
            broken.append(f"difference {metric['difference']:+.4f} < no_worse {float(rule['no_worse']):+.4f}")
        verdict = "FAIL" if broken else "pass"
        lines.append(f"  {role} {verdict}" + compare.metric_line(metric).removeprefix(" "))
        failures += [f"step {step_id} {label}: {role} {metric['metric']} {why}" for why in broken]
    return lines


def _check_require(gate: Mapping[str, Any], where: str, data: Suite) -> None:
    """``require``: only ``{ private_share = X }`` is checked, and only when the manifest sets it."""

    require = gate.get("require")
    if require is None:
        return
    if not isinstance(require, dict) or set(require) - {"private_share"}:
        raise RunRefused(f"{where} requires {require!r}; eval gate checks only private_share")
    if not _number(require.get("private_share")):
        raise RunRefused(f"{where}: require.private_share is not a number")
    minimum = float(require["private_share"])
    share = held_out_share(data)
    if share < minimum:
        raise RunRefused(f"held-out share {share:.2f} < {minimum:.2f}")


def _runs(connection: sqlite3.Connection, step_id: str, suite: str, paired_to: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The newest finished run of ``step_id`` on ``suite`` and the run ``paired_to`` names for it."""

    if paired_to not in DEFAULT_PAIRED_TO.values():
        raise RunRefused(f"paired_to {paired_to!r} is not one of {sorted(DEFAULT_PAIRED_TO.values())}")
    cursor = connection.execute(
        "SELECT * FROM runs WHERE step_id = ? AND suite IN (?, 'release') AND calls_made IS NOT NULL ORDER BY created_at, rowid",
        (step_id, suite),
    )
    names = [column[0] for column in cursor.description]
    runs = [dict(zip(names, found, strict=True)) for found in cursor.fetchall()]
    of_suite = [run for run in runs if run["suite"] == suite]
    if not of_suite:
        raise RunRefused(f"step {step_id!r} has no finished {suite} run to gate")
    candidate = of_suite[-1]
    earlier = runs[: runs.index(candidate)]
    if paired_to == "last-release":
        found = [run for run in earlier if run["suite"] == "release"]
    else:
        models = _models(connection, candidate)
        found = [run for run in earlier if run["suite"] == suite and _models(connection, run) != models]
    if not found:
        raise RunRefused(f"run {candidate['run_id']} has no {paired_to} run of step {step_id!r} to pair with")
    return candidate, found[-1]


def _models(connection: sqlite3.Connection, run: Mapping[str, Any]) -> set[str]:
    return {
        str(model)
        for (model,) in connection.execute(
            "SELECT DISTINCT model_id FROM traces WHERE run_id = ? AND step_id = ?", (run["run_id"], run["step_id"])
        )
    }


def _upgrade_report(pairing: compare.Pairing, paired_to: str) -> list[str]:
    lines = [f"  k: {paired_to} run {pairing.a['k'] or 1}, candidate {pairing.b['k'] or 1}"]
    scorer = pairing.shown[0]
    for role, run, values in (("baseline", pairing.a, pairing.values_a), ("candidate", pairing.b, pairing.values_b)):
        matrix = [values[scorer][item] for item in sorted(values[scorer])]
        repeats = {len(row) for row in matrix}
        if len(repeats) != 1 or min(repeats) < 2:
            lines.append(f"  variance components ({role} {run['run_id']}): need the same k >= 2 on every item")
            continue
        parts = stats.variance_components(matrix)
        lines.append(
            f"  variance components ({role} {run['run_id']}, {compare._metric(scorer)}): within-item"
            f" {parts['within_item']:.4f}, between-item {parts['between_item']:.4f}, stderr {parts['stderr_mean']:.4f}"
        )
    return lines


def _gate(entry: Mapping[str, Any], suite: str) -> dict[str, Any] | None:
    gates = entry.get("gates") if isinstance(entry.get("gates"), dict) else {}
    return next((g for g in gates.values() if isinstance(g, dict) and g.get("suite") == suite), None)


def _slice_names(rows: Collection[Mapping[str, Any]]) -> set[str]:
    names: set[str] = set()
    for row in rows:
        if isinstance(row.get("slice"), str):
            names.add(row["slice"])
        if isinstance(row.get("tags"), list):
            names.update(tag for tag in row["tags"] if isinstance(tag, str))
    return names

"""``gigai <gig> eval list [--json]``: the gig's eval steps as its manifest declares them, the state of their files,
and what ``evals.sqlite`` holds about them.

Reads the manifest only: scorers and postprocess are listed as the ``module:attr`` strings the manifest gives and are
never imported (``gig check`` item 11 imports them). Prompt files and datasets resolve as item 11 resolves them
(``gigs.eval_manifest``): package-relative, or under ``{data}`` (the gig folder). A private dataset absent under
``{data}`` lists as ``absent (private)``; only a ``ci`` dataset that is not there makes the verb exit 1. The last
run of each step and the latest judge validation come from the eval store, opened read-only; with no store yet
both are null.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import click

from ..gigs.eval_manifest import (
    OUTSIDE,
    PRESENT,
    UNCHECKED,
    data_folder,
    dataset_status,
    eval_steps,
    file_sha256,
    resolve,
    step_datasets,
)
from ..gigs.manifest import entry_points, manifest_location


@click.command("list")
@click.option("--json", "as_json", is_flag=True, help="Print one JSON object instead of text.")
@click.pass_context
def list_command(ctx: click.Context, as_json: bool) -> None:
    """List this gig's eval steps: prompts, scorers, datasets (with provenance and whether they are there), gates and
    the last recorded run. Exits 1 when a ci dataset is missing or a dataset path leaves its root."""

    gig = ctx.obj  # the gig's GigContext, set by the gig's group
    listing = eval_listing(gig.manifest, gig.name, gig.home, _package_dir(gig.name))
    if as_json:
        click.echo(json.dumps(listing, indent=2, sort_keys=True))
    else:
        for line in _text(listing):
            click.echo(line)
    unusable = [
        f"{step['id']}.{suite}: {dataset['path']} ({dataset['status']})"
        for step in listing["steps"]
        for suite, dataset in step["datasets"].items()
        if dataset["status"] == OUTSIDE or (suite == "ci" and dataset["status"] not in (PRESENT, UNCHECKED))
    ]
    if unusable:
        click.echo(f"dataset not where the manifest says: {'; '.join(unusable)}", err=True)
        raise click.exceptions.Exit(1)


def eval_listing(manifest: dict[str, Any], gig: str, home: Path, package_dir: Path | None) -> dict[str, Any]:
    """The listing ``eval list --json`` prints: one entry per ``[[evals.steps]]``, in manifest order."""

    evals = manifest.get("evals") if isinstance(manifest.get("evals"), dict) else {}
    data_dir = data_folder(home, gig)
    last_runs, validations, store_problem = _store_state(gig, home)
    steps = []
    for step in eval_steps(manifest):
        step_id = step.get("id")
        datasets = {}
        for dataset in step_datasets(step):
            status, _ = dataset_status(dataset, package_dir, data_dir)
            datasets[dataset.suite] = {**dataset.provenance(), "status": status}
        steps.append(
            {
                "id": step_id,
                "model_target": step.get("model_target"),
                "prompt": _prompt(step.get("prompt"), package_dir),
                "postprocess": step.get("postprocess"),
                "scorers": list(step.get("scorers", [])),
                "datasets": datasets,
                "gates": step.get("gates", {}),
                "grades": step.get("grades"),
                "dimension": step.get("dimension"),
                "validation": step.get("validation"),
                "input_schema": step.get("input_schema"),
                "output_schema": step.get("output_schema"),
                "last_run": last_runs.get(step_id),
                "last_validation": validations.get(step_id) or validations.get(f"step:{step_id}"),
            }
        )
    return {"gig": gig, "cluster": evals.get("cluster"), "store": store_problem, "steps": steps}


def _package_dir(gig: str) -> Path | None:
    """The folder of the installed gig's ``gig.toml`` (its package), located without importing the gig."""

    for entry_point in entry_points():
        if entry_point.name == gig:
            location = manifest_location(entry_point)
            return location.parent if location is not None else None
    return None


def _prompt(prompt: Any, package_dir: Path | None) -> dict[str, Any] | None:
    if not isinstance(prompt, dict):
        return None
    listed = {"id": prompt.get("id"), "version": prompt.get("version"), "file": prompt.get("file"), "sha256": None}
    path = resolve(prompt["file"], package_dir, None) if isinstance(prompt.get("file"), str) else None
    if package_dir is None:
        listed["status"] = UNCHECKED
    elif path is not None and path.is_file():
        listed.update(status=PRESENT, sha256=file_sha256(path))
    else:
        listed["status"] = "missing"
    return listed


_LAST_RUNS = """
SELECT t.step_id, t.run_id, r.created_at, r.dataset_id, r.dataset_sha256,
       COUNT(*), SUM(t.outcome != 'succeeded'), MAX(t.recorded_at) AS last_at
FROM traces AS t JOIN runs AS r ON r.run_id = t.run_id
GROUP BY t.step_id, t.run_id
ORDER BY last_at, t.run_id
"""

_VALIDATIONS = """
SELECT scorer_id, scorer_version, judge_model, dataset_id, n_items, metric, value, recorded_at
FROM judge_validations
ORDER BY recorded_at, validation_id
"""


def _store_state(gig: str, home: Path) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    """``(last run per step, latest validation per scorer id, why the store could not be read)``; empty without a
    store."""

    from ..sdk.store import StoreVersionError
    from .records import EvalsError, _open_existing, evals_db_path

    try:
        if not evals_db_path(gig, home).is_file():
            return {}, {}, None
        connection = _open_existing(gig, home)
    except (EvalsError, StoreVersionError, ValueError, sqlite3.Error) as exc:
        return {}, {}, str(exc)
    try:
        last_runs = {}
        for step_id, run_id, created_at, dataset_id, dataset_sha256, traces, failed, last_at in connection.execute(_LAST_RUNS):
            last_runs[step_id] = {
                "run_id": run_id,
                "created_at": created_at,
                "dataset_id": dataset_id,
                "dataset_sha256": dataset_sha256,
                "traces": traces,
                "failed": failed,
                "last_trace_at": last_at,
            }
        validations: dict[str, dict[str, Any]] = {}
        for scorer_id, version, judge_model, dataset_id, n_items, metric, value, recorded_at in connection.execute(_VALIDATIONS):
            latest = validations.setdefault(scorer_id, {"metrics": {}})
            latest.update(scorer_version=version, judge_model=judge_model, dataset_id=dataset_id, n_items=n_items, recorded_at=recorded_at)
            latest["metrics"][metric] = value
    except sqlite3.Error as exc:
        return {}, {}, str(exc)
    finally:
        connection.close()
    return last_runs, validations, None


def _text(listing: dict[str, Any]) -> list[str]:
    cluster = f", cluster {listing['cluster']}" if listing["cluster"] else ""
    lines = [f"{listing['gig']}: {len(listing['steps'])} eval step(s){cluster}"]
    if listing["store"]:
        lines.append(f"  eval store unreadable: {listing['store']}")
    for step in listing["steps"]:
        lines.append(f"{step['id']}  target {step['model_target']}")
        prompt = step["prompt"]
        if prompt is not None:
            digest = f" sha256 {prompt['sha256']}" if prompt["sha256"] else ""
            lines.append(f"  prompt {prompt['id']} v{prompt['version']}  {prompt['file']}  {prompt['status']}{digest}")
        if step["postprocess"]:
            lines.append(f"  postprocess {step['postprocess']}")
        if step["scorers"]:
            lines.append(f"  scorers {', '.join(step['scorers'])}")
        if step["grades"]:
            dimension = f" ({step['dimension']})" if step["dimension"] else ""
            lines.append(f"  grades {step['grades']}{dimension}")
        for suite, dataset in step["datasets"].items():
            facts = [dataset["licence"] or "no licence", dataset["source"] or "no source"]
            if dataset["sha256"]:
                facts.append(f"sha256 {dataset['sha256']}")
            lines.append(f"  dataset {suite}  {dataset['path']}  {dataset['status']}  {'  '.join(facts)}")
        if step["gates"]:
            lines.append(f"  gates {', '.join(sorted(step['gates']))}")
        run = step["last_run"]
        if run is None:
            lines.append("  last run: none")
        else:
            lines.append(f"  last run {run['run_id']}  {run['last_trace_at']}  {run['traces']} trace(s), {run['failed']} failed")
        validation = step["last_validation"]
        if step["grades"] and validation is not None:
            metrics = ", ".join(f"{name} {value:g}" for name, value in sorted(validation["metrics"].items()))
            lines.append(f"  last validation {validation['recorded_at']}  n={validation['n_items']}  {metrics}")
    return lines

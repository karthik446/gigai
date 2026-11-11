"""``gigai <gig> eval ablate``: one pre-registered ablation = one ``eval run`` per design cell, then the level comparisons.

``--axis A[,B...]`` names what moves (``AXES`` below), ``--design ofat|l9`` how (``designs``), ``--prereg FILE`` the
plan written before the first run (``prereg``: step, suite, metric, levels, the comparisons that make up the Holm
family, alpha, direction). Every run carries the plan's digest and ``registered_at`` in its ``prereg`` block; a
later ablation under the same id from a changed file is refused.

A level is set per run the way ``eval run`` already does it: request settings for the sampling axes (``settings=``),
the registered prompt file for the prompt axes (``template=``), a target name for ``model`` and ``judge_model``
(``targets=``: the step's, or its judge's, binding for that run alone; ``steps.toml`` is not written, and a judge
target without a passing validation is refused before any run row). The ``InvocationRequest`` fields that are not
request settings (``reasoning_effort``, ``max_output_tokens``, ``timeout``) wait for core fields and are refused up
front. A sampling axis an adapter the step may run on does not carry (``request_fields``) is refused before any run
row.

Comparisons pool, per item, the scores of the runs in each level's arm (``designs.Design.arm``) and go through
``compare.measure`` (McNemar or Wilcoxon, MDE, interval at ten clusters). Registered comparisons are Holm-corrected
together at the registered alpha; every other level pair is reported as exploratory with its raw p. ``--max-calls``
is one counter of paid calls over all runs: a call on a deterministic target is free and not counted, so an offline
ablation runs fully under ``--max-calls 0``. A run that reaches the cap keeps its row (``calls_made`` as far as it
got), the runs after it refuse their first paid call (a row with the free calls alone), the table is printed, and
the command exits 2 with the cap message.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from ..sdk.context import GigContext
from ..steps import Target, _package_file
from . import records, stats
from .compare import Pairing, _metric, _scores, measure
from .designs import DESIGNS, Design, DesignError, design as make_design
from .prereg import PreregError, Registration, block, load_registration
from .run import CapReachedError, RunRefused, ScorerSpec, _called_steps, _entry, _targets, check_judge, execute, guard, load_suite, resolve_scorer

OUTPUT_MODES = ("text", "json", "schema")


@dataclass(frozen=True)
class Axis:
    """One thing an ablation can move and how a run gets it."""

    id: str
    knob: str
    #: The request setting the levels go to (``settings=``); ``None`` for prompt axes and fixed knobs.
    setting: str | None = None
    #: The levels used when the pre-registration lists none.
    defaults: tuple[Any, ...] = ()
    #: Levels are prompt files of the gig package, sent as the step's template.
    prompt: bool = False
    #: Levels are target names, bound per run to the step (``"step"``) or to its judge (``"judge"``).
    target: str | None = None
    #: Why a run cannot move it yet (refused up front).
    fixed: str | None = None


AXES: Mapping[str, Axis] = {
    axis.id: axis
    for axis in (
        Axis("model", "the step's target", target="step"),
        Axis("prompt", "the step's prompt template", prompt=True),
        Axis("temperature", "request setting temperature", setting="temperature", defaults=(0.0, 0.5, 1.0)),
        Axis("reasoning_effort", "InvocationRequest.reasoning_effort", fixed="reasoning_effort is not a request setting a run can carry yet"),
        Axis("max_output_tokens", "InvocationRequest.max_output_tokens", fixed="max_output_tokens is not a request setting a run can carry yet"),
        Axis("output_mode", "request setting output_mode", setting="output_mode", defaults=OUTPUT_MODES),
        Axis("judge_model", "the judge step's target", target="judge"),
        Axis("top_p", "request setting top_p", setting="top_p", defaults=(0.1, 0.5, 1.0)),
        Axis("few_shot", "the step's prompt template with the examples baked in", prompt=True),
        Axis("context", "the step's prompt template with the context placed differently", prompt=True),
        Axis("retry_policy", "request setting retry_policy (attempts)", setting="retry_policy", defaults=(1, 2, 3)),
        Axis("timeout", "InvocationRequest.timeout_seconds", fixed="timeout_seconds is not a request setting a run can carry yet"),
    )
}


@click.command("ablate")
@click.option(
    "--axis",
    "axes",
    multiple=True,
    required=True,
    metavar="A[,B...]",
    help=(
        f"What moves (repeatable): {', '.join(axis.id for axis in AXES.values() if axis.fixed is None)}."
        f" Not yet (they need core request fields): {', '.join(axis.id for axis in AXES.values() if axis.fixed is not None)}."
    ),
)
@click.option("--design", type=click.Choice(DESIGNS), default="ofat", show_default=True, help="One factor at a time, or the L9 orthogonal array.")
@click.option("--prereg", type=click.Path(path_type=Path), required=True, help="The pre-registration file (TOML), written before the first run.")
@click.option("--max-calls", type=click.IntRange(0), help="Paid (non-deterministic) model calls allowed over all runs of this ablation.")
@click.option("--json", "as_json", is_flag=True, help="Print the ablation as JSON.")
@click.pass_context
def ablate_command(ctx: click.Context, axes: tuple[str, ...], design: str, prereg: Path, max_calls: int | None, as_json: bool) -> None:
    """Run a pre-registered ablation: one eval run per design cell, then the level comparisons."""

    gig: GigContext = ctx.obj
    names = [name.strip() for group in axes for name in group.split(",") if name.strip()]
    result = guard(gig, lambda: ablate(gig, names, design, prereg, max_calls=max_calls))
    for line in [json.dumps(result, indent=2, sort_keys=True)] if as_json else report(result):
        click.echo(line)
    if result["capped"] is not None:
        click.echo(f"gigai {gig.name} eval: {result['capped']}", err=True)
        raise click.exceptions.Exit(2)


def ablate(gig: GigContext, axis_ids: Sequence[str], design_name: str, prereg_path: Path, *, max_calls: int | None = None) -> dict[str, Any]:
    """The ablation over ``axis_ids`` in ``design_name`` under ``prereg_path``: its runs and comparisons (``RunRefused``
    before any run row when the plan, the axes, the design or the adapter cannot take it)."""

    try:
        registration = load_registration(prereg_path)
    except PreregError as exc:
        raise RunRefused(str(exc)) from exc
    axes = _axes(axis_ids)
    entry = _entry(gig, registration.step)
    scorer = next((name for name in entry.get("scorers") or () if _metric(name) == registration.primary_metric), None)
    if scorer is None:
        raise RunRefused(f"primary metric {registration.primary_metric!r} is not a scorer of step {registration.step}")
    scorers = [resolve_scorer(name) for name in entry.get("scorers") or ()]
    steps = {"step": registration.step, "judge": _judge_step(registration.step, scorers, axes)}
    bound = _targets(gig, _called_steps(registration.step, scorers))
    targets = {axis.id: _target_levels(gig, axis, registration, steps[axis.target]) for axis in axes if axis.target is not None}
    adapters = sorted({target.adapter for target in targets.get("model", {}).values()} or {bound[registration.step].adapter})
    levels = {axis.id: tuple(targets[axis.id]) if axis.target is not None else _levels(gig, axis, registration, adapters) for axis in axes}
    try:
        design = make_design(design_name, levels)
    except DesignError as exc:
        raise RunRefused(str(exc)) from exc
    for comparison in registration.comparisons:
        if comparison.axis not in design.axes or not {comparison.a, comparison.b} <= set(design.axes[comparison.axis]):
            raise RunRefused(
                f"pre-registration compares {comparison.axis} {comparison.a!r} with {comparison.b!r}, which this ablation does not run"
            )
    rows = len(load_suite(gig, entry, registration.suite).rows)
    if rows != registration.n_planned:
        raise RunRefused(
            f"pre-registration plans {registration.n_planned} items per run but the {registration.suite} suite of step"
            f" {registration.step} has {rows}"
        )
    _check_unchanged(gig, registration)
    if "judge_model" in targets:
        _check_judges(gig, next(spec for spec in scorers if spec.judge_step == steps["judge"]), targets["judge_model"])

    runs: list[dict[str, Any]] = []
    capped: str | None = None
    remaining = max_calls
    for index, cell in enumerate(design.cells):
        tag = f"ablate:{registration.id}:{design.name}:{index}"
        settings: dict[str, Any] = {}
        template: str | None = None
        moved: dict[str, str] = {}
        for axis in axes:
            if axis.prompt:
                template = _prompt(gig, cell[axis.id])
            elif axis.target is not None:
                moved[steps[axis.target]] = cell[axis.id]
            else:
                settings.update(_settings(axis, cell[axis.id], registration))
        paid = [step for step, target in _targets(gig, _called_steps(registration.step, scorers), moved).items() if target.adapter != "deterministic"]
        try:
            execute(
                gig,
                registration.step,
                registration.suite,
                k=registration.k,
                max_paid_calls=remaining,
                tag=tag,
                settings=settings or None,
                template=template,
                prereg=block(registration, design.name, design.axes, cell),
                targets=moved or None,
            )
        except CapReachedError:
            # One cap over the whole ablation: the cell saw only what was left of it.
            capped = capped or f"cap {max_calls} reached before paid call {(max_calls or 0) + 1}"
            remaining = 0
        run_id, made = _latest_run(gig, tag)
        if remaining is not None and paid:
            remaining = max(0, remaining - _paid_calls(gig, run_id, paid))
        runs.append({"cell": index, "run_id": run_id, "levels": dict(cell), "calls": made})

    comparisons = _comparisons(gig, registration, design, scorer, runs)
    return {
        "ablation": registration.id,
        "prereg_sha256": registration.sha256,
        "registered_at": registration.registered_at,
        "design": design.name,
        "step": registration.step,
        "suite": registration.suite,
        "items": rows,
        "k": registration.k,
        "primary_metric": registration.primary_metric,
        "scorer": scorer,
        "alpha": registration.alpha,
        "direction": registration.direction,
        "axes": {axis: list(values) for axis, values in design.axes.items()},
        "runs": runs,
        "comparisons": comparisons,
        "capped": capped,
    }


def report(result: Mapping[str, Any]) -> list[str]:
    """The ablation as text: a head line, one line per run, one per comparison."""

    lines = [
        f"ablate {result['ablation']}: design {result['design']}, step {result['step']}, suite {result['suite']},"
        f" {result['items']} items x {result['k']}, {len(result['runs'])} runs, prereg {result['prereg_sha256']}"
        f" registered {result['registered_at']}, alpha {result['alpha']} {result['direction']}"
    ]
    for run in result["runs"]:
        where = ", ".join(f"{axis}={level}" for axis, level in run["levels"].items())
        lines.append(f"  run {run['run_id']}: {where}, {run['calls']} calls")
    for c in result["comparisons"]:
        head = f"  {c['axis']} {c['a']} -> {c['b']} ({'registered' if c['registered'] else 'exploratory'})"
        if c["p"] is None:
            lines.append(f"{head}: not measured ({c['items']} scored items)")
            continue
        parts = [f"{head}: {result['primary_metric']} {c['mean_a']:.4f} -> {c['mean_b']:.4f}, difference {c['difference']:+.4f}"]
        if c["ci"] is not None:
            parts.append(f"{c['ci']['level']:.0%} CI {c['ci']['lower']:+.4f} to {c['ci']['upper']:+.4f}")
        parts.append(f"{'McNemar' if c['test'] == 'mcnemar' else 'Wilcoxon'} p={c['p']:.4f}")
        if c["p_adjusted"] is not None:
            parts.append(f"holm p={c['p_adjusted']:.4f}")
        mde = c["mde"]["mde"]
        mde_text = f"MDE {'none' if mde is None else f'{mde:.4f}'} at n={c['mde']['n']}"
        parts.append(f"rejected, {mde_text}" if c["reject"] else f"no effect detected, {mde_text}")
        lines.append(", ".join(parts))
    return lines


# --- internals -------------------------------------------------------------------------------------------------


def _axes(axis_ids: Sequence[str]) -> list[Axis]:
    if not axis_ids:
        raise RunRefused("--axis names nothing to move")
    axes = []
    for name in axis_ids:
        axis = AXES.get(name)
        if axis is None:
            raise RunRefused(f"unknown axis {name!r}: one of {', '.join(AXES)}")
        if axis.fixed is not None:
            raise RunRefused(f"axis {name} cannot move per run yet: {axis.fixed}")
        if axis in axes:
            raise RunRefused(f"axis {name} is named twice")
        axes.append(axis)
    prompts = [axis.id for axis in axes if axis.prompt]
    if len(prompts) > 1:
        raise RunRefused(f"axes {' and '.join(prompts)} both set the step's prompt template; move one of them per ablation")
    return axes


def _request_fields(adapter: str) -> frozenset[str]:
    """The request settings adapter ``adapter`` carries (its class's ``request_fields``), as the factory maps names."""

    from ..adapters import claude_cli, codex_cli, deterministic, ollama_local, openai_api, openrouter_api

    classes = {
        "deterministic": deterministic.DeterministicAdapter,
        "claude_cli": claude_cli.ClaudeCLIAdapter,
        "codex_cli": codex_cli.CodexCLIAdapter,
        "ollama_local": ollama_local.OllamaLocalAdapter,
        "openai_api": openai_api.OpenAIAPIAdapter,
        "openrouter_api": openrouter_api.OpenRouterAPIAdapter,
    }
    return frozenset(classes[adapter].request_fields) if adapter in classes else frozenset()


def _judge_step(step_id: str, scorers: Sequence[ScorerSpec], axes: Sequence[Axis]) -> str | None:
    """The judge step ``judge_model`` moves: the one ``step:<judge>`` scorer of the step; ``None`` when it does not move."""

    if not any(axis.target == "judge" for axis in axes):
        return None
    judges = sorted({spec.judge_step for spec in scorers if spec.judge_step is not None})
    if len(judges) != 1:
        raise RunRefused(
            f"axis judge_model: step {step_id} has {'no judge scorer' if not judges else 'judge scorers ' + ', '.join(judges)}"
            f"; it moves one step:<judge> scorer's target"
        )
    return judges[0]


def _target_levels(gig: GigContext, axis: Axis, registration: Registration, step_id: str) -> dict[str, Target]:
    """The registered target names of ``axis``, each resolved for ``step_id`` (a name config.toml lacks, or that
    cannot carry the step's settings, is a refusal)."""

    levels = registration.axes.get(axis.id) or ()
    if not levels:
        raise RunRefused(f"axis {axis.id} has no default levels: register [axes.{axis.id}] levels (target names) in the pre-registration")
    resolved = {}
    for level in levels:
        if type(level) is not str:
            raise RunRefused(f"axis {axis.id}: level {level!r} is not a target name")
        resolved[level] = _targets(gig, [step_id], {step_id: level})[step_id]
    return resolved


def _check_judges(gig: GigContext, spec: ScorerSpec, judges: Mapping[str, Target]) -> None:
    """Every judge level must pass the judge step's validation bar before the first run row."""

    connection = records.open_evals_db(gig.name, gig.home)
    try:
        for target in judges.values():
            check_judge(connection, gig, spec, target.id)
    finally:
        connection.close()


def _levels(gig: GigContext, axis: Axis, registration: Registration, adapters: Sequence[str]) -> tuple[Any, ...]:
    """The levels of ``axis`` for this ablation (registered, else the defaults), each checked for the knob;
    ``adapters`` are the adapters the step's runs go through, each of which must carry a setting axis."""

    if axis.setting is not None:
        for adapter in adapters:
            fields = _request_fields(adapter)
            if axis.setting not in fields:
                raise RunRefused(f"axis unsupported by adapter {adapter}: {axis.id} (target carries {', '.join(sorted(fields)) or 'nothing'})")
    levels = registration.axes.get(axis.id) or axis.defaults
    if not levels:
        raise RunRefused(f"axis {axis.id} has no default levels: register [axes.{axis.id}] levels in the pre-registration")
    for level in levels:
        if axis.prompt and not (type(level) is str and _package_file(gig.manifest, level) is not None):
            raise RunRefused(f"axis {axis.id}: level {level!r} is not a prompt file of the gig package")
        if axis.id in ("temperature", "top_p") and (type(level) not in (int, float) or not 0 <= level <= 2):
            raise RunRefused(f"axis {axis.id}: level {level!r} is not a number from 0 to 2")
        if axis.id == "output_mode" and level not in OUTPUT_MODES:
            raise RunRefused(f"axis output_mode: level {level!r} is not one of {', '.join(OUTPUT_MODES)}")
        if axis.id == "retry_policy" and (type(level) is not int or not 1 <= level <= 10):
            raise RunRefused(f"axis retry_policy: level {level!r} is not an attempts count from 1 to 10")
    return tuple(levels)


def _settings(axis: Axis, level: Any, registration: Registration) -> dict[str, Any]:
    if axis.id == "retry_policy":
        return {"retry_policy": {"attempts": level}}
    if axis.id == "output_mode":
        schema = registration.schemas.get("output_mode")
        return {"output_mode": level, **({"schema": schema} if level == "schema" and schema is not None else {})}
    assert axis.setting is not None
    return {axis.setting: level}


def _prompt(gig: GigContext, name: str) -> str:
    text = _package_file(gig.manifest, name)
    if text is None:
        raise RunRefused(f"prompt file {name!r} is not in the gig package")
    return text.decode("utf-8")


def _store(gig: GigContext) -> sqlite3.Connection | None:
    if not records.evals_db_path(gig.name, gig.home).is_file():
        return None
    return records._open_existing(gig.name, gig.home)


def _check_unchanged(gig: GigContext, registration: Registration) -> None:
    """Every stored run of this ablation must carry the digest of the file as it is now."""

    connection = _store(gig)
    if connection is None:
        return
    try:
        for stored in connection.execute("SELECT prereg FROM runs WHERE prereg IS NOT NULL ORDER BY created_at, rowid"):
            try:
                prereg = json.loads(stored[0])
            except ValueError:
                continue
            if isinstance(prereg, dict) and prereg.get("ablation") == registration.id and prereg.get("prereg_sha256") != registration.sha256:
                raise RunRefused(
                    f"pre-registration changed: ablation {registration.id!r} was registered as {prereg.get('prereg_sha256')},"
                    f" {registration.path} now hashes {registration.sha256}; register a changed plan under a new id"
                )
    finally:
        connection.close()


def _latest_run(gig: GigContext, tag: str) -> tuple[str, int]:
    """The run id and calls made of the newest run tagged ``tag`` (the one ``execute`` just wrote)."""

    connection = _store(gig)
    if connection is None:
        raise RunRefused(f"no run tagged {tag!r} was recorded")
    try:
        found = connection.execute("SELECT run_id, calls_made FROM runs WHERE tag = ? ORDER BY created_at DESC, rowid DESC LIMIT 1", (tag,)).fetchone()
    finally:
        connection.close()
    if found is None:
        raise RunRefused(f"no run tagged {tag!r} was recorded")
    return str(found[0]), int(found[1] or 0)


def _paid_calls(gig: GigContext, run_id: str, steps: Sequence[str]) -> int:
    """How many of run ``run_id``'s traces went through ``steps`` (the ones on a non-deterministic target)."""

    connection = _store(gig)
    if connection is None:
        return 0
    try:
        marks = ", ".join("?" * len(steps))
        return int(connection.execute(f"SELECT COUNT(*) FROM traces WHERE run_id = ? AND step_id IN ({marks})", (run_id, *steps)).fetchone()[0])
    finally:
        connection.close()


def _comparisons(gig: GigContext, registration: Registration, design: Design, scorer: str, runs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every level pair of every axis measured on the pooled arms; the registered ones Holm-corrected together."""

    connection = _store(gig)
    assert connection is not None  # the runs above created the store
    try:
        scored = [_scores(connection, {"run_id": run["run_id"], "step_id": registration.step}) for run in runs]
    finally:
        connection.close()
    registered = {(c.axis, c.a, c.b) for c in registration.comparisons}
    out = []
    for axis in design.axes:
        for a, b in design.pairs(axis):
            key = (axis, a, b) if (axis, a, b) in registered else (axis, b, a) if (axis, b, a) in registered else None
            if key is not None and key != (axis, a, b):
                a, b = b, a
            arm_a, arm_b = design.arm(axis, a), design.arm(axis, b)
            out.append(
                {
                    "axis": axis,
                    "a": a,
                    "b": b,
                    "registered": key is not None,
                    "runs_a": [runs[i]["run_id"] for i in arm_a],
                    "runs_b": [runs[i]["run_id"] for i in arm_b],
                    **_measure(registration, scorer, [scored[i] for i in arm_a], [scored[i] for i in arm_b]),
                }
            )
    family = [c for c in out if c["registered"] and c["p"] is not None]
    if family:
        corrected = stats.holm([c["p"] for c in family], registration.alpha)
        for c, adjusted, reject in zip(family, corrected["adjusted"], corrected["reject"], strict=True):
            c["p_adjusted"], c["reject"] = adjusted, reject
    return out


def _measure(registration: Registration, scorer: str, arm_a: Sequence[tuple], arm_b: Sequence[tuple]) -> dict[str, Any]:
    """``compare.measure`` of the primary metric over the pooled arms, one-sided when registered so; ``p`` is ``None``
    when either arm scored nothing."""

    values_a, clusters = _pool(scorer, arm_a)
    values_b, more = _pool(scorer, arm_b)
    clusters.update(more)
    items = sorted(set(values_a) & set(values_b))
    if not items:
        return {"items": 0, "p": None, "p_adjusted": None, "reject": None, "test": None, "mde": None}
    pairing = Pairing(
        a={"k": registration.k * len(arm_a)},
        b={"k": registration.k * len(arm_b)},
        values_a={scorer: values_a},
        values_b={scorer: values_b},
        clusters=clusters,
        family=(),
        shown=(scorer,),
    )
    result = measure(pairing, scorer, items)
    if registration.direction != "two-sided":
        hypothesised = result["difference"] > 0 if registration.direction == "greater" else result["difference"] < 0
        result["p"] = result["p"] / 2 if hypothesised else 1 - result["p"] / 2
    result["p_adjusted"], result["reject"] = None, None
    return result


def _pool(scorer: str, arm: Sequence[tuple]) -> tuple[dict[str, list[float]], dict[str, str]]:
    values: dict[str, list[float]] = {}
    clusters: dict[str, str] = {}
    for run_values, _versions, run_clusters in arm:
        for item, repeats in run_values.get(scorer, {}).items():
            values.setdefault(item, []).extend(repeats)
        clusters.update(run_clusters)
    return values, clusters

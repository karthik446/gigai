"""``gigai <gig> eval run``: run one eval step over a suite's dataset, record every call and score every trace.

``eval run --step S --suite ci`` renders the step's prompt on each dataset row (``template.format_map(row)``),
calls the step's model ``k`` times per row through ``ctx.model`` (so each call is traced in ``evals.sqlite`` with
its ``repeat_index`` and the step's ``postprocess`` output), then runs the step's scorers on the stored traces and
writes one ``scores`` row per trace and scorer. ``runs`` carries the step, suite, ``--tag``, ``k`` and, when the run
ends or stops, ``calls_made``.

Calls are capped before they are made: ``--max-calls N`` bounds every adapter call of the run (judge calls
included), the ``max_calls`` of the step's gate for the suite bounds the calls to non-deterministic targets, and
``ctx.budget.max_calls`` (when the context carries one) bounds them all; the run stops with exit 2 and
``cap N reached before call N+1`` instead of making the next call. A release or upgrade run that would call a
non-deterministic target with none of these caps is refused before it starts. Every target, prompt and request
setting is resolved and checked before the ``runs`` row is written, so a refusal leaves no empty run.

``eval run --from-traces RUN`` makes a new run (``replay_of`` = RUN) from RUN's stored traces: they are copied,
parsed again by the postprocess and scored again; no adapter is called (``calls_made`` 0). A ``step:<id>`` judge
scorer then reads the judge's stored replies instead of asking again. ``eval rescore RUN [--scorer NAME]`` writes
new score rows on RUN's own traces (its step's scorers, or the named ones), again without a call or a new trace.

Privacy: nothing from the dataset rows or the model outputs is printed; an error names the input by its SHA-256 and
its message has every fragment of the item's prompt, row, output and parse replaced by ``<redacted>``.
"""

from __future__ import annotations

import importlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import click

from ..canonical import digest_imported_bytes
from ..gigs.eval_manifest import (
    ABSENT_PRIVATE,
    MISSING,
    OUTSIDE,
    PRESENT,
    SUITES,
    EvalDataset,
    data_folder,
    dataset_status,
    eval_steps,
    file_sha256,
    step_datasets,
)
from ..sdk.context import GigContext
from ..steps import StepTargetError, Target, _declared, _package_file, check_settings, resolve_target
from . import contracts, records
from .list import _package_dir

#: The shortest run of characters an error message may not share with the item's prompt, row, output or parse.
_FRAGMENT = 8
_REDACTED = "<redacted>"


class RunRefused(records.EvalsError):
    """The run cannot start, or cannot make its next call; nothing more is called (exit 2)."""


class CapReachedError(RunRefused):
    """The next call would pass a call cap."""


class JudgeNotValidatedError(RunRefused):
    """A ``step:<id>`` judge scorer has no passing ``judge_validations`` row for its model and prompt."""


class RunFailed(records.EvalsError):
    """A call or a scorer failed mid-run (exit 1)."""


@dataclass(frozen=True)
class ScorerSpec:
    """One scorer of a run: the name it is recorded under, its version, and the code or judge step behind it."""

    id: str
    version: str
    score: contracts.Scorer | None = None
    #: The judge step of a ``step:<id>`` scorer.
    judge_step: str | None = None

    @property
    def metric(self) -> str:
        """The metric name a gate's ``primary.metric`` uses: the part after the last ``:``."""

        return self.id.rpartition(":")[2]


@dataclass
class CallBudget:
    """Counts a run's adapter calls and refuses the one that would pass a cap."""

    max_calls: int | None
    paid_cap: int | None
    deterministic: Mapping[str, bool]
    made: int = 0
    paid: int = 0

    def call(self, gig: GigContext, step_id: str, prompt: str, *, private: Sequence[object] = (), **fields: Any) -> None:
        """Make one counted call; an error's text is redacted against ``prompt`` and the ``private`` item values."""

        if self.max_calls is not None and self.made >= self.max_calls:
            raise CapReachedError(f"cap {self.max_calls} reached before call {self.max_calls + 1}")
        paid = not self.deterministic[step_id]
        if paid and self.paid_cap is not None and self.paid >= self.paid_cap:
            raise CapReachedError(f"cap {self.paid_cap} reached before call {self.paid_cap + 1}")
        try:
            gig.model(step_id, prompt, **fields)
        except (StepTargetError, KeyError) as exc:  # refused before the adapter was called
            raise RunRefused(_redact(_message(exc), prompt, *private)) from exc
        except Exception as exc:
            self.made += 1
            self.paid += paid
            # A postprocess failure (``_guarded``) is already redacted, against the output as well.
            reason = str(exc) if isinstance(exc, RunFailed) else _redacted(exc, prompt, *private)
            raise RunFailed(f"call {self.made} (input {_input_sha(prompt)}) failed: {reason}") from exc
        self.made += 1
        self.paid += paid


@dataclass(frozen=True)
class Suite:
    """A suite's dataset as the run reads it: the declaration, its rows by item id, and its ``runs`` record."""

    declared: EvalDataset
    rows: dict[str, dict[str, Any]]
    record: records.Dataset = field(repr=False)


@click.command("run")
@click.option("--step", "step_id", required=True, help="The [[evals.steps]] entry to run.")
@click.option("--suite", type=click.Choice(SUITES), help="The dataset to run on (ci, release, upgrade).")
@click.option("--k", type=click.IntRange(min=1), help="Repeats per item (default: the gate's k, else 1).")
@click.option("--max-calls", type=click.IntRange(min=0), help="Stop before adapter call N+1 (exit 2).")
@click.option("--tag", help="A label stored on the run.")
@click.option("--temperature", type=float, help="Sampling temperature sent with every call.")
@click.option("--seed", type=int, help="Sampling seed sent with every call.")
@click.option("--output-mode", type=click.Choice(["text", "json", "schema"]), help="Ask for text, JSON or the item's schema.")
@click.option("--from-traces", "source_run", metavar="RUN", help="Re-score RUN's stored traces as a new run; no calls.")
@click.pass_context
def run_command(
    ctx: click.Context,
    step_id: str,
    suite: str | None,
    k: int | None,
    max_calls: int | None,
    tag: str | None,
    temperature: float | None,
    seed: int | None,
    output_mode: str | None,
    source_run: str | None,
) -> None:
    """Run an eval step over a suite's dataset: call, trace, score."""

    gig: GigContext = ctx.obj
    settings = {
        name: value
        for name, value in (("temperature", temperature), ("seed", seed), ("output_mode", output_mode))
        if value is not None
    }
    if source_run is not None:
        flags = (("--suite", suite), ("--k", k), ("--max-calls", max_calls))
        sampling = [name for name, value in flags if value is not None]
        if sampling or settings:
            raise click.UsageError(f"--from-traces makes no calls; drop {', '.join(sampling + sorted(settings))}")
        outcome = guard(gig, lambda: replay(gig, step_id, source_run, tag))
    elif suite is None:
        raise click.UsageError("--suite is required (or --from-traces RUN)")
    else:
        outcome = guard(gig, lambda: execute(gig, step_id, suite, k=k, max_calls=max_calls, tag=tag, settings=settings))
    for line in outcome:
        click.echo(line)


@click.command("rescore")
@click.argument("run_id", metavar="RUN")
@click.option(
    "--scorer",
    "names",
    multiple=True,
    help="A scorer to run instead of the step's: match, includes, step:<judge> or module:attr (repeatable).",
)
@click.pass_context
def rescore_command(ctx: click.Context, run_id: str, names: tuple[str, ...]) -> None:
    """Score a run's stored traces again, with no model call and no new trace."""

    gig: GigContext = ctx.obj
    for line in guard(gig, lambda: rescore(gig, run_id, names)):
        click.echo(line)


def execute(
    gig: GigContext,
    step_id: str,
    suite: str,
    *,
    k: int | None = None,
    max_calls: int | None = None,
    tag: str | None = None,
    settings: Mapping[str, Any] | None = None,
) -> list[str]:
    """``eval run --step step_id --suite suite``; returns the summary lines."""

    entry = _entry(gig, step_id)
    scorers = [resolve_scorer(name) for name in entry.get("scorers") or ()]
    gate = next((g for g in (entry.get("gates") or {}).values() if isinstance(g, dict) and g.get("suite") == suite), {})
    repeats = k or gate.get("k") or 1
    targets = _targets(gig, _called_steps(step_id, scorers))
    budget_cap = getattr(getattr(gig, "budget", None), "max_calls", None)
    caps = [cap for cap in (max_calls, budget_cap) if isinstance(cap, int)]
    calls = CallBudget(
        min(caps) if caps else None,
        gate.get("max_calls"),
        {name: target.adapter == "deterministic" for name, target in targets.items()},
    )
    paid = sorted(name for name, deterministic in calls.deterministic.items() if not deterministic)
    if suite in ("release", "upgrade") and paid and calls.max_calls is None and calls.paid_cap is None:
        raise RunRefused(
            f"the {suite} suite has no call cap for step(s) {', '.join(paid)} on a non-deterministic target:"
            f" pass --max-calls N (or give the step {'an' if suite == 'upgrade' else 'a'} {suite} gate with max_calls)"
        )
    data = load_suite(gig, entry, suite)
    template = _template(gig, entry)
    postprocess = _import(entry["postprocess"], "postprocess") if entry.get("postprocess") else None
    # Every prompt and request is built and checked before the run row exists, so a refusal leaves no empty run.
    planned = []
    for item_id, row in data.rows.items():
        prompt = _render(template, row, step_id)
        planned.append((item_id, row, prompt, {**(settings or {}), **_schema_settings(row, settings or {}, prompt)}))
    _check_settings(targets, step_id, [call_settings for *_, call_settings in planned])
    run = records.RunRecord(
        run_id=records.new_id("run"),
        gig=gig.name,
        manifest_digest=_manifest_digest(gig),
        dataset=data.record,
        prereg=_prereg(gate, len(data.rows) * repeats),
        step_id=step_id,
        suite=suite,
        tag=tag,
        k=repeats,
    )
    connection = records.open_evals_db(gig.name, gig.home)
    try:
        for spec in scorers:
            if spec.judge_step is not None:
                check_judge(connection, gig, spec, targets[spec.judge_step].id)
        records.write_run(connection, run)
        try:
            for item_id, row, prompt, call_settings in planned:
                for repeat in range(repeats):
                    calls.call(
                        gig,
                        step_id,
                        prompt,
                        private=(row,),
                        run=run,
                        item=_item(item_id, row),
                        repeat_index=repeat,
                        postprocess=_guarded(postprocess, prompt, row),
                        **call_settings,
                    )

            def ask(judge_step: str, prompt: str, item: records.ItemRef, repeat_index: int, private: Sequence[object]) -> None:
                calls.call(gig, judge_step, prompt, private=private, run=run, item=item, repeat_index=repeat_index)

            metrics = score_run(connection, run.run_id, step_id, scorers, data.rows, ask=ask)
        finally:
            records.finish_run(connection, run.run_id, calls.made)
    finally:
        connection.close()
    return _summary(run, step_id, suite, len(data.rows), repeats, calls.made, metrics)


def replay(gig: GigContext, step_id: str, source_run: str, tag: str | None = None) -> list[str]:
    """``eval run --step step_id --from-traces source_run``: a new run from stored traces; no adapter call."""

    entry = _entry(gig, step_id)
    postprocess = _import(entry["postprocess"], "postprocess") if entry.get("postprocess") else None
    scorers = [resolve_scorer(name) for name in entry.get("scorers") or ()]
    connection = records.open_evals_db(gig.name, gig.home)
    try:
        source = _run_row(connection, source_run, step_id)
        data = load_suite(gig, entry, source["suite"], expect_sha256=source["dataset_sha256"])
        _check_stored_judges(connection, gig, scorers)
        run = records.RunRecord(
            run_id=records.new_id("run"),
            gig=gig.name,
            manifest_digest=_manifest_digest(gig),
            dataset=data.record,
            step_id=step_id,
            suite=source["suite"],
            tag=tag,
            k=source["k"],
            replay_of=source_run,
        )
        records.copy_traces(connection, source_run, run, step_id, _guarded(postprocess, *data.rows.values()))
        for judge in sorted({spec.judge_step for spec in scorers if spec.judge_step is not None}):
            records.copy_traces(connection, source_run, run, judge, None)
        try:
            metrics = score_run(connection, run.run_id, step_id, scorers, data.rows)
        finally:
            records.finish_run(connection, run.run_id, 0)
    finally:
        connection.close()
    return _summary(run, step_id, run.suite or "", len(data.rows), run.k or 1, 0, metrics)


def rescore(gig: GigContext, run_id: str, names: Sequence[str] = ()) -> list[str]:
    """``eval rescore run_id``: new score rows on run ``run_id``'s stored traces (its step's scorers, or ``names``)."""

    connection = records.open_evals_db(gig.name, gig.home)
    try:
        source = _run_row(connection, run_id, None)
        entry = _entry(gig, source["step_id"])
        scorers = [resolve_scorer(name) for name in names or entry.get("scorers") or ()]
        if not scorers:
            raise RunRefused(f"step {source['step_id']!r} names no scorers; pass --scorer")
        data = load_suite(gig, entry, source["suite"], expect_sha256=source["dataset_sha256"])
        _check_stored_judges(connection, gig, scorers)
        metrics = score_run(connection, run_id, source["step_id"], scorers, data.rows)
    finally:
        connection.close()
    head = f"rescored run {run_id}: step {source['step_id']}, suite {source['suite']}, 0 calls"
    return [head, *(_metric_line(name, values) for name, values in metrics.items())]


def score_run(
    connection: sqlite3.Connection,
    run_id: str,
    step_id: str,
    scorers: Sequence[ScorerSpec],
    rows: Mapping[str, Mapping[str, Any]],
    *,
    ask: Callable[[str, str, records.ItemRef, int, Sequence[object]], None] | None = None,
) -> dict[str, list[float]]:
    """Score run ``run_id``'s traces of ``step_id`` with ``scorers``; write the rows; return the values per scorer.

    With ``ask`` a judge scorer asks its judge step (``ask(judge_step, prompt, item, repeat_index, private)``, a
    counted call whose error text is redacted against ``private``); without, it reads the judge's reply already stored
    in the run for the same item and repeat; none is refused.
    """

    values: dict[str, list[float]] = {spec.id: [] for spec in scorers}
    for trace, item_version, cluster_id in _traces(connection, run_id, step_id):
        if trace.item_id not in rows:
            raise RunRefused(f"trace {trace.trace_id} answers an item the dataset no longer has")
        row = rows[trace.item_id]
        reference = row.get("expected")
        private = (trace.input, trace.output, trace.parsed, row)
        for spec in scorers:
            judge: tuple[str, str | None] | None = None
            if spec.judge_step is None:
                score = _scored(spec, trace, reference, private)
            else:
                prompt = contracts.judge_prompt(trace.input, trace.output, reference)
                if ask is not None:
                    item = records.ItemRef(trace.item_id, item_version, cluster_id)
                    ask(spec.judge_step, prompt, item, trace.repeat_index, private)
                stored = _judge_reply(connection, run_id, spec.judge_step, trace)
                if stored is None:
                    raise RunRefused(f"run {run_id} has no stored reply of judge step {spec.judge_step!r}")
                judge_trace, reply, judge_model = stored
                judge = (judge_trace, judge_model)
                score = contracts.judge_score(reply)
            value = _value(spec, trace, score, private)
            records.write_score(
                connection,
                records.ScoreRecord(
                    score_id=records.new_id("score"),
                    trace_id=trace.trace_id,
                    scorer_id=spec.id,
                    scorer_version=spec.version,
                    value=value,
                    explanation=score.explanation,
                    judge_model=None if judge is None else judge[1],
                    judge_prompt_sha256=None if judge is None else contracts.JUDGE_PROMPT_SHA256,
                    position_swapped=None if judge is None else False,
                    judge_invocation_id=None if judge is None else judge[0],
                ),
            )
            values[spec.id].append(value)
    return values


def resolve_scorer(name: str) -> ScorerSpec:
    """A scorer as a manifest or ``--scorer`` names it: ``step:<judge>``, a lifted name, or ``module:attr``."""

    if name.startswith("step:"):
        return ScorerSpec(name, contracts.LIFTED_VERSION, judge_step=name.removeprefix("step:"))
    if name in contracts.LIFTED_SCORERS:
        return ScorerSpec(name, contracts.LIFTED_VERSION, contracts.LIFTED_SCORERS[name])
    function = _import(name, "scorer")
    lifted = getattr(function, "__module__", None) == contracts.__name__
    return ScorerSpec(name, contracts.LIFTED_VERSION if lifted else str(getattr(function, "version", "1")), function)


def check_judge(connection: sqlite3.Connection, gig: GigContext, spec: ScorerSpec, judge_model: str) -> None:
    """Refuse judge scorer ``spec`` unless the latest kappa validation of its model and prompt passes its step's bar
    and, when the row records one (``eval validate-judge``), its own pass rule."""

    entry = _entry(gig, spec.judge_step or "")
    validation = entry.get("validation") if isinstance(entry.get("validation"), dict) else None
    if validation is None:
        raise JudgeNotValidatedError(f"scorer {spec.id}: judge step {spec.judge_step!r} declares no validation")
    min_n, min_kappa = int(validation.get("min_n", 1)), float(validation.get("min_kappa", 0.0))
    latest = connection.execute(
        "SELECT n_items, value, details FROM judge_validations WHERE scorer_id = ? AND judge_model = ?"
        " AND judge_prompt_sha256 = ? AND metric = 'kappa' ORDER BY recorded_at DESC, rowid DESC LIMIT 1",
        (spec.id, judge_model, contracts.JUDGE_PROMPT_SHA256),
    ).fetchone()
    reasons = [] if latest is None else _validation_failures(latest[0], latest[1], latest[2], min_n, min_kappa)
    if latest is None or reasons:
        found = "none" if latest is None else f"kappa {_number(latest[1])} over {latest[0]} items"
        why = f", which fails: {', '.join(reasons)}" if reasons else ""
        raise JudgeNotValidatedError(
            f"scorer {spec.id} refused: judge {judge_model} with prompt {contracts.JUDGE_PROMPT_SHA256} needs a"
            f" validation of kappa >= {min_kappa} over >= {min_n} items (latest: {found}){why}; validate the judge first"
        )


def _validation_failures(n_items: int, kappa: float | None, details: str | None, min_n: int, min_kappa: float) -> list[str]:
    """Why a ``judge_validations`` kappa row does not unlock its scorer: the step's bar, then the row's own recorded
    rule (``details.pass`` with ``details.reasons``, written by ``eval validate-judge``; a row without details has
    none)."""

    reasons: list[str] = []
    if n_items < min_n:
        reasons.append(f"n < {min_n}")
    if kappa is None or kappa < min_kappa:
        reasons.append(f"kappa < {min_kappa}")
    try:
        recorded = json.loads(details) if details else None
    except json.JSONDecodeError:
        recorded = None
    if isinstance(recorded, dict) and recorded.get("pass") is False:
        rule = recorded.get("reasons") if isinstance(recorded.get("reasons"), list) else []
        reasons.extend(str(reason) for reason in rule if str(reason) not in reasons)
        if not rule:
            reasons.append("recorded pass rule not met")
    return reasons


def _number(value: float | None) -> str:
    return "undefined" if value is None else f"{value:.3f}"


def load_suite(gig: GigContext, entry: Mapping[str, Any], suite: str, *, expect_sha256: str | None = None) -> Suite:
    """Read step ``entry``'s ``suite`` dataset, checked against the manifest's (or ``expect_sha256``'s) SHA-256."""

    declared = next((d for d in step_datasets(dict(entry)) if d.suite == suite), None)
    if declared is None:
        raise RunRefused(f"step {entry['id']!r} declares no {suite} dataset ([[evals.steps]] datasets.{suite})")
    status, path = dataset_status(declared, _package_dir(gig.name), data_folder(gig.home, gig.name))
    if status != PRESENT or path is None:
        pinned = f" (sha256 {declared.sha256})" if declared.sha256 else ""
        if status in (ABSENT_PRIVATE, MISSING) and path is not None:
            raise RunRefused(f"the {suite} dataset is not there: put it at {path}{pinned} and run again")
        reason = "leaves its root" if status == OUTSIDE else "cannot be located (is the gig installed?)"
        raise RunRefused(f"the {suite} dataset {declared.path} {reason}")
    bare = file_sha256(path)
    if declared.sha256 and bare != declared.sha256:
        raise RunRefused(f"the {suite} dataset {path.name} has sha256 {bare}, not the manifest's {declared.sha256}")
    digest = dataset_digest(bare)
    if expect_sha256 is not None and digest != expect_sha256:
        raise RunRefused(f"the {suite} dataset changed since the run being replayed ({expect_sha256}, now {digest})")
    rows: dict[str, dict[str, Any]] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            row = None
        if not isinstance(row, dict) or not isinstance(row.get("id"), str | int) or str(row["id"]) in rows:
            raise RunRefused(f"the {suite} dataset line {number} is not a JSON object with a new id")
        rows[str(row["id"])] = row
    recipe = declared.slice_recipe if isinstance(declared.slice_recipe, dict) else {"recipe": declared.slice_recipe}
    record = records.Dataset(
        id=declared.path,
        version=declared.split or "1",
        sha256=digest,
        slice_recipe={**recipe, "count": len(rows)},
        seed=declared.seed,
    )
    return Suite(declared, rows, record)


def dataset_digest(bare_sha256: str) -> str:
    """The manifest's bare-hex dataset SHA-256 as ``runs.dataset_sha256`` stores it (``sha256:<hex>``)."""

    return f"sha256:{bare_sha256}"


def guard(gig: GigContext, action: Callable[[], list[str]]) -> list[str]:
    """Run ``action``; an eval refusal exits 2 and a failure exits 1, each with one line on stderr."""

    try:
        return action()
    except RunRefused as exc:
        click.echo(f"gigai {gig.name} eval: {exc}", err=True)
        raise click.exceptions.Exit(2) from exc
    except (records.EvalsError, OSError) as exc:
        click.echo(f"gigai {gig.name} eval: {exc}", err=True)
        raise click.exceptions.Exit(1) from exc


# --- internals -------------------------------------------------------------------------------------------------


def _entry(gig: GigContext, step_id: str) -> dict[str, Any]:
    entry = next((step for step in eval_steps(gig.manifest) if step.get("id") == step_id), None)
    if entry is None:
        raise RunRefused(f"no [[evals.steps]] entry has id {step_id!r}")
    return entry


def _template(gig: GigContext, entry: Mapping[str, Any]) -> str:
    """The step's prompt template: the entry's ``prompt.file``, else ``[steps].<id>.prompt``."""

    prompt = entry.get("prompt")
    name = prompt.get("file") if isinstance(prompt, dict) else _declared(gig.manifest).get(entry["id"], {}).get("prompt")
    text = _package_file(gig.manifest, name) if isinstance(name, str) else None
    if text is None:
        raise RunRefused(f"step {entry['id']!r} has no prompt file in the gig package")
    return text.decode("utf-8")


def _render(template: str, row: Mapping[str, Any], step_id: str) -> str:
    try:
        return template.format_map(row)
    except (KeyError, IndexError) as exc:
        raise RunRefused(f"the prompt names {{{exc.args[0]}}}, which item {row['id']} does not have") from exc
    except ValueError as exc:  # the template is gig text, not row data: the message may name it
        raise RunRefused(f"the prompt template of step {step_id} is not a valid format string: {exc}") from exc


def _schema_settings(row: Mapping[str, Any], settings: Mapping[str, Any], prompt: str) -> dict[str, Any]:
    """``output_mode="schema"`` plus the item's schema for a schema item (tag ``output_mode:schema``) or mode."""

    tags = row.get("tags") if isinstance(row.get("tags"), list) else []
    if settings.get("output_mode") != "schema" and "output_mode:schema" not in tags:
        return {}
    expected = row.get("expected")
    schema = expected.get("schema") if isinstance(expected, dict) else None
    if not isinstance(schema, dict):
        raise RunRefused(f"input {_input_sha(prompt)} asks for schema output but its item has no expected.schema")
    return {"output_mode": "schema", "schema": schema}


def _item(item_id: str, row: Mapping[str, Any]) -> records.ItemRef:
    cluster = row.get("cluster_id", row.get("cluster"))
    return records.ItemRef(item_id, str(row.get("version", "1")), None if cluster is None else str(cluster))


def _called_steps(step_id: str, scorers: Sequence[ScorerSpec]) -> list[str]:
    return [step_id, *sorted({spec.judge_step for spec in scorers if spec.judge_step is not None})]


def _targets(gig: GigContext, step_ids: Sequence[str]) -> dict[str, Target]:
    """The model target of each step in ``step_ids``; a step or target the config cannot resolve is a refusal."""

    from ..config import ConfigurationError, load_config

    try:
        config = load_config(gig.home)
        return {name: resolve_target(gig.home, gig.name, gig.manifest, name, config) for name in step_ids}
    except (StepTargetError, ConfigurationError, KeyError) as exc:
        raise RunRefused(_message(exc)) from exc


def _check_settings(targets: Mapping[str, Target], step_id: str, call_settings: Sequence[Mapping[str, Any]]) -> None:
    """Refuse, before any call, a request setting a target cannot carry (``steps.call`` checks the same per call)."""

    try:
        for settings in call_settings:
            check_settings(targets[step_id], {**targets[step_id].settings, **settings})
        for name, target in targets.items():
            if name != step_id:
                check_settings(target, target.settings)
    except StepTargetError as exc:
        raise RunRefused(str(exc)) from exc


def _guarded(postprocess: records.Postprocess | None, *private: object) -> records.Postprocess | None:
    """``postprocess`` with its failure as ``RunFailed`` whose text is redacted against the output and ``private``."""

    if postprocess is None:
        return None

    def guarded(raw_text: str) -> tuple[Any, str]:
        try:
            return postprocess(raw_text)
        except Exception as exc:
            raise RunFailed(f"postprocess failed: {_redacted(exc, raw_text, *private)}") from exc

    return guarded


def _check_stored_judges(connection: sqlite3.Connection, gig: GigContext, scorers: Sequence[ScorerSpec]) -> None:
    """Without a call, a judge scorer is still refused unless its judge (as resolved now) is validated."""

    judges = [spec for spec in scorers if spec.judge_step is not None]
    if judges:
        targets = _targets(gig, [spec.judge_step or "" for spec in judges])
        for spec in judges:
            check_judge(connection, gig, spec, targets[spec.judge_step or ""].id)


def _traces(connection: sqlite3.Connection, run_id: str, step_id: str) -> Iterator[tuple[contracts.ScoredTrace, str, str | None]]:
    rows = connection.execute(
        "SELECT trace_id, item_id, item_version, cluster_id, repeat_index, input, output_raw_text, output_parsed,"
        " parse_status FROM traces WHERE run_id = ? AND step_id = ? ORDER BY item_id, repeat_index, attempt_index",
        (run_id, step_id),
    ).fetchall()
    for trace_id, item_id, version, cluster, repeat, text, output, parsed, status in rows:
        loaded = None if parsed is None else json.loads(parsed)
        yield contracts.ScoredTrace(trace_id, step_id, item_id, repeat, text, output, loaded, status), version, cluster


def _judge_reply(
    connection: sqlite3.Connection, run_id: str, judge_step: str, trace: contracts.ScoredTrace
) -> tuple[str, str, str | None] | None:
    found = connection.execute(
        "SELECT trace_id, output_raw_text, model_id FROM traces WHERE run_id = ? AND step_id = ? AND item_id = ?"
        " AND repeat_index = ? ORDER BY recorded_at DESC, rowid DESC LIMIT 1",
        (run_id, judge_step, trace.item_id, trace.repeat_index),
    ).fetchone()
    return None if found is None else (found[0], found[1], found[2])


def _scored(spec: ScorerSpec, trace: contracts.ScoredTrace, reference: Any, private: Sequence[object]) -> contracts.Score:
    assert spec.score is not None
    try:
        return spec.score(trace, reference)
    except Exception as exc:
        raise RunFailed(f"scorer {spec.id} failed on input {_input_sha(trace.input)}: {_redacted(exc, *private)}") from exc


def _value(spec: ScorerSpec, trace: contracts.ScoredTrace, score: object, private: Sequence[object]) -> float:
    try:
        return contracts.score_value(score)
    except records.EvalsError as exc:
        raise RunFailed(f"scorer {spec.id} on input {_input_sha(trace.input)}: {_redact(str(exc), *private)}") from exc


def _run_row(connection: sqlite3.Connection, run_id: str, step_id: str | None) -> dict[str, Any]:
    cursor = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
    found = cursor.fetchone()
    if found is None:
        raise RunRefused(f"no run {run_id} in this gig's eval store")
    row = dict(zip([column[0] for column in cursor.description], found, strict=True))
    if row["suite"] is None or row["step_id"] is None:
        raise RunRefused(f"run {run_id} was not made by eval run")
    if step_id is not None and row["step_id"] != step_id:
        raise RunRefused(f"run {run_id} ran step {row['step_id']!r}, not {step_id!r}")
    return row


def _import(name: str, what: str) -> Any:
    module_name, _, attribute = name.partition(":")
    try:
        found = getattr(importlib.import_module(module_name), attribute)
    except (ImportError, AttributeError, ValueError) as exc:
        raise RunRefused(f"cannot import the {what} {name!r}: {exc}") from exc
    if not callable(found):
        raise RunRefused(f"the {what} {name!r} is not callable")
    return found


def _prereg(gate: Mapping[str, Any], n_planned: int) -> records.Prereg | None:
    primary = gate.get("primary")
    if not isinstance(primary, dict):
        return None
    rule, margin = next(
        ((name, float(primary[name])) for name in ("min_ci_lower", "no_worse") if name in primary), ("none", 0.0)
    )
    guard = gate.get("guard")
    return records.Prereg(
        primary_metric=str(primary["metric"]),
        pass_rule=rule,
        margin=margin,
        n_planned=n_planned,
        registered_at=datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        guard_metric=str(guard["metric"]) if isinstance(guard, dict) and "metric" in guard else None,
    )


def _manifest_digest(gig: GigContext) -> str:
    return digest_imported_bytes(json.dumps(gig.manifest, sort_keys=True, default=str).encode("utf-8"))


def _input_sha(text: str) -> str:
    return digest_imported_bytes(text.encode("utf-8"))


def _message(exc: Exception) -> str:
    """An exception's text; a ``KeyError``'s is its first argument, not its repr."""

    return str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc)


def _redacted(exc: Exception, *private: object) -> str:
    """``exc`` as ``Type: message``, its message redacted against the ``private`` values (see ``_redact``)."""

    return f"{type(exc).__name__}: {_redact(_message(exc), *private)}"


def _redact(message: str, *private: object) -> str:
    """``message`` with every run of at least ``_FRAGMENT`` characters that occurs in a ``private`` value, and every
    shorter private value standing as a word of its own, replaced by ``<redacted>``.

    ``private`` holds the item's prompt, row, output and parse; strings are also matched in their repr/JSON forms
    and containers by their values, so an error that quotes a slice of the input or the expected answer prints
    neither.
    """

    fragments, words = _private_texts(private)
    haystack = "\0".join(fragments)
    spans: list[tuple[int, int]] = []
    start = 0
    while haystack and start + _FRAGMENT <= len(message):
        end = start + _FRAGMENT
        if message[start:end] not in haystack:
            start += 1
            continue
        while end < len(message) and message[start : end + 1] in haystack:
            end += 1
        spans.append((start, end))
        start = end
    for word in words:
        spans.extend(found.span() for found in re.finditer(rf"(?<!\w){re.escape(word)}(?!\w)", message))
    out, last = [], 0
    for begin, end in sorted(spans):  # overlapping spans make one <redacted>
        if begin >= last:
            out += [message[last:begin], _REDACTED]
        last = max(last, end)
    out.append(message[last:])
    return "".join(out)


def _private_texts(private: Sequence[object]) -> tuple[list[str], set[str]]:
    """The strings to redact: the leaf values (a container counts by its values, not its keys) of ``_FRAGMENT``
    characters or more in their plain, repr and JSON forms, and the shorter ones as words."""

    fragments: set[str] = set()
    words: set[str] = set()
    pending = list(private)
    while pending:
        value = pending.pop()
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, Mapping | list | tuple):
            pending.extend(value.values() if isinstance(value, Mapping) else value)
            continue
        text = value if isinstance(value, str) else str(value)
        fragments.update(
            form
            for form in (text, repr(text)[1:-1], json.dumps(text)[1:-1], json.dumps(text, ensure_ascii=False)[1:-1])
            if len(form) >= _FRAGMENT
        )
        if 0 < len(text.strip()) < _FRAGMENT:
            words.add(text.strip())
    return sorted(fragments), words


def _summary(
    run: records.RunRecord, step_id: str, suite: str, items: int, k: int, calls: int, metrics: Mapping[str, list[float]]
) -> list[str]:
    head = f"run {run.run_id}: step {step_id}, suite {suite}, {items} items x {k}, {calls} calls"
    if run.replay_of:
        head += f", replay of {run.replay_of}"
    return [head, *(_metric_line(name, values) for name, values in metrics.items())]


def _metric_line(name: str, values: Sequence[float]) -> str:
    mean = sum(values) / len(values) if values else float("nan")
    return f"  {name}: {mean:.4f} over {len(values)} traces"


__all__ = [
    "CallBudget",
    "CapReachedError",
    "JudgeNotValidatedError",
    "RunFailed",
    "RunRefused",
    "ScorerSpec",
    "Suite",
    "check_judge",
    "dataset_digest",
    "execute",
    "guard",
    "load_suite",
    "replay",
    "rescore",
    "rescore_command",
    "resolve_scorer",
    "run_command",
    "score_run",
]

"""``gigai <gig> eval label``: a blind page that asks a person met / not met / unknown about each traced row of a step.

The page shows a row's input and output, never which run, arm, model or target gave it, and stores each answer
in ``evals.sqlite``'s ``labels`` table with the labeller's login, the time, the rubric and a one-line critique.
``--export`` writes a step's labels as the labels JSONL format (``labels_format.md``) and ``--from`` imports such a
batch; :func:`validate_batch` is the format's one validator.
"""

from __future__ import annotations

import getpass
import json
import math
import os
import random
import re
import sqlite3
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import astuple, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from ..canonical import sha256_hex
from . import records

if TYPE_CHECKING:
    from ..sdk import GigContext

#: The keys of one labels JSONL row, in the documented order; a row has exactly these.
LABEL_KEYS = (
    "run_id",
    "item_id",
    "step_id",
    "label",
    "critique",
    "rubric",
    "who",
    "at",
    "round",
)
#: The answers a label can hold.
LABELS = ("met", "not met", "unknown")
#: The longest critique, id, login or rubric a label may carry, in characters.
FIELD_MAX = 200
#: The highest ``round`` a label may carry: a relabel is round 2, so this only keeps a number SQLite can hold.
ROUND_MAX = 1000
#: A row is met when every scorer's mean value on it is at least this (full marks).
MET = 1.0
#: The rubric the page asks under; stored in ``labels.rubric``.
PAGE_RUBRIC = "gigai-met-1"
RUBRIC_TEXT = "met: the output does what the step asks for this input; not met: it does not; unknown: you cannot tell."
#: What ``labels.source`` holds for a label a person gave through ``eval label``.
SOURCE = "human"

_IDENTITY = ("run_id", "item_id", "step_id", "who", "at", "round")
_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z")
_ANSWERS = {
    "m": "met",
    "met": "met",
    "n": "not met",
    "not met": "not met",
    "u": "unknown",
    "unknown": "unknown",
}
_QUIT = {"q", "quit"}


class LabelRefused(records.EvalsError):
    """The labels command refuses its input or options; nothing is written (exit 2)."""


@dataclass(frozen=True)
class LabelRow:
    """One row of the labels JSONL format."""

    run_id: str
    item_id: str
    step_id: str
    label: str
    critique: str
    rubric: str
    who: str
    at: str
    round: int

    @property
    def key(self) -> tuple[str, str, str, str, str, int]:
        """The row's identity: a second row with this key is the same label."""

        return (self.run_id, self.item_id, self.step_id, self.who, self.at, self.round)

    def as_json(self) -> str:
        return json.dumps(
            dict(zip(LABEL_KEYS, astuple(self), strict=True)),
            sort_keys=True,
            ensure_ascii=False,
        )


@dataclass(frozen=True)
class Row:
    """One traced row of a step a person can label: one run's answer to one item."""

    run_id: str
    item_id: str
    item_version: str
    dataset_id: str


@dataclass(frozen=True)
class Stored:
    """A label already in ``labels`` for a row of the step."""

    label_id: str
    run_id: str
    item_id: str
    who: str
    round: int
    relabel_of: str | None


def _select(
    _ctx: click.Context, _param: click.Parameter, value: str | None
) -> str | None:
    if value is None or value in ("contested", "below:met"):
        return value
    kind, _, count = value.partition(":")
    if kind == "random" and count.isdecimal() and int(count) >= 1:
        return f"random:{int(count)}"
    raise click.BadParameter("use contested, below:met or random:N (N >= 1)")


def _fraction(
    _ctx: click.Context, _param: click.Parameter, value: float | None
) -> float | None:
    if value is not None and not (math.isfinite(value) and 0.0 < value <= 1.0):
        raise click.BadParameter("must be a fraction above 0 and at most 1")
    return value


@click.command("label")
@click.option(
    "--step",
    "step_id",
    required=True,
    help="The [[evals.steps]] entry whose rows to label.",
)
@click.option(
    "--select",
    "select",
    callback=_select,
    metavar="MODE",
    help="contested, below:met or random:N.",
)
@click.option(
    "--relabel",
    type=float,
    callback=_fraction,
    metavar="FRACTION",
    help="Re-ask a seeded share of your labels.",
)
@click.option(
    "--export",
    "export_path",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write the step's labels as JSONL.",
)
@click.option(
    "--from",
    "from_path",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Import a labels JSONL batch.",
)
@click.pass_context
def label_command(
    ctx: click.Context,
    step_id: str,
    select: str | None,
    relabel: float | None,
    export_path: Path | None,
    from_path: Path | None,
) -> None:
    """Label a step's traced rows met / not met / unknown, blind to run, arm and model."""

    from ..sdk.store import StoreVersionError

    gig: GigContext = ctx.obj
    if export_path is not None and from_path is not None:
        raise click.UsageError("use --export or --from, not both")
    if (export_path is not None or from_path is not None) and (
        select is not None or relabel is not None
    ):
        raise click.UsageError(
            "--select and --relabel belong to the page, not to --export or --from"
        )
    try:
        _check_step(gig, step_id)
        if from_path is not None:
            lines = import_labels(gig, step_id, from_path)
        elif export_path is not None:
            lines = export_labels(gig, step_id, export_path)
        else:
            lines = page(gig, step_id, select, relabel)
    except (LabelRefused, StoreVersionError) as exc:
        click.echo(f"gigai {gig.name} eval label: {exc}", err=True)
        raise click.exceptions.Exit(2) from exc
    except (records.EvalsError, OSError, ValueError, sqlite3.Error) as exc:
        click.echo(f"gigai {gig.name} eval label: {exc}", err=True)
        raise click.exceptions.Exit(1) from exc
    for line in lines:
        click.echo(line)


# --- the labels JSONL format -----------------------------------------------------------------------------------


def validate_batch(text: str, step_id: str | None = None) -> list[LabelRow]:
    """The rows of labels JSONL ``text``, or :class:`LabelRefused` naming the first bad line and key.

    A row is a JSON object with exactly :data:`LABEL_KEYS`; any other key (``input``, say) or a missing one refuses
    the whole batch. A repeated row is read once; two rows with one key and different content refuse the batch.
    With ``step_id``, every row must be of that step. A message names lines and keys, never a value.
    """

    return [row for _number, row in _numbered_rows(text, step_id)]


def _numbered_rows(text: str, step_id: str | None) -> list[tuple[int, LabelRow]]:
    """:func:`validate_batch`, with each row's line number for the errors found after it."""

    rows: list[tuple[int, LabelRow]] = []
    seen: dict[tuple[str, str, str, str, str, int], tuple[int, LabelRow]] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        row = _row(number, line)
        if step_id is not None and row.step_id != step_id:
            raise LabelRefused(f"line {number}: step_id is not the --step")
        earlier = seen.get(row.key)
        if earlier is not None:
            if earlier[1] != row:
                raise LabelRefused(
                    f"line {number} has the key of line {earlier[0]} with a different label"
                )
            continue
        seen[row.key] = (number, row)
        rows.append((number, row))
    return rows


def _row(number: int, line: str) -> LabelRow:
    try:
        found = json.loads(
            line,
            object_pairs_hook=_unique_keys(number),
            parse_constant=_no_constant(number),
        )
    except LabelRefused:
        raise
    except ValueError:
        raise LabelRefused(f"line {number}: not a JSON object") from None
    if not isinstance(found, dict):
        raise LabelRefused(f"line {number}: not a JSON object")
    extra = sorted(key for key in found if key not in LABEL_KEYS)
    if extra:
        names = ", ".join(_name(key) for key in extra)
        raise LabelRefused(
            f"line {number}: key {names} is not in the labels format (keys: {', '.join(LABEL_KEYS)})"
        )
    missing = [key for key in LABEL_KEYS if key not in found]
    if missing:
        raise LabelRefused(f"line {number}: missing key {', '.join(missing)}")
    for key in ("run_id", "item_id", "step_id", "who", "rubric"):
        if not _short_text(found[key], empty=False):
            raise LabelRefused(
                f"line {number}: {key} must be a non-empty one-line string of at most {FIELD_MAX} characters"
            )
    if not _short_text(found["critique"], empty=True):
        raise LabelRefused(
            f"line {number}: critique must be a one-line string of at most {FIELD_MAX} characters"
        )
    if found["label"] not in LABELS:
        raise LabelRefused(f"line {number}: label must be one of {', '.join(LABELS)}")
    if not _utc_text(found["at"]):
        raise LabelRefused(
            f"line {number}: at must be a UTC time like 2026-01-31T09:30:00Z"
        )
    if type(found["round"]) is not int or not 1 <= found["round"] <= ROUND_MAX:
        raise LabelRefused(
            f"line {number}: round must be an integer from 1 to {ROUND_MAX}"
        )
    return LabelRow(*(found[key] for key in LABEL_KEYS))


def _unique_keys(number: int) -> Any:
    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        found: dict[str, Any] = {}
        for key, value in pairs:
            if key in found:
                raise LabelRefused(f"line {number}: key {_name(key)} appears twice")
            found[key] = value
        return found

    return hook


def _no_constant(number: int) -> Any:
    def refuse(_constant: str) -> None:
        raise LabelRefused(f"line {number}: NaN and Infinity are not JSON")

    return refuse


def _name(key: str) -> str:
    """A key as an error may name it: quoted, and cut short so a long key cannot carry text."""

    return repr(key if len(key) <= 40 else key[:40] + "...")


def _short_text(value: object, *, empty: bool) -> bool:
    return (
        isinstance(value, str)
        and (empty or value.strip() != "")
        and len(value) <= FIELD_MAX
        and not any(
            char in value for char in "\r\n\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"
        )
    )


def _utc_text(value: object) -> bool:
    if not isinstance(value, str) or _AT.fullmatch(value) is None:
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


# --- --from and --export ---------------------------------------------------------------------------------------


def import_labels(gig: GigContext, step_id: str, source: Path) -> list[str]:
    """``eval label --from source``: every row of the batch, or none; a row already stored is skipped."""

    try:
        text = source.read_bytes().decode("utf-8")
    except FileNotFoundError:
        raise LabelRefused(f"no labels file at {source}") from None
    except UnicodeDecodeError:
        raise LabelRefused(f"{source} is not UTF-8 text") from None
    rows = _numbered_rows(text, step_id)
    if not rows:
        raise LabelRefused(f"no rows in {source}")
    connection = _existing_store(gig)
    fresh: list[tuple[LabelRow, Row]] = []
    try:
        with records._transaction(connection):
            for number, row in rows:
                if _stored_same(connection, number, row):
                    continue
                fresh.append((row, _traced(connection, number, row)))
            for row, traced in sorted(
                fresh, key=lambda pair: (pair[0].round, pair[0].key)
            ):
                _insert(
                    connection, traced, row, relabel_of=_previous_round(connection, row)
                )
    finally:
        connection.close()
    return [
        f"imported {len(fresh)} labels of step {step_id} ({len(rows) - len(fresh)} already stored)"
    ]


def export_labels(gig: GigContext, step_id: str, target: Path) -> list[str]:
    """``eval label --export target``: step ``step_id``'s labels as labels JSONL, sorted by key; a new file only."""

    connection = _existing_store(gig)
    try:
        rows = _exportable(connection, step_id)
    finally:
        connection.close()
    if not rows:
        raise LabelRefused(f"no labels of step {step_id} to export")
    payload = "".join(row.as_json() + "\n" for row in rows).encode("utf-8")
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise LabelRefused(f"{target} exists; export writes a new file") from None
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return [f"exported {len(rows)} labels of step {step_id} to {target}"]


def _exportable(connection: sqlite3.Connection, step_id: str) -> list[LabelRow]:
    found = connection.execute(
        f"SELECT label, {', '.join(key for key in LABEL_KEYS if key != 'label')} FROM labels"
        " WHERE step_id = ? AND run_id IS NOT NULL AND critique IS NOT NULL AND rubric IS NOT NULL"
        " AND who IS NOT NULL AND at IS NOT NULL AND round IS NOT NULL",
        (step_id,),
    ).fetchall()
    rows = []
    for label, run_id, item_id, step, critique, rubric, who, at, round_ in found:
        value = json.loads(label)
        if value in LABELS:
            rows.append(
                LabelRow(
                    run_id, item_id, step, value, critique, rubric, who, at, round_
                )
            )
    return sorted(rows, key=lambda row: row.key)


def _stored_same(connection: sqlite3.Connection, number: int, row: LabelRow) -> bool:
    """Whether ``row`` is stored already; a stored label with its key and other content refuses the batch."""

    found = connection.execute(
        "SELECT label, critique, rubric FROM labels WHERE run_id = ? AND item_id = ? AND step_id = ? AND who = ?"
        " AND at = ? AND round = ?",
        row.key,
    ).fetchone()
    if found is None:
        return False
    if (json.loads(found[0]), found[1], found[2]) != (
        row.label,
        row.critique,
        row.rubric,
    ):
        raise LabelRefused(
            f"line {number} has the key of a stored label with a different label"
        )
    return True


def _traced(connection: sqlite3.Connection, number: int, row: LabelRow) -> Row:
    found = connection.execute(
        "SELECT t.item_version, r.dataset_id FROM traces t JOIN runs r ON r.run_id = t.run_id"
        " WHERE t.run_id = ? AND t.step_id = ? AND t.item_id = ? ORDER BY t.repeat_index, t.attempt_index LIMIT 1",
        (row.run_id, row.step_id, row.item_id),
    ).fetchone()
    if found is None:
        raise LabelRefused(
            f"line {number} names a run, step and item with no trace in evals.sqlite"
        )
    return Row(row.run_id, row.item_id, found[0], found[1])


def _previous_round(connection: sqlite3.Connection, row: LabelRow) -> str | None:
    if row.round == 1:
        return None
    found = connection.execute(
        "SELECT label_id FROM labels WHERE run_id = ? AND item_id = ? AND step_id = ? AND who = ? AND round = ?"
        " ORDER BY at, label_id LIMIT 1",
        (row.run_id, row.item_id, row.step_id, row.who, row.round - 1),
    ).fetchone()
    return None if found is None else str(found[0])


def _insert(
    connection: sqlite3.Connection,
    traced: Row,
    row: LabelRow,
    *,
    relabel_of: str | None,
) -> str:
    label_id = f"label_{uuid.uuid4()}"
    connection.execute(
        "INSERT INTO labels (label_id, dataset_id, item_id, item_version, label, source, recorded_at, run_id, step_id,"
        " critique, rubric, who, at, round, blind, relabel_of) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
        (
            label_id,
            traced.dataset_id,
            row.item_id,
            traced.item_version,
            json.dumps(row.label),
            SOURCE,
            _utc_now(),
            row.run_id,
            row.step_id,
            row.critique,
            row.rubric,
            row.who,
            row.at,
            row.round,
            relabel_of,
        ),
    )
    return label_id


# --- the page --------------------------------------------------------------------------------------------------


def page(
    gig: GigContext, step_id: str, select: str | None, relabel: float | None
) -> list[str]:
    """Ask, one row at a time, met / not met / unknown and a critique; each answer is stored as it is given."""

    who = _who()
    connection = _existing_store(gig)
    try:
        rows = selected_rows(connection, gig.name, step_id, select)
        mine = [label for label in _stored(connection, step_id) if label.who == who]
        if relabel is None:
            done = {(label.run_id, label.item_id) for label in mine}
            todo = [
                (row, None) for row in rows if (row.run_id, row.item_id) not in done
            ]
            click.echo(
                f"step {step_id}: {len(rows)} rows, {len(rows) - len(todo)} labelled by {who}, {len(todo)} to go."
            )
        else:
            todo = _relabel_todo(rows, mine, relabel, _seed(gig.name, step_id) + 1)
            click.echo(f"step {step_id}: {len(todo)} of your labels to give again.")
        click.echo(f"rubric: {RUBRIC_TEXT}")
        given = 0
        for position, (row, previous) in enumerate(todo, start=1):
            _show(connection, step_id, row, position, len(todo))
            answer = _ask_label()
            critique = None if answer is None else _ask_critique()
            if answer is None or critique is None:
                break
            label = LabelRow(
                row.run_id,
                row.item_id,
                step_id,
                answer,
                critique,
                PAGE_RUBRIC,
                who,
                _utc_now(),
                1 if previous is None else previous.round + 1,
            )
            with records._transaction(connection):
                _insert(
                    connection,
                    row,
                    label,
                    relabel_of=None if previous is None else previous.label_id,
                )
            given += 1
    finally:
        connection.close()
    return [f"{given} labelled, {len(todo) - given} left."]


def selected_rows(
    connection: sqlite3.Connection, gig_name: str, step_id: str, select: str | None
) -> list[Row]:
    """Step ``step_id``'s traced rows in the page's seeded order, narrowed by ``select``."""

    found = connection.execute(
        "SELECT t.run_id, t.item_id, min(t.item_version), r.dataset_id FROM traces t JOIN runs r ON r.run_id = t.run_id"
        " WHERE t.step_id = ? GROUP BY t.run_id, t.item_id ORDER BY t.run_id, t.item_id",
        (step_id,),
    ).fetchall()
    rows = [Row(*map(str, entry)) for entry in found]
    random.Random(_seed(gig_name, step_id)).shuffle(rows)
    if select is None:
        return rows
    if select.startswith("random:"):
        return rows[: int(select.partition(":")[2])]
    verdicts = row_verdicts(connection, step_id)
    if select == "below:met":
        keep = {
            key for key, by_scorer in verdicts.items() if not all(by_scorer.values())
        }
    else:
        keep = contested(verdicts)
    return [row for row in rows if (row.run_id, row.item_id) in keep]


def row_verdicts(
    connection: sqlite3.Connection, step_id: str
) -> dict[tuple[str, str], dict[str, bool]]:
    """Per (run, item) of the step, per scorer: whether the mean of its latest value on each trace is at least MET."""

    latest: dict[tuple[str, str], tuple[str, str, float]] = {}
    for trace_id, run_id, item_id, scorer_id, value in connection.execute(
        "SELECT s.trace_id, t.run_id, t.item_id, s.scorer_id, s.value FROM scores s JOIN traces t ON t.trace_id = s.trace_id"
        " WHERE t.step_id = ? ORDER BY s.recorded_at, s.rowid",
        (step_id,),
    ):
        latest[(trace_id, scorer_id)] = (run_id, item_id, float(value))
    values: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for (_trace, scorer_id), (run_id, item_id, value) in latest.items():
        values[(run_id, item_id)][scorer_id].append(value)
    return {
        key: {
            scorer: sum(found) / len(found) >= MET
            for scorer, found in by_scorer.items()
        }
        for key, by_scorer in values.items()
    }


def contested(
    verdicts: Mapping[tuple[str, str], Mapping[str, bool]],
) -> set[tuple[str, str]]:
    """The (run, item) rows whose scorers disagree on met, or whose item one scorer calls met in one run and not in
    another."""

    keep = {
        key for key, by_scorer in verdicts.items() if len(set(by_scorer.values())) > 1
    }
    across: dict[tuple[str, str], set[bool]] = defaultdict(set)
    for (_run, item_id), by_scorer in verdicts.items():
        for scorer, verdict in by_scorer.items():
            across[(item_id, scorer)].add(verdict)
    split = {key for key, found in across.items() if len(found) > 1}
    for (run_id, item_id), by_scorer in verdicts.items():
        if any((item_id, scorer) in split for scorer in by_scorer):
            keep.add((run_id, item_id))
    return keep


def _relabel_todo(
    rows: Sequence[Row], mine: Sequence[Stored], fraction: float, seed: int
) -> list[tuple[Row, Stored]]:
    """A seeded ``fraction`` (at least one) of this person's first labels on ``rows`` not yet given again."""

    by_key = {(row.run_id, row.item_id): row for row in rows}
    pool = sorted(
        (
            label
            for label in mine
            if label.relabel_of is None and (label.run_id, label.item_id) in by_key
        ),
        key=lambda label: (label.run_id, label.item_id, label.label_id),
    )
    random.Random(seed).shuffle(pool)
    picked = pool[: max(1, math.floor(fraction * len(pool) + 0.5))] if pool else []
    again = {label.relabel_of for label in mine if label.relabel_of is not None}
    return [
        (by_key[(label.run_id, label.item_id)], label)
        for label in picked
        if label.label_id not in again
    ]


def _stored(connection: sqlite3.Connection, step_id: str) -> list[Stored]:
    return [
        Stored(*entry)
        for entry in connection.execute(
            "SELECT label_id, run_id, item_id, who, round, relabel_of FROM labels"
            " WHERE step_id = ? AND run_id IS NOT NULL AND who IS NOT NULL AND round IS NOT NULL",
            (step_id,),
        )
    ]


def _show(
    connection: sqlite3.Connection, step_id: str, row: Row, position: int, total: int
) -> None:
    """Print the row's input and its outputs (the last attempt of each repeat); nothing names the run or model."""

    finals: dict[int, tuple[str, str, str]] = {}
    for repeat, text_in, text_out, outcome in connection.execute(
        "SELECT repeat_index, input, output_raw_text, outcome FROM traces WHERE run_id = ? AND step_id = ? AND item_id = ?"
        " ORDER BY repeat_index, attempt_index",
        (row.run_id, step_id, row.item_id),
    ):
        finals[repeat] = (text_in, text_out, outcome)
    outputs = [finals[repeat] for repeat in sorted(finals)]
    click.echo("")
    click.echo(f"--- row {position} of {total} ---")
    click.echo("input:")
    click.echo(outputs[0][0])
    for number, (_input, text_out, outcome) in enumerate(outputs, start=1):
        click.echo(
            "output:" if len(outputs) == 1 else f"output {number} of {len(outputs)}:"
        )
        click.echo(
            text_out if outcome == "succeeded" else "(the call failed; no output)"
        )


def _ask_label() -> str | None:
    """met, not met or unknown; ``None`` when the person quits or input ends."""

    while True:
        try:
            raw = click.prompt(
                "met, not met, unknown or q", default="", show_default=False
            )
        except click.Abort:
            return None
        answer = raw.strip().lower()
        if answer in _QUIT:
            return None
        if answer in _ANSWERS:
            return _ANSWERS[answer]
        click.echo("answer met (m), not met (n), unknown (u) or q to stop")


def _ask_critique() -> str | None:
    while True:
        try:
            raw = click.prompt("critique (one line)", default="", show_default=False)
        except click.Abort:
            return None
        critique = raw.strip()
        if _short_text(critique, empty=True):
            return critique
        click.echo(f"a critique is one line of at most {FIELD_MAX} characters")


# --- internals -------------------------------------------------------------------------------------------------


def _check_step(gig: GigContext, step_id: str) -> None:
    from ..gigs.eval_manifest import eval_steps

    if not any(step.get("id") == step_id for step in eval_steps(gig.manifest)):
        raise LabelRefused(f"no [[evals.steps]] entry has id {step_id!r}")


def _existing_store(gig: GigContext) -> sqlite3.Connection:
    """The gig's ``evals.sqlite`` opened to write; a gig with none yet is a refusal, not a new file."""

    if not records.evals_db_path(gig.name, gig.home).is_file():
        raise LabelRefused("this gig has no evals.sqlite yet; run `eval run` first")
    return records.open_evals_db(gig.name, gig.home)


def _who() -> str:
    """The labeller: ``$USER``, else the OS login."""

    try:
        who = os.environ.get("USER") or getpass.getuser()
    except (OSError, KeyError):
        who = ""
    if not _short_text(who, empty=False):
        raise LabelRefused("cannot tell who is labelling; set USER")
    return who


def _seed(gig_name: str, step_id: str) -> int:
    return int.from_bytes(
        bytes.fromhex(sha256_hex(f"{gig_name}\0{step_id}".encode()))[:8], "big"
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

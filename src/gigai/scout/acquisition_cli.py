"""Normal CLI for explicitly importing already-acquired public Scout rows."""

from __future__ import annotations

import json
from pathlib import Path
import click

from .acquisition_records import (
    ScoutAcquisitionError,
    import_public_rows,
    read_public_rows_file,
    read_public_acquisition_status,
    resume_public_acquisition,
)
from ..setup import default_home_root
from ..workpad import WorkpadError, resolve_workpad


@click.group("scout-acquisition")
def acquisition_group() -> None:
    """Import and inspect bounded public Scout acquisition batches."""


def _emit(value: object, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(value, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(json.dumps(value, sort_keys=True, indent=2))


def _resolve(home: Path | None, target: Path | None, gig: str):
    return resolve_workpad(
        home_root=home or default_home_root(), requested_target=target, gig_id=gig,
        allow_semantic_state=True,
    )


def _error(exc: Exception, as_json: bool, fallback: str) -> None:
    value = {"status": "error", "error": {"code": getattr(exc, "code", fallback), "message": str(exc)}}
    _emit(value, as_json)
    raise click.exceptions.Exit(1)


def _rows(path: Path) -> list[dict[str, object]]:
    return read_public_rows_file(path)


def _common_options(function):
    function = click.option("--json", "as_json", is_flag=True)(function)
    function = click.option("--home", type=click.Path(path_type=Path, file_okay=False))(function)
    function = click.option("--target", type=click.Path(path_type=Path, file_okay=False))(function)
    function = click.option("--gig", required=True)(function)
    return function


@acquisition_group.command("import")
@click.option("--batch-id", required=True)
@click.option("--rows-file", "rows_file", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--input-file", "rows_file_alias", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--deadline-seconds", type=float, default=30.0, show_default=True)
@_common_options
def import_command(batch_id: str, rows_file: Path, rows_file_alias: Path | None, deadline_seconds: float, gig: str, target: Path | None, home: Path | None, as_json: bool) -> None:
    """Persist one bounded batch of caller-supplied public rows."""
    path = rows_file_alias or rows_file
    if path is None:
        _error(ScoutAcquisitionError("acquisition_source_invalid", "provide --rows-file or --input-file"), as_json, "acquisition_source_invalid")
        return
    try:
        result = import_public_rows(resolved=_resolve(home, target, gig), batch_id=batch_id, rows=_rows(path), deadline_seconds=deadline_seconds)
    except (ScoutAcquisitionError, WorkpadError, OSError, ValueError) as exc:
        _error(exc, as_json, "acquisition_import_failed")
        return
    _emit({"ok": True, "result": result.to_json()}, as_json)


@acquisition_group.command("resume")
@click.option("--batch-id", required=True)
@click.option("--deadline-seconds", type=float, default=30.0, show_default=True)
@_common_options
def resume_command(batch_id: str, deadline_seconds: float, gig: str, target: Path | None, home: Path | None, as_json: bool) -> None:
    """Resume the exact committed input snapshot for a partial batch."""
    try:
        result = resume_public_acquisition(resolved=_resolve(home, target, gig), batch_id=batch_id, deadline_seconds=deadline_seconds)
    except (ScoutAcquisitionError, WorkpadError, OSError, ValueError) as exc:
        _error(exc, as_json, "acquisition_resume_failed")
        return
    _emit({"ok": True, "result": result.to_json()}, as_json)


@acquisition_group.command("status")
@click.option("--batch-id", required=True)
@_common_options
def status_command(batch_id: str, gig: str, target: Path | None, home: Path | None, as_json: bool) -> None:
    """Read one journal-authenticated batch and its cumulative outcome ledger."""
    try:
        result = read_public_acquisition_status(resolved=_resolve(home, target, gig), batch_id=batch_id)
    except (ScoutAcquisitionError, WorkpadError, OSError, ValueError) as exc:
        _error(exc, as_json, "acquisition_status_failed")
        return
    _emit({"ok": True, "result": result.to_json()}, as_json)


__all__ = ["acquisition_group", "import_command", "resume_command", "status_command"]

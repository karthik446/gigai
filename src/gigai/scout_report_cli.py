"""Standalone local Scout report commands for integration into ``gig.py``."""

from __future__ import annotations

import json
from pathlib import Path
import click

from .scout_report import ScoutReportError, publish_report, read_current_report
from .setup import default_home_root
from .workpad import WorkpadError, resolve_workpad


@click.group("scout-report")
def report_group() -> None:
    """Build or inspect the journal-derived local Scout tracker."""


def _emit(value: object, as_json: bool) -> None:
    click.echo(json.dumps(value, sort_keys=True, separators=(",", ":")) if as_json else value)


def _resolve(home: Path | None, target: Path | None, gig: str):
    return resolve_workpad(
        home_root=home or default_home_root(),
        requested_target=target,
        gig_id=gig,
        allow_semantic_state=True,
    )


@report_group.command("generate")
@click.option("--gig", required=True)
@click.option("--target", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", type=click.Path(path_type=Path, file_okay=False))
@click.option("--template", "template_path", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--json", "as_json", is_flag=True)
def generate(gig: str, target: Path | None, home: Path | None, template_path: Path | None, as_json: bool) -> None:
    """Generate a complete atomic report bundle from committed history."""
    try:
        result = publish_report(resolved=_resolve(home, target, gig), template_path=template_path)
    except (ScoutReportError, WorkpadError, OSError, ValueError) as exc:
        _emit({"status": "error", "error": {"code": getattr(exc, "code", "report_failed"), "message": str(exc)}}, as_json)
        raise click.exceptions.Exit(1)
    _emit(result, as_json)


@report_group.command("status")
@click.option("--gig", required=True)
@click.option("--target", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def status(gig: str, target: Path | None, home: Path | None, as_json: bool) -> None:
    """Inspect the selected report and whether its journal cursor is stale."""
    try:
        result = read_current_report(resolved=_resolve(home, target, gig))
    except (ScoutReportError, WorkpadError, OSError, ValueError) as exc:
        _emit({"status": "error", "error": {"code": getattr(exc, "code", "report_unavailable"), "message": str(exc)}}, as_json)
        raise click.exceptions.Exit(1)
    _emit(result, as_json)


__all__ = ["generate", "report_group", "status"]

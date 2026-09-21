from __future__ import annotations
import json
from pathlib import Path
import click
from .application_events import (
    ApplicationEventError,
    read_application,
    record_application,
)
from .canonical import parse_json_bytes
from .setup import default_home_root
from .workpad import resolve_workpad, WorkpadError


@click.group("application")
def application_group():
    """Record and inspect journal-authoritative application history."""


def _emit(payload, as_json):
    click.echo(
        json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if as_json
        else str(payload)
    )


@application_group.command("record")
@click.option("--gig", required=True)
@click.option(
    "--input",
    "input_path",
    type=click.Path(path_type=Path, dir_okay=False),
    required=True,
)
@click.option("--confirm", is_flag=True)
@click.option("--target", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def record(gig, input_path, confirm, target, home, as_json):
    try:
        data = parse_json_bytes(input_path.read_bytes())
        resolved = resolve_workpad(
            home_root=home or default_home_root(),
            requested_target=target,
            gig_id=gig,
            allow_semantic_state=True,
        )
        result = record_application(resolved=resolved, data=data, confirm=confirm)
        _emit(result, as_json)
    except (ApplicationEventError, WorkpadError, OSError, ValueError) as exc:
        payload = {
            "status": "error",
            "error": {
                "code": getattr(exc, "code", "application_event_invalid"),
                "message": str(exc),
            },
        }
        _emit(payload, as_json)
        raise click.exceptions.Exit(1)


@application_group.command("history")
@click.option("--gig", required=True)
@click.option("--opportunity")
@click.option("--target", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def history(gig, opportunity, target, home, as_json):
    try:
        resolved = resolve_workpad(
            home_root=home or default_home_root(),
            requested_target=target,
            gig_id=gig,
            allow_semantic_state=True,
        )
        _emit(read_application(resolved=resolved, opportunity_ref=opportunity), as_json)
    except (ApplicationEventError, WorkpadError, OSError, ValueError) as exc:
        _emit(
            {
                "status": "error",
                "error": {
                    "code": getattr(exc, "code", "application_history_unavailable"),
                    "message": str(exc),
                },
            },
            as_json,
        )
        raise click.exceptions.Exit(1)


@application_group.command("status")
@click.option("--gig", required=True)
@click.option("--opportunity")
@click.option("--target", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def status(gig, opportunity, target, home, as_json):
    return history.callback(gig, opportunity, target, home, as_json)

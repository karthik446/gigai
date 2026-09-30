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


def _normalized_external_ref(data):
    """``data`` with an http(s) ``external_ref`` normalized; anything else verbatim.

    uat-bug-018: Scout joins an event to a posting by exact match on the
    posting's normalized URL, so a URL typed with a tracking parameter or a
    trailing slash would never join. A ref that is not an http(s) URL (a
    pasted posting's ``text:sha256:...``, any other opaque string), or a URL
    the normalizer refuses, is recorded as given.
    """
    ref = data.get("external_ref") if isinstance(data, dict) else None
    if not isinstance(ref, str) or not ref.lower().startswith(("http://", "https://")):
        return data
    # Imported here: the posting URL rule is Scout's, and only this command
    # needs it.
    from .scout.find_jobs.contracts import normalize_url

    try:
        normalized = normalize_url(ref)
    except ValueError:
        return data
    return {**data, "external_ref": normalized}


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
        data = _normalized_external_ref(parse_json_bytes(input_path.read_bytes()))
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

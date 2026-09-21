"""Independently mountable ``gigai external`` Click command group."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Callable

import click

from . import external_recording as service
from .setup import default_home_root


def _emit(value: object, as_json: bool) -> None:
    click.echo(
        json.dumps(value, sort_keys=True, separators=(",", ":"))
        if as_json
        else json.dumps(value, indent=2, sort_keys=True)
    )


def _input(path: Path | None, stdin: bool) -> dict[str, object]:
    if (path is None) == (not stdin):
        raise service.ExternalRecordingError(
            "external_invocation_invalid",
            "choose exactly one of --invocation or --stdin",
        )
    try:
        raw = sys.stdin.buffer.read(262145) if stdin else path.read_bytes()  # type: ignore[union-attr]
        if len(raw) > 262144:
            raise ValueError
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise service.ExternalRecordingError(
            "external_invocation_invalid", "invocation must be bounded UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise service.ExternalRecordingError(
            "external_invocation_invalid", "invocation must be a JSON object"
        )
    return value


def _call(
    function: Callable[..., object],
    *,
    home: Path | None,
    target: Path | None,
    gig: str,
    invocation: Path | None,
    stdin: bool,
    as_json: bool,
    protocol_version: str = "1",
) -> None:
    try:
        version = int(protocol_version)
        if version not in {1, 2}:
            raise service.ExternalRecordingError(
                "external_protocol_unsupported",
                "external protocol version is unsupported",
                next_action="use_supported_external_protocol",
            )
        if version == 2:
            v2_function = getattr(service, f"{function.__name__}_v2", None)
            if v2_function is None:
                raise service.ExternalRecordingError(
                    "external_protocol_unsupported",
                    "operation does not support external protocol version 2",
                    next_action="use_supported_external_protocol",
                )
            function = v2_function
        result = function(
            home_root=home or default_home_root(),
            requested_target=target,
            gig_id=gig,
            envelope=_input(invocation, stdin),
        )
    except service.ExternalRecordingError as exc:
        _emit(exc.result(), True)
        raise click.exceptions.Exit(1) from exc
    payload = result.payload if isinstance(result, service.ExternalResult) else result
    _emit(payload, as_json)


@click.group("external")
def external_group() -> None:
    """Record externally performed work under an approved Gig; no provider runs."""


@external_group.command("graphs")
@click.option("--gig", required=True)
@click.option("--target", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def graphs_command(
    gig: str, target: Path | None, home: Path | None, as_json: bool
) -> None:
    try:
        _emit(
            {
                "graphs": service.graphs(
                    home_root=home or default_home_root(),
                    requested_target=target,
                    gig_id=gig,
                )
            },
            as_json,
        )
    except service.ExternalRecordingError as exc:
        _emit(exc.result(), True)
        raise click.exceptions.Exit(1) from exc


@external_group.command("requirements")
@click.option("--gig", required=True)
@click.option("--graph", "graph_selector", required=True)
@click.option("--target", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def requirements_command(
    gig: str, graph_selector: str, target: Path | None, home: Path | None, as_json: bool
) -> None:
    try:
        _emit(
            service.requirements(
                home_root=home or default_home_root(),
                requested_target=target,
                gig_id=gig,
                graph_selector=graph_selector,
            ),
            as_json,
        )
    except service.ExternalRecordingError as exc:
        _emit(exc.result(), True)
        raise click.exceptions.Exit(1) from exc


def _writer(name: str) -> None:
    function = getattr(service, name)
    command = external_group.command(name)(
        click.option("--gig", required=True)(
            click.option(
                "--invocation", type=click.Path(path_type=Path, dir_okay=False)
            )(
                click.option("--stdin", "stdin", is_flag=True)(
                    click.option(
                        "--target", type=click.Path(path_type=Path, file_okay=False)
                    )(
                        click.option(
                            "--home", type=click.Path(path_type=Path, file_okay=False)
                        )(
                            click.option("--json", "as_json", is_flag=True)(
                                click.option(
                                    "--protocol-version",
                                    type=click.Choice(("1", "2")),
                                    default="1",
                                    show_default=True,
                                )(
                                lambda gig,
                                invocation,
                                stdin,
                                target,
                                home,
                                as_json,
                                protocol_version: _call(
                                    function,
                                    home=home,
                                    target=target,
                                    gig=gig,
                                    invocation=invocation,
                                    stdin=stdin,
                                    as_json=as_json,
                                    protocol_version=protocol_version,
                                )
                                )
                            )
                        )
                    )
                )
            )
        )
    )
    command.__name__ = f"{name}_command"


for _name in ("plan", "start", "checkpoint", "submit", "cancel"):
    _writer(_name)


@external_group.command("inspect")
@click.option("--gig", required=True)
@click.option("--run", "run_id")
@click.option("--target", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def inspect_command(
    gig: str, run_id: str | None, target: Path | None, home: Path | None, as_json: bool
) -> None:
    try:
        _emit(
            service.inspect(
                home_root=home or default_home_root(),
                requested_target=target,
                gig_id=gig,
                run_id=run_id,
            ),
            as_json,
        )
    except service.ExternalRecordingError as exc:
        _emit(exc.result(), True)
        raise click.exceptions.Exit(1) from exc

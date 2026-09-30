"""``gigai secrets add|list|rm`` — local operator secret management.

Values are read from a hidden, non-echoing prompt (or one line from stdin
with ``--stdin``, for scripts/tests) and are never echoed, logged, or
included in ``--json`` output. ``list`` reports only whether a known
service's variable is set, and if so whether it currently resolves from the
process environment or from the local secrets store — never the value.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import click

from . import secrets_store
from .secrets_catalog import UnknownServiceError, env_var_for, known_services
from .setup import default_home_root


def _emit(payload: dict[str, object], as_json: bool, plain: str) -> None:
    click.echo(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) if as_json else plain
    )


def _fail(exc: Exception, *, as_json: bool) -> None:
    code = getattr(exc, "code", "secret_invalid")
    if as_json:
        click.echo(
            json.dumps(
                {"status": "error", "error": {"code": code, "message": str(exc)}},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        raise click.exceptions.Exit(1)
    raise click.ClickException(str(exc))


@click.group("secrets", help="Store and inspect local provider API keys.")
def secrets_group() -> None:
    pass


@secrets_group.command("add")
@click.argument("service")
@click.option(
    "--stdin",
    "from_stdin",
    is_flag=True,
    help="Read the secret value as one line from stdin instead of prompting.",
)
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def add_command(service: str, from_stdin: bool, home_value: Path | None, as_json: bool) -> None:
    try:
        env_var = env_var_for(service)
    except UnknownServiceError as exc:
        _fail(exc, as_json=as_json)
        return

    if from_stdin:
        value = sys.stdin.readline().rstrip("\n")
    else:
        value = click.prompt(
            f"Value for {service} ({env_var})",
            hide_input=True,
            confirmation_prompt=True,
        )

    if not value:
        _fail(ValueError(f"{service!r} requires a non-empty value"), as_json=as_json)
        return

    home_root = home_value or default_home_root()
    secrets_store.set(env_var, value, home_root=home_root)
    _emit({"ok": True, "service": service}, as_json, f"Stored secret for {service}.")


@secrets_group.command("list")
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def list_command(home_value: Path | None, as_json: bool) -> None:
    home_root = home_value or default_home_root()
    stored = secrets_store.names_set(home_root=home_root)
    rows = []
    for service in known_services():
        env_var = env_var_for(service)
        environ_value = os.environ.get(env_var)
        in_environ = bool(environ_value and environ_value.strip())
        in_store = env_var in stored
        if in_environ:
            source = "environment"
        elif in_store:
            source = ".env"
        else:
            source = None
        rows.append(
            {
                "service": service,
                "env_var": env_var,
                "set": in_environ or in_store,
                "source": source,
            }
        )

    if as_json:
        _emit({"secrets": rows}, True, "")
        return

    lines = [
        f"{row['service']}: {'set (' + row['source'] + ')' if row['set'] else 'unset'}"
        for row in rows
    ]
    click.echo("\n".join(lines))


@secrets_group.command("rm")
@click.argument("service")
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def rm_command(service: str, home_value: Path | None, as_json: bool) -> None:
    try:
        env_var = env_var_for(service)
    except UnknownServiceError as exc:
        _fail(exc, as_json=as_json)
        return

    home_root = home_value or default_home_root()
    secrets_store.remove(env_var, home_root=home_root)
    _emit({"ok": True, "service": service}, as_json, f"Removed secret for {service}.")


__all__ = ["secrets_group"]

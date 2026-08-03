"""GigAI's installed command surface, expanded only by approved goals."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import click

from .config import ConfigurationError, CredentialReference, load_config
from .diagnostics import render_report_json, run_doctor
from .setup import (
    build_config,
    default_home_root,
    default_workpad_root,
    resolve_editor_argv,
    run_setup,
)


@click.group(
    invoke_without_command=True,
    context_settings={"help_option_names": ["--help"]},
    help=(
        "Configure and diagnose this contract-first GigAI installation. "
        "No target or Gig workpad commands are implemented yet."
    ),
)
@click.version_option(
    package_name="gigai",
    prog_name="gigai",
    message="%(prog)s %(version)s",
)
@click.pass_context
def cli(context: click.Context) -> None:
    """Expose only goal-approved, independently useful operations."""

    if context.invoked_subcommand is None:
        raise click.UsageError("Choose 'setup' or 'doctor'; use --help for details.")


@cli.command("setup")
@click.option("--non-interactive", is_flag=True, help="Refuse prompts and use explicit options.")
@click.option(
    "--home",
    "home_value",
    type=click.Path(path_type=Path, file_okay=False),
    help="GigAI machine-state directory (default: GIGAI_HOME or ~/.gigai).",
)
@click.option(
    "--workpad-root",
    type=click.Path(path_type=Path, file_okay=False),
    help="Authoritative workpad mount; never silently replaced by a default.",
)
@click.option("--editor", help="Editor executable; stored as argv, never a shell command.")
@click.option("--editor-arg", multiple=True, help="One literal editor argv item; repeat as needed.")
@click.option(
    "--open-with-target/--no-open-with-target",
    default=None,
    help="Record whether later open operations should include the target.",
)
@click.option(
    "--credential-ref",
    multiple=True,
    metavar="NAME=KIND:REFERENCE",
    help="Record an environment or secret-manager reference, never a value.",
)
@click.option(
    "--clear-credentials",
    is_flag=True,
    help="Explicitly remove all credential references; values are never accessed.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit a stable machine-readable summary.")
def setup_command(
    non_interactive: bool,
    home_value: Path | None,
    workpad_root: Path | None,
    editor: str | None,
    editor_arg: tuple[str, ...],
    open_with_target: bool | None,
    credential_ref: tuple[str, ...],
    clear_credentials: bool,
    as_json: bool,
) -> None:
    """Create or update local config and the deterministic standard pack."""

    _require_supported_platform()
    requested_home = (home_value or default_home_root()).expanduser().resolve(strict=False)
    existing = None
    if (requested_home / "config.toml").exists():
        try:
            existing = load_config(requested_home)
        except ConfigurationError as exc:
            raise click.ClickException(str(exc)) from exc

    if non_interactive:
        resolved_workpad = workpad_root or (
            existing.workpad_root if existing else default_workpad_root(requested_home)
        )
        resolved_editor = resolve_editor_argv(
            editor or (existing.editor_argv[0] if existing else None),
            (
                editor_arg
                if editor is not None or editor_arg
                else existing.editor_argv[1:] if existing else ()
            ),
        )
        resolved_open = (
            open_with_target
            if open_with_target is not None
            else existing.open_with_target if existing else False
        )
    else:
        requested_home = Path(
            click.prompt("GigAI home", default=os.fspath(requested_home), show_default=True)
        ).expanduser().resolve(strict=False)
        if existing is not None and existing.home_root != requested_home:
            existing = None
        if existing is None and (requested_home / "config.toml").exists():
            try:
                existing = load_config(requested_home)
            except ConfigurationError as exc:
                raise click.ClickException(str(exc)) from exc
        default_workpad = workpad_root or (
            existing.workpad_root if existing else default_workpad_root(requested_home)
        )
        resolved_workpad = Path(
            click.prompt("Authoritative workpad root", default=os.fspath(default_workpad))
        ).expanduser().resolve(strict=False)
        default_editor = editor or (existing.editor_argv[0] if existing else None)
        environment_editor_args: tuple[str, ...] = ()
        if default_editor is None:
            configured_environment_editor = (
                os.environ.get("VISUAL") or os.environ.get("EDITOR")
            )
            if configured_environment_editor:
                environment_editor = resolve_editor_argv(None)
                default_editor = environment_editor[0]
                environment_editor_args = environment_editor[1:]
        resolved_editor = resolve_editor_argv(
            click.prompt("Editor executable", default=default_editor, show_default=True),
            (
                editor_arg
                if editor is not None or editor_arg
                else existing.editor_argv[1:] if existing else environment_editor_args
            ),
        )
        resolved_open = click.confirm(
            "Open workpads with their target later?",
            default=(
                open_with_target
                if open_with_target is not None
                else existing.open_with_target if existing else False
            ),
        )

    try:
        if clear_credentials and credential_ref:
            raise ValueError("--clear-credentials cannot be combined with --credential-ref")
        credentials = tuple(_parse_credential_reference(value) for value in credential_ref)
        if clear_credentials:
            credentials = ()
        elif existing and not credential_ref:
            credentials = existing.credentials
        if not non_interactive:
            credential_summary = [
                {"name": item.name, "kind": item.kind} for item in credentials
            ]
            click.echo(f"Home: {requested_home}")
            click.echo(f"Authoritative workpad root: {resolved_workpad}")
            click.echo(f"Editor argv: {json.dumps(resolved_editor)}")
            click.echo(f"Credential references: {json.dumps(credential_summary)}")
            click.echo("Offline endpoint: offline (deterministic)")
            click.echo("Model target: offline-default (fixture-v1)")
            click.echo("Profile: default")
            click.echo("Standard pack: standard version 1")
            if not click.confirm("Apply this setup?", default=True):
                raise click.Abort()
        config = build_config(
            home_root=requested_home,
            workpad_root=resolved_workpad,
            editor_argv=resolved_editor,
            open_with_target=resolved_open,
            credentials=credentials,
        )
        result = run_setup(config)
    except (ConfigurationError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    payload = {
        "schema_version": result.config.schema_version,
        "home_root": os.fspath(result.config.home_root),
        "workpad_root": os.fspath(result.config.workpad_root),
        "config_changed": result.config_changed,
        "standard_pack_changed": result.pack_changed,
        "mount_checks": [
            {"id": check.id, "status": check.status} for check in result.mount_checks
        ],
    }
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        changed = "updated" if result.config_changed else "unchanged"
        click.echo(f"GigAI setup complete; configuration {changed}.")
        click.echo(f"Authoritative workpad root: {result.config.workpad_root}")


@cli.command("doctor")
@click.option(
    "--home",
    "home_value",
    type=click.Path(path_type=Path, file_okay=False),
    help="GigAI machine-state directory (default: GIGAI_HOME or ~/.gigai).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit stable structured diagnostics.")
def doctor_command(home_value: Path | None, as_json: bool) -> None:
    """Run offline, zero-token installation and configured-mount checks."""

    _require_supported_platform()
    report = run_doctor((home_value or default_home_root()).expanduser().resolve(strict=False))
    if as_json:
        click.echo(render_report_json(report), nl=False)
    else:
        for check in report.checks:
            click.echo(f"{check.status:4} {check.id}: {check.summary}")
        click.echo(f"Overall: {report.overall_status}")
    if report.overall_status == "FAIL":
        raise click.exceptions.Exit(1)


def _parse_credential_reference(value: str) -> CredentialReference:
    try:
        name, locator = value.split("=", 1)
        kind, reference = locator.split(":", 1)
    except ValueError as exc:
        raise click.BadParameter(
            "credential references use NAME=environment:ENV_VAR or "
            "NAME=secret-manager:LOCATOR"
        ) from exc
    if not name or not kind or not reference:
        raise click.BadParameter("credential reference components must not be empty")
    return CredentialReference(name=name, kind=kind, reference=reference)


def _require_supported_platform() -> None:
    if sys.platform != "darwin" and not sys.platform.startswith("linux"):
        raise click.ClickException(
            "unsupported_platform: GigAI v1 setup and doctor require macOS or Linux"
        )

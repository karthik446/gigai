"""GigAI's installed command surface, expanded only by approved goals."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from dataclasses import replace

import click

from .config import (
    ConfigurationError,
    CredentialReference,
    Endpoint,
    ModelTarget,
    load_config,
    migrate_config,
)
from .diagnostics import render_report_json, run_doctor, run_live_doctor
from .lifecycle import (
    LifecycleError,
    approve_offline,
    create_offline,
    record_feedback,
    reject_offline,
    revise_offline,
)
from .setup import (
    build_config,
    default_home_root,
    default_workpad_root,
    resolve_editor_argv,
    run_setup,
)
from .target_binding import TargetBindingError, initialize_target
from .validators import validate_proposal_workpad
from .workpad import WorkpadError, open_locations, resolve_workpad


@click.group(
    invoke_without_command=True,
    context_settings={"help_option_names": ["--help"]},
    help=(
        "Configure, diagnose, and bind targets for this contract-first GigAI "
        "installation. Resolve and open only already-provisioned Gig workpads."
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
        raise click.UsageError(
            "Choose 'setup', 'doctor', 'init', 'create', 'feedback', 'revise', "
            "'approve', 'reject', 'workpad', 'check', or 'open'; "
            "use --help for details."
        )


@cli.command("setup")
@click.option(
    "--non-interactive", is_flag=True, help="Refuse prompts and use explicit options."
)
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
@click.option(
    "--editor", help="Editor executable; stored as argv, never a shell command."
)
@click.option(
    "--editor-arg",
    multiple=True,
    help="One literal editor argv item; repeat as needed.",
)
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
@click.option(
    "--endpoint",
    "endpoint_spec",
    multiple=True,
    metavar="NAME=ADAPTER:CREDENTIAL[:HTTPS_BASE_URL]",
    help="Add a remote endpoint by credential reference name, never a credential value.",
)
@click.option(
    "--model-target",
    "model_target_spec",
    multiple=True,
    metavar="NAME=ENDPOINT:MODEL",
    help="Add a text model target resolved through a configured endpoint.",
)
@click.option(
    "--target-output-limit",
    "target_output_limit_spec",
    multiple=True,
    metavar="TARGET=MAX_OUTPUT_TOKENS",
    help="Set the explicit maximum output length for a model target.",
)
@click.option(
    "--target-reasoning-effort",
    "target_reasoning_effort_spec",
    multiple=True,
    metavar="TARGET=none|low|medium|high|xhigh|max",
    help="Set a provider-supported reasoning effort for a model target.",
)
@click.option(
    "--json", "as_json", is_flag=True, help="Emit a stable machine-readable summary."
)
def setup_command(
    non_interactive: bool,
    home_value: Path | None,
    workpad_root: Path | None,
    editor: str | None,
    editor_arg: tuple[str, ...],
    open_with_target: bool | None,
    credential_ref: tuple[str, ...],
    clear_credentials: bool,
    endpoint_spec: tuple[str, ...],
    model_target_spec: tuple[str, ...],
    target_output_limit_spec: tuple[str, ...],
    target_reasoning_effort_spec: tuple[str, ...],
    as_json: bool,
) -> None:
    """Create or update local config and the deterministic standard pack."""

    _require_supported_platform()
    requested_home = (
        (home_value or default_home_root()).expanduser().resolve(strict=False)
    )
    existing = None
    if (requested_home / "config.toml").exists():
        try:
            existing = load_config(requested_home)
        except ConfigurationError as exc:
            try:
                existing, _ = migrate_config(requested_home)
            except ConfigurationError:
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
                else existing.editor_argv[1:]
                if existing
                else ()
            ),
        )
        resolved_open = (
            open_with_target
            if open_with_target is not None
            else existing.open_with_target
            if existing
            else False
        )
    else:
        requested_home = (
            Path(
                click.prompt(
                    "GigAI home", default=os.fspath(requested_home), show_default=True
                )
            )
            .expanduser()
            .resolve(strict=False)
        )
        if existing is not None and existing.home_root != requested_home:
            existing = None
        if existing is None and (requested_home / "config.toml").exists():
            try:
                existing = load_config(requested_home)
            except ConfigurationError as exc:
                try:
                    existing, _ = migrate_config(requested_home)
                except ConfigurationError:
                    raise click.ClickException(str(exc)) from exc
        default_workpad = workpad_root or (
            existing.workpad_root if existing else default_workpad_root(requested_home)
        )
        resolved_workpad = (
            Path(
                click.prompt(
                    "Authoritative workpad root", default=os.fspath(default_workpad)
                )
            )
            .expanduser()
            .resolve(strict=False)
        )
        default_editor = editor or (existing.editor_argv[0] if existing else None)
        environment_editor_args: tuple[str, ...] = ()
        if default_editor is None:
            configured_environment_editor = os.environ.get("VISUAL") or os.environ.get(
                "EDITOR"
            )
            if configured_environment_editor:
                environment_editor = resolve_editor_argv(None)
                default_editor = environment_editor[0]
                environment_editor_args = environment_editor[1:]
        resolved_editor = resolve_editor_argv(
            click.prompt(
                "Editor executable", default=default_editor, show_default=True
            ),
            (
                editor_arg
                if editor is not None or editor_arg
                else existing.editor_argv[1:]
                if existing
                else environment_editor_args
            ),
        )
        resolved_open = click.confirm(
            "Open workpads with their target later?",
            default=(
                open_with_target
                if open_with_target is not None
                else existing.open_with_target
                if existing
                else False
            ),
        )

    try:
        if clear_credentials and credential_ref:
            raise ValueError(
                "--clear-credentials cannot be combined with --credential-ref"
            )
        credentials = tuple(
            _parse_credential_reference(value) for value in credential_ref
        )
        if clear_credentials:
            credentials = ()
        elif existing and not credential_ref:
            credentials = existing.credentials
        endpoint_specs = tuple(_parse_endpoint_spec(value) for value in endpoint_spec)
        output_limits = _parse_target_output_limits(target_output_limit_spec)
        reasoning_efforts = _parse_target_reasoning_efforts(
            target_reasoning_effort_spec
        )
        target_specs = tuple(
            _parse_model_target_spec(value, output_limits, reasoning_efforts)
            for value in model_target_spec
        )
        existing_endpoints = (
            existing.endpoints
            if existing is not None
            else (Endpoint(name="offline", adapter="deterministic"),)
        )
        existing_targets = (
            existing.model_targets
            if existing is not None
            else (
                ModelTarget(
                    name="offline-default",
                    endpoint="offline",
                    model="fixture-v1",
                    capabilities=("text",),
                    max_output_tokens=64,
                    reasoning_effort=None,
                ),
            )
        )
        existing_target_names = {item.name for item in existing_targets}
        added_target_names = {item.name for item in target_specs}
        unknown_limits = set(output_limits) - existing_target_names - added_target_names
        unknown_efforts = (
            set(reasoning_efforts) - existing_target_names - added_target_names
        )
        if unknown_limits or unknown_efforts:
            raise ValueError(
                "target output limits or reasoning efforts reference no configured or newly "
                f"added target: {sorted(unknown_limits | unknown_efforts)}"
            )
        targets = (
            tuple(
                replace(
                    target,
                    max_output_tokens=output_limits.get(
                        target.name, target.max_output_tokens
                    ),
                    reasoning_effort=reasoning_efforts.get(
                        target.name, target.reasoning_effort
                    ),
                )
                if target.name in output_limits or target.name in reasoning_efforts
                else target
                for target in existing_targets
            )
            + target_specs
        )
        endpoints = (*existing_endpoints, *endpoint_specs)
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
            endpoints=endpoints,
            model_targets=targets,
            profiles=existing.profiles if existing is not None else None,
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
@click.option(
    "--json", "as_json", is_flag=True, help="Emit stable structured diagnostics."
)
@click.option(
    "--live",
    is_flag=True,
    help="Explicitly make one budget-bounded local provider diagnostic call.",
)
@click.option(
    "--model-target",
    help="Configured remote model target required with --live.",
)
def doctor_command(
    home_value: Path | None, as_json: bool, live: bool, model_target: str | None
) -> None:
    """Run offline, zero-token installation and configured-mount checks."""

    _require_supported_platform()
    if live != (model_target is not None):
        raise click.UsageError("--live and --model-target must be supplied together")
    home_root = (home_value or default_home_root()).expanduser().resolve(strict=False)
    report = (
        run_live_doctor(home_root, model_target)
        if live and model_target is not None
        else run_doctor(home_root)
    )
    if as_json:
        click.echo(render_report_json(report), nl=False)
    else:
        for check in report.checks:
            click.echo(f"{check.status:4} {check.id}: {check.summary}")
        click.echo(f"Overall: {report.overall_status}")
    if report.overall_status == "FAIL":
        raise click.exceptions.Exit(1)


@cli.command("init")
@click.option(
    "--target",
    type=click.Path(path_type=Path, file_okay=False),
    help=(
        "Target directory. Defaults to the current Git repository; an explicit "
        "path is required for non-Git targets."
    ),
)
@click.option(
    "--home",
    "home_value",
    type=click.Path(path_type=Path, file_okay=False),
    help="GigAI machine-state directory (default: GIGAI_HOME or ~/.gigai).",
)
@click.option(
    "--json", "as_json", is_flag=True, help="Emit a path-free result summary."
)
def init_command(target: Path | None, home_value: Path | None, as_json: bool) -> None:
    """Bind one target without creating a Gig, workpad, journal, or remote."""

    _require_supported_platform()
    try:
        result = initialize_target(
            home_root=(home_value or default_home_root()),
            requested_target=target,
        )
    except (TargetBindingError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc

    payload = {
        "binding_created": result.binding_created,
        "exclude_changed": result.exclude_changed,
        "project_id": result.project_id,
        "reconciled": result.reconciled,
        "registry_changed": result.registry_changed,
        "target_kind": result.target_kind,
        "workpad_locator": f"registry:{result.project_id}",
    }
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        disposition = "created" if result.binding_created else "confirmed"
        click.echo(
            f"GigAI target binding {disposition}: {result.project_id} "
            f"({result.target_kind})."
        )
        if result.reconciled:
            click.echo("Derived registry or exclude state was reconciled.")


@cli.command("create")
@click.argument("name")
@click.option(
    "--commission", help="Human-readable commission recorded in the proposal."
)
@click.option(
    "--target",
    "target_value",
    type=click.Path(path_type=Path, file_okay=False),
    help="Explicit target path; defaults to the current Git repository.",
)
@click.option(
    "--home",
    "home_value",
    type=click.Path(path_type=Path, file_okay=False),
    help="GigAI machine-state directory (default: GIGAI_HOME or ~/.gigai).",
)
@click.option(
    "--model-target",
    default="offline-default",
    show_default=True,
    help="Configured deterministic model target used only for offline fixture drafting.",
)
@click.option(
    "--open/--no-open",
    "open_editor",
    default=True,
    help="Open the proposal for review.",
)
@click.option(
    "--json", "as_json", is_flag=True, help="Emit a stable path-safe result summary."
)
def create_command(
    name: str,
    commission: str | None,
    target_value: Path | None,
    home_value: Path | None,
    model_target: str,
    open_editor: bool,
    as_json: bool,
) -> None:
    """Create one offline, review-only Gig Proposal and stop for approval."""

    _require_supported_platform()
    try:
        result = create_offline(
            home_root=home_value or default_home_root(),
            requested_target=target_value,
            name=name,
            commission=commission,
            model_target=model_target,
            open_editor=open_editor,
        )
    except (LifecycleError, WorkpadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    payload = {
        "gig_id": result.gig_id,
        "project_id": result.project_id,
        "proposal_id": result.proposal_id,
        "resumed": result.resumed,
        "status": "proposed",
    }
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(
            f"Gig proposal {result.proposal_id} is ready for operator review; "
            "no Gig version or Run was created."
        )


@cli.command("feedback")
@click.argument("proposal_id")
@click.option(
    "--text", required=True, help="Exact operator feedback to preserve in the journal."
)
@click.option(
    "--target", "target_value", type=click.Path(path_type=Path, file_okay=False)
)
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
def feedback_command(
    proposal_id: str, text: str, target_value: Path | None, home_value: Path | None
) -> None:
    """Record verbatim operator feedback for one pending proposal."""

    _require_supported_platform()
    try:
        entry = record_feedback(
            home_root=home_value or default_home_root(),
            requested_target=target_value,
            proposal_id=proposal_id,
            feedback=text,
        )
    except (LifecycleError, WorkpadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Recorded feedback in journal sequence {entry.sequence}.")


@cli.command("revise")
@click.argument("proposal_id")
@click.option(
    "--change",
    "change_request",
    required=True,
    help="Explicit change request for the new proposal.",
)
@click.option(
    "--target", "target_value", type=click.Path(path_type=Path, file_okay=False)
)
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option(
    "--json", "as_json", is_flag=True, help="Emit the new canonical proposal ID."
)
def revise_command(
    proposal_id: str,
    change_request: str,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """Produce a new validated proposal linked to a prior pending proposal."""

    _require_supported_platform()
    try:
        result = revise_offline(
            home_root=home_value or default_home_root(),
            requested_target=target_value,
            proposal_id=proposal_id,
            change_request=change_request,
        )
    except (LifecycleError, WorkpadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(
            json.dumps(
                {
                    "gig_id": result.gig_id,
                    "parent_proposal_id": result.parent_proposal_id,
                    "proposal_id": result.proposal_id,
                    "status": "proposed",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    else:
        click.echo(
            f"Proposal {result.proposal_id} supersedes {result.parent_proposal_id} for review."
        )


@cli.command("approve")
@click.argument("proposal_id")
@click.option(
    "--target", "target_value", type=click.Path(path_type=Path, file_okay=False)
)
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option(
    "--json", "as_json", is_flag=True, help="Emit the sealed version and commit IDs."
)
def approve_command(
    proposal_id: str, target_value: Path | None, home_value: Path | None, as_json: bool
) -> None:
    """Seal one pending proposal as an offline approved Gig version."""

    _require_supported_platform()
    try:
        result = approve_offline(
            home_root=home_value or default_home_root(),
            requested_target=target_value,
            proposal_id=proposal_id,
        )
    except (LifecycleError, WorkpadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    payload = {
        "gig_id": result.gig_id,
        "proposal_id": result.proposal_id,
        "sealed_commit": result.sealed_commit,
        "status": "approved",
        "tag": result.tag,
        "version": result.version,
    }
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(
            f"Approved {result.proposal_id} as {result.tag}; no Run was started."
        )


@cli.command("reject")
@click.argument("proposal_id")
@click.option(
    "--reason", required=True, help="Operator reason retained in the rejection handoff."
)
@click.option(
    "--target", "target_value", type=click.Path(path_type=Path, file_okay=False)
)
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
def reject_command(
    proposal_id: str, reason: str, target_value: Path | None, home_value: Path | None
) -> None:
    """Reject one pending proposal without creating an executable Gig version."""

    _require_supported_platform()
    try:
        entry = reject_offline(
            home_root=home_value or default_home_root(),
            requested_target=target_value,
            proposal_id=proposal_id,
            reason=reason,
        )
    except (LifecycleError, WorkpadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Rejected proposal in journal sequence {entry.sequence}; no Gig version was created."
    )


@cli.group("workpad")
def workpad_group() -> None:
    """Inspect an existing registered workpad."""


@workpad_group.command("path")
@click.argument("gig_id", required=False)
@click.option(
    "--target",
    "target_value",
    type=click.Path(path_type=Path, file_okay=False),
    help="Explicit target path; defaults to the current Git repository.",
)
@click.option(
    "--home",
    "home_value",
    type=click.Path(path_type=Path, file_okay=False),
    help="GigAI machine-state directory (default: GIGAI_HOME or ~/.gigai).",
)
def workpad_path_command(
    gig_id: str | None,
    target_value: Path | None,
    home_value: Path | None,
) -> None:
    """Print the canonical path of one already-registered Gig workpad."""

    _require_supported_platform()
    try:
        resolved = resolve_workpad(
            home_root=(home_value or default_home_root()),
            requested_target=target_value,
            gig_id=gig_id,
        )
    except (WorkpadError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(os.fspath(resolved.path))


@cli.command("check")
@click.argument("gig_id", required=False)
@click.option(
    "--target",
    "target_value",
    type=click.Path(path_type=Path, file_okay=False),
    help="Explicit target path; defaults to the current Git repository.",
)
@click.option(
    "--home",
    "home_value",
    type=click.Path(path_type=Path, file_okay=False),
    help="GigAI machine-state directory (default: GIGAI_HOME or ~/.gigai).",
)
@click.option(
    "--json", "as_json", is_flag=True, help="Emit a stable path-safe validation report."
)
def check_command(
    gig_id: str | None,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """Validate one existing proposal workpad without creating or changing it."""

    _require_supported_platform()
    try:
        resolved = resolve_workpad(
            home_root=(home_value or default_home_root()),
            requested_target=target_value,
            gig_id=gig_id,
            allow_semantic_state=True,
        )
    except (WorkpadError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    report = validate_proposal_workpad(resolved.path)
    if as_json:
        click.echo(json.dumps(report.as_dict(), sort_keys=True, separators=(",", ":")))
    elif report.valid:
        click.echo("Gig proposal validation passed.")
    else:
        for finding in report.findings:
            click.echo(
                f"{finding.code} {finding.location}: {finding.message}", err=True
            )
    if not report.valid:
        raise click.exceptions.Exit(1)


@cli.command("open")
@click.argument("gig_id", required=False)
@click.option(
    "--target",
    "target_only",
    is_flag=True,
    help="Open only the bound target; no active Gig is required.",
)
@click.option(
    "--with-target",
    is_flag=True,
    help="Open the resolved workpad and its bound target together.",
)
@click.option(
    "--target-root",
    type=click.Path(path_type=Path, file_okay=False),
    help="Explicit target path; defaults to the current Git repository.",
)
@click.option(
    "--home",
    "home_value",
    type=click.Path(path_type=Path, file_okay=False),
    help="GigAI machine-state directory (default: GIGAI_HOME or ~/.gigai).",
)
def open_command(
    gig_id: str | None,
    target_only: bool,
    with_target: bool,
    target_root: Path | None,
    home_value: Path | None,
) -> None:
    """Launch the structured editor for declared, existing locations."""

    _require_supported_platform()
    try:
        result = open_locations(
            home_root=(home_value or default_home_root()),
            requested_target=target_root,
            gig_id=gig_id,
            target_only=target_only,
            with_target=with_target,
            allow_semantic_state=True,
        )
    except (WorkpadError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    if result.opened_workpad and result.opened_target:
        click.echo("Opened the registered workpad and bound target.")
    elif result.opened_workpad:
        click.echo("Opened the registered workpad.")
    else:
        click.echo("Opened the bound target.")


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


def _parse_endpoint_spec(value: str) -> Endpoint:
    try:
        name, remainder = value.split("=", 1)
        adapter, credential, *base_url = remainder.split(":", 2)
    except ValueError as exc:
        raise click.BadParameter(
            "endpoints use NAME=openai_api:CREDENTIAL or "
            "NAME=openrouter_api:CREDENTIAL[:HTTPS_BASE_URL]"
        ) from exc
    if not name or not adapter or not credential or len(base_url) > 1:
        raise click.BadParameter("endpoint components must not be empty")
    if adapter not in {"openai_api", "openrouter_api"}:
        raise click.BadParameter("G11 endpoints use openai_api or openrouter_api")
    return Endpoint(
        name=name,
        adapter=adapter,
        credential=credential,
        base_url=base_url[0] if base_url else None,
    )


def _parse_target_output_limits(values: tuple[str, ...]) -> dict[str, int]:
    limits: dict[str, int] = {}
    for value in values:
        try:
            name, maximum = value.split("=", 1)
            parsed_maximum = int(maximum)
        except ValueError as exc:
            raise click.BadParameter(
                "target output limits use TARGET=MAX_OUTPUT_TOKENS"
            ) from exc
        if not name or parsed_maximum <= 0 or name in limits:
            raise click.BadParameter(
                "target output limits must be unique, non-empty, and positive"
            )
        limits[name] = parsed_maximum
    return limits


def _parse_target_reasoning_efforts(values: tuple[str, ...]) -> dict[str, str]:
    efforts: dict[str, str] = {}
    allowed = {"none", "low", "medium", "high", "xhigh", "max"}
    for value in values:
        try:
            name, effort = value.split("=", 1)
        except ValueError as exc:
            raise click.BadParameter("reasoning efforts use TARGET=EFFORT") from exc
        if not name or effort not in allowed or name in efforts:
            raise click.BadParameter(
                "reasoning efforts must be unique and one of none, low, medium, high, xhigh, max"
            )
        efforts[name] = effort
    return efforts


def _parse_model_target_spec(
    value: str, output_limits: dict[str, int], reasoning_efforts: dict[str, str]
) -> ModelTarget:
    try:
        name, remainder = value.split("=", 1)
        endpoint, model = remainder.split(":", 1)
    except ValueError as exc:
        raise click.BadParameter("model targets use NAME=ENDPOINT:MODEL") from exc
    if not name or not endpoint or not model:
        raise click.BadParameter("model target components must not be empty")
    maximum = output_limits.get(name, 4096)
    return ModelTarget(
        name=name,
        endpoint=endpoint,
        model=model,
        capabilities=("text",),
        max_output_tokens=maximum,
        reasoning_effort=reasoning_efforts.get(name),
    )


def _require_supported_platform() -> None:
    if sys.platform != "darwin" and not sys.platform.startswith("linux"):
        raise click.ClickException(
            "unsupported_platform: GigAI v1 requires macOS or Linux"
        )

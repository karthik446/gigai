"""GigAI's installed command surface, expanded only by approved goals."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from dataclasses import replace
import webbrowser

import click

from .config import (
    ConfigurationError,
    CredentialReference,
    Endpoint,
    ModelTarget,
    Profile,
    load_config,
    migrate_config,
)
from .comparison import ComparisonError, compare_occurrences
from .diagnostics import render_report_json, run_doctor, run_live_doctor
from .evaluation import EvaluationError, load_manifest, score_behavior, write_report
from .index import JournalIndexError, JournalProjection, read_index
from .lifecycle import (
    LifecycleError,
    approve_interview_session,
    approve_offline,
    build_interview_proposal,
    create_offline,
    persist_interview_session,
    record_feedback,
    record_builder_state,
    recover_builder_session,
    reject_offline,
    revise_offline,
    select_interview_references,
    stage_improvement_manifest,
    start_interview,
)
from .model_discovery import discover_installed_models, resolve_target_readiness
from .proposal_interview import InterviewHTTPServer, block_session, request_revision
from .occurrence import (
    OccurrenceError,
    close_occurrence,
    declare_occurrence,
    mark_occurrence,
    read_occurrence,
    reconcile_occurrence,
    trigger_occurrence,
)
from .question_generation import generate_model_questions
from .question_generation import G26_QUESTION_PROMPTS
from .setup import (
    build_config,
    detect_editor_argv,
    default_home_root,
    default_workpad_root,
    resolve_editor_argv,
    run_setup,
)
from .registry import open_project_registry
from .run import RunError, launch_run, read_run_details
from .target_binding import TargetBindingError, initialize_target
from .validators import validate_proposal_workpad
from .workpad import ResolvedWorkpad, WorkpadError, open_locations, resolve_workpad


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
            "Choose 'setup', 'doctor', 'init', 'create', 'improve', 'feedback', 'revise', "
            "'approve', 'reject', 'gigs', 'proposals', 'status', 'show', 'history', "
            "'plan', 'run', 'run-details', 'occurrence', 'workpad', 'check', 'models', 'eval', or 'open'; "
            "use --help for details."
        )


@cli.command("models")
@click.option(
    "--home",
    "home_value",
    type=click.Path(path_type=Path, file_okay=False),
    help="GigAI machine-state directory (default: GIGAI_HOME or ~/.gigai).",
)
@click.option("--json", "as_json", is_flag=True)
def models_command(home_value: Path | None, as_json: bool) -> None:
    """Show detected local model CLIs and configured target readiness."""

    try:
        home = home_value or default_home_root()
        config = load_config(home)
        payload = {
            "detected": [
                {
                    "name": item.name,
                    "executable": str(item.executable) if item.executable else None,
                    "readiness": item.readiness,
                }
                for item in discover_installed_models()
            ],
            "configured": [
                resolve_target_readiness(config, item.name).__dict__
                for item in config.model_targets
            ],
        }
        if as_json:
            click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
            return
        for item in payload["detected"]:
            location = f" ({item['executable']})" if item["executable"] else ""
            click.echo(f"{item['name']}: {item['readiness']}{location}")
        for item in payload["configured"]:
            click.echo(
                f"target {item['target_name']}: {item['readiness']} "
                f"({item['adapter'] or 'unresolved'} / {item['model'] or 'unknown'})"
            )
    except (ConfigurationError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@cli.group("eval")
def eval_group() -> None:
    """Validate and run explicit GigAI evaluation contracts."""


@eval_group.command("contract")
@click.option("--manifest", type=click.Path(path_type=Path, dir_okay=False), required=True)
def eval_contract_command(manifest: Path) -> None:
    """Validate a versioned behavioral evaluation manifest."""

    try:
        loaded = load_manifest(manifest)
    except EvaluationError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        json.dumps(
            {
                "kind": "evaluation_contract_report",
                "manifest": str(manifest),
                "manifest_digest": loaded.digest,
                "corpus_id": loaded.corpus_id,
                "case_count": len(loaded.cases),
                "status": "pass",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


@eval_group.command("behavior")
@click.option("--manifest", type=click.Path(path_type=Path, dir_okay=False), required=True)
@click.option("--observations", type=click.Path(path_type=Path, dir_okay=False), required=True)
@click.option(
    "--split",
    type=click.Choice(["development", "calibration", "final_held_out_acceptance"]),
    required=True,
)
@click.option("--output", type=click.Path(path_type=Path, dir_okay=False))
def eval_behavior_command(manifest: Path, observations: Path, split: str, output: Path | None) -> None:
    """Score separately supplied Solver observations against a Case corpus."""

    try:
        loaded = load_manifest(manifest)
        observation_payload = json.loads(observations.read_text(encoding="utf-8"))
        report = score_behavior(loaded, observation_payload, split)
        write_report(report, output)
    except (EvaluationError, OSError, json.JSONDecodeError) as exc:
        raise click.ClickException(str(exc)) from exc
    if report["status"] != "pass":
        raise click.exceptions.Exit(1)


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
    "--create-model-target",
    help="Select the configured model target used by create (defaults to the saved setup choice).",
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
    create_model_target: str | None,
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
        if default_editor is None:
            detected_editor = detect_editor_argv()
            if detected_editor is not None:
                default_editor = detected_editor[0]
        resolved_editor = resolve_editor_argv(
            click.prompt(
                "Editor program (used to open workpads)",
                default=default_editor,
                show_default=True,
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
        target_names = tuple(target.name for target in targets)
        saved_create_target = next(
            (
                profile.planner
                for profile in (existing.profiles if existing is not None else ())
                if profile.name == "default"
            ),
            "offline-default",
        )
        selected_create_target = create_model_target or saved_create_target
        if not non_interactive and create_model_target is None:
            selected_create_target = click.prompt(
                "Model for Gig creation",
                type=click.Choice(target_names),
                default=saved_create_target if saved_create_target in target_names else target_names[0],
                show_default=True,
            )
        if selected_create_target not in target_names:
            raise ValueError(
                f"create model target {selected_create_target!r} is not configured; "
                f"choose one of {sorted(target_names)}"
            )
        current_profiles = existing.profiles if existing is not None else None
        if current_profiles is None:
            profiles = (
                Profile(
                    name="default",
                    planner=selected_create_target,
                    critic="offline-default",
                    adjudicator="offline-default",
                ),
            )
        else:
            profiles = tuple(
                replace(profile, planner=selected_create_target)
                if profile.name == "default"
                else profile
                for profile in current_profiles
            )
        if not non_interactive:
            credential_summary = [
                {"name": item.name, "kind": item.kind} for item in credentials
            ]
            preview_config = build_config(
                home_root=requested_home,
                workpad_root=resolved_workpad,
                editor_argv=resolved_editor,
                open_with_target=resolved_open,
                credentials=credentials,
                endpoints=endpoints,
                model_targets=targets,
                profiles=profiles,
            )
            click.secho("\nGigAI setup review", bold=True, fg="cyan")
            click.echo(f"  GigAI home: {requested_home}")
            click.echo(f"  Workpad storage: {resolved_workpad}")
            click.echo(
                f"  Editor argv: {json.dumps(resolved_editor)} "
                "(program used to open workpads)"
            )
            click.echo(f"  Credential references: {json.dumps(credential_summary)}")
            click.secho("  Gig creation choices:", bold=True)
            for candidate in targets:
                readiness = resolve_target_readiness(preview_config, candidate.name)
                endpoint = next(item for item in endpoints if item.name == candidate.endpoint)
                mode = (
                    "deterministic fixture"
                    if endpoint.adapter == "deterministic"
                    else "configured API"
                )
                selected = " [selected]" if candidate.name == selected_create_target else ""
                click.echo(
                    f"    - {candidate.name}: {mode}; readiness={readiness.readiness}{selected}"
                )
            detected = discover_installed_models()
            for item in detected:
                if item.executable:
                    click.echo(
                        f"    - {item.name}: detected; readiness=unsupported "
                        "(no GigAI adapter; not invoked)"
                    )
            if not any(item.executable for item in detected):
                click.echo("    - codex/claude: not detected; no local CLI candidate")
            click.echo(
                "  Built-in local mode: no network or provider credentials "
                "(offline-default / fixture-v1)"
            )
            click.echo("  Profile: default")
            click.echo("  Standard pack: standard version 1")
            click.secho("\nThese are machine-local changes. Nothing will be written to a target repository.", dim=True)
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
            profiles=profiles,
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
    default=None,
    help="Override the setup-selected model target for this invocation.",
)
@click.option(
    "--request",
    "request_value",
    help="Free-form request presented to the local proposal interview.",
)
@click.option(
    "--reference",
    "reference_values",
    multiple=True,
    type=click.Path(path_type=Path, dir_okay=False),
    help="Explicit local reference; repeat for each selected candidate.",
)
@click.option(
    "--offline",
    is_flag=True,
    help="Use the legacy deterministic proposal fixture instead of the local interview.",
)
@click.option(
    "--allow-provider-network",
    is_flag=True,
    help="Allow a configured remote model target to ask questions and build the draft.",
)
@click.option(
    "--max-rounds",
    type=click.IntRange(min=1, max=1024),
    default=3,
    show_default=True,
    help="Maximum clarification rounds for the local interview.",
)
@click.option(
    "--open/--no-open",
    "open_browser",
    default=True,
    help="Open the loopback interview in the configured browser.",
)
@click.option(
    "--json", "as_json", is_flag=True, help="Emit a stable path-safe result summary."
)
def create_command(
    name: str,
    commission: str | None,
    target_value: Path | None,
    home_value: Path | None,
    model_target: str | None,
    request_value: str | None,
    reference_values: tuple[Path, ...],
    offline: bool,
    allow_provider_network: bool,
    max_rounds: int,
    open_browser: bool,
    as_json: bool,
) -> None:
    """Create a local deliberative proposal interview, or an explicit offline fixture."""

    _require_supported_platform()
    try:
        home = home_value or default_home_root()
        if offline:
            offline_model_target = model_target or _default_create_model_target(load_config(home))
            result = create_offline(
                home_root=home,
                requested_target=target_value,
                name=name,
                commission=commission,
                model_target=offline_model_target,
                open_editor=open_browser,
            )
        else:
            config = load_config(home)
            selected_model_target = model_target or _default_create_model_target(config)
            selected_endpoint = next(
                endpoint
                for endpoint in config.endpoints
                if endpoint.name
                == next(target.endpoint for target in config.model_targets if target.name == selected_model_target)
            )
            selected_network_policy = selected_endpoint.adapter != "deterministic"
            started = start_interview(
                home_root=home,
                requested_target=target_value,
                name=name,
                request=request_value or commission or name,
                reference_paths=reference_values,
                max_rounds=max_rounds,
            )
            reference_bytes = dict(started.reference_bytes)
            recovered_builder = recover_builder_session(start=started)
            built_proposal_id: str | None = recovered_builder.proposal_id
            builder_review: dict[str, object] = dict(recovered_builder.review)

            def select_references(session, paths: tuple[str, ...]):
                updated, selected_ids, labels, selected_bytes = select_interview_references(
                    home_root=home,
                    requested_target=target_value,
                    start=started,
                    session=session,
                    paths=paths,
                )
                reference_bytes.update(selected_bytes)
                return updated, selected_ids, labels

            def builder_questions(session):
                question_ids = {item.question_id for item in session.questions}
                if "main-drive" not in question_ids:
                    prompt_name = G26_QUESTION_PROMPTS[0]
                elif "success-definition" not in question_ids:
                    prompt_name = G26_QUESTION_PROMPTS[1]
                else:
                    return session
                return generate_model_questions(
                    config=load_config(home),
                    model_target=selected_model_target,
                    session=session,
                    reference_bytes=reference_bytes,
                    prompt_name=prompt_name,
                    network_allowed=allow_provider_network or selected_network_policy,
                )

            def build_proposal(session):
                nonlocal built_proposal_id
                built = build_interview_proposal(
                    home_root=home,
                    requested_target=target_value,
                    start=started,
                    session=session,
                    model_target=selected_model_target,
                    reference_bytes=reference_bytes,
                    network_allowed=allow_provider_network or selected_network_policy,
                )
                proposal = json.loads(
                    (started.workpad / "manifests" / "gig-proposal.json").read_text(
                        encoding="utf-8"
                    )
                )
                built_proposal_id = str(proposal["proposal_id"])
                draft_manifest = json.loads(
                    (started.workpad / "manifests/proposal-draft-manifest.json").read_text(
                        encoding="utf-8"
                    )
                )
                builder_review.update(draft_manifest.get("research", {}))
                return built

            def revise_proposal(session):
                nonlocal built_proposal_id
                built_proposal_id = None
                revised = request_revision(session)
                record_builder_state(
                    start=started,
                    session=revised,
                    state="revised",
                    terminal_reason=None,
                    transition="gig_builder_revised",
                )
                return revised

            def reject_proposal(session):
                rejected = block_session(session, "operator_rejected")
                record_builder_state(
                    start=started,
                    session=rejected,
                    state="rejected",
                    terminal_reason="operator_rejected",
                    transition="gig_builder_rejected",
                )
                return rejected

            server = InterviewHTTPServer(
                started.session,
                on_session=lambda session: persist_interview_session(
                    workpad=started.workpad,
                    project_id=started.project_id,
                    gig_id=started.gig_id,
                    session=session,
                ),
                on_questions=builder_questions,
                on_reference_paths=select_references,
                reference_labels={},
                on_approval=lambda session: approve_interview_session(
                    home_root=home,
                    requested_target=target_value,
                    start=started,
                    session=session,
                    existing_proposal_id=built_proposal_id,
                ),
                on_build=build_proposal,
                on_revision=revise_proposal,
                on_rejection=reject_proposal,
                builder_review=builder_review,
                builder_mode=True,
                builder_ready=recovered_builder.builder_ready,
            ).start()
            try:
                click.echo(f"GigAI local interview: {server.url}", err=True)
                if open_browser:
                    webbrowser.open(server.url, new=2)
                session = server.wait()
            finally:
                server.close()
            payload = {
                "gig_id": started.gig_id,
                "project_id": started.project_id,
                "proposal_id": session.proposal_id,
                "session_id": session.session_id,
                "status": session.state,
                "url": server.url,
            }
            if as_json:
                click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
            else:
                click.echo(
                    f"GigAI interview {session.session_id} ended in {session.state}; "
                    "no Run was started."
                )
            return
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


@cli.command("improve")
@click.argument("manifest", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--request", "request_value", required=True, help="Human-readable improvement request.")
@click.option("--reference", "reference_values", multiple=True, type=click.Path(path_type=Path, dir_okay=False), required=True, help="Explicit local evidence reference; repeat as needed.")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--model-target", default="offline-default", show_default=True)
@click.option("--max-rounds", type=click.IntRange(min=1, max=1024), default=3, show_default=True)
@click.option("--open/--no-open", "open_browser", default=True)
@click.option("--json", "as_json", is_flag=True)
def improve_command(
    manifest: Path,
    request_value: str,
    reference_values: tuple[Path, ...],
    target_value: Path | None,
    home_value: Path | None,
    model_target: str,
    max_rounds: int,
    open_browser: bool,
    as_json: bool,
) -> None:
    """Open an explicit G20 improvement proposal interview."""

    _require_supported_platform()
    try:
        home = home_value or default_home_root()
        stage_improvement_manifest(
            home_root=home,
            requested_target=target_value,
            manifest=manifest.read_bytes(),
        )
        started = start_interview(
            home_root=home,
            requested_target=target_value,
            name="improve",
            request=request_value,
            reference_paths=reference_values,
            max_rounds=max_rounds,
            improve=True,
        )
        server = InterviewHTTPServer(
            started.session,
            on_session=lambda session: persist_interview_session(
                workpad=started.workpad,
                project_id=started.project_id,
                gig_id=started.gig_id,
                session=session,
            ),
            on_questions=lambda session: (
                session
                if any(item.question_id == "operator-confirmation" for item in session.questions)
                else generate_model_questions(
                    config=load_config(home),
                    model_target=model_target,
                    session=session,
                    reference_bytes=dict(started.reference_bytes),
                )
            ),
            on_approval=lambda session: approve_interview_session(
                home_root=home,
                requested_target=target_value,
                start=started,
                session=session,
            ),
        ).start()
        try:
            click.echo(f"GigAI local improve interview: {server.url}", err=True)
            if open_browser:
                webbrowser.open(server.url, new=2)
            session = server.wait()
        finally:
            server.close()
        payload = {
            "gig_id": started.gig_id,
            "project_id": started.project_id,
            "proposal_id": session.proposal_id,
            "session_id": session.session_id,
            "status": session.state,
            "kind": "improve",
            "url": server.url,
        }
        if as_json:
            click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        else:
            click.echo(f"GigAI improve interview {session.session_id} ended in {session.state}.")
    except (LifecycleError, WorkpadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


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
    "--capability-manifest-id",
    type=str,
    default=None,
    help="Bind an existing local capability manifest to the approved Gig version.",
)
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
    proposal_id: str,
    target_value: Path | None,
    home_value: Path | None,
    capability_manifest_id: str | None,
    as_json: bool,
) -> None:
    """Seal one pending proposal as an offline approved Gig version."""

    _require_supported_platform()
    try:
        result = approve_offline(
            home_root=home_value or default_home_root(),
            requested_target=target_value,
            proposal_id=proposal_id,
            capability_manifest_id=capability_manifest_id,
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


@cli.command("run")
@click.argument("gig_id", required=False)
@click.option("--version", type=click.IntRange(min=1))
@click.option("--wait", is_flag=True)
@click.option(
    "--target", "target_value", type=click.Path(path_type=Path, file_okay=False)
)
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def run_command(
    gig_id: str | None,
    version: int | None,
    wait: bool,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """Run one approved Gig through its deterministic local capability."""

    _require_supported_platform()
    try:
        result = launch_run(
            home_root=home_value or default_home_root(),
            requested_target=target_value,
            gig_id=gig_id,
            version=version,
            wait=wait,
            invocation_argv=tuple(sys.argv),
        )
    except (RunError, WorkpadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    payload = {
        "gig_id": result.gig_id,
        "gig_version": result.gig_version,
        "run_id": result.run_id,
        "status": result.status,
    }
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(f"Run {result.run_id} {result.status} for {result.gig_id}.")


@cli.command("run-details")
@click.argument("run_id")
@click.option("--gig-id")
@click.option(
    "--target", "target_value", type=click.Path(path_type=Path, file_okay=False)
)
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def run_details_command(
    run_id: str,
    gig_id: str | None,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """Read durable state for one Run without starting work."""

    _require_supported_platform()
    try:
        payload = read_run_details(
            home_root=home_value or default_home_root(),
            requested_target=target_value,
            run_id=run_id,
            gig_id=gig_id,
        )
    except (RunError, WorkpadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(f"{payload['run_id']}: {payload['status']}")


@cli.group("occurrence")
def occurrence_group() -> None:
    """Manually declare, trigger, reconcile, and compare G21 occurrences."""


def _occurrence_payload(result) -> dict[str, object]:
    payload = read_occurrence(workpad=result.workpad, occurrence_id=result.occurrence_id)
    payload["workpad"] = str(result.workpad)
    return payload


@occurrence_group.command("declare")
@click.argument("cadence", type=click.Choice(["daily", "weekly", "monthly"]))
@click.argument("occurrence_key")
@click.option("--snapshot", "snapshot_path", required=True, help="Relative Review Bundle manifest path.")
@click.option("--prior-occurrence")
@click.option("--version", type=click.IntRange(min=1))
@click.option("--scheduled-for")
@click.option("--gig-id")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def occurrence_declare_command(
    cadence: str,
    occurrence_key: str,
    snapshot_path: str,
    prior_occurrence: str | None,
    version: int | None,
    scheduled_for: str | None,
    gig_id: str | None,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """Declare one explicit recurrence slot without starting a Run."""

    _require_supported_platform()
    try:
        result = declare_occurrence(
            home_root=home_value or default_home_root(),
            requested_target=target_value,
            gig_id=gig_id,
            version=version,
            cadence=cadence,
            occurrence_key=occurrence_key,
            snapshot_path=snapshot_path,
            prior_occurrence_id=prior_occurrence,
            scheduled_for=scheduled_for,
        )
        payload = _occurrence_payload(result)
    except (OccurrenceError, WorkpadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(f"Declared {payload['occurrence_id']} for {payload['cadence']}:{payload['occurrence_key']}.")


@occurrence_group.command("trigger")
@click.argument("occurrence_id")
@click.option("--wait", is_flag=True)
@click.option("--gig-id")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def occurrence_trigger_command(
    occurrence_id: str,
    wait: bool,
    gig_id: str | None,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """Trigger one declared occurrence through the existing Run path."""

    _require_supported_platform()
    try:
        result = trigger_occurrence(
            home_root=home_value or default_home_root(),
            requested_target=target_value,
            gig_id=gig_id,
            occurrence_id=occurrence_id,
            wait=wait,
        )
        payload = _occurrence_payload(result)
    except (OccurrenceError, RunError, WorkpadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(f"Occurrence {payload['occurrence_id']} is {payload['state']}.")


@occurrence_group.command("reconcile")
@click.argument("occurrence_id")
@click.option("--gig-id")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def occurrence_reconcile_command(
    occurrence_id: str,
    gig_id: str | None,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """Reconcile a prepared occurrence without relaunching its Run."""

    _require_supported_platform()
    try:
        result = reconcile_occurrence(
            home_root=home_value or default_home_root(),
            requested_target=target_value,
            gig_id=gig_id,
            occurrence_id=occurrence_id,
        )
        payload = _occurrence_payload(result)
    except (OccurrenceError, RunError, WorkpadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(f"Occurrence {payload['occurrence_id']} is {payload['state']}.")


@occurrence_group.command("mark")
@click.argument("occurrence_id")
@click.argument("state", type=click.Choice(["missed", "skipped", "unavailable", "cancelled", "blocked", "failed"]))
@click.option("--reason", required=True)
@click.option("--actor-id", required=True)
@click.option("--gig-id")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def occurrence_mark_command(
    occurrence_id: str,
    state: str,
    reason: str,
    actor_id: str,
    gig_id: str | None,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """Record an explicit occurrence outcome without starting a Run."""

    _require_supported_platform()
    try:
        result = mark_occurrence(
            home_root=home_value or default_home_root(),
            requested_target=target_value,
            gig_id=gig_id,
            occurrence_id=occurrence_id,
            state=state,
            reason=reason,
            outcome_actor={"kind": "operator", "id": actor_id},
        )
        payload = _occurrence_payload(result)
    except (OccurrenceError, WorkpadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(f"Occurrence {payload['occurrence_id']} is {payload['state']}.")


@occurrence_group.command("close")
@click.argument("occurrence_id")
@click.option("--gig-id")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def occurrence_close_command(
    occurrence_id: str,
    gig_id: str | None,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """Close a terminal occurrence without creating another Run."""

    _require_supported_platform()
    try:
        result = close_occurrence(
            home_root=home_value or default_home_root(),
            requested_target=target_value,
            gig_id=gig_id,
            occurrence_id=occurrence_id,
        )
        payload = _occurrence_payload(result)
    except (OccurrenceError, WorkpadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(f"Occurrence {payload['occurrence_id']} is {payload['state']}.")


@occurrence_group.command("compare")
@click.argument("current_occurrence_id")
@click.option("--prior-occurrence")
@click.option("--gig-id")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def occurrence_compare_command(
    current_occurrence_id: str,
    prior_occurrence: str | None,
    gig_id: str | None,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """Compare one completed occurrence with its explicitly named prior."""

    _require_supported_platform()
    try:
        comparison, result = compare_occurrences(
            home_root=home_value or default_home_root(),
            requested_target=target_value,
            gig_id=gig_id,
            current_occurrence_id=current_occurrence_id,
            prior_occurrence_id=prior_occurrence,
        )
    except (ComparisonError, OccurrenceError, WorkpadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(comparison, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(f"Comparison {comparison['comparison_id']} is {comparison['result']} for {result.occurrence_id}.")


def _read_projection(
    *, home_value: Path | None, target_value: Path | None, gig_id: str | None
) -> tuple[ResolvedWorkpad, JournalProjection]:
    resolved = resolve_workpad(
        home_root=home_value or default_home_root(),
        requested_target=target_value,
        gig_id=gig_id,
        allow_semantic_state=True,
    )
    return resolved, read_index(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id
    )


def _projection_options(command):
    command = click.argument("gig_id", required=False)(command)
    command = click.option(
        "--target", "target_value", type=click.Path(path_type=Path, file_okay=False)
    )(command)
    command = click.option(
        "--home", "home_value", type=click.Path(path_type=Path, file_okay=False)
    )(command)
    return click.option("--json", "as_json", is_flag=True)(command)


@cli.command("gigs")
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def gigs_command(home_value: Path | None, as_json: bool) -> None:
    """List registered Gig identities without reading credentials or the network."""

    _require_supported_platform()
    try:
        registry, _ = open_project_registry(
            (home_value or default_home_root()).expanduser().resolve(strict=False),
            create=False,
        )
        payload = [
            {"gig_id": item.gig_id, "project_id": item.project_id}
            for item in registry.workpad_records()
        ]
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    elif payload:
        for item in payload:
            click.echo(f"{item['gig_id']} {item['project_id']}")
    else:
        click.echo("No registered Gigs.")


@cli.command("proposals")
@_projection_options
def proposals_command(
    gig_id: str | None,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """Show the proposal envelope currently committed for one Gig."""

    _require_supported_platform()
    try:
        _resolved, projection = _read_projection(
            home_value=home_value, target_value=target_value, gig_id=gig_id
        )
    except (JournalIndexError, WorkpadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    payload = projection.proposal
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    elif payload is None:
        click.echo("No committed proposal.")
    else:
        click.echo(f"{payload['proposal_id']} {payload['status']} {payload['name']}")


@cli.command("status")
@_projection_options
def status_command(
    gig_id: str | None,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """Report the explicit proposal and active-version state for one Gig."""

    _require_supported_platform()
    try:
        resolved, projection = _read_projection(
            home_value=home_value, target_value=target_value, gig_id=gig_id
        )
    except (JournalIndexError, WorkpadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    proposal = projection.proposal or {}
    active = projection.active_version or {}
    payload = {
        "active_version": active.get("active_version"),
        "gig_id": resolved.gig_id,
        "journal_head": projection.head,
        "proposal_id": proposal.get("proposal_id"),
        "proposal_status": proposal.get("status"),
    }
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(
            f"{payload['gig_id']}: proposal={payload['proposal_status']} "
            f"active_version={payload['active_version']}"
        )


@cli.command("show")
@_projection_options
def show_command(
    gig_id: str | None,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """Show the canonical projection for one explicit or active Gig."""

    _require_supported_platform()
    try:
        _resolved, projection = _read_projection(
            home_value=home_value, target_value=target_value, gig_id=gig_id
        )
    except (JournalIndexError, WorkpadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(
            json.dumps(projection.as_dict(), sort_keys=True, separators=(",", ":"))
        )
    else:
        proposal = projection.proposal or {}
        click.echo(f"Gig {projection.gig_id}")
        click.echo(f"Proposal: {proposal.get('proposal_id', 'none')}")
        click.echo(f"State: {proposal.get('status', 'none')}")


@cli.command("history")
@_projection_options
def history_command(
    gig_id: str | None,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """List committed journal transitions for one Gig in sequence order."""

    _require_supported_platform()
    try:
        _resolved, projection = _read_projection(
            home_value=home_value, target_value=target_value, gig_id=gig_id
        )
    except (JournalIndexError, WorkpadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(
            json.dumps(list(projection.entries), sort_keys=True, separators=(",", ":"))
        )
    else:
        for item in projection.entries:
            click.echo(
                f"{item['sequence']:012d} {item['transition']} {item['handoff_id']}"
            )


@cli.command("plan")
@_projection_options
def plan_command(
    gig_id: str | None,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """Render the proposed or approved Goal Graph without starting work."""

    _require_supported_platform()
    try:
        _resolved, projection = _read_projection(
            home_value=home_value, target_value=target_value, gig_id=gig_id
        )
    except (JournalIndexError, WorkpadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    proposal = projection.proposal
    if proposal is None:
        raise click.ClickException("no committed proposal supplies a Goal Graph")
    authority = "approved" if projection.active_version is not None else "proposed"
    payload = {"authority": authority, "goal_graph": proposal["goal_graph"]}
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(f"{authority.title()} authority")
        click.echo(f"Goal graph: {proposal['goal_graph']['path']}")


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


def _default_create_model_target(config) -> str:
    """Resolve the setup-selected create target from the default profile."""

    target_names = {target.name for target in config.model_targets}
    for profile in config.profiles:
        if profile.name == "default" and profile.planner in target_names:
            return profile.planner
    if "offline-default" in target_names:
        return "offline-default"
    return config.model_targets[0].name


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

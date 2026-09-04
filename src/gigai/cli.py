"""GigAI's installed command surface, expanded only by approved goals."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
import sys
from dataclasses import replace
import webbrowser

import click
import questionary

from .config import (
    ConfigurationError,
    CredentialReference,
    Endpoint,
    ModelTarget,
    Profile,
    load_config,
    migrate_config,
)
from .catalog import (
    catalog_entries,
    get_catalog_entry,
    materialize_catalog_package,
    validate_catalog_entry,
)
from .credentials import reference_is_available
from .comparison import ComparisonError, compare_occurrences
from .diagnostics import render_report_json, run_doctor, run_live_doctor
from .evaluation import EvaluationError, load_manifest, score_behavior, write_report
from .index import JournalIndexError, JournalProjection, read_index
from .listing import GigListingError, list_gigs
from .invocation import InvocationValidationError, load_invocation_bytes
from .lifecycle import (
    LifecycleError,
    approve_interview_session,
    approve_offline,
    create_offline,
    persist_interview_session,
    persist_discovery_manifest,
    record_feedback,
    reject_offline,
    revise_offline,
    stage_improvement_manifest,
    start_interview,
)
from .model_discovery import (
    DetectedModel,
    discover_installed_models,
    discover_runtime_snapshot,
    persist_discovery_snapshot,
    persist_target_readiness,
    probe_target_readiness,
    resolve_target_readiness,
)
from .package import (
    PackageError,
    export_package,
    initialize_project_package,
    inspect_package,
    install_package,
    upgrade_installation,
)
from .proposal_interview import InterviewHTTPServer
from .occurrence import (
    OccurrenceError,
    close_occurrence,
    declare_occurrence,
    mark_occurrence,
    read_occurrence,
    reconcile_occurrence,
    trigger_occurrence,
)
from .question_generation import G27_DISCOVERY_PROMPT, generate_model_questions
from .setup import (
    build_config,
    detect_editor_argv,
    default_home_root,
    default_workpad_root,
    resolve_editor_argv,
    run_setup,
)
from .setup_interview import SetupDraft, SetupHTTPServer
from .run import RunError, launch_run, read_run_details
from .run_plan import RunPlanError, create_run_plan, list_run_plans, read_run_plan
from .provider_review import ProviderReviewError
from .target_binding import TargetBindingError, resolve_target
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
            "Choose 'setup', 'doctor', 'init', 'create', 'feedback', 'revise', "
            "'approve', 'reject', 'gigs', 'proposals', 'status', 'show', 'history', "
            "'plan', 'run-plan', 'run', 'run-details', 'occurrence', 'workpad', 'check', 'models', 'invoke', or 'open'; "
            "use --help for details."
        )


@cli.group("internal", hidden=True)
def internal_group() -> None:
    """Developer-only commands retained outside the public CLI surface."""


def _raise_cli_error(message: str, *, as_json: bool, code: str) -> None:
    """Emit one stable diagnostic shape for machine-readable CLI failures."""

    if as_json:
        click.echo(
            json.dumps(
                {"status": "error", "error": {"code": code, "message": message}},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        raise click.exceptions.Exit(1)
    raise click.ClickException(message)


@cli.command("models")
@click.option(
    "--home",
    "home_value",
    type=click.Path(path_type=Path, file_okay=False),
    help="GigAI machine-state directory (default: GIGAI_HOME or ~/.gigai).",
)
@click.option("--json", "as_json", is_flag=True)
@click.option(
    "--probe",
    "probe_target",
    metavar="TARGET",
    help="Explicitly run one bounded readiness invocation for TARGET.",
)
@click.option(
    "--refresh",
    is_flag=True,
    help="Capture a fresh local runtime discovery snapshot.",
)
def models_command(
    home_value: Path | None,
    as_json: bool,
    probe_target: str | None,
    refresh: bool,
) -> None:
    """Show model discovery; optionally run one explicit readiness probe."""

    try:
        home = home_value or default_home_root()
        config = load_config(home)
        snapshot = discover_runtime_snapshot(
            refresh_reason="explicit_refresh" if refresh else "models"
        )
        persist_discovery_snapshot(home, snapshot)
        runtime_executables = {
            item.name: str(item.executable)
            for item in snapshot.models
            if item.executable is not None
        }
        default_profile = next(
            (profile for profile in config.profiles if profile.name == "default"),
            None,
        )
        endpoints = {item.name: item for item in config.endpoints}
        detected = {item.name: item for item in snapshot.models}
        public_targets = tuple(
            target
            for target in config.model_targets
            if endpoints.get(target.endpoint) is not None
            and endpoints[target.endpoint].adapter != "deterministic"
        )

        def target_projection(
            target: ModelTarget,
            readiness,
        ) -> dict[str, object]:
            endpoint = endpoints.get(target.endpoint)
            adapter = endpoint.adapter if endpoint is not None else None
            label = _model_target_label(target, config)
            states = list(getattr(readiness, "states", ()))
            credential_status = "not_required"
            next_action = ""
            primary_state = readiness.readiness
            if adapter in {"codex_cli", "claude_cli"}:
                runtime = detected.get(endpoint.name if endpoint else "")
                if runtime is not None and runtime.executable is not None:
                    if "detected" not in states:
                        states.insert(0, "detected")
                    if "compatible" not in states:
                        states.append("compatible")
                    next_action = f"Run `gigai models --probe {target.name}` to verify readiness."
                else:
                    next_action = (
                        f"Install {label} or refresh discovery with `gigai models --refresh`."
                    )
            elif adapter in {"openai_api", "openrouter_api", "anthropic_api"}:
                credential = (
                    next(
                        (
                            item
                            for item in config.credentials
                            if endpoint is not None and item.name == endpoint.credential
                        ),
                        None,
                    )
                    if endpoint is not None
                    else None
                )
                available = None
                if credential is not None:
                    try:
                        available = reference_is_available(credential)
                    except ValueError:
                        available = False
                if credential is None or available is False or available is None:
                    primary_state = "credential_reference_missing"
                    credential_status = "missing_or_unusable"
                    if "credential_reference_missing" not in states:
                        states.append("credential_reference_missing")
                    next_action = (
                        "Set the configured external credential reference, then run "
                        "`gigai models --refresh`."
                    )
                else:
                    credential_status = "available"
                    if "compatible" not in states:
                        states.append("compatible")
                    next_action = f"Run `gigai models --probe {target.name}` to verify readiness."
            if selected_roles := tuple(
                role
                for role in (
                    "planner",
                    "critic",
                    "adjudicator",
                    "reviewer",
                    "verifier",
                    "researcher",
                    "gig_creator",
                )
                if default_profile is not None
                and getattr(default_profile, role) == target.name
            ):
                if "selected" not in states:
                    states.append("selected")
            return {
                **readiness.__dict__,
                "display_label": label,
                "state": primary_state,
                "states": tuple(dict.fromkeys(states)),
                "selected_roles": selected_roles,
                "credential_status": credential_status,
                "next_action": next_action,
            }

        def configured_target_payload(target: ModelTarget) -> dict[str, object]:
            readiness = resolve_target_readiness(
                config,
                target.name,
                executable_overrides=runtime_executables,
            )
            return target_projection(target, readiness)

        payload: dict[str, object] = {
            "snapshot": {
                **snapshot.to_shareable_dict(),
                "path": "<redacted>",
            },
            "detected": [
                {
                    "name": item.name,
                    "display_label": (
                        "Codex CLI" if item.name == "codex" else "Claude Code"
                    ),
                    "executable": "<redacted>" if item.executable else None,
                    "readiness": item.readiness,
                    "state": "detected" if item.executable else "not_detected",
                    "version": item.version,
                    "resolution": item.resolution,
                    "path_source": item.path_source,
                    "failure_code": item.failure_code,
                }
                for item in snapshot.models
            ],
            "configured": [
                configured_target_payload(item)
                for item in public_targets
            ],
        }
        configured_providers = {
            {
                "openai_api": "openai",
                "openrouter_api": "openrouter",
            }.get(endpoints[item.endpoint].adapter)
            for item in public_targets
            if endpoints.get(item.endpoint) is not None
        }
        payload["not_configured"] = [
            {
                "provider": provider,
                "display_label": label,
                "state": "not_configured",
                "next_action": (
                    f"Configure a credential reference and target for {label} with `gigai setup`."
                ),
            }
            for provider, label in (("openai", "OpenAI API"), ("openrouter", "OpenRouter API"))
            if provider not in configured_providers
        ]
        if probe_target is not None:
            configured_target = next(
                (item for item in public_targets if item.name == probe_target),
                None,
            )
            hidden_target = next(
                (item for item in config.model_targets if item.name == probe_target),
                None,
            )
            if hidden_target is not None and configured_target is None:
                _raise_cli_error(
                    "the requested model target is not available through the public CLI",
                    as_json=as_json,
                    code="model_target_not_public",
                )
            configured_projection = (
                configured_target_payload(configured_target)
                if configured_target is not None
                else None
            )
            if configured_projection is not None and configured_projection["state"] == "credential_reference_missing":
                payload["probe"] = {
                    **configured_projection,
                    "reason": "credential reference is missing or unusable; no probe was attempted",
                }
            else:
                probed = probe_target_readiness(
                    config,
                    probe_target,
                    executable_overrides=runtime_executables,
                )
                # An unknown target still has a useful diagnostic projection,
                # but it has no configured identity that a readiness proof can
                # safely bind to.
                if configured_target is not None:
                    persist_target_readiness(home, config, probed)
                payload["probe"] = (
                    target_projection(configured_target, probed)
                    if configured_target is not None
                    else {
                        **probed.__dict__,
                        "display_label": "Requested model target",
                        "state": probed.readiness,
                        "next_action": "Configure this target with `gigai setup`.",
                    }
                )
        if as_json:
            click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
            if probe_target is not None and payload["probe"]["readiness"] != "usable":
                raise click.exceptions.Exit(1)
            return
        for item in payload["detected"]:
            version = f" · v{item['version']}" if item["version"] else ""
            click.echo(f"{item['display_label']}{version}: {item['state']}")
        for item in payload["configured"]:
            click.echo(
                f"{item['display_label']}: {item['state']} — {item['next_action']}"
            )
        for item in payload["not_configured"]:
            click.echo(f"{item['display_label']}: {item['state']} — {item['next_action']}")
        if probe_target is not None:
            probe = payload["probe"]
            click.echo(
                f"Probe {probe['display_label']}: {probe['state']}"
            )
            if probe["state"] != "usable":
                raise click.exceptions.Exit(1)
    except ConfigurationError:
        _raise_cli_error(
            "GigAI is not configured. Run `gigai setup` before checking models.",
            as_json=as_json,
            code="models_not_configured",
        )
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command("invoke")
@click.option(
    "--input",
    "input_path",
    type=click.Path(path_type=Path, dir_okay=False),
    help="Read one explicit agent invocation envelope from this JSON file; otherwise read stdin.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the normalized envelope as JSON.")
def invoke_command(input_path: Path | None, as_json: bool) -> None:
    """Validate one explicit agent envelope without creating GigAI authority."""

    try:
        data = input_path.read_bytes() if input_path is not None else sys.stdin.buffer.read()
        invocation = load_invocation_bytes(data)
    except (InvocationValidationError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    payload = {
        "status": "accepted",
        "authority_created": False,
        "invocation": invocation.to_dict(),
    }
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(
            f"Accepted {invocation.command} invocation {invocation.invocation_id}; "
            "no GigAI authority was created."
        )


@internal_group.group("eval")
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
    "--terminal", is_flag=True, help="Use terminal prompts for setup."
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
@click.option(
    "--open/--no-open",
    "open_browser",
    default=False,
    hidden=True,
    help="Deprecated compatibility option; setup is terminal-native.",
)
def setup_command(
    non_interactive: bool,
    terminal: bool,
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
    open_browser: bool,
) -> None:
    """Run terminal setup, or update config non-interactively."""

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
                _raise_cli_error(str(exc), as_json=as_json, code="setup_configuration_invalid")

    if non_interactive:
        resolved_workpad = workpad_root or (
            existing.workpad_root if existing else default_workpad_root(requested_home)
        )
        try:
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
        except ValueError as exc:
            _raise_cli_error(str(exc), as_json=as_json, code="setup_editor_invalid")
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
                _setup_text_prompt(
                    "GigAI home",
                    default=_display_local_path(requested_home),
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
                    _raise_cli_error(
                        str(exc),
                        as_json=as_json,
                        code="setup_configuration_invalid",
                    )
        default_workpad = workpad_root or (
            existing.workpad_root if existing else default_workpad_root(requested_home)
        )
        resolved_workpad = (
            Path(
                _setup_text_prompt(
                    "Authoritative workpad root",
                    default=_display_local_path(default_workpad),
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
                try:
                    environment_editor = resolve_editor_argv(None)
                except ValueError as exc:
                    _raise_cli_error(str(exc), as_json=as_json, code="setup_editor_invalid")
                default_editor = environment_editor[0]
                environment_editor_args = environment_editor[1:]
        if default_editor is None:
            detected_editor = detect_editor_argv()
            if detected_editor is not None:
                default_editor = detected_editor[0]
        try:
            resolved_editor = resolve_editor_argv(
                _setup_text_prompt(
                    "Editor program (used to open workpads)",
                    default=default_editor or "",
                ),
                (
                    editor_arg
                    if editor is not None or editor_arg
                    else existing.editor_argv[1:]
                    if existing
                    else environment_editor_args
                ),
            )
        except ValueError as exc:
            _raise_cli_error(str(exc), as_json=as_json, code="setup_editor_invalid")
        resolved_open = _setup_confirm(
            "Open workpads with their target later?",
            default=(
                open_with_target
                if open_with_target is not None
                else existing.open_with_target
                if existing
                else False
            ),
        )

    discovery_snapshot = discover_runtime_snapshot(refresh_reason="setup")
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
        existing_endpoints = existing.endpoints if existing is not None else ()
        existing_targets = existing.model_targets if existing is not None else ()
        deterministic_endpoint_names = {
            endpoint.name
            for endpoint in existing_endpoints
            if endpoint.adapter == "deterministic"
        }
        existing_endpoints = tuple(
            endpoint
            for endpoint in existing_endpoints
            if endpoint.name not in deterministic_endpoint_names
        )
        existing_targets = tuple(
            target
            for target in existing_targets
            if target.endpoint not in deterministic_endpoint_names
        )
        endpoint_names = {endpoint.name for endpoint in existing_endpoints}
        target_names = {target.name for target in existing_targets}
        discovered_endpoints = list(existing_endpoints)
        discovered_targets = list(existing_targets)
        for detected in discovery_snapshot.models:
            if detected.executable is None:
                continue
            endpoint_name = detected.name
            target_name = f"{detected.name}-default"
            if endpoint_name not in endpoint_names:
                discovered_endpoints.append(
                    Endpoint(name=endpoint_name, adapter=f"{detected.name}_cli")
                )
                endpoint_names.add(endpoint_name)
            if target_name not in target_names:
                discovered_targets.append(
                    ModelTarget(
                        name=target_name,
                        endpoint=endpoint_name,
                        model="default",
                        capabilities=("text",),
                        max_output_tokens=512,
                    )
                )
                target_names.add(target_name)
        existing_endpoints = tuple(discovered_endpoints)
        existing_targets = tuple(discovered_targets)
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
        )
        endpoint_by_name = {endpoint.name: endpoint for endpoint in existing_endpoints}
        for endpoint in endpoint_specs:
            previous = endpoint_by_name.get(endpoint.name)
            if previous is not None and previous != endpoint:
                raise ValueError(
                    f"endpoint {endpoint.name!r} is already configured differently"
                )
            endpoint_by_name[endpoint.name] = endpoint
        endpoints = tuple(endpoint_by_name.values())
        target_by_name = {target.name: target for target in targets}
        for target in target_specs:
            previous = target_by_name.get(target.name)
            if previous is not None and previous != target:
                raise ValueError(
                    f"model target {target.name!r} is already configured differently"
                )
            target_by_name[target.name] = target
        targets = tuple(target_by_name.values())
        target_names = tuple(target.name for target in targets)
        saved_create_target = next(
            (
                profile.planner
                for profile in (existing.profiles if existing is not None else ())
                if profile.name == "default"
            ),
            None,
        )
        detected_create_target = next(
            (
                f"{provider}-default"
                for provider in ("codex", "claude")
                if any(
                    item.name == provider and item.executable is not None
                    for item in discovery_snapshot.models
                )
                and f"{provider}-default" in target_names
            ),
            None,
        )
        # Terminal setup should work from the runtime already present on the
        # machine. Never create or select a deterministic fixture as a default.
        setup_default_target = (
            saved_create_target
            if saved_create_target in target_names
            else detected_create_target
            if detected_create_target is not None
            else None
        )
        selected_create_target = create_model_target or setup_default_target
        if selected_create_target is None:
            raise ValueError(
                "no usable model runtime is configured; install or configure Codex, "
                "Claude, or an API target, then rerun `gigai setup`"
            )
        if selected_create_target not in target_names:
            raise ValueError(
                f"create model target {selected_create_target!r} is not configured; "
                f"choose one of {sorted(target_names)}"
            )
        runtime_options = _terminal_runtime_options(
            targets=targets,
            endpoints=endpoints,
            detected_models=discovery_snapshot.models,
        )
        if not non_interactive and create_model_target is None:
            selected_create_target = _select_terminal_create_target(
                options=runtime_options,
                default=selected_create_target,
            )
        current_profiles = existing.profiles if existing is not None else None
        if current_profiles is None:
            profiles = (
                Profile(
                    name="default",
                    planner=selected_create_target,
                    critic=selected_create_target,
                    adjudicator=selected_create_target,
                ),
            )
        else:
            profiles_list = []
            replaced_default = False
            for profile in current_profiles:
                if profile.name == "default":
                    profiles_list.append(
                        replace(
                            profile,
                            planner=selected_create_target,
                            critic=(
                                selected_create_target
                                if profile.critic not in target_names
                                else profile.critic
                            ),
                            adjudicator=(
                                selected_create_target
                                if profile.adjudicator not in target_names
                                else profile.adjudicator
                            ),
                        )
                    )
                    replaced_default = True
                else:
                    profiles_list.append(profile)
            if not replaced_default:
                profiles_list.append(
                    Profile(
                        name="default",
                        planner=selected_create_target,
                        critic=selected_create_target,
                        adjudicator=selected_create_target,
                    )
                )
            profiles = tuple(profiles_list)
        if not non_interactive:
            click.secho("\nGigAI setup", bold=True, fg="cyan")
            click.echo(
                "  GigAI home: "
                + click.style(_display_local_path(requested_home), fg="bright_black")
            )
            click.echo(
                "  Workpad storage: "
                + click.style(_display_local_path(resolved_workpad), fg="bright_black")
            )
            click.echo(
                "  Editor: " + click.style(resolved_editor[0], fg="bright_black")
            )
            selected_option = next(
                item for item in runtime_options if item[0] == selected_create_target
            )
            click.secho("  Runtime:", bold=True, nl=False)
            click.echo(
                " " + click.style(selected_option[1], fg="green", bold=True)
            )
            click.echo(
                "  API providers: optional reference-only configuration; "
                "see `gigai setup --help` for supported forms."
            )
            click.secho("\nThese are machine-local changes. Nothing will be written to a target repository.", dim=True)
            if not _setup_confirm("Apply this setup?", default=True):
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
        persist_discovery_snapshot(result.config.home_root, discovery_snapshot)
    except (ConfigurationError, OSError, ValueError) as exc:
        _raise_cli_error(str(exc), as_json=as_json, code="setup_invalid")

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
        click.echo(
            "Authoritative workpad root: "
            + _display_local_path(result.config.workpad_root)
        )


def _run_browser_setup(
    *,
    home_value: Path | None,
    workpad_root: Path | None,
    editor: str | None,
    open_with_target: bool | None,
    create_model_target: str | None,
    credential_ref: tuple[str, ...],
    clear_credentials: bool,
    endpoint_spec: tuple[str, ...],
    model_target_spec: tuple[str, ...],
    target_output_limit_spec: tuple[str, ...],
    target_reasoning_effort_spec: tuple[str, ...],
    as_json: bool,
    open_browser: bool,
) -> None:
    """Run the human setup flow without exposing internal target identifiers."""

    advanced = (
        credential_ref
        or clear_credentials
        or endpoint_spec
        or model_target_spec
        or target_output_limit_spec
        or target_reasoning_effort_spec
    )
    if advanced:
        raise click.UsageError(
            "advanced credentials and model flags require --non-interactive; "
            "ordinary setup is browser-first"
        )
    requested_home = (home_value or default_home_root()).expanduser().resolve(strict=False)
    existing = None
    if (requested_home / "config.toml").exists():
        try:
            existing = load_config(requested_home)
        except ConfigurationError as exc:
            try:
                existing, _ = migrate_config(requested_home)
            except ConfigurationError:
                _raise_cli_error(str(exc), as_json=as_json, code="setup_configuration_invalid")

    detected_editor = detect_editor_argv()
    existing_editor = existing.editor_argv[0] if existing else None
    editor_value = editor or existing_editor or (detected_editor[0] if detected_editor else "")
    resolved_workpad = workpad_root or (
        existing.workpad_root if existing else default_workpad_root(requested_home)
    )
    if existing is None:
        preview = build_config(
            home_root=requested_home,
            workpad_root=resolved_workpad,
            editor_argv=(editor_value or "/usr/bin/true",),
            open_with_target=open_with_target if open_with_target is not None else False,
        )
    else:
        preview = existing
    detected_models = discover_installed_models()
    preview = _browser_preview_config(preview, detected_models)
    real_targets = tuple(
        target
        for target in preview.model_targets
        if next(item for item in preview.endpoints if item.name == target.endpoint).adapter
        != "deterministic"
    )
    default_profile = next(
        (profile for profile in preview.profiles if profile.name == "default"), None
    )
    enabled_names: list[str] = []
    for target in real_targets:
        endpoint = next(item for item in preview.endpoints if item.name == target.endpoint)
        if endpoint.adapter in {"codex_cli", "claude_cli"}:
            enabled_names.append(target.name)
        elif existing is not None and target.name in {
            item.name for item in existing.model_targets if item.enabled
        }:
            enabled_names.append(target.name)
    enabled = tuple(enabled_names)
    selected = create_model_target if create_model_target in set(enabled) else (
        next(iter(enabled), "")
    )
    reviewer = _profile_target(default_profile, "reviewer", "critic", selected)
    verifier = _profile_target(default_profile, "verifier", "adjudicator", selected)
    researcher = _profile_target(default_profile, "researcher", "planner", selected)
    browser_home = os.fspath(requested_home)
    browser_workpad = os.fspath(resolved_workpad)
    model_options = tuple(
        {
            "id": target.name,
            "label": _model_target_label(target, preview),
            "description": _browser_model_description(target, preview, detected_models),
            "kind": "api"
            if next(item for item in preview.endpoints if item.name == target.endpoint).adapter
            in {"openai_api", "openrouter_api", "anthropic_api"}
            else "cli",
        }
        for target in real_targets
    )
    openai_api_env, openai_api_model = _browser_provider_values(existing, "openai")
    openrouter_api_env, openrouter_api_model = _browser_provider_values(existing, "openrouter")
    provider_status = _browser_provider_status(existing)

    def probe_setup_target(target_name: str, draft: SetupDraft) -> Mapping[str, object]:
        provider_credentials, provider_endpoints, provider_targets = _browser_provider_config(
            preview, draft
        )
        # A previously disabled target must be probeable before setup can
        # re-enable it. The probe is an explicit readiness action; it does not
        # persist this temporary enablement.
        provider_targets = _enable_probe_target(provider_targets, target_name)
        probe_config = build_config(
            home_root=preview.home_root,
            workpad_root=preview.workpad_root,
            editor_argv=preview.editor_argv,
            open_with_target=preview.open_with_target,
            credentials=provider_credentials,
            endpoints=provider_endpoints,
            model_targets=provider_targets,
            profiles=preview.profiles,
        )
        return probe_target_readiness(probe_config, target_name).__dict__

    def apply_setup(draft: SetupDraft) -> Mapping[str, object]:
        resolved_editor = resolve_editor_argv(
            draft.editor,
            existing.editor_argv[1:] if existing is not None else (),
        )
        provider_credentials, provider_endpoints, provider_targets = _browser_provider_config(
            preview, draft
        )
        target_by_name = {target.name: target for target in provider_targets}
        enabled_names = set(draft.enabled_model_targets)
        role_targets = {
            "reviewer": draft.reviewer_model_target,
            "verifier": draft.verifier_model_target,
            "researcher": draft.researcher_model_target,
            "gig_creator": draft.selected_model_target,
        }
        if not enabled_names:
            raise ValueError("enable at least one usable model before applying setup")
        missing = sorted(
            name for name in (*enabled_names, *role_targets.values()) if name not in target_by_name
        )
        if missing:
            raise ValueError(
                "selected model is not configured or usable: " + ", ".join(dict.fromkeys(missing))
            )
        if any(name not in enabled_names for name in role_targets.values()):
            raise ValueError("reviewer, verifier, researcher, and Gig creation defaults must be enabled")
        verified_names = set(draft.verified_model_targets)
        unverified = sorted(
            name
            for name in (*enabled_names, *role_targets.values())
            if name not in verified_names
        )
        if unverified:
            raise ValueError(
                "run Check readiness before applying setup for: "
                + ", ".join(dict.fromkeys(unverified))
            )
        provider_targets = tuple(
            replace(target, enabled=target.name in enabled_names)
            for target in provider_targets
        )
        profiles = list(existing.profiles if existing is not None else preview.profiles)
        default = Profile(
            name="default",
            planner=draft.selected_model_target,
            critic=draft.reviewer_model_target,
            adjudicator=draft.verifier_model_target,
            reviewer=draft.reviewer_model_target,
            verifier=draft.verifier_model_target,
            researcher=draft.researcher_model_target,
            gig_creator=draft.selected_model_target,
        )
        replaced = False
        for index, profile in enumerate(profiles):
            if profile.name == "default":
                profiles[index] = default
                replaced = True
                break
        if not replaced:
            profiles.append(default)
        config = build_config(
            home_root=Path(draft.home_root).expanduser().resolve(strict=False),
            workpad_root=Path(draft.workpad_root).expanduser().resolve(strict=False),
            editor_argv=resolved_editor,
            open_with_target=draft.open_with_target,
            credentials=provider_credentials,
            endpoints=provider_endpoints,
            model_targets=provider_targets,
            profiles=tuple(profiles),
        )
        result = run_setup(config)
        return {
            "home_root": os.fspath(result.config.home_root),
            "workpad_root": os.fspath(result.config.workpad_root),
            "config_changed": result.config_changed,
            "standard_pack_changed": result.pack_changed,
            "selected_model": _model_target_label(
                next(item for item in result.config.model_targets if item.name == draft.selected_model_target),
                result.config,
            ),
        }

    server = SetupHTTPServer(
        SetupDraft(
            home_root=browser_home,
            workpad_root=browser_workpad,
            editor=editor_value,
            open_with_target=(
                open_with_target
                if open_with_target is not None
                else existing.open_with_target
                if existing is not None
                else False
            ),
            selected_model_target=selected,
            openai_api_env=openai_api_env,
            openai_api_model=openai_api_model,
            openrouter_api_env=openrouter_api_env,
            openrouter_api_model=openrouter_api_model,
            enabled_model_targets=enabled,
            reviewer_model_target=reviewer,
            verifier_model_target=verifier,
            researcher_model_target=researcher,
        ),
        model_options=model_options,
        detected_models=detected_models,
        provider_status=provider_status,
        on_apply=apply_setup,
        on_probe=probe_setup_target,
    ).start()
    try:
        click.echo(f"GigAI local setup: {server.url}", err=True)
        if open_browser:
            webbrowser.open(server.url, new=2)
        result = server.wait()
    finally:
        server.close()
    if result is None:
        raise click.ClickException("setup was cancelled or expired; no changes were applied")
    if as_json:
        click.echo(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        click.echo("GigAI setup complete; configuration updated.")
        click.echo(f"Authoritative workpad root: {result['workpad_root']}")


def _terminal_runtime_options(
    *,
    targets: tuple[ModelTarget, ...],
    endpoints: tuple[Endpoint, ...],
    detected_models: tuple[object, ...],
) -> tuple[tuple[str, str, str], ...]:
    """Render one operator-facing runtime choice per configured target."""

    endpoints_by_name = {item.name: item for item in endpoints}
    detected_by_name = {getattr(item, "name"): item for item in detected_models}

    def priority(target: ModelTarget) -> tuple[int, str]:
        adapter = endpoints_by_name[target.endpoint].adapter
        if adapter == "codex_cli":
            return (0, target.name)
        if adapter == "claude_cli":
            return (1, target.name)
        if adapter == "deterministic":
            return (3, target.name)
        return (2, target.name)

    options: list[tuple[str, str, str]] = []
    for target in sorted(targets, key=priority):
        endpoint = endpoints_by_name[target.endpoint]
        if endpoint.adapter == "deterministic":
            continue
        elif endpoint.adapter in {"codex_cli", "claude_cli"}:
            provider = endpoint.name
            detected = detected_by_name.get(provider)
            runtime_name = "Codex CLI" if provider == "codex" else "Claude Code"
            version = getattr(detected, "version", None)
            version_suffix = f" · {_display_runtime_version(version)}" if version else ""
            label = runtime_name + version_suffix
            if getattr(detected, "executable", None) is None:
                description = "Configured, but not detected on this shell"
            else:
                description = (
                    f"Detected at {_display_local_path(detected.executable)}"
                )
        else:
            label = f"{endpoint.name} API"
            description = f"Configured target · {target.model}"
        options.append((target.name, label, description))
    return tuple(options)


def _display_local_path(value: Path, *, home: Path | None = None) -> str:
    """Show a local path without needlessly exposing the operator's home prefix."""

    # This is presentation only. Do not resolve: `/opt/homebrew/bin/codex` is
    # the useful operator-facing path, while its resolved Node-module target is
    # noisy and exposes implementation detail.
    expanded_value = value.expanduser()
    expanded_home = (home or Path.home()).expanduser()
    try:
        relative = expanded_value.relative_to(expanded_home)
    except ValueError:
        return os.fspath(expanded_value)
    return "~" if relative == Path(".") else f"~/{relative}"


def _display_runtime_version(value: str) -> str:
    """Render a provider version as a short operator-facing tag."""

    for token in value.replace("(", " ").replace(")", " ").split():
        normalized = token.removeprefix("v")
        if normalized and normalized[0].isdigit():
            return f"v{normalized}"
    return value


def _setup_prompt_style() -> questionary.Style:
    return questionary.Style(
        [
            ("qmark", "fg:#35c9ff bold"),
            ("question", "fg:#35c9ff bold"),
            ("answer", "fg:#43d17a bold"),
            ("instruction", "fg:#7f8a99 italic"),
            ("pointer", "fg:#35c9ff bold"),
            ("checkbox", "fg:#b8c0cc"),
            ("selected", "fg:#43d17a bold"),
            ("highlighted", "fg:#ffffff bold"),
            ("text", "fg:#b8c0cc"),
            ("validation-toolbar", "fg:#ffcc66"),
        ]
    )


def _setup_text_prompt(
    label: str, *, default: str, is_tty: bool | None = None
) -> str:
    if is_tty is None:
        is_tty = sys.stdin.isatty()
    if not is_tty:
        return click.prompt(label, default=default, show_default=True)
    answer = questionary.text(
        label + ":",
        default="",
        qmark="◆",
        style=_setup_prompt_style(),
        instruction=f"\n  default ({default}): Type path to change\n",
    ).ask()
    if answer is None:
        raise click.Abort()
    return answer or default


def _setup_confirm(label: str, *, default: bool) -> bool:
    if not sys.stdin.isatty():
        return click.confirm(label, default=default)
    answer = questionary.confirm(
        label,
        default=default,
        qmark="◆",
        style=_setup_prompt_style(),
    ).ask()
    if answer is None:
        raise click.Abort()
    return answer


def _select_terminal_create_target(
    *, options: tuple[tuple[str, str, str], ...], default: str, is_tty: bool | None = None
) -> str:
    """Use Questionary's styled terminal picker for one creation runtime."""

    if is_tty is None:
        is_tty = sys.stdin.isatty()
    if not is_tty:
        return default

    def one_runtime(values: list[str]) -> bool | str:
        return True if len(values) == 1 else "Choose exactly one Gig creation runtime."

    answer = questionary.checkbox(
        "Select Gig creation runtime",
        choices=[
            questionary.Choice(title=label, value=name, description=description)
            for name, label, description in options
        ],
        initial_choice=default,
        validate=one_runtime,
        qmark="◆",
        pointer="›",
        instruction="(↑/↓ move · Space chooses · Enter continues)",
        style=_setup_prompt_style(),
    ).ask()
    if answer is None:
        raise click.Abort()
    return answer[0]


def _model_target_label(target: ModelTarget, config) -> str:
    endpoint = next(item for item in config.endpoints if item.name == target.endpoint)
    if endpoint.adapter == "deterministic":
        return "Deterministic test adapter"
    if endpoint.adapter == "openrouter_api":
        return "OpenRouter API"
    if endpoint.adapter == "openai_api":
        return "OpenAI API"
    if endpoint.adapter == "codex_cli":
        return "Codex CLI"
    if endpoint.adapter == "claude_cli":
        return "Claude Code"
    return "Configured model"


def _browser_preview_config(config, detected_models):
    """Add selectable provider candidates without reading credentials or invoking models."""

    credentials = list(config.credentials)
    endpoints = list(config.endpoints)
    targets = list(config.model_targets)
    endpoint_names = {item.name for item in endpoints}
    target_names = {item.name for item in targets}
    credential_names = {item.name for item in credentials}

    api_defaults = (
        ("openai", "openai_api", "openai-api", "OPENAI_API_KEY", "gpt-4.1-mini"),
        ("openrouter", "openrouter_api", "openrouter-api", "OPENROUTER_API_KEY", "openai/gpt-4o-mini"),
    )
    for provider, adapter, credential_name, environment_name, model in api_defaults:
        if credential_name not in credential_names:
            credentials.append(CredentialReference(credential_name, "environment", environment_name))
        if provider not in endpoint_names:
            endpoints.append(Endpoint(provider, adapter, credential=credential_name))
        if f"{provider}-default" not in target_names:
            targets.append(ModelTarget(f"{provider}-default", provider, model, ("text",), 512))

    for detected in detected_models:
        if detected.executable is None or detected.name in endpoint_names:
            continue
        adapter = f"{detected.name}_cli"
        target_name = f"{detected.name}-default"
        endpoints.append(Endpoint(detected.name, adapter))
        if target_name not in target_names:
            targets.append(ModelTarget(target_name, detected.name, "default", ("text",), 512))

    return build_config(
        home_root=config.home_root,
        workpad_root=config.workpad_root,
        editor_argv=config.editor_argv,
        open_with_target=config.open_with_target,
        credentials=tuple(credentials),
        endpoints=tuple(endpoints),
        model_targets=tuple(targets),
        profiles=config.profiles,
    )


def _browser_provider_values(config, provider: str) -> tuple[str, str]:
    if config is None:
        defaults = {
            "openai": ("", "gpt-4.1-mini"),
            "openrouter": ("", "openai/gpt-4o-mini"),
        }
        return defaults.get(provider, ("", ""))
    endpoint = next((item for item in config.endpoints if item.name == provider), None)
    if endpoint is None or endpoint.credential is None:
        return "", ""
    credential = next((item for item in config.credentials if item.name == endpoint.credential), None)
    target = next((item for item in config.model_targets if item.endpoint == provider), None)
    return (
        credential.reference if credential is not None and credential.kind == "environment" else "",
        target.model if target is not None else "",
    )


def _browser_provider_status(config) -> dict[str, str]:
    status: dict[str, str] = {}
    labels = {"openai": "OpenAI", "openrouter": "OpenRouter"}
    for provider in labels:
        endpoint = next((item for item in config.endpoints if item.name == provider), None) if config else None
        credential = (
            next((item for item in config.credentials if item.name == endpoint.credential), None)
            if config and endpoint and endpoint.credential
            else None
        )
        if endpoint is None or credential is None:
            status[labels[provider]] = "Not configured"
            continue
        try:
            available = reference_is_available(credential)
        except ValueError:
            available = False
        status[labels[provider]] = (
            "Configured"
            if available is True
            else "Reference saved — value unavailable"
        )
    return status


def _browser_provider_config(config, draft: SetupDraft):
    """Apply browser-provided environment references without accepting secret values."""

    credentials = list(config.credentials)
    endpoints = list(config.endpoints)
    targets = list(config.model_targets)
    providers = (
        ("openai", "openai_api", "openai-api", draft.openai_api_env, draft.openai_api_model, "gpt-4.1-mini"),
        ("openrouter", "openrouter_api", "openrouter-api", draft.openrouter_api_env, draft.openrouter_api_model, "openai/gpt-4o-mini"),
    )
    for provider, adapter, credential_name, environment_name, model, default_model in providers:
        credentials = [item for item in credentials if item.name != credential_name]
        endpoints = [item for item in endpoints if item.name != provider]
        targets = [item for item in targets if item.name != f"{provider}-default"]
        if not environment_name:
            continue
        credentials.append(CredentialReference(credential_name, "environment", environment_name))
        endpoints.append(Endpoint(provider, adapter, credential=credential_name))
        targets.append(ModelTarget(f"{provider}-default", provider, model or default_model, ("text",), 512))
    return tuple(credentials), tuple(endpoints), tuple(targets)


def _enable_probe_target(
    targets: tuple[ModelTarget, ...], target_name: str
) -> tuple[ModelTarget, ...]:
    """Temporarily enable one target so setup can verify a disabled target."""

    return tuple(
        replace(target, enabled=True) if target.name == target_name else target
        for target in targets
    )


def _profile_target(profile, role: str, legacy_role: str, fallback: str) -> str:
    if profile is None:
        return fallback
    return getattr(profile, role) or getattr(profile, legacy_role) or fallback


def _model_target_description(target: ModelTarget, config) -> str:
    endpoint = next(item for item in config.endpoints if item.name == target.endpoint)
    if endpoint.adapter == "deterministic":
        return "Local deterministic fixture; no network or provider credentials. Use only for demos and contract checks."
    if endpoint.adapter == "codex_cli":
        return "Provider-default Codex model; authentication remains owned by the installed CLI."
    if endpoint.adapter == "claude_cli":
        if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            return (
                "Claude CLI; CLAUDE_CODE_OAUTH_TOKEN is available for the bounded GigAI process."
            )
        return (
            "Claude CLI detected, but GigAI needs CLAUDE_CODE_OAUTH_TOKEN for its bounded process. "
            "Run claude setup-token, export it, then reopen setup."
        )
    readiness = resolve_target_readiness(config, target.name)
    return f"Model {target.model}; readiness={readiness.readiness}. Credential values are never read during setup."


def _browser_model_description(
    target: ModelTarget, config, detected_models: tuple[DetectedModel, ...]
) -> str:
    description = _model_target_description(target, config)
    endpoint = next(item for item in config.endpoints if item.name == target.endpoint)
    if endpoint.adapter in {"codex_cli", "claude_cli"}:
        detected = next((item for item in detected_models if item.name == endpoint.name), None)
        if detected is not None and detected.version:
            description += f" Installed version: {detected.version}."
        elif detected is not None:
            description += " Version could not be confirmed yet."
    return description


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
@click.option(
    "--adopt-package",
    is_flag=True,
    help="Adopt one already-tracked, validated portable package.",
)
@click.option(
    "--confirm",
    "confirmed",
    is_flag=True,
    help="Confirm package adoption when --adopt-package is used.",
)
def init_command(
    target: Path | None,
    home_value: Path | None,
    as_json: bool,
    adopt_package: bool,
    confirmed: bool,
) -> None:
    """Initialize or reconcile the project-local portable package boundary."""

    _require_supported_platform()
    try:
        result = initialize_project_package(
            home_root=(home_value or default_home_root()),
            requested_target=target,
            adopt_package=adopt_package,
            confirmed=confirmed,
        )
    except (PackageError, TargetBindingError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc

    payload = {
        "binding_created": result.binding_created,
        "exclude_changed": result.exclude_changed,
        "package_digest": result.package_digest,
        "package_id": result.package_id,
        "project_id": result.project_id,
        "reconciled": result.reconciled,
        "registry_changed": result.registry_changed,
        "target_kind": result.target_kind,
        "adopted": result.adopted,
    }
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        disposition = "adopted" if result.adopted else ("created" if result.binding_created else "confirmed")
        click.echo(
            f"GigAI project package {disposition}: {result.package_id} "
            f"for {result.project_id} ({result.target_kind})."
        )
        if result.reconciled:
            click.echo("Derived registry or exclude state was reconciled.")


@cli.group("package")
def package_group() -> None:
    """Inspect and install portable project-local packages."""


@cli.group("catalog")
def catalog_group() -> None:
    """Inspect and explicitly install built-in Gig definitions."""


@catalog_group.command("list")
@click.option("--json", "as_json", is_flag=True)
def catalog_list_command(as_json: bool) -> None:
    entries = catalog_entries()
    payload = [
        {
            "catalog_id": entry.catalog_id,
            "definition_version": entry.definition_version,
            "title": entry.title,
            "summary": entry.summary,
            "package_id": entry.package_id,
            "entry_content_digest": entry.entry_content_digest,
        }
        for entry in entries
    ]
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        for item in payload:
            click.echo(f"{item['catalog_id']}@{item['definition_version']} — {item['title']}")


@catalog_group.command("inspect")
@click.argument("catalog_id")
@click.option("--version", "definition_version", default="1.0", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def catalog_inspect_command(catalog_id: str, definition_version: str, as_json: bool) -> None:
    try:
        entry = get_catalog_entry(catalog_id, definition_version)
        validate_catalog_entry(entry)
    except PackageError as exc:
        raise click.ClickException(str(exc)) from exc
    payload = dict(entry.entry_metadata())
    payload.update({"package_id": entry.package_id, "files": sorted(entry.package_files())})
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(f"{entry.catalog_id}@{entry.definition_version}: {entry.title}")
        click.echo(entry.summary)
        click.echo(f"Package {entry.package_id}; {entry.entry_content_digest}")


@catalog_group.command("validate")
@click.argument("catalog_id")
@click.option("--version", "definition_version", default="1.0", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def catalog_validate_command(catalog_id: str, definition_version: str, as_json: bool) -> None:
    try:
        entry = get_catalog_entry(catalog_id, definition_version)
        validate_catalog_entry(entry)
    except PackageError as exc:
        raise click.ClickException(str(exc)) from exc
    payload = {"catalog_id": entry.catalog_id, "definition_version": entry.definition_version, "valid": True, "authority_changed": False}
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(f"Catalog entry {entry.catalog_id}@{entry.definition_version} is valid.")


@catalog_group.command("install")
@click.argument("catalog_id")
@click.option("--version", "definition_version", default="1.0", show_default=True)
@click.option("--target", required=True, type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def catalog_install_command(catalog_id: str, definition_version: str, target: Path, as_json: bool) -> None:
    try:
        entry = get_catalog_entry(catalog_id, definition_version)
        resolved_target = resolve_target(target)
        result = materialize_catalog_package(entry, resolved_target.root)
    except (PackageError, TargetBindingError) as exc:
        raise click.ClickException(str(exc)) from exc
    payload = {
        "catalog_id": entry.catalog_id,
        "definition_version": entry.definition_version,
        "package_id": result.package_id,
        "package_digest": result.content_digest,
        "target": resolved_target.kind,
        "authority_changed": False,
        "bootstrap_next": (
            "gigai init --adopt-package --confirm"
            if resolved_target.kind == "git"
            else "gigai init"
        ),
    }
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(f"Installed catalog package {result.package_id} ({result.content_digest}).")
        click.echo(f"Next: {payload['bootstrap_next']}")


@package_group.command("inspect")
@click.argument("package_path", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def package_inspect_command(package_path: Path, as_json: bool) -> None:
    """Validate one package and report its portable identity."""

    try:
        result = inspect_package(package_path)
    except PackageError as exc:
        raise click.ClickException(str(exc)) from exc
    payload = {
        "package_id": result.package_id,
        "package_version": result.package_version,
        "content_digest": result.content_digest,
        "files": list(result.files),
        "valid": True,
    }
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(f"Package {result.package_id} is valid ({result.content_digest}).")


@package_group.command("install")
@click.argument("package_path", type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", required=True, type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def package_install_command(
    package_path: Path, target: Path, home_value: Path | None, as_json: bool
) -> None:
    """Install portable bytes without importing project or Gig authority."""

    try:
        result = install_package(
            home_root=home_value or default_home_root(),
            requested_target=target,
            source_package=package_path,
        )
    except PackageError as exc:
        raise click.ClickException(str(exc)) from exc
    payload = {
        "package_id": result.package_id,
        "package_digest": result.package_digest,
        "package_root": ".gigai/packages/" + result.package_id,
        "installation_status": result.status,
        "authority_imported": False,
    }
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(f"Installed package {result.package_id} ({result.status}).")


@package_group.command("export")
@click.argument("package_path", type=click.Path(path_type=Path, file_okay=False))
@click.argument("destination", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def package_export_command(package_path: Path, destination: Path, as_json: bool) -> None:
    """Export validated portable bytes without project or Gig authority."""

    try:
        result = export_package(source_package=package_path, destination=destination)
    except PackageError as exc:
        raise click.ClickException(str(exc)) from exc
    payload = {
        "package_id": result.package_id,
        "package_digest": result.package_digest,
        "destination": result.destination.name,
        "export_status": result.status,
        "authority_imported": False,
    }
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(f"Exported package {result.package_id} ({result.status}).")


@cli.command("upgrade")
@click.option(
    "--target",
    type=click.Path(path_type=Path, file_okay=False),
    help="Target directory whose project package boundary should be upgraded.",
)
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option(
    "--confirm",
    "confirmed",
    is_flag=True,
    help="Confirm the v0.1.6-to-v0.1.7 migration.",
)
@click.option("--json", "as_json", is_flag=True)
def upgrade_command(
    target: Path | None, home_value: Path | None, confirmed: bool, as_json: bool
) -> None:
    """Migrate a supported v0.1.6 installation and project boundary."""

    try:
        result = upgrade_installation(
            home_root=home_value or default_home_root(),
            requested_target=target,
            confirmed=confirmed,
        )
    except PackageError as exc:
        raise click.ClickException(str(exc)) from exc
    payload = {
        "migration": "v0.1.6-to-v0.1.7",
        "status": "completed",
        "package_id": result.package.package_id,
        "package_digest": result.package.package_digest,
        "source_config_digest": result.source_config_digest,
        "destination_config_digest": result.destination_config_digest,
        "registry_preserved": result.registry_preserved,
    }
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(f"Upgrade completed with package {result.package.package_id}.")


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
    "--invocation",
    "invocation_path",
    type=click.Path(path_type=Path, dir_okay=False),
    help="Explicit agent invocation envelope containing the proposal input.",
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
    invocation_path: Path | None,
    as_json: bool,
) -> None:
    """Create a proposal from an explicit agent invocation envelope."""

    _require_supported_platform()
    try:
        home = home_value or default_home_root()
        runtime_snapshot = discover_runtime_snapshot(refresh_reason="create")
        persist_discovery_snapshot(home, runtime_snapshot)
        runtime_executables = {
            item.name: str(item.executable)
            for item in runtime_snapshot.models
            if item.executable is not None
        }
        if invocation_path is None:
            raise click.ClickException(
                "create requires an explicit --invocation JSON envelope; "
                "ordinary conversation is not imported"
            )
        invocation = load_invocation_bytes(invocation_path.read_bytes())
        if invocation.command != "create":
            raise click.ClickException("create requires an invocation with command=create")
        envelope_home = invocation.target.get("home")
        if (
            envelope_home is not None
            and Path(envelope_home).expanduser().resolve(strict=False)
            != home.resolve(strict=False)
        ):
            raise click.ClickException(
                "invocation target.home does not match the selected GigAI home"
            )
        intent = invocation.input.get("intent")
        if not isinstance(intent, str) or not intent.strip():
            intent = commission or name
        proposal_input = invocation.input.get("proposal")
        model_output = (
            json.dumps(proposal_input, sort_keys=True, separators=(",", ":"))
            if isinstance(proposal_input, dict)
            else intent
        )
        config = load_config(home)
        requested_models = invocation.requested.get("models", [])
        selected_model_target = model_target or (
            requested_models[0]
            if requested_models
            else _default_create_model_target(config)
        )
        result = create_offline(
            home_root=home,
            requested_target=target_value,
            name=name,
            commission=intent,
            model_target=selected_model_target,
            model_output=model_output,
            runtime_executables=runtime_executables,
            open_editor=False,
        )
        payload = {
            "gig_id": result.gig_id,
            "project_id": result.project_id,
            "proposal_id": result.proposal_id,
            "resumed": result.resumed,
            "status": "proposed",
            "authority_created": False,
        }
        if as_json:
            click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        else:
            click.echo(
                f"Gig proposal {result.proposal_id} is ready for operator review; "
                "no Gig version or Run was created."
            )
        return
    except (LifecycleError, WorkpadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@internal_group.command("improve")
@click.argument("manifest", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--request", "request_value", required=True, help="Human-readable improvement request.")
@click.option("--reference", "reference_values", multiple=True, type=click.Path(path_type=Path, dir_okay=False), required=True, help="Explicit local evidence reference; repeat as needed.")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--model-target")
@click.option("--max-rounds", type=click.IntRange(min=1, max=1024), default=3, show_default=True)
@click.option("--open/--no-open", "open_browser", default=True)
@click.option("--json", "as_json", is_flag=True)
def improve_command(
    manifest: Path,
    request_value: str,
    reference_values: tuple[Path, ...],
    target_value: Path | None,
    home_value: Path | None,
    model_target: str | None,
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
        improve_reference_bytes = dict(started.reference_bytes)
        improve_config = load_config(home)
        selected_model_target = model_target or _default_create_model_target(improve_config)

        def improve_questions(session):
            if (started.workpad / "manifests/gig-discovery-manifest.json").exists():
                return session
            updated = generate_model_questions(
                config=improve_config,
                model_target=selected_model_target,
                session=session,
                reference_bytes=improve_reference_bytes,
                prompt_name=G27_DISCOVERY_PROMPT,
            )
            persist_discovery_manifest(
                start=started,
                session=updated,
                config=improve_config,
                model_target=selected_model_target,
                reference_bytes=improve_reference_bytes,
            )
            return updated

        improve_readiness = resolve_target_readiness(improve_config, selected_model_target)
        improve_capabilities = {
            "local_reference_read": "usable",
            "model_invocation": improve_readiness.readiness,
            "bounded_research": "usable" if improve_readiness.readiness == "usable" else "unavailable",
            "proposal_construction": "usable",
            "approved_run_execution": "unsupported",
            "target_effect": "unsupported",
        }
        active_pointer = json.loads(
            (started.workpad / "manifests/active-gig-version.json").read_text(encoding="utf-8")
        )
        active_proposal = json.loads(
            (started.workpad / "manifests/gig-proposal.json").read_text(encoding="utf-8")
        )
        improve_context_summary = {
            "gig": str(active_proposal.get("name", started.gig_id)),
            "active_version": str(active_pointer.get("active_version", "unknown")),
            "original_references": str(len(started.session.references)),
            "context_boundary": "selected G20 learning records only",
        }
        server = InterviewHTTPServer(
            started.session,
            on_session=lambda session: persist_interview_session(
                workpad=started.workpad,
                project_id=started.project_id,
                gig_id=started.gig_id,
                session=session,
            ),
            on_questions=improve_questions,
            on_approval=lambda session: approve_interview_session(
                home_root=home,
                requested_target=target_value,
                start=started,
                session=session,
            ),
            capability_summary=improve_capabilities,
            context_summary=improve_context_summary,
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
    "--capability-manifest-id",
    default=None,
    help="Optional approved capability manifest referenced by the proposal.",
)
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
        "publication_commit": result.publication_commit,
        "journal_commit": result.sealed_commit,
        "active_pointer": {
            "path": "manifests/active-gig-version.json",
            "publication_commit": result.publication_commit,
        },
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


def _run_plan_payload(result) -> dict[str, object]:
    plan = result.plan
    return {
        "run_plan_id": result.run_plan_id,
        "content_sha256": result.content_sha256,
        "state": plan["state"],
        "gig_id": plan["gig_id"],
        "gig_version": plan["gig_version"],
        "classification": plan["classification"],
        "profile": plan["profile"],
        "phases": plan["phases"],
        "participants": plan["participants"],
        "budget": plan["budget"],
        "sealed_sources": plan["sealed_sources"],
    }


def _run_plan_error(exc: Exception, *, as_json: bool) -> None:
    code = getattr(exc, "code", "run_plan_invalid")
    if as_json:
        click.echo(json.dumps({"ok": False, "error": {"code": code, "message": str(exc), "retryable": False, "invocation_id": None}}, sort_keys=True, separators=(",", ":")))
        raise click.exceptions.Exit(1)
    raise click.ClickException(f"{code}: {exc}")


@cli.group("run-plan")
def run_plan_group() -> None:
    """Create and inspect sealed, bounded review preparation evidence."""


@run_plan_group.command("create")
@click.option("--gig", "gig_id")
@click.option("--version", type=click.IntRange(min=1))
@click.option("--class", "task_class", type=click.Choice(["planning", "research", "fact_check", "document_review", "code_review", "comparison"]))
@click.option("--artifact-class", type=click.Choice(["text", "code", "structured_data", "mixed", "unknown"]))
@click.option("--profile", "profile_id", type=click.Choice(["focused", "standard", "deep", "var"]))
@click.option("--input", "input_paths", type=click.Path(path_type=Path, dir_okay=False), multiple=True, required=True)
@click.option("--reviewer-target", "reviewer_targets", multiple=True, help="Seal one target per reviewer, in participant order.")
@click.option("--verifier-target", "verifier_targets", multiple=True, help="Seal one target per verifier, in participant order.")
@click.option("--adjudicator-target", "adjudicator_targets", multiple=True, help="Seal one target per adjudicator, in participant order.")
@click.option("--reason", "override_reason")
@click.option("--profile-opt-in-reason")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def run_plan_create_command(gig_id: str | None, version: int | None, task_class: str | None, artifact_class: str | None, profile_id: str | None, input_paths: tuple[Path, ...], reviewer_targets: tuple[str, ...], verifier_targets: tuple[str, ...], adjudicator_targets: tuple[str, ...], override_reason: str | None, profile_opt_in_reason: str | None, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """Seal one immutable plan; this command never allocates a Run."""
    try:
        result = create_run_plan(home_root=home_value or default_home_root(), requested_target=target_value, gig_id=gig_id, version=version, task_class=task_class, artifact_class=artifact_class, profile_id=profile_id, input_paths=input_paths, override_reason=override_reason, profile_opt_in_reason=profile_opt_in_reason, reviewer_targets=reviewer_targets, verifier_targets=verifier_targets, adjudicator_targets=adjudicator_targets)
    except (RunPlanError, RunError, WorkpadError, OSError, ValueError) as exc:
        _run_plan_error(exc, as_json=as_json)
        return
    payload = {"ok": True, "plan": _run_plan_payload(result), "diagnostics": []}
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(f"Sealed Run Plan {result.run_plan_id}; no Run was allocated.")


@run_plan_group.command("list")
@click.option("--gig", "gig_id")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def run_plan_list_command(gig_id: str | None, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    try:
        plans = list_run_plans(home_root=home_value or default_home_root(), requested_target=target_value, gig_id=gig_id)
    except (RunPlanError, WorkpadError, OSError, ValueError) as exc:
        _run_plan_error(exc, as_json=as_json)
        return
    payload = {"ok": True, "plans": [_run_plan_payload(item) for item in plans], "diagnostics": []}
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        for item in plans:
            click.echo(f"{item.run_plan_id} {item.plan['state']} {item.plan['profile']['profile_id']}@{item.plan['profile']['profile_version']}")


@run_plan_group.command("show")
@click.argument("run_plan_id")
@click.option("--gig", "gig_id")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def run_plan_show_command(run_plan_id: str, gig_id: str | None, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    try:
        result = read_run_plan(home_root=home_value or default_home_root(), requested_target=target_value, gig_id=gig_id, run_plan_id=run_plan_id)
    except (RunPlanError, WorkpadError, OSError, ValueError) as exc:
        _run_plan_error(exc, as_json=as_json)
        return
    payload = {"ok": True, "plan": _run_plan_payload(result), "diagnostics": []}
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        click.echo(f"Run Plan {result.run_plan_id} {result.plan['state']} for {result.plan['gig_id']}.")


@cli.command("run")
@click.argument("gig_id", required=False)
@click.option("--version", type=click.IntRange(min=1))
@click.option("--plan", "run_plan_id", help="Consume one exact sealed Run Plan with fresh --confirm consent.")
@click.option("--wait", is_flag=True)
@click.option(
    "--execute-review",
    "execute_provider_review",
    is_flag=True,
    help="Execute the sealed provider-backed document-review participants as part of this confirmed Run.",
)
@click.option(
    "--invocation",
    "invocation_path",
    type=click.Path(path_type=Path, dir_okay=False),
    help="Explicit run envelope whose selected Run must also receive direct --confirm.",
)
@click.option(
    "--confirm",
    "confirmed",
    is_flag=True,
    help="Record explicit local-operator consent for this Run.",
)
@click.option(
    "--target", "target_value", type=click.Path(path_type=Path, file_okay=False)
)
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def run_command(
    gig_id: str | None,
    version: int | None,
    run_plan_id: str | None,
    wait: bool,
    execute_provider_review: bool,
    invocation_path: Path | None,
    confirmed: bool,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """Run one approved Gig through its deterministic local capability."""

    _require_supported_platform()
    try:
        selected_home = (home_value or default_home_root()).resolve(strict=False)
        selected_gig_id = gig_id
        selected_version = version
        selected_wait = wait
        invocation = None
        if invocation_path is not None:
            invocation = load_invocation_bytes(invocation_path.read_bytes())
            if invocation.command != "run":
                raise click.ClickException("run requires an invocation with command=run")
            if not confirmed:
                raise click.ClickException(
                    "an agent run envelope requires direct --confirm operator consent"
                )
            envelope_home = invocation.target.get("home")
            envelope_project = invocation.target.get("project")
            if envelope_home is None or envelope_project is None:
                raise click.ClickException(
                    "run invocation target requires home and project"
                )
            if Path(envelope_home).expanduser().resolve(strict=False) != selected_home:
                raise click.ClickException("invocation target.home does not match the selected GigAI home")
            invocation_gig_id = invocation.input.get("gig_id")
            invocation_version = invocation.input.get("version")
            invocation_wait = invocation.input.get("wait")
            if not isinstance(invocation_gig_id, str) or not isinstance(invocation_version, int) or not isinstance(invocation_wait, bool):
                raise click.ClickException(
                    "run invocation input requires gig_id, version, and wait"
                )
            if gig_id is not None and gig_id != invocation_gig_id:
                raise click.ClickException("CLI gig_id does not match invocation input.gig_id")
            if version is not None and version != invocation_version:
                raise click.ClickException("CLI version does not match invocation input.version")
            if wait != invocation_wait:
                raise click.ClickException("CLI --wait does not match invocation input.wait")
            selected_gig_id = invocation_gig_id
            selected_version = invocation_version
            selected_wait = invocation_wait
            resolved = resolve_workpad(
                home_root=selected_home,
                requested_target=target_value,
                gig_id=selected_gig_id,
                allow_semantic_state=True,
            )
            if envelope_project != resolved.project_id:
                raise click.ClickException(
                    "invocation target.project does not match the resolved project"
                )
        if not confirmed:
            raise click.ClickException("run requires direct --confirm operator consent")
        if execute_provider_review and run_plan_id is None:
            raise click.ClickException("--execute-review requires one sealed --plan")
        operator_consent = {
            "schema_version": "1.0",
            "kind": "operator_run_consent",
            "action": "run",
            "actor": {"kind": "operator", "id": "local-user"},
            "source": "direct_cli_confirm",
            **({"invocation_id": invocation.invocation_id} if invocation else {}),
        }
        result = launch_run(
            home_root=selected_home,
            requested_target=target_value,
            gig_id=selected_gig_id,
            version=selected_version,
            wait=selected_wait,
            invocation_argv=tuple(sys.argv),
            operator_consent=operator_consent,
            run_plan_id=run_plan_id,
            execute_provider_review=execute_provider_review,
        )
    except (RunError, ProviderReviewError, WorkpadError, OSError, ValueError, InvocationValidationError) as exc:
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
@click.option(
    "--confirm",
    "confirmed",
    is_flag=True,
    help="Record explicit local-operator consent for this Run.",
)
@click.option("--json", "as_json", is_flag=True)
def occurrence_trigger_command(
    occurrence_id: str,
    wait: bool,
    gig_id: str | None,
    target_value: Path | None,
    home_value: Path | None,
    confirmed: bool,
    as_json: bool,
) -> None:
    """Trigger one declared occurrence through the existing Run path."""

    _require_supported_platform()
    if not confirmed:
        raise click.ClickException(
            "occurrence trigger requires direct --confirm operator consent"
        )
    operator_consent = {
        "schema_version": "1.0",
        "kind": "operator_run_consent",
        "action": "run",
        "actor": {"kind": "operator", "id": "local-user"},
        "source": "direct_cli_confirm",
        "occurrence_id": occurrence_id,
    }
    try:
        result = trigger_occurrence(
            home_root=home_value or default_home_root(),
            requested_target=target_value,
            gig_id=gig_id,
            occurrence_id=occurrence_id,
            wait=wait,
            operator_consent=operator_consent,
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


def _friendly_workflow_message(exc: BaseException) -> tuple[str, str]:
    message = str(exc)
    if "target is not bound to a GigAI project" in message:
        return (
            "project_unbound",
            "This target is not initialized for GigAI. Run `gigai init --target PATH`, "
            "then retry this command.",
        )
    if "registry" in message.lower() and any(
        marker in message.lower() for marker in ("not found", "missing", "does not exist")
    ):
        return (
            "project_unbound",
            "This target is not initialized for GigAI. Run `gigai init --target PATH`, "
            "then retry this command.",
        )
    if "no_active_gig" in message:
        return (
            "no_active_gig",
            "no_active_gig: This project has no selected active Gig. Run `gigai gigs` to inspect "
            "registered Gigs or provide a Gig ID.",
        )
    if "no committed proposal" in message:
        return (
            "proposal_missing",
            "This Gig has no committed proposal. Run `gigai create NAME --target PATH` "
            "to create one.",
        )
    if "target path is unavailable" in message:
        return (
            "target_unavailable",
            "The target path is unavailable. Provide an existing project with "
            "`--target PATH`, then run `gigai init --target PATH`.",
        )
    return (getattr(exc, "code", "cli_workflow_error"), message)


def _raise_projection_error(exc: BaseException, *, as_json: bool) -> None:
    code, message = _friendly_workflow_message(exc)
    _raise_cli_error(message, as_json=as_json, code=code)


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
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
@click.option("--all", "all_projects", is_flag=True, help="List Gigs from all registered projects.")
def gigs_command(
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
    all_projects: bool,
) -> None:
    """List registered Gigs for the bound project without writing workpad state."""

    _require_supported_platform()
    try:
        result = list_gigs(
            home_root=home_value or default_home_root(),
            requested_target=target_value,
            all_projects=all_projects,
        )
    except GigListingError as exc:
        if as_json:
            click.echo(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "error": {"code": exc.code, "message": str(exc)},
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            raise click.exceptions.Exit(1)
        raise click.ClickException(f"{exc.code}: {exc}") from exc
    payload = result.as_dict()
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return
    scope = payload["scope"]
    assert isinstance(scope, dict)
    if scope["kind"] == "all":
        click.echo("All projects")
        click.echo("PROJECT  TITLE  STATUS  VERSION")
        for item in payload["entries"]:
            assert isinstance(item, dict)
            click.echo(
                f"{item['project_label']}  {item['title']}  {item['status']}  {item['version']}"
            )
    else:
        click.echo(f"Project: {scope['label']}")
        click.echo("TITLE  STATUS  VERSION")
        for item in payload["entries"]:
            assert isinstance(item, dict)
            click.echo(f"{item['title']}  {item['status']}  {item['version']}")
    if not payload["entries"]:
        click.echo("No registered Gigs.")
    for diagnostic in payload["diagnostics"]:
        assert isinstance(diagnostic, dict)
        click.echo(
            f"Warning [{diagnostic['code']}]: {diagnostic['message']}",
            err=True,
        )


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
        _raise_projection_error(exc, as_json=as_json)
    payload = projection.proposal
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    elif payload is None:
        click.echo(
            "No committed proposal. Next: run `gigai create NAME --target PATH`."
        )
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
        _raise_projection_error(exc, as_json=as_json)
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
        if payload["proposal_status"] is None:
            click.echo(
                f"{payload['gig_id']}: no committed proposal. Next: run "
                "`gigai create NAME --target PATH`."
            )
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
        _raise_projection_error(exc, as_json=as_json)
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
        _raise_projection_error(exc, as_json=as_json)
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
        _raise_projection_error(exc, as_json=as_json)
    proposal = projection.proposal
    if proposal is None:
        _raise_projection_error(
            WorkpadError("no committed proposal supplies a Goal Graph"),
            as_json=as_json,
        )
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
        _raise_projection_error(exc, as_json=False)
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
        _raise_projection_error(exc, as_json=as_json)
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
        _raise_projection_error(exc, as_json=False)
    if result.opened_workpad and result.opened_target:
        click.echo("Opened the registered workpad and bound target.")
    elif result.opened_workpad:
        click.echo("Opened the registered workpad.")
    else:
        click.echo("Opened the bound target.")


def _default_create_model_target(config) -> str:
    """Resolve the setup-selected create target from the default profile."""

    endpoints = {endpoint.name: endpoint for endpoint in config.endpoints}
    target_names = {
        target.name
        for target in config.model_targets
        if target.enabled
        and endpoints.get(target.endpoint) is not None
        and endpoints[target.endpoint].adapter != "deterministic"
    }
    for profile in config.profiles:
        if profile.name != "default":
            continue
        candidate = profile.gig_creator or profile.planner
        if candidate in target_names:
            return candidate
    if not target_names:
        raise click.ClickException(
            "no usable model runtime is configured; run `gigai setup` and choose "
            "Codex, Claude, or an API target"
        )
    return sorted(target_names)[0]


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

"""``gigai scout install|resume`` — one-step Scout setup for a bound project.

Each command wraps a sequence that previously required the internal
``initialize_defaults``/``approve``/``record create`` path by hand (see the
v0.1.8 UAT log, rows U6/U14/U15): install binds, approves, and activates
Scout in one call; ``resume add`` imports a resume reference and creates the
``g45_reference`` record wrapper find-jobs needs, in one call. Both are
idempotent for the same inputs.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..canonical import canonical_json_bytes, digest_imported_bytes
from ..private_records import PrivateRecordError, create_record, import_reference
from ..setup import default_home_root
from ..workpad import WorkpadError, resolve_bound_project
from .find_jobs.contracts import FindJobsConfig, SourceToggles
from .find_jobs.discovery import (
    DiscoveryBudgetExceeded,
    DiscoveryPrefsError,
    latest_discovery,
    load_prefs,
    run_discovery,
)
from .interview_prep import InterviewPrepError, build_prep
from .template import ScoutInstallError, install_scout


def _resolved_target(target_value: Path | None, home_root: Path) -> Path | None:
    """Turn an implicit ``--target`` into the cwd's already-bound target path.

    Several downstream helpers (``install_scout`` -> ``initialize_defaults``,
    in particular) re-resolve the target themselves via the raw, registry-
    unaware ``resolve_target``, which rejects an implicit (no ``--target``)
    non-Git cwd even when that exact directory is already a registered
    non-Git target (``resolve_bound_project`` -- used by ``gigai scout
    status``/``stop``/``run`` reuse -- already handles this case). Passing an
    *explicit* resolved path down sidesteps that gap without touching those
    helpers: an explicit path never falls into the "no --target given"
    branch. When ``target_value`` was already given, or when no binding can
    be found for the cwd, this returns ``target_value`` unchanged so the
    normal (git-discovery or not-bound) error paths are unchanged.
    """

    if target_value is not None:
        return target_value
    try:
        bound = resolve_bound_project(home_root=home_root, requested_target=None)
    except WorkpadError:
        return None
    return bound.target_root


STARTER_FIND_JOBS_CONFIG = FindJobsConfig(
    roles=("REPLACE_WITH_YOUR_ROLE (e.g. software engineer)",),
    merged_queries=("REPLACE_WITH_YOUR_ROLE (e.g. software engineer)",),
    location="REPLACE_WITH_YOUR_LOCATION (e.g. Denver, CO, or null for any)",
    remote=True,
    published_after=None,
    sources=SourceToggles(exa=True, ats=True, hiringcafe=False),
)


def _emit(payload: dict[str, object], as_json: bool, plain: str) -> None:
    click.echo(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) if as_json else plain
    )


def _fail(exc: Exception, *, as_json: bool, fallback: str) -> None:
    code = getattr(exc, "code", fallback)
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


def write_starter_find_jobs_config(target_root: Path) -> bool:
    """Write a placeholder ``find-jobs.json`` if one doesn't already exist.

    Returns ``True`` if the file was written, ``False`` if it already existed
    (an existing file is never overwritten). The written file validates
    against ``FindJobsConfig.from_json`` like any other find-jobs.json.
    """

    path = target_root / "find-jobs.json"
    if path.exists():
        return False
    encoded = canonical_json_bytes(STARTER_FIND_JOBS_CONFIG.to_json())
    # Round-trip through from_json so a contract change here fails loudly
    # (as a test failure) instead of shipping an unparsable starter file.
    FindJobsConfig.from_json(json.loads(encoded))
    path.write_bytes(encoded)
    return True


@click.group("scout", help="Install and configure the Scout gig for this project.")
def scout_group() -> None:
    pass


@scout_group.command("install")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def install_command(target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """Bind, approve, and activate Scout for the bound project; safe to rerun."""

    home_root = home_value or default_home_root()
    resolved_target = _resolved_target(target_value, home_root)
    try:
        result = install_scout(home_root=home_root, requested_target=resolved_target)
    except (ScoutInstallError, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_install_failed")
        return

    wrote_config = False
    try:
        target_root = (resolved_target or Path.cwd()).expanduser().resolve(strict=True)
        wrote_config = write_starter_find_jobs_config(target_root)
    except OSError as exc:
        _fail(exc, as_json=as_json, fallback="scout_install_failed")
        return

    changed = result.bound or result.approved or result.activated or wrote_config
    payload = {
        "ok": True,
        "gig_id": result.gig_id,
        "bound": result.bound,
        "approved": result.approved,
        "activated": result.activated,
        "wrote_starter_config": wrote_config,
        "changed": changed,
    }
    if as_json:
        _emit(payload, True, "")
        return
    if changed:
        click.echo(
            f"Scout ({result.gig_id}) is installed, approved, and active. "
            "Next: `gigai scout resume add <file>`."
        )
    else:
        click.echo(f"Scout ({result.gig_id}) was already installed, approved, and active.")


@scout_group.group("resume")
def resume_group() -> None:
    """Manage the resume Scout uses for find-jobs runs."""


@resume_group.command("add")
@click.argument("file", type=click.Path(path_type=Path, dir_okay=False, exists=True))
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--gig", "gig_id", help="Explicit Gig ID; defaults to the project's active Gig.")
@click.option("--json", "as_json", is_flag=True)
def resume_add_command(
    file: Path,
    target_value: Path | None,
    home_value: Path | None,
    gig_id: str | None,
    as_json: bool,
) -> None:
    """Import FILE as the resume reference and create the record find-jobs reads.

    Installs/approves/activates Scout first if it isn't yet (same idempotent
    step ``gigai scout install`` and ``gigai scout run`` perform), so this is
    a true one-step command on a freshly bound target. Rerunning is a no-op
    once Scout is installed: this both imports the reference (kind
    ``resume``) and creates the ``g45_reference`` record wrapper (family)
    find-jobs' resume resolution requires, in one call. Rerunning with the
    same file bytes is a no-op: the reference import dedupes by content
    digest and the record uses a digest-derived operation key.
    """

    home_root = home_value or default_home_root()
    resolved_target = _resolved_target(target_value, home_root)
    try:
        install_result = install_scout(home_root=home_root, requested_target=resolved_target)
        installed_scout = install_result.bound or install_result.approved or install_result.activated
        # Matches ensure_scout_ready()'s install_scout -> write starter config
        # sequence in run_supervisor.py, so `resume add` alone (before `scout
        # run`) leaves the target in the same state `scout run` would.
        target_root = (resolved_target or Path.cwd()).expanduser().resolve(strict=True)
        write_starter_find_jobs_config(target_root)
        # Key by name + content digest (not name alone) so re-adding the
        # SAME bytes under the same file name stays idempotent (identical
        # key -> the existing receipt is reused) while re-adding EDITED
        # bytes under the same file name creates a new resume revision
        # instead of conflicting on a stale operation key (P0-4).
        content_digest = digest_imported_bytes(file.read_bytes())
        imported = import_reference(
            home_root=home_root,
            requested_target=resolved_target,
            gig_id=gig_id,
            kind="resume",
            source=file,
            operation_key=f"scout-resume-add:{file.name}:{content_digest}",
        )
        record = create_record(
            home_root=home_root,
            requested_target=resolved_target,
            gig_id=gig_id,
            kind="imported_reference",
            content_family="g45_reference",
            content_id=imported.item_id,
            actor={"kind": "operator", "id": "local-user"},
            origin="imported",
            operation_key=f"scout-resume-record:{imported.item_id}",
        )
    except (ScoutInstallError, PrivateRecordError, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_resume_add_failed")
        return

    payload = {
        "ok": True,
        "scout_installed": installed_scout,
        "gig_id": install_result.gig_id,
        "reference_id": imported.item_id,
        "reference_created": imported.created,
        "record_id": record.record_id,
        "revision_id": record.revision_id,
        "record_created": record.created,
    }
    if as_json:
        _emit(payload, True, "")
        return
    if installed_scout:
        click.echo(f"Scout ({install_result.gig_id}) was installed, approved, and activated.")
    click.echo(
        f"Resume reference {imported.item_id} and record {record.record_id} are ready. "
        "Next: `gigai scout run`."
    )


@scout_group.command("run")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--port", "port", type=int, default=None, help="Loopback port (default 8765).")
@click.option("--no-browser", "no_browser", is_flag=True, help="Don't open a browser tab.")
@click.option(
    "--foreground",
    "foreground",
    is_flag=True,
    help="Run the server in this process (Ctrl-C stops it) instead of detaching it.",
)
@click.option("--json", "as_json", is_flag=True)
def run_command(
    target_value: Path | None,
    home_value: Path | None,
    port: int | None,
    no_browser: bool,
    foreground: bool,
    as_json: bool,
) -> None:
    """Install/activate Scout if needed, then start (or reuse) its API + UI.

    Backgrounded by default: prints the URL and log path and returns. Use
    `gigai scout status` / `gigai scout stop` to check on or stop it, or pass
    --foreground to run it in this process instead (Ctrl-C stops it).
    """

    from . import run_supervisor

    home_root = home_value or default_home_root()
    resolved_target = _resolved_target(target_value, home_root)
    try:
        result = run_supervisor.start(
            home_root=home_root,
            requested_target=resolved_target,
            port=port,
            foreground=foreground,
            open_browser=not no_browser,
        )
    except (run_supervisor.ScoutRunError, WorkpadError, ScoutInstallError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_run_failed")
        return

    payload = {
        "ok": True,
        "reused": result.reused,
        "cleaned_stale": result.cleaned_stale,
        **result.state.to_json(),
    }
    if as_json:
        _emit(payload, True, "")
        return
    if result.cleaned_stale:
        click.echo("Cleaned up a stale Scout run state (its process was no longer running).")
    if result.reused:
        click.echo(f"Scout is already running at {result.state.url} (log: {result.state.log_path}).")
    else:
        click.echo(f"Scout is running at {result.state.url} (log: {result.state.log_path}).")


@scout_group.command("stop")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def stop_command(target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """Stop this project's running Scout instance, if any. Safe to rerun."""

    from . import run_supervisor

    home_root = home_value or default_home_root()
    try:
        stopped = run_supervisor.stop(home_root=home_root, requested_target=target_value)
    except (WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_stop_failed")
        return

    payload = {"ok": True, "stopped": stopped}
    if as_json:
        _emit(payload, True, "")
        return
    click.echo("Stopped Scout." if stopped else "Scout was not running.")


@scout_group.command("status")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def status_command(target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """Show whether this project's Scout instance is running, stopped, or crashed."""

    from . import run_supervisor

    home_root = home_value or default_home_root()
    try:
        current = run_supervisor.status(home_root=home_root, requested_target=target_value)
    except (WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_status_failed")
        return

    payload = {"ok": True, **current.to_json()}
    if as_json:
        _emit(payload, True, "")
        return
    if current.state == "running":
        click.echo(f"running: {current.url} (pid {current.pid}, log: {current.log_path})")
    elif current.state == "crashed":
        click.echo(
            f"crashed: last known pid {current.pid} is no longer running "
            f"(log: {current.log_path}). Run `gigai scout run` to restart it."
        )
    else:
        click.echo("stopped")


def _relative_days_ago(iso_timestamp: str) -> str:
    from datetime import UTC, datetime

    try:
        then = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return iso_timestamp
    delta = datetime.now(UTC) - then
    days = delta.days
    if days <= 0:
        return "today"
    if days == 1:
        return "1 day ago"
    return f"{days} days ago"


@scout_group.command("discover")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--runs", "runs", type=int, default=3, help="Number of OpenAI web_search runs to merge (default 3).")
@click.option("--status", "show_status", is_flag=True, help="Show the last discovery run instead of starting a new one.")
@click.option("--json", "as_json", is_flag=True)
def discover_command(
    target_value: Path | None,
    home_value: Path | None,
    runs: int,
    show_status: bool,
    as_json: bool,
) -> None:
    """Run the weekly company-discovery engine (OpenAI web_search + H-1B baseline).

    Runs in the foreground -- this can take 5-30 minutes (OpenAI web_search
    latency under TPM backoff). Prints a summary of new watchlist boards,
    cost, and where results are stored. Requires `gigai scout discover`'s
    prefs to already be set (the setup interview -- packet S2-B); use
    `--status` to see the last run without starting a new one.
    """

    home_root = home_value or default_home_root()
    resolved_target = _resolved_target(target_value, home_root)
    target = (resolved_target or Path.cwd()).expanduser().resolve(strict=True)

    if show_status:
        try:
            result = latest_discovery(home_root=home_root, target=target)
        except (WorkpadError, OSError, ValueError) as exc:
            _fail(exc, as_json=as_json, fallback="scout_discover_status_failed")
            return
        if result is None:
            payload: dict[str, object] = {"ok": True, "has_run": False}
            if as_json:
                _emit(payload, True, "")
                return
            click.echo("No discovery run yet. Run `gigai scout discover` to start one.")
            return
        payload = {"ok": True, "has_run": True, **result.to_json()}
        if as_json:
            _emit(payload, True, "")
            return
        when = _relative_days_ago(result.started_at)
        click.echo(
            f"Last discovery run: {result.status} ({when}), "
            f"{len(result.new_boards)} new board(s), cost ${result.cost_usd:.4f}."
        )
        return

    try:
        prefs = load_prefs(home_root=home_root, target=target)
    except (DiscoveryPrefsError, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_discover_failed")
        return
    if prefs is None:
        _fail(
            DiscoveryPrefsError(
                "discovery_prefs_missing",
                "no discovery preferences set yet; run the Scout setup interview first",
            ),
            as_json=as_json,
            fallback="scout_discover_failed",
        )
        return

    def _on_progress(event: dict) -> None:
        if as_json:
            return
        stage = event.get("stage", "")
        if stage == "discovery_start":
            click.echo(f"Starting discovery ({event.get('runs')} OpenAI run(s))...")
        elif stage == "openai_call":
            click.echo(f"OpenAI web_search run {event.get('run_index', 0) + 1}/{event.get('of')}...")
        elif stage == "openai_retry":
            click.echo(f"  rate limited, waiting {event.get('wait_seconds', 0):.0f}s...")
        elif stage == "h1b_download_start":
            size = event.get("size_bytes")
            size_text = f"{size / 1_000_000:.1f}MB" if isinstance(size, int) else "unknown size"
            click.echo(f"Downloading DOL H-1B disclosure file ({size_text})...")
        elif stage == "merge_board_check":
            click.echo(f"Checking board {event.get('index', 0) + 1}/{event.get('of')}: {event.get('company')}")

    try:
        result = run_discovery(home_root=home_root, target=target, prefs=prefs, runs=runs, on_progress=_on_progress)
    except (DiscoveryBudgetExceeded, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_discover_failed")
        return

    payload = {"ok": True, **result.to_json()}
    if as_json:
        _emit(payload, True, "")
        return
    click.echo(f"Discovery {result.status}: {len(result.new_boards)} new board(s) added to the watchlist.")
    for board in result.new_boards:
        click.echo(f"  + {board['company']} ({board['provider']}:{board['board_token']})")
    click.echo(f"Cost: ${result.cost_usd:.4f}. Stored under discovery/runs/{result.discovery_id}.json (GigAI home).")
    if result.skipped:
        skipped_text = ", ".join(f"{reason}: {count}" for reason, count in sorted(result.skipped.items()))
        click.echo(f"Skipped: {skipped_text}")
    for source in result.sources:
        if source.error:
            click.echo(f"Warning: {source.name} had an error: {source.error}")
        if source.skip_reason:
            click.echo(f"Note: {source.name} was skipped: {source.skip_reason}")


@scout_group.command("prep")
@click.argument("posting_url")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--run", "run_id", help="Find-jobs run ID to resolve the posting from (default: newest run that has it).")
@click.option("--refresh", is_flag=True, help="Re-run prep even if one is already stored for this posting and resume revision.")
@click.option("--budget", "budget_usd", type=float, default=0.50, show_default=True, help="Max USD to spend on company-research web search.")
@click.option("--json", "as_json", is_flag=True)
def prep_command(
    posting_url: str,
    target_value: Path | None,
    home_value: Path | None,
    run_id: str | None,
    refresh: bool,
    budget_usd: float,
    as_json: bool,
) -> None:
    """Prepare for an interview at POSTING_URL (a find-jobs-acquired posting).

    Foreground -- company research (OpenAI web_search, ~seconds) plus one
    model call for likely question categories. Idempotent per (posting,
    resume revision); pass --refresh to re-run. The resume is sent only to
    the question-category model call, never to the company-research web
    search. Prints a summary and where the prep is stored.
    """

    home_root = home_value or default_home_root()
    resolved_target = _resolved_target(target_value, home_root)
    target = (resolved_target or Path.cwd()).expanduser().resolve(strict=True)

    def _on_progress(event: dict) -> None:
        if as_json:
            return
        stage = event.get("stage", "")
        if stage == "resolve_posting":
            click.echo("Resolving posting from find-jobs acquire output...")
        elif stage == "company_research":
            click.echo("Researching company (OpenAI web_search)...")
        elif stage == "openai_retry":
            click.echo(f"  rate limited, waiting {event.get('wait_seconds', 0):.0f}s...")
        elif stage == "role_research":
            click.echo("Reading role research from the posting text...")
        elif stage == "question_categories":
            click.echo("Predicting likely question categories...")

    try:
        prep = build_prep(
            home_root=home_root, target=target, posting_url=posting_url,
            run_id=run_id, refresh=refresh, budget_usd=budget_usd,
            on_progress=_on_progress,
        )
    except InterviewPrepError as exc:
        _fail(exc, as_json=as_json, fallback="scout_prep_failed")
        return

    prep_json = prep.to_json()
    payload = {"ok": True, **prep_json}
    if as_json:
        _emit(payload, True, "")
        return
    click.echo(f"Interview prep for {prep.title} at {prep.company}:")
    if prep.company_research.skipped:
        click.echo(f"  Company research skipped: {prep.company_research.skipped}")
    else:
        click.echo(f"  Company research: {len(prep.company_research.claims)} sourced claim(s), ${prep.company_research.cost_usd:.4f}")
    click.echo(f"  Role research: {len(prep.role_research.responsibilities)} responsibilit(y/ies), {len(prep.role_research.requirements)} requirement(s)")
    click.echo(f"  Likely question categories ({prep.model_target}):")
    for category in prep.question_categories:
        click.echo(f"    - {category.category}: {category.why}")
    if prep.prep_notes.matrix_source == "assess":
        click.echo(f"  Prep notes: {len(prep.prep_notes.resume_points)} resume point(s) to lead with, {len(prep.prep_notes.gaps)} gap(s) to prepare for.")
    else:
        click.echo("  Prep notes: no assess matrix found for this posting yet; run `gigai scout run` to assess it for richer notes.")
    click.echo(f"  Total cost: ${prep.cost_usd:.4f}. Stored under scout/interview_prep/{prep.posting_id}.json (GigAI home).")


__all__ = ["scout_group", "write_starter_find_jobs_config", "STARTER_FIND_JOBS_CONFIG"]

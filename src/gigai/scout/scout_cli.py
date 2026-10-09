"""``gigai scout install|resume`` — one-step Scout setup for a bound project.

Each command wraps a sequence that previously required the internal
``initialize_defaults``/``approve``/``record create`` path by hand (see the
v0.1.8 UAT log, rows U6/U14/U15): install binds, approves, and activates
Scout in one call; ``resume add`` imports a resume reference and creates the
``g45_reference`` record wrapper find-jobs needs, in one call. Both are
idempotent for the same inputs.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
import time
from typing import TYPE_CHECKING

import click

from ..canonical import canonical_json_bytes
from ..private_records import PrivateRecordError
from ..setup import default_home_root
from ..workpad import WorkpadError, committed_read_cache
from .find_jobs.contracts import FindJobsConfig, ModelTarget, SourceToggles
from .find_jobs.discovery import (
    DiscoveryBudgetExceeded,
    DiscoveryPrefsError,
    latest_discovery,
    load_prefs,
    run_discovery,
)
from .interview_prep import InterviewPrepError, build_prep
from .master_cli import master_group
from .resume_gate_cli import resume_check_command, resume_clean_command
from .resume_import import import_resume_file
from .resume_pii import REMOVED_MESSAGE, RESUME_WARNING, removed_summary
from .resume_privacy import heading_link_message
from .target_resolution import ScoutTargetError, _display_path, resolve_scout_target
from .template import ScoutInstallError, install_scout

if TYPE_CHECKING:
    from .run_supervisor import OtherScoutServer


def _reads_committed(command):
    """A command that only reads: one ``workpad.committed_read_cache`` around all of it (0110-044).

    Outside that block every ``resolve_workpad`` and every journal read checks
    the workpad again (ten git subprocesses each; ``story-bank list`` did it
    nine times). Inside it a check that passed, and a committed read that was
    made, are reused while the workpad's fingerprint is the same.
    """

    @functools.wraps(command)
    def reading(*args, **kwargs):
        with committed_read_cache():
            return command(*args, **kwargs)

    return reading


def _resolved_target(
    target_value: Path | None,
    home_root: Path,
    *,
    username: str | None = None,
    as_json: bool = False,
) -> Path:
    """Resolve the folder a Scout command should act on.

    Delegates to the shared resolver (``--target``, else ``<home>/scout``,
    created when missing; the cwd never matters, uat-bug-017) so every
    ``gigai scout ...`` command shares one resolution order instead of
    each re-deriving it. Unlike the old per-command helper this always
    returns a usable path; a genuinely unresolvable case raises
    ``ScoutTargetError``, which callers catch alongside their other target
    errors. When resolution creates ``<home>/scout``, prints which username
    was used, and the first time it sees a Scout project registered
    elsewhere, a notice naming it (both plain text only -- ``--json`` output
    stays parseable, and the notice waits for the next plain-text command).
    """

    def _announce(created: Path, resolved_username: str) -> None:
        if as_json:
            return
        click.echo(f"No Scout target found; created {created} (owner: {resolved_username}).")

    def _notice(message: str) -> None:
        click.echo(message, err=True)

    return resolve_scout_target(
        home_root=home_root,
        requested_target=target_value,
        requested_username=username,
        on_created=_announce,
        notify=None if as_json else _notice,
    )


# uat-bug-033: required: one model target; optional: Exa. A NEW starter config
# writes sources.exa = false (the bundled company list and board index are the
# source); Exa is an extra the operator turns on in Settings. An EXISTING
# find-jobs.json keeps whatever it saved: `sources.exa` is a required key of
# the contract (never defaulted on read), so no old file changes meaning.
STARTER_FIND_JOBS_CONFIG = FindJobsConfig(
    roles=("REPLACE_WITH_YOUR_ROLE (e.g. software engineer)",),
    merged_queries=("REPLACE_WITH_YOUR_ROLE (e.g. software engineer)",),
    # uat-bug-028: no location (no area preference) rather than placeholder
    # text: an area is set in the setup wizard, and the placeholder is
    # refused on save (and read as no location in an older file).
    location=None,
    remote=True,
    published_after=None,
    sources=SourceToggles(exa=False, ats=True, hiringcafe=False),
    # uat-bug-049: the starter's model target is the contract default
    # (ollama_local, a local target), so its cap is "all" like a new wizard
    # config with a local target; an existing file is never rewritten.
    default_assess_cap="all",
)


def _emit(payload: dict[str, object], as_json: bool, plain: str) -> None:
    click.echo(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) if as_json else plain
    )


def _pipeline_fired(pending: object, payload: dict[str, object] | None = None, *, as_json: bool = True) -> None:
    """0.1.10.7 PL5: queue the jobs a saved answer or story concerns (never fails the save) and say so.

    With ``payload`` (JSON output) the trigger's result is added as ``pipeline`` when it queued anything;
    otherwise one line is printed.
    """

    fired = pending.fire()  # type: ignore[attr-defined]
    if fired.state != "fired":
        return
    if payload is not None:
        payload["pipeline"] = fired.to_json()
    elif not as_json and fired.line():
        click.echo(fired.line())


def _fail(exc: Exception, *, as_json: bool, fallback: str, assess: bool = False) -> None:
    code = getattr(exc, "code", fallback)
    # 0110-10-13: a failed ASSESSMENT also says whether a model call started, may have used tokens, and what to do next.
    from .assess_causes import cause_fields, cause_lines

    if as_json:
        click.echo(
            json.dumps(
                {"status": "error", "error": {"code": code, "message": str(exc), **(cause_fields(code) if assess else {})}},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        raise click.exceptions.Exit(1)
    raise click.ClickException("\n".join([str(exc), *(cause_lines(code) if assess else ())]))


def _ensure_gigai_settings(home_root: Path, *, as_json: bool) -> None:
    """On a fresh machine, write the settings `gigai setup` writes by default.

    uat-bug-050: `gigai scout run` / `install` are the first commands a new
    user types. With no ``<home>/config.toml`` they used to stop at "run
    'gigai setup'"; now they write exactly what an Enter-through ``gigai
    setup`` writes and carry on. An existing config.toml (valid, invalid, or
    older) is never touched here: it is loaded and reported as before.
    """

    config_file = home_root.expanduser().resolve(strict=False) / "config.toml"
    if config_file.exists() or config_file.is_symlink():
        return
    # Imported here: gigai.cli imports this module to register `gigai scout`.
    from ..cli import write_default_setup

    write_default_setup(home_root, as_json=as_json)
    if not as_json:
        click.echo(f"Created GigAI settings with defaults at {_display_path(config_file)}")


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
@click.option(
    "--username",
    "username",
    help=(
        "Workspace-owner display name to use if this creates a new default "
        "Scout target (<home>/scout). Ignored otherwise; see `gigai init "
        "--username`."
    ),
)
@click.option("--json", "as_json", is_flag=True)
def install_command(
    target_value: Path | None, home_value: Path | None, username: str | None, as_json: bool
) -> None:
    """Bind, approve, and activate Scout for the bound project; safe to rerun."""

    home_root = home_value or default_home_root()
    _ensure_gigai_settings(home_root, as_json=as_json)
    try:
        resolved_target = _resolved_target(target_value, home_root, username=username, as_json=as_json)
        result = install_scout(home_root=home_root, requested_target=resolved_target)
    except (ScoutTargetError, ScoutInstallError, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_install_failed")
        return

    wrote_config = False
    try:
        target_root = resolved_target.expanduser().resolve(strict=True)
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
        # install never touches a server; the key keeps the shape the same as `run`.
        "stopped_server": None,
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


resume_group.add_command(resume_check_command)
resume_group.add_command(resume_clean_command)
resume_group.add_command(master_group)  # 0.1.10.9 master P1: `gigai scout resume master show|init|history`


@resume_group.command("add")
@click.argument("file", type=click.Path(path_type=Path, dir_okay=False, exists=True))
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--gig", "gig_id", help="Explicit Gig ID; defaults to the project's active Gig.")
@click.option("--profile", "profile_id", help="Scout profile ID to attach this resume to; defaults to the SELECTED profile.")
@click.option("--json", "as_json", is_flag=True)
def resume_add_command(
    file: Path,
    target_value: Path | None,
    home_value: Path | None,
    gig_id: str | None,
    profile_id: str | None,
    as_json: bool,
) -> None:
    """Import FILE as the resume reference and create the record find-jobs reads.

    On a fresh machine it first writes the default GigAI settings (as `gigai
    scout run` does), so it works as the very first command. Then it
    installs/approves/activates Scout if it isn't yet (same idempotent
    step ``gigai scout install`` and ``gigai scout run`` perform), so this is
    a true one-step command on a freshly bound target. Rerunning is a no-op
    once Scout is installed: this both imports the reference (kind
    ``resume``) and creates the ``g45_reference`` record wrapper (family)
    find-jobs' resume resolution requires, in one call. Rerunning with the
    same file bytes is a no-op: the reference import dedupes by content
    digest and the record uses a digest-derived operation key.

    S25 F1-b2 (operator decision): without ``--profile``, the imported
    resume is ATTACHED to the gig's SELECTED profile -- its ``resume_ref``
    moves to the newly imported resume (``profile_records.write_profile``,
    a revision bump) -- and this command PRINTS which profile it attached
    to (label + profile_id; ``profile_id`` alone in ``--json`` output). A
    given ``--profile`` attaches to that profile instead of the selected
    one. When no profile can be resolved yet (no ``find-jobs.json``/no prior
    resume to migrate from), the import still succeeds exactly as before
    F1-b2 -- there is simply nothing to attach to yet; a later migration or
    profile creation will pin this resume as its own initial ``resume_ref``.
    """

    home_root = home_value or default_home_root()
    # 0.1.10 (ledger 33b): as the very first command on a fresh HOME, set up
    # exactly as `scout run` / `scout install` do (uat-bug-050).
    _ensure_gigai_settings(home_root, as_json=as_json)
    try:
        resolved_target = _resolved_target(target_value, home_root, as_json=as_json)
        install_result = install_scout(home_root=home_root, requested_target=resolved_target)
        installed_scout = install_result.bound or install_result.approved or install_result.activated
        # Matches ensure_scout_ready()'s install_scout -> write starter config
        # sequence in run_supervisor.py, so `resume add` alone (before `scout
        # run`) leaves the target in the same state `scout run` would.
        target_root = resolved_target.expanduser().resolve(strict=True)
        write_starter_find_jobs_config(target_root)
        # uat-bug-020: the import itself (reference + record, and their
        # operation keys) is resume_import.import_resume_file, shared with
        # the setup wizard's POST /api/resumes.
        resume = import_resume_file(
            home_root=home_root,
            requested_target=resolved_target,
            source=file,
            gig_id=gig_id,
        )

        attached_profile = _attach_resume_to_profile(
            home_root=home_root,
            target_root=target_root,
            gig_id=gig_id,
            profile_id=profile_id,
            record_id=resume.record_id,
            revision_id=resume.revision_id,
            content_sha256=resume.content_sha256,
        )
    except (ScoutTargetError, ScoutInstallError, PrivateRecordError, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_resume_add_failed")
        return
    if attached_profile is not None:
        # 0.1.10.7 PL5: a new resume re-opens this profile's pipeline steps whose inputs changed (never fails the add).
        from .pipeline import triggers as pipeline_triggers

        pipeline_triggers.profile_changed(home_root, target_root, attached_profile.profile_id)

    payload = {
        "ok": True,
        "scout_installed": installed_scout,
        "gig_id": install_result.gig_id,
        "reference_id": resume.reference_id,
        "reference_created": resume.reference_created,
        "record_id": resume.record_id,
        "revision_id": resume.revision_id,
        "record_created": resume.record_created,
        "profile_id": attached_profile.profile_id if attached_profile is not None else None,
        "warning": RESUME_WARNING,
        # 0110-046: what the import removed and discarded (counts only), or null.
        "contact_removed": {"removed": resume.contact_removed, "message": REMOVED_MESSAGE} if resume.contact_removed else None,
    }
    if resume.heading_links:
        # 0.1.10.11: a link in a heading went and the heading is kept: by line number and the heading's words, never the address.
        payload["contact_removed"]["headings"] = [  # type: ignore[index]
            {"kind": "link", "line": line, "heading": words, "where": "line_under_heading" if under else "heading", "message": heading_link_message(words, under)}
            for line, words, under in resume.heading_links
        ]
    if as_json:
        _emit(payload, True, "")
        return
    if installed_scout:
        click.echo(f"Scout ({install_result.gig_id}) was installed, approved, and activated.")
    click.echo(
        f"Resume reference {resume.reference_id} and record {resume.record_id} are ready. "
        "Next: `gigai scout run`."
    )
    if attached_profile is not None:
        click.echo(f"Attached to profile {attached_profile.label} ({attached_profile.profile_id})")
    if resume.contact_removed:
        click.echo(f"{REMOVED_MESSAGE} (removed: {removed_summary(resume.contact_removed)})")
    if resume.heading_links:
        click.echo("Kept without its link: " + "; ".join(f"line {line}: {heading_link_message(words, under)}" for line, words, under in resume.heading_links) + ".")
    click.echo(f"Warning: {RESUME_WARNING}")


def _attach_resume_to_profile(
    *,
    home_root: Path,
    target_root: Path,
    gig_id: str | None,
    profile_id: str | None,
    record_id: str,
    revision_id: str,
    content_sha256: str,
):
    """Move a profile's ``resume_ref`` to the just-imported resume; ``None`` if there's no profile yet.

    ``profile_id=None`` (the default) attaches to the gig's SELECTED
    profile (migrating a default profile on first read, like every other
    F1-b2 profile-aware path); a given ``profile_id`` attaches to that
    committed profile instead, refusing one that isn't committed in this
    gig. Returns ``None`` (no attach) only when no profile exists yet at
    all and none was explicitly named -- the import above still succeeded.
    """

    from ..workpad import resolve_workpad
    from .find_jobs.contracts import PinnedResume
    from . import profile_records

    resolved = resolve_workpad(
        home_root=home_root, requested_target=target_root, gig_id=gig_id, allow_semantic_state=True
    )
    resume_ref = PinnedResume(record_id=record_id, revision_id=revision_id, content_sha256=content_sha256)

    if profile_id is None:
        target_profile = profile_records.selected_profile(resolved, home_root=home_root, target=target_root)
        if target_profile is None:
            return None
    else:
        profiles = {item.profile_id: item for item in profile_records.list_profiles(resolved)}
        target_profile = profiles.get(profile_id)
        if target_profile is None:
            raise ScoutTargetError(f"profile {profile_id!r} is not committed in this gig")

    return profile_records.write_profile(resolved, profile_id=target_profile.profile_id, resume_ref=resume_ref)


@resume_group.command("length")
@click.option("--job-url", "job_url", required=True, help="The posting URL the resume was tailored to.")
@click.option("--profile", "profile_id", help="The Scout profile ID the resume was tailored for (default: the newest).")
@click.option("--restore", "restore", is_flag=True, help="Put back every role and bullet that was left out for length.")
@click.option("--cut", "cut", is_flag=True, help="After --restore: leave the same roles and bullets out again.")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def resume_length_command(
    job_url: str, profile_id: str | None, restore: bool, cut: bool, target_value: Path | None, home_value: Path | None, as_json: bool
) -> None:
    """What a stored tailored resume left out for length, and the way back.

    A tailored resume over 2 pages leaves out whole roles, the oldest first,
    until it fits, and a role that ended more than 8 years ago keeps its
    first 3 bullets; nothing else is cut for length. With no flag this
    prints what was left out. --restore puts all of it back; --cut leaves
    the same things out again. No model call.
    """

    from .find_jobs.contracts import FindJobsContractError
    from .find_jobs.job_state import normalize_job_identity
    from .quick_assess import QuickAssessError
    from .tailor_length import length_note
    from .tailor_length_store import change_stored_length
    from .tailored_resume import list_tailored_resumes

    home_root = home_value or default_home_root()
    if restore and cut:
        _fail(ValueError("pass at most one of --restore or --cut"), as_json=as_json, fallback="invalid_value")
        return
    try:
        target = _resolved_target(target_value, home_root, as_json=as_json).expanduser().resolve(strict=True)
        job_identity = normalize_job_identity(job_url)
        if restore or cut:
            stored = change_stored_length(
                home_root, target, profile_id=profile_id or None, job_identity=job_identity, use="restore" if restore else "cut"
            )
        else:
            items = list_tailored_resumes(home_root, target, profile_id=profile_id or None, job_identity=job_identity)
            if not items:
                raise QuickAssessError("tailored_resume_not_found", "no stored resume for that job; assess it (`gigai scout assess --job-url ...`) so a resume is picked, then run this again")
            stored = items[0]
    except (ScoutTargetError, WorkpadError, QuickAssessError, FindJobsContractError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_resume_length_failed")
        return
    length = stored.result.length
    payload = {
        "ok": True,
        "profile_id": stored.resume.profile_id,
        "job_identity": stored.job.job_identity,
        "updated_at": stored.updated_at,
        "length": None if length is None else length.to_json(),
        "markdown_path": stored.markdown_path,
    }
    lines = [length_note(length) or "Length: fits with nothing left out."]
    if length is not None and length.status == "cut":
        lines.append(f"Put it back: gigai scout resume length --job-url {job_url} --restore")
    elif length is not None and length.status == "restored":
        lines.append(f"Cut for length again: gigai scout resume length --job-url {job_url} --cut")
    lines.append(f"Markdown: {stored.markdown_path}")
    _emit(payload, as_json, "\n".join(lines))


@resume_group.command("tailor", hidden=True)
@click.option("--job-url", "job_url", help="Public job posting URL to fetch and tailor the resume to.")
@click.option("--job-text", "job_text_file", help="File with the posting text (or - for stdin).")
@click.option("--profile", "profile_id", help="Scout profile ID whose pinned resume to tailor (default: the selected profile).")
@click.option("--resume", "resume_file", help="Resume text FILE (or - for stdin), used for this call only; never imported.")
@click.option("--resume-text", "resume_text", help="Resume text inline, used for this call only; never imported.")
@click.option("--title", "title", help="Job title override (pasted text has none).")
@click.option("--company", "company", help="Company override (pasted text has none).")
@click.option("--model-target", "model_target", type=click.Choice([item.value for item in ModelTarget]), help="Adapter kind to tailor with (default: find-jobs.json's default_model_target).")
@click.option("--out", "out_file", type=click.Path(path_type=Path, dir_okay=False), help="Also write the markdown to FILE.")
@click.option("--in", "in_file", help="With --job-url: store this edited resume markdown FILE (or - for stdin) as the job's tailored resume instead of asking a model.")
@click.option("--as", "--actor", "actor", type=click.Choice(["operator", "agent"]), default="operator", show_default=True, help="With --in: who wrote the edited resume. An agent passes --as agent.")
@click.option("--source", "source", help="With --in: where the edit came from, in your own words (one line).")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def resume_tailor_command(
    job_url: str | None,
    job_text_file: str | None,
    profile_id: str | None,
    resume_file: str | None,
    resume_text: str | None,
    title: str | None,
    company: str | None,
    model_target: str | None,
    out_file: Path | None,
    in_file: str | None,
    actor: str,
    source: str | None,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """Tailor a resume to ONE job posting right now (Q3) -- markdown, every line sourced.

    Pass exactly one of --job-url / --job-text, and at most one of --profile /
    --resume / --resume-text (none means the selected profile's resume). Every
    line of the output is either a resume line copied verbatim or a rewrite
    that cites the resume lines / answered questions it came from; the
    validator rejects any number or posting skill the cited sources do not
    state (one retry, then an error). Synchronous: one model call plus at
    most one retry. Prints the markdown path (and copies the markdown to
    --out FILE when given). The markdown also goes to your jobs folder
    (`gigai scout jobs-folder`).

    With --in FILE --job-url URL no model tailors: FILE (resume markdown in
    GigAI's format, for example the job's resume.md from your jobs folder,
    edited) is stored as that job's tailored resume, marked edited with who
    wrote it (--as). An unchanged line keeps its sources; a changed or new
    line is checked: no name or contact detail, and every number and skill
    it states must be in your resume or an answer (save an answer first when
    it is not). The Scout ATS score and the Scout label are then run again
    on it (one model call for the re-assessment), and background tailoring
    never replaces an edited resume.
    """

    from .find_jobs.assess_contracts import AssessJobInput, AssessResumeInput
    from .find_jobs.contracts import FindJobsContractError
    from .quick_assess import QuickAssessError
    from .tailored_resume import TailorRequest, run_tailored_resume

    home_root = home_value or default_home_root()
    try:
        resolved_target = _resolved_target(target_value, home_root, as_json=as_json)
        target = resolved_target.expanduser().resolve(strict=True)
    except (ScoutTargetError, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_resume_tailor_failed")
        return

    if job_url and job_text_file:
        _fail(ValueError("pass exactly one of --job-url or --job-text"), as_json=as_json, fallback="job_input_invalid")
        return
    if in_file:
        if not job_url or any(item for item in (resume_file, resume_text, title, company, model_target)):
            _fail(
                ValueError("--in FILE goes with --job-url URL (and --profile, --as, --source, --out): no model tailors, so no other option applies"),
                as_json=as_json, fallback="invalid_value",
            )
            return
        from .resume_job_cli import store_resume  # 0.1.11 N5: the old spelling of `gigai scout resume store`

        store_resume(in_file, job_url, profile_id, actor, source, out_file, home_root, target, as_json, renamed_from="gigai scout resume tailor --in")
        return
    from .pipeline.settings import pipeline_setting
    from .pipeline.steps import StepError

    if not pipeline_setting(home_root, target).enabled:
        # 0.1.11: tailoring is switched off (the background pipeline's switch); the resume is picked from the master. No model call.
        _fail(
            StepError("tailoring_off", "tailoring is switched off in 0.1.11: the resume is picked from your master, word for word (`gigai scout resume pick`)"),
            as_json=as_json, fallback="tailoring_off",
        )
        return
    if sum(1 for item in (profile_id, resume_file, resume_text) if item) > 1:
        _fail(ValueError("pass at most one of --profile, --resume or --resume-text"), as_json=as_json, fallback="resume_input_invalid")
        return
    try:
        job_text = _read_text_option(job_text_file, flag="--job-text") if job_text_file else None
        if resume_file:
            resume_text = _read_text_option(resume_file, flag="--resume")
    except OSError as exc:
        _fail(exc, as_json=as_json, fallback="input_file_unreadable")
        return

    try:
        request = TailorRequest(
            job=AssessJobInput(job_url=job_url or None, job_text=job_text or None, title=title, company=company),
            resume=AssessResumeInput(profile_id=profile_id or None, resume_text=resume_text or None),
            model_target=None if model_target is None else ModelTarget(model_target),
        )
    except FindJobsContractError as exc:
        _fail(exc, as_json=as_json, fallback="invalid_value")
        return

    if not as_json:
        click.echo("Tailoring (one model call plus at most one retry; this can take a minute)...")
    try:
        response = run_tailored_resume(request, home_root=home_root, target=target)
    except QuickAssessError as exc:
        _fail(exc, as_json=as_json, fallback="scout_resume_tailor_failed")
        return

    out_path: Path | None = None
    if out_file is not None:
        out_path = out_file.expanduser()
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(response.markdown, encoding="utf-8")
        except OSError as exc:
            _fail(exc, as_json=as_json, fallback="output_file_unwritable")
            return

    if as_json:
        _emit({"ok": True, **response.to_json(), "out_path": None if out_path is None else str(out_path)}, True, "")
        return
    job = response.job
    heading = job.title or "(untitled posting)"
    if job.company:
        heading += f" at {_company_shown(home_root, job.company)}"
    result = response.result
    rewritten = len(result.rewritten_lines())
    click.echo(f"Tailored resume for {heading}:")
    from .tailor_master import made_from

    click.echo(f"  Resume: {made_from(response)}")
    click.echo(f"  Sections: {', '.join(section.heading for section in result.sections)}")
    click.echo(f"  Lines: {result.line_count()} ({rewritten} rewritten, every one citing its resume lines / answers)")
    if result.length is not None:
        from .tailor_length import length_note

        click.echo(f"  {length_note(result.length)}")
        if result.length.status == "cut" and job_url:
            click.echo(f"  Put it back: gigai scout resume length --job-url {job_url} --restore")
    click.echo(f"  Model: {response.producer.model_target.value} ({response.producer.adapter})")
    click.echo(f"  Markdown: {response.markdown_path}")
    click.echo(f"  Stored at {response.stored_path}")
    folder_file = _resume_folder_file(home_root, response)
    if folder_file is not None:
        click.echo(f"  In your jobs folder: {folder_file}")
    if out_path is not None:
        click.echo(f"  Copied to {out_path}")


def _resume_folder_file(home_root: Path, response: object) -> str | None:
    """Where the jobs folder holds this stored job resume's markdown (as the user types it), or ``None``."""

    from . import jobs_folder

    folder = jobs_folder.stored_job_folder(home_root, response.stored_path)  # type: ignore[attr-defined]
    return None if folder is None else folder.resume_shown


def _attach_edited_resume(
    in_file: str, job_url: str, profile_id: str | None, actor: str, source: str | None, out_file: Path | None,
    home_root: Path, target: Path, as_json: bool,
) -> None:
    """``gigai scout resume tailor --in FILE --job-url URL`` (0110-10-05 B): store the edited markdown, then run the checks again."""

    from .pipeline.runner import pipeline_status, run_once
    from .quick_assess import QuickAssessError
    from .tailored_resume_edit import attach_edited_resume, queue_recheck

    try:
        markdown = _read_text_option(in_file, flag="--in")
    except (OSError, ValueError) as exc:  # ValueError: not UTF-8
        _fail(exc, as_json=as_json, fallback="input_file_unreadable")
        return
    try:
        attached = attach_edited_resume(
            markdown, job_url=job_url, profile_id=profile_id or None, written_by=actor, source=source, home_root=home_root, target=target
        )
    except QuickAssessError as exc:
        _fail(exc, as_json=as_json, fallback="scout_resume_tailor_failed")
        return
    response = attached.response
    out_path: Path | None = None
    if out_file is not None:
        out_path = out_file.expanduser()
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(response.markdown, encoding="utf-8")
        except OSError as exc:
            _fail(exc, as_json=as_json, fallback="output_file_unwritable")
            return

    recheck = queue_recheck(home_root, target, attached)
    drain: dict[str, object] | None = None
    status: dict[str, object] | None = None
    pair = (response.resume.profile_id, response.job.job_identity)
    if attached.changed and recheck["error_code"] is None and recheck["result"] != "pipeline_off" and pair[0] is not None:
        if not as_json:
            click.echo("Stored. Checking it again (the assessment against it is one model call; this can take a minute)...")
        try:
            drain = run_once(home_root, target, only=pair, force_enabled=True).to_json()  # type: ignore[arg-type]
            status = pipeline_status(home_root, target, profile_id=pair[0], job=pair[1])
        except _pipeline_errors() as exc:  # the resume is stored: a failed check is reported, never the command's failure
            recheck = {**recheck, "result": "not_run", "error_code": getattr(exc, "code", None) or "scout_pipeline_failed"}
    folder_file = _resume_folder_file(home_root, response)
    if as_json:
        _emit(
            {
                "ok": True, **response.to_json(), "changed": attached.changed, "out_path": None if out_path is None else str(out_path),
                "folder_path": folder_file, "recheck": recheck, "drain": drain, "status": status,
            },
            True, "",
        )
        return
    job = response.job
    heading = job.title or "(untitled posting)"
    if job.company:
        heading += f" at {_company_shown(home_root, job.company)}"
    if not attached.changed:
        click.echo(f"No change: this is already the stored tailored resume for {heading}.")
    else:
        from .tailored_resume import tailor_line_stats

        stats = tailor_line_stats(response.result)
        click.echo(f"Stored your edited resume for {heading} (written by {response.edited.written_by}):")  # type: ignore[union-attr]
        click.echo(f"  Lines: {response.result.line_count()} ({stats.edited} edited, {stats.copied} copied from your resume, {stats.shown_rewritten} kept rewrites)")
    click.echo(f"  Markdown: {response.markdown_path}")
    if folder_file is not None:
        click.echo(f"  In your jobs folder: {folder_file}")
    if out_path is not None:
        click.echo(f"  Copied to {out_path}")
    if recheck["error_code"] == "assessment_missing":
        click.echo("  Not checked again: this job has no assessment for this profile yet. Run `gigai scout assess --job-url ...`, then `gigai scout pipeline process <url>`.")
    elif recheck["error_code"] is not None:
        click.echo(f"  Not checked again ({recheck['error_code']}); see `gigai scout pipeline status`.")
    elif drain is not None and status is not None:
        click.echo("  " + _pipeline_drain_line(drain))
        for line in _pipeline_status_lines(status)[1:]:
            click.echo("  " + line)


# 0.1.11 N5: `gigai scout resume brief | store | pick` and `gigai scout suggestions` (resume_job_cli).
from .resume_job_cli import register as _register_resume_job_commands  # noqa: E402

_register_resume_job_commands(scout_group, resume_group)

# 0.1.11.4 C2: `gigai scout cover-letter brief | pdf` (cover_letter_cli).
from .cover_letter_cli import register as _register_cover_letter_commands  # noqa: E402

_register_cover_letter_commands(scout_group)


@resume_group.command("pdf")
@click.option("--in", "in_file", help="Resume markdown FILE in GigAI's resume format (or - for stdin).")
@click.option("--tailored", "tailored", is_flag=True, help="Render the resume STORED for --job-url (the picked one, or the one you edited) instead of a markdown file. --job-url alone does the same.")
@click.option("--job-url", "job_url", help="The posting URL whose stored resume (picked or edited) to render.")
@click.option("--out", "out_file", type=click.Path(path_type=Path, dir_okay=False), help="Write the PDF to FILE (default: <company>-<role>-<YYYY-MM-DD>.pdf, or resume-<YYYY-MM-DD>.pdf, in your resumes folder).")
@click.option("--profile", "profile_id", help="With --tailored: the Scout profile ID the resume was tailored for (default: the newest).")
@click.option("--spacing", "spacing", type=float, help="Spacing scale 0.7-1.4 for this render (turns auto fit off unless --auto-fit is given); below 1.0 the body's lines tighten too. Default: the spacing saved for the job on its page in Scout, else the saved setting. A stored job resume is tightened when that keeps it on its page limit.")
@click.option("--auto-fit/--no-auto-fit", "auto_fit", default=None, help="Pick the spacing that ends the content near a page boundary. Default: the saved setting.")
@click.option("--header", "header_value", type=click.Path(path_type=Path, dir_okay=False), help="With --out: fill the PDF's header (name, contact line, work authorization) from this JSON FILE of yours. Default: ~/Documents/GigAI/header.json when it exists. GigAI only reads it.")
@click.option("--no-header", "no_header", is_flag=True, help="Make the PDF without a header even when your header file exists.")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def resume_pdf_command(
    in_file: str | None,
    tailored: bool,
    job_url: str | None,
    out_file: Path | None,
    profile_id: str | None,
    spacing: float | None,
    auto_fit: bool | None,
    header_value: Path | None,
    no_header: bool,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """Render a resume to PDF, locally: no model call, no network (0110-032).

    Pass exactly one of --in FILE (resume markdown in GigAI's format: `## Summary`,
    `## Experience` with `### <employer>` entries and `- ` bullets, `## Skills`,
    `## Education`, `## Projects`, `## Other`) or --tailored --job-url URL (the
    stored tailored resume for that posting). Either way the PDF uses the same
    template as the Scout UI's "Generate PDF". GigAI stores no name or contact
    details, so this PDF has no header (a blank block keeps the page layout):
    the command prints the local Scout page where you add yours in the
    Generate PDF form and download (an agent cannot finish that step unless it
    drives your browser). --spacing / --auto-fit change the layout for this
    render only. The file is named <company>-<role>-<YYYY-MM-DD>.pdf, never
    after you, and without --out it is written into your resumes folder
    (`gigai scout resume folder`; ~/Documents/GigAI/resumes unless you chose
    another), never the current directory. That folder never holds contact
    details: markdown whose printed text has an email, a phone number or a
    profile link needs --out FILE.

    A PDF WITH your header, without the browser: keep your details in a JSON
    file you own, ~/Documents/GigAI/header.json (or name one with --header
    FILE), and pass --out FILE. The file:

    \b
      {
        "name": "Jane Example",
        "email": "jane@example.com",
        "phone": "555-0100",
        "location": "Springfield, IL",
        "github": "jane-example",
        "linkedin": "jane-example",
        "work_authorization": "VISA: H1B"
      }

    Every field is optional but the name. The header is the name and ONE
    line: location | work authorization | links | email | phone (set smaller
    before it wraps). github and linkedin take just your id (website: a site
    address; links: [{"label", "url"}] also works); a link prints without
    https:// or www. and is clickable. work_authorization prints as written;
    without that key it comes from the profile's sponsorship answer. GigAI only reads the file when it makes the
    PDF: it is never copied into GigAI's store, a log or a model prompt, and a
    PDF with a header is written only to --out, never to the resumes folder.
    --no-header makes the PDF without one.
    """

    from .find_jobs.contracts import FindJobsContractError
    from .find_jobs.job_state import normalize_job_identity
    from .quick_assess import QuickAssessError
    from . import run_supervisor
    from . import resumes_folder
    from .resume_pdf import FINISH_LINE, ResumeMarkdownError, finish_url, markdown_resume_pdf, printed_text, stored_resume_pdf
    from .tailored_resume import list_tailored_resumes
    from .target_resolution import home_scout_target

    home_root = home_value or default_home_root()
    tailored = tailored or bool(job_url and not in_file)  # 0.1.11 (SPEC 4.4): --job-url alone is the stored job resume; --tailored stays an accepted spelling
    if bool(in_file) == bool(tailored):
        _fail(ValueError("pass exactly one of --in FILE or --tailored --job-url URL"), as_json=as_json, fallback="invalid_value")
        return
    from . import pdf_header_cli

    try:
        pdf_header_cli.check_flags(header_value, no_header=no_header, out_file=out_file)
    except pdf_header_cli.PdfHeaderRefusal as exc:
        _fail(exc, as_json=as_json, fallback=exc.code)
        return
    if bool(job_url) != bool(tailored):
        _fail(ValueError("--tailored and --job-url go together"), as_json=as_json, fallback="invalid_value")
        return
    try:
        if tailored:
            target: Path | None = _resolved_target(target_value, home_root, as_json=as_json).expanduser().resolve(strict=True)
        else:
            # Rendering a file never creates a Scout folder.
            candidate = target_value or home_scout_target(home_root)
            target = candidate.expanduser().resolve() if candidate.is_dir() else None
    except (ScoutTargetError, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_resume_pdf_failed")
        return

    # 0.1.11.3 item 13: the header comes from the user's own file, read only for this PDF (pdf_header_cli: the one reading).
    try:
        chosen = pdf_header_cli.header_for_pdf(header_value, no_header=no_header, out_file=out_file, home_root=home_root, target=target)
    except pdf_header_cli.PdfHeaderRefusal as exc:
        _fail(exc, as_json=as_json, fallback=exc.code)
        return
    form, header_file, header_note = chosen.form, chosen.header_file, chosen.header_note

    failure: tuple[Exception, str] | None = None
    rendered = None
    finish_ids: tuple[str | None, str | None] = (None, None)
    # What the resumes folder is told about the file: the job it belongs to and the text it prints.
    folder_key: str | None = None
    printed = ""
    try:
        if tailored:
            assert job_url is not None and target is not None
            items = list_tailored_resumes(home_root, target, profile_id=profile_id or None, job_identity=normalize_job_identity(job_url))
            if not items:
                raise QuickAssessError("tailored_resume_not_found", "no resume is stored for that job; pick it on the job's page in Scout, or run `gigai scout resume pick --job-url URL --refresh` (the job must be assessed first)")
            rendered, file_name = stored_resume_pdf(items[0], home_root=home_root, target=target, form=form, spacing_scale=spacing, auto_fit=auto_fit, count_pages=True)
            finish_ids = (items[0].resume.profile_id or "ephemeral", items[0].job.job_identity)
            folder_key, printed = resumes_folder.job_key(home_root, items[0].stored_path), items[0].markdown
        else:
            assert in_file is not None
            markdown = _read_text_option(in_file, flag="--in")
            rendered, file_name = markdown_resume_pdf(markdown, home_root=home_root, profile_id=profile_id or None, form=form, spacing_scale=spacing, auto_fit=auto_fit)
            printed = printed_text(markdown)
    except OSError as exc:
        failure = (exc, "input_file_unreadable")
    except (ResumeMarkdownError, QuickAssessError, FindJobsContractError, ValueError) as exc:  # ValueError: --spacing out of range, undecodable input
        failure = (exc, "invalid_value")
    except Exception:  # noqa: BLE001 - a render failure is typed, and never echoes the resume
        failure = (RuntimeError("the PDF could not be rendered"), "pdf_render_failed")
    if failure is not None or rendered is None:
        exc, fallback = failure or (RuntimeError("the PDF could not be rendered"), "pdf_render_failed")
        _fail(exc, as_json=as_json, fallback=fallback)
        return

    # Print the path as the user types it (``~/...`` for the folder): an absolute path would carry the home folder into an agent's transcript.
    if out_file is not None:
        out_path, shown_path = out_file.expanduser(), str(out_file)
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(rendered.pdf)
        except OSError as exc:
            _fail(exc, as_json=as_json, fallback="output_file_unwritable")
            return
    else:
        # 0110-10-05 A: the headerless PDF goes to the resumes folder, never the current directory.
        try:
            saved = resumes_folder.save_pdf(home_root, name=file_name, pdf=rendered.pdf, text=printed, key=folder_key)
        except resumes_folder.ResumesFolderError as exc:
            if exc.code == "contact_data_found":
                exc = resumes_folder.ResumesFolderError(exc.code, f"{exc}; pass --out FILE to save this PDF where you choose")
            _fail(exc, as_json=as_json, fallback="output_file_unwritable")
            return
        shown_path = saved.shown
    # 0110-046: the PDF has no header; the running Scout's page finishes it (the default port when none runs).
    base, running = f"http://127.0.0.1:{run_supervisor.DEFAULT_PORT}", False
    if target is not None:
        try:
            current = run_supervisor.status(home_root=home_root, requested_target=target)
            # 0110-10-13: a live process whose API did not answer from here (a sandbox) still has its own address.
            if current.state in ("running", run_supervisor.STATE_UNREACHABLE) and current.url:
                base, running = current.url, True
        except Exception:  # noqa: BLE001 - no bound project or no state: the default local address
            pass
    link = finish_url(base, *finish_ids) if form is None else None
    payload: dict[str, object] = {
        "ok": True,
        "out_path": shown_path,
        "in_resumes_folder": out_file is None,
        "source": "tailored" if tailored else "markdown",
        "pages": rendered.pages,
        "bytes": len(rendered.pdf),
        "spacing_scale": rendered.spacing_scale,
        "header": form is not None,
        "header_file": header_file,
        "header_note": header_note,
        "finish_url": link,
        "scout_running": running,
        "note": rendered.note,
    }
    wrote = f"Wrote {shown_path} ({rendered.pages} page{'' if rendered.pages == 1 else 's'}, spacing {rendered.spacing_scale:g})"
    if form is not None:
        lines = [f"{wrote}, with your name and contact details from {header_file}.", *([rendered.note] if rendered.note else []), *([header_note] if header_note else [])]
    else:
        lines = [
            f"{wrote}, without your name and contact details.",
            *([rendered.note] if rendered.note else []),
            *([header_note] if header_note else []),
            f"{FINISH_LINE}: {link}" + ("" if running else " (start Scout first: `gigai scout run`)"),
        ]
        if not tailored:
            lines.append(f"There, choose {in_file if in_file != '-' else 'the same markdown'} as the resume.")
    _emit(payload, as_json, "\n".join(lines))


@resume_group.command("folder")
@click.option("--set", "set_value", help="Use this folder from now on (an absolute path, or one that starts with ~); it is created when missing.")
@click.option("--reset", "reset", is_flag=True, help="Go back to the default folder.")
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def resume_folder_command(set_value: str | None, reset: bool, home_value: Path | None, as_json: bool) -> None:
    """Show or change your resumes folder: where GigAI keeps master.md and the PDFs made without a header.

    Default ~/Documents/GigAI/resumes. It holds your master resume's file
    (master.md) and the PDFs `gigai scout resume pdf` makes without --out,
    named <company>-<role>-<YYYY-MM-DD>.pdf: never your name or contact
    details (a PDF with the Generate PDF form's header is saved only where
    you save it). A job's resume is no longer written here: since 0.1.11.4
    it is resume.md in the job's own folder of your jobs folder
    (`gigai scout jobs-folder`); the <company>-<role>-<date>.md files
    already here are left as they are. GigAI replaces a file there only
    when it is exactly what GigAI last wrote, so a file you edit is yours.
    --set moves nothing: files already written stay in the old folder.
    """

    from . import resumes_folder

    home_root = home_value or default_home_root()
    if set_value is not None and reset:
        _fail(ValueError("pass --set PATH or --reset, not both"), as_json=as_json, fallback="invalid_value")
        return
    try:
        if set_value is not None or reset:
            folder = resumes_folder.set_resumes_folder(home_root, None if reset else set_value)
        else:
            folder = resumes_folder.resumes_folder(home_root)
    except resumes_folder.ResumesFolderError as exc:
        _fail(exc, as_json=as_json, fallback="invalid_value")
        return
    # The folder is the home's, so this command takes no --target and never creates a Scout project.
    # 0.1.11.4 J1: nothing is copied in any more (``copied`` stays in the JSON, always 0): a job's resume goes to the jobs folder.
    payload = {"ok": True, **folder.to_json(), "copied": 0}
    lines = [f"Resumes folder: {folder.shown}" + (" (the default)" if folder.source == resumes_folder.SOURCE_DEFAULT else "")]
    _emit(payload, as_json, "\n".join(lines))


@scout_group.command("jobs-folder")
@click.argument("action", required=False, type=click.Choice(["migrate"]))
@click.option("--set", "set_value", help="Use this folder from now on (an absolute path, or one that starts with ~); it is created when missing.")
@click.option("--reset", "reset", is_flag=True, help="Go back to the default folder.")
@click.option("--dry-run", "dry_run", is_flag=True, help="With migrate: list every planned copy and write nothing.")
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def jobs_folder_command(action: str | None, set_value: str | None, reset: bool, dry_run: bool, home_value: Path | None, as_json: bool) -> None:
    """Show or change your jobs folder: one folder per application, where GigAI puts a job's files.

    Default ~/Documents/GigAI/jobs. Each job has its own folder,
    <company>/<role>/ (lowercase, hyphens, no dates: thrive-market/
    staff-software-engineer-fullstack/), and its picked resume is resume.md
    there: clean markdown, never your name or contact details, and never a
    PDF (a PDF goes where you save it). Two roles at one company are two
    folders; when two postings would get the same folder the later one's
    name ends in a short id. The same job always keeps the same folder.
    cover-letter.md and interview/ are the names of the cover letter and the
    interview package there; GigAI never creates either one empty. GigAI
    replaces resume.md only when it is exactly what GigAI last wrote, so a
    file you edit is yours (the new resume is then written beside it as
    resume-2.md). --set moves nothing: folders already written stay in the
    old folder.

    migrate copies the job resumes of your old resumes folder (the flat
    <company>-<role>-<date>.md files) into this layout, once. The company
    and role come from the stored job, never from the file's name. The old
    folder is left as it is; master.md and PDFs are not copied. Run it with
    --dry-run first: that lists every copy and writes nothing.
    """

    from . import jobs_folder, jobs_folder_migrate

    home_root = home_value or default_home_root()
    if set_value is not None and reset:
        _fail(ValueError("pass --set PATH or --reset, not both"), as_json=as_json, fallback="invalid_value")
        return
    if action is None and dry_run:
        _fail(ValueError("--dry-run goes with migrate: gigai scout jobs-folder migrate --dry-run"), as_json=as_json, fallback="invalid_value")
        return
    if action == "migrate":
        if set_value is not None or reset:
            _fail(ValueError("migrate takes no --set or --reset: change the folder first, then migrate"), as_json=as_json, fallback="invalid_value")
            return
        try:
            report = jobs_folder_migrate.migrate(home_root, dry_run=dry_run)
        except jobs_folder.JobsFolderError as exc:
            _fail(exc, as_json=as_json, fallback="invalid_value")
            return
        _emit({"ok": True, **report.to_json()}, as_json, "\n".join(report.lines()))
        return
    try:
        if set_value is not None or reset:
            folder = jobs_folder.set_jobs_folder(home_root, None if reset else set_value)
        else:
            folder = jobs_folder.jobs_folder(home_root)
    except jobs_folder.JobsFolderError as exc:
        _fail(exc, as_json=as_json, fallback="invalid_value")
        return
    # The folder is the home's, so this command takes no --target and never creates a Scout project.
    # 0.1.11.4 J2: the old flat files still to import (the two indexes; no file is read).
    legacy = jobs_folder_migrate.pending_count(home_root)
    offer = jobs_folder_migrate.pending_line(legacy)
    payload = {"ok": True, **folder.to_json(), "legacy_pending": legacy}
    lines = [f"Jobs folder: {folder.shown}" + (" (the default)" if folder.source == jobs_folder.SOURCE_DEFAULT else "")]
    _emit(payload, as_json, "\n".join(lines + ([offer] if offer else [])))


@scout_group.command("migrate-job-stores")
@click.option("--apply", "apply", is_flag=True, help="Make the copies. Without it this is a dry run: it prints the counts and writes nothing.")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
@_reads_committed
def migrate_job_stores_command(apply: bool, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """Give every job ONE assessment, ONE resume and ONE suggestion record (0.1.11.9). A dry run unless --apply.

    Before 0.1.11.9 GigAI kept these once per role, so a job two roles found
    had two of each. This copies one of them into the job's own place: the
    role with the application, else the one whose resume you edited (a line
    of yours, a pin, an exclude or a saved spacing), else the only one with
    a stored resume, else the newest assessment. It copies and never moves:
    each role's own folder is left byte for byte as it was, nothing is
    deleted, and what was not chosen stays readable there.

    Without --apply it prints the counts and writes nothing at all. An
    applied job with a stored resume under two roles is listed: the
    application does not say which resume was sent, so both are kept. A
    second --apply changes nothing. Applications, answers and rank scores
    are not touched.
    """

    from . import job_store_migration
    from .target_resolution import home_scout_target

    home_root = home_value or default_home_root()
    # Never creates a Scout project: a home without one has nothing to migrate.
    target = target_value or home_scout_target(home_root)
    try:
        report = job_store_migration.migrate(home_root, target, apply=apply)
    except job_store_migration.JobStoreMigrationError as exc:
        _fail(exc, as_json=as_json, fallback="invalid_value")
        return
    _emit({"ok": True, **report.to_json()}, as_json, "\n".join(report.lines()))


def _other_server_label(other: OtherScoutServer) -> str:
    """The folder another project's Scout server serves, the way the operator types it."""

    if other.target is None:
        return f"project {other.project_id}"
    return _display_path(Path(other.target))


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

    def _stopped_other(other: OtherScoutServer) -> None:
        if as_json:
            return
        click.echo(
            f"Stopped the Scout server for {_other_server_label(other)} (pid {other.pid}) "
            f"so this one can use port {other.port}."
        )

    home_root = home_value or default_home_root()
    _ensure_gigai_settings(home_root, as_json=as_json)
    try:
        resolved_target = _resolved_target(target_value, home_root, as_json=as_json)
        # 0110-046: the one-time contact cleanup (idempotent, never raises); its report is also in the UI once.
        cleanup = _contact_cleanup(home_root, resolved_target)
        result = run_supervisor.start(
            home_root=home_root,
            requested_target=resolved_target,
            port=port,
            foreground=foreground,
            open_browser=not no_browser,
            on_stopped_other=_stopped_other,
        )
    except (ScoutTargetError, run_supervisor.ScoutRunError, WorkpadError, ScoutInstallError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_run_failed")
        return

    payload = {
        "ok": True,
        "reused": result.reused,
        "cleaned_stale": result.cleaned_stale,
        "restarted_from_version": result.restarted_from_version,
        "stopped_other": result.stopped_other.to_json() if result.stopped_other is not None else None,
        "stopped_server": result.stopped_server.to_json() if result.stopped_server is not None else None,
        "contact_cleanup": cleanup,
        **result.state.to_json(),
    }
    if as_json:
        _emit(payload, True, "")
        return
    if cleanup.get("status") == "done" and cleanup.get("removed_any"):
        click.echo(f"{cleanup['text']} (`gigai scout privacy` shows this again.)")
    if result.cleaned_stale:
        click.echo("Cleaned up a stale Scout run state (its process was no longer running).")
    if result.restarted_from_version is not None:
        click.echo(f"Restarted Scout: it was running {result.restarted_from_version} from before your upgrade.")
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
        resolved_target = _resolved_target(target_value, home_root, as_json=as_json)
        stopped = run_supervisor.stop(home_root=home_root, requested_target=resolved_target)
    except (ScoutTargetError, WorkpadError, OSError, ValueError) as exc:
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
@_reads_committed
def status_command(target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """Show whether this project's Scout instance is running, stopped, crashed, or not reachable from here.

    The process (the recorded pid) and the API (does it answer from here) are
    checked apart. A live process whose API did not answer is "unreachable",
    never "running" and never "stopped": inside an agent sandbox a localhost
    check can fail while Scout is fine.
    """

    from . import run_supervisor

    home_root = home_value or default_home_root()
    try:
        resolved_target = _resolved_target(target_value, home_root, as_json=as_json)
        current = run_supervisor.status(home_root=home_root, requested_target=resolved_target)
    except (ScoutTargetError, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_status_failed")
        return

    from . import jobs_folder, jobs_folder_migrate, resumes_folder

    folder = resumes_folder.resumes_folder(home_root)
    jobs = jobs_folder.jobs_folder(home_root)
    legacy = jobs_folder_migrate.pending_count(home_root)
    legacy_offer = jobs_folder_migrate.pending_line(legacy)
    # 0.1.10.9 master P8: how the master resume's file in that folder stands (the index and one digest; no journal read).
    master_file = resumes_folder.master_file(home_root)
    payload = {"ok": True, **current.to_json(), "resumes_folder": folder.to_json(), "jobs_folder": jobs.to_json(), "master_file": master_file.to_json()}
    payload["legacy_pending"] = legacy
    if as_json:
        _emit(payload, True, "")
        return
    if current.state == "running":
        click.echo(f"running: {current.url} (pid {current.pid}, log: {current.log_path})")
        if current.outdated:
            old = current.outdated_version or "an earlier build"
            click.echo(f"running an old version ({old}); run `gigai scout run` to restart")
    elif current.state == "crashed":
        click.echo(
            f"crashed: last known pid {current.pid} is no longer running "
            f"(log: {current.log_path}). Run `gigai scout run` to restart it."
        )
    elif current.state == run_supervisor.STATE_UNREACHABLE:
        # 0110-10-13: process evidence and API reachability, each said as what it is.
        for line in _unreachable_lines(current):
            click.echo(line)
    else:
        click.echo("stopped")
    click.echo(f"Resumes folder: {folder.shown}")
    click.echo(f"Jobs folder: {jobs.shown}")
    if legacy_offer:
        click.echo(legacy_offer)
    if master_file.state == resumes_folder.MASTER_CHANGED:
        click.echo(f"{master_file.name} has changes not imported yet ({master_file.shown}). Import them: `gigai scout resume master sync`.")
    for other in current.other_servers:
        click.echo(
            f"The Scout server for {_other_server_label(other)} is running at {other.url} "
            f"(pid {other.pid}); `gigai scout run` stops it when it holds the port this one needs."
        )


_API_ERROR_WORDS = {
    "not_permitted": "the connection was not permitted: this sandbox blocks localhost",
    "refused": "connection refused",
    "timeout": "no answer in time",
    "failed": "the health check failed",
}


def _unreachable_lines(current: object) -> list[str]:
    """`gigai scout status` for a live process whose API did not answer from here (0110-10-13)."""

    process = (
        f"process: running (pid {current.pid})"  # type: ignore[attr-defined]
        if current.process_identity == "scout"  # type: ignore[attr-defined]
        else f"process: pid {current.pid} is alive (could not confirm it is Scout: its command line is not readable from here)"  # type: ignore[attr-defined]
    )
    why = _API_ERROR_WORDS.get(str(current.api_error), str(current.api_error))  # type: ignore[attr-defined]
    return [
        f"{process}; API: not reachable from here ({why}) at {current.url}",  # type: ignore[attr-defined]
        "Inside an agent sandbox a localhost check can fail while Scout is fine. Check from outside the sandbox "
        f"(or reload the page) before you restart anything. Log: {current.log_path}",  # type: ignore[attr-defined]
    ]


def _contact_cleanup(home_root: Path, target: Path | None) -> dict[str, object]:
    """Run the one-time contact cleanup for this home and target (0110-046); its report. Never raises."""

    from .contact_cleanup import run_cleanup

    resolved = None
    if target is not None:
        try:
            resolved = target.expanduser().resolve(strict=True)
        except OSError:
            resolved = None
    return run_cleanup(home_root=home_root, target=resolved)


@scout_group.command("privacy")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def privacy_command(target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """Show what the one-time contact cleanup removed (running it first if it has not run).

    GigAI no longer stores your name or contact details (0.1.10.7). An install
    from before kept them in stored resumes and in the PDF settings; this
    removes them once, through the normal write path, and reports what kinds
    and how many were removed, where (counts only). Earlier copies remain in
    the workpad's local history: the cleanup does not rewrite it.
    """

    home_root = home_value or default_home_root()
    try:
        resolved_target = _resolved_target(target_value, home_root, as_json=as_json)  # the folder `scout status` reads
    except (ScoutTargetError, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_privacy_failed")
        return
    report = _contact_cleanup(home_root, resolved_target)
    if report.get("status") == "failed":
        _fail(RuntimeError(f"the contact cleanup could not run ({report.get('code')}); it is tried again on the next start"), as_json=as_json, fallback="contact_cleanup_failed")
        return
    if as_json:
        _emit({"ok": True, **report}, True, "")
        return
    click.echo(str(report["text"]))


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
    try:
        resolved_target = _resolved_target(target_value, home_root, as_json=as_json)
    except (ScoutTargetError, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_discover_failed")
        return
    target = resolved_target.expanduser().resolve(strict=True)

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


@scout_group.command("prep", hidden=True)
@click.argument("posting_url")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--run", "run_id", help="Find-jobs run ID to resolve the posting from (default: newest run that has it).")
@click.option("--profile", "profile_id", help="Scout profile ID to prepare against (default: the selected profile).")
@click.option("--refresh", is_flag=True, help="Re-run prep even if one is already stored for this posting and resume revision.")
@click.option("--budget", "budget_usd", type=float, default=0.50, show_default=True, help="Max USD to spend on company-research web search.")
@click.option("--json", "as_json", is_flag=True)
def prep_command(
    posting_url: str,
    target_value: Path | None,
    home_value: Path | None,
    run_id: str | None,
    profile_id: str | None,
    refresh: bool,
    budget_usd: float,
    as_json: bool,
) -> None:
    """Prepare for an interview at POSTING_URL (a find-jobs-acquired posting).

    Foreground -- company research (OpenAI web_search, ~seconds) plus one
    model call for likely question categories. Idempotent per (profile,
    posting, resume revision); pass --refresh to re-run. The resume is sent
    only to the question-category model call, never to the company-research
    web search. Prints a summary and where the prep is stored. Without
    --profile, prepares against the SELECTED profile (S25 F1-b2).
    """

    home_root = home_value or default_home_root()
    try:
        resolved_target = _resolved_target(target_value, home_root, as_json=as_json)
    except (ScoutTargetError, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_prep_failed")
        return
    target = resolved_target.expanduser().resolve(strict=True)

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
            profile_id=profile_id, on_progress=_on_progress,
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
    click.echo(f"  Total cost: ${prep.cost_usd:.4f}. Stored under scout/interview_prep/ (GigAI home).")



def _read_text_option(value: str, *, flag: str) -> str:
    """Read ``FILE`` (or ``-`` for stdin) for a ``--job-text``/``--resume`` flag."""

    if value == "-":
        return click.get_text_stream("stdin").read()
    path = Path(value).expanduser()
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OSError(f"{flag}: cannot read {path}: {exc.strerror or exc}") from exc


@scout_group.command("assess")
@click.option("--job-url", "job_url", help="Public job posting URL to fetch and assess.")
@click.option("--job-text", "job_text_file", help="File with the posting text (or - for stdin).")
@click.option("--profile", "profile_id", help="Scout profile ID whose pinned resume to assess against (default: the selected profile).")
@click.option("--resume", "resume_file", help="Resume text FILE (or - for stdin), used for this call only; never stored.")
@click.option("--resume-text", "resume_text", help="Resume text inline, used for this call only; never stored.")
@click.option("--visa/--no-visa", "visa", default=None, help="Override find-jobs.json's visa-sponsorship-required flag.")
@click.option("--title", "title", help="Job title override (pasted text has none).")
@click.option("--company", "company", help="Company override (pasted text has none).")
@click.option("--model-target", "model_target", type=click.Choice([item.value for item in ModelTarget]), help="Adapter kind to assess with (default: find-jobs.json's default_model_target).")
@click.option("--origin", "origin", type=click.Choice(["quick_assess", "job_page"]), default="quick_assess", show_default=True, help="Where this assessment was started; the UI lists quick_assess ones under Assessments.")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def assess_command(
    job_url: str | None,
    job_text_file: str | None,
    profile_id: str | None,
    resume_file: str | None,
    resume_text: str | None,
    visa: bool | None,
    title: str | None,
    company: str | None,
    model_target: str | None,
    origin: str,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """Assess ONE job against a resume right now -- no find-jobs run.

    Pass exactly one of --job-url / --job-text, and at most one of --profile /
    --resume / --resume-text (none means the selected profile's resume). A
    pasted resume is used for this call only and never imported or stored.
    Synchronous: the configured model is called once (plus one retry on a
    malformed answer). Prints the verdict, the requirement matrix, the
    questions with their ids, and where the result is stored.
    """

    from .find_jobs.assess_contracts import AssessJobInput, AssessPreferences, AssessRequest, AssessResumeInput
    from .find_jobs.contracts import FindJobsContractError
    from .quick_assess import QuickAssessError, run_quick_assessment

    home_root = home_value or default_home_root()
    try:
        resolved_target = _resolved_target(target_value, home_root, as_json=as_json)
        target = resolved_target.expanduser().resolve(strict=True)
    except (ScoutTargetError, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_assess_failed")
        return

    if job_url and job_text_file:
        _fail(ValueError("pass exactly one of --job-url or --job-text"), as_json=as_json, fallback="job_input_invalid")
        return
    if sum(1 for item in (profile_id, resume_file, resume_text) if item) > 1:
        _fail(ValueError("pass at most one of --profile, --resume or --resume-text"), as_json=as_json, fallback="resume_input_invalid")
        return
    try:
        job_text = _read_text_option(job_text_file, flag="--job-text") if job_text_file else None
        if resume_file:
            resume_text = _read_text_option(resume_file, flag="--resume")
    except OSError as exc:
        _fail(exc, as_json=as_json, fallback="input_file_unreadable")
        return

    try:
        request = AssessRequest(
            job=AssessJobInput(job_url=job_url or None, job_text=job_text or None, title=title, company=company),
            resume=AssessResumeInput(profile_id=profile_id or None, resume_text=resume_text or None),
            preferences=None if visa is None else AssessPreferences(visa_sponsorship_required=visa),
            model_target=None if model_target is None else ModelTarget(model_target),
            origin=origin,
        )
    except FindJobsContractError as exc:
        _fail(exc, as_json=as_json, fallback="invalid_value")
        return

    from .find_jobs.posting_live import CLOSED_ASSESS_MESSAGE, ERROR_POSTING_CLOSED, closed_before_assess

    if closed_before_assess(home_root, target, job_url or None) is not None:  # 0.1.11.4 R1: no model call for a closed posting
        _fail(QuickAssessError(ERROR_POSTING_CLOSED, CLOSED_ASSESS_MESSAGE), as_json=as_json, fallback="scout_assess_failed", assess=True)
        return
    if not as_json:
        click.echo("Assessing (one model call; this can take up to a minute)...")
    try:
        response = run_quick_assessment(request, home_root=home_root, target=target)
    except QuickAssessError as exc:
        _fail(exc, as_json=as_json, fallback="scout_assess_failed", assess=True)
        return

    if as_json:
        _emit({"ok": True, **response.to_json()}, True, "")
        return
    result = response.result
    job = response.job
    heading = job.title or "(untitled posting)"
    if job.company:
        heading += f" at {_company_shown(home_root, job.company)}"
    click.echo(f"Assessment for {heading}:")
    from .requirement_weights import minor_gap_text, minor_gaps

    gap_text = minor_gap_text(minor_gaps(result.matrix))  # 0110-10-03: "matched_above_threshold, 1 minor gap: Helm"
    click.echo(f"  Verdict: {result.verdict.value if result.verdict is not None else 'none returned'}" + (f", {gap_text}" if gap_text else ""))
    from .fit import THIN_LABEL, THIN_POSTING_ROWS
    from .quick_assess import requirements_note_text

    note = response.requirements_note or ""
    if result.verdict is not None and result.verdict.value == "matched_above_threshold" and len(result.matrix) < THIN_POSTING_ROWS:
        # 0.1.11.2: ONE line for a match read from fewer than 4 requirement rows, in place of the GUARDFIX sentence
        # ("Only 2 requirements were read ..."); anything else the stored note says follows it.
        for count in range(THIN_POSTING_ROWS):
            note = note.replace(requirements_note_text(count), "")
        note = " ".join(f"{THIN_LABEL} ({len(result.matrix)} read). Open the posting to check. {note}".split())
    if note:
        click.echo(f"  Note: {note}")  # GUARDFIX: the posting's requirements were thinly read
    if result.not_a_match_reason:
        click.echo(f"  Reason: {result.not_a_match_reason}")
    if result.sponsorship is not None:
        click.echo(f"  Sponsorship: {result.sponsorship.value}")
    resume_label = response.resume.profile_id or "pasted resume (not stored)"
    click.echo(f"  Resume: {resume_label}")
    prefs = response.preferences
    click.echo(
        f"  Preferences: visa required = {'yes' if prefs.visa_sponsorship_required else 'no'}; "
        f"countries = {', '.join(prefs.countries or ()) or 'any'}; titles = {', '.join(prefs.titles or ()) or 'unspecified'}"
    )
    click.echo("  Requirements:")
    for row in result.matrix:
        klass = f" [{row.requirement_class.value}]" if row.requirement_class is not None else ""
        evidence = f" -- {'; '.join(row.resume_evidence)}" if row.resume_evidence else ""
        click.echo(f"    {row.status.value:8} {row.requirement}{klass}{evidence}")
    if result.rows_not_shown:
        click.echo(f"    +{result.rows_not_shown} not shown")
    if result.structured_questions:
        click.echo("  Questions:")
        for question in result.structured_questions:
            click.echo(f"    {question.question_id}: {question.question}")
    elif result.questions:
        click.echo("  Questions:")
        for text in result.questions:
            click.echo(f"    - {text}")
    if result.suggestions:
        click.echo("  Suggestions:")
        for suggestion in result.suggestions:
            click.echo(f"    - {suggestion}")
    click.echo(f"  Model: {response.producer.model_target.value} ({response.producer.adapter})")
    click.echo(f"  Stored at {response.stored_path}")


@scout_group.command("answer")
@click.argument("question_id")
@click.option("--answer-text", "answer_text", help="Answer text inline.")
@click.option("--answer-file", "answer_file", help="Answer text FILE (or - for stdin).")
@click.option("--reassess", "reassess", help="Job URL or job_identity to re-assess with this answer applied.")
@click.option("--profile", "profile_id", help="With --reassess: the Scout profile ID to re-assess the job for (default: the one profile that has assessed it; required when more than one has).")
@click.option("--question", "question_text", help="The question's own words, kept with the answer.")
@click.option("--tag", "tag", help="Your own tag; default: a tag from the question.")
@click.option("--revision", "revision", type=click.IntRange(min=0), help="The revision of the answer you read; the write is refused when it changed since.")
@click.option("--as", "--actor", "actor", type=click.Choice(["operator", "agent"]), default="operator", show_default=True, help="Who is writing: recorded on the answer. An agent passes --as agent.")
@click.option("--source", "source", help="Where the answer came from, in free text (e.g. \"from the user's repo, at the user's request\").")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def answer_command(
    question_id: str,
    answer_text: str | None,
    answer_file: str | None,
    reassess: str | None,
    profile_id: str | None,
    question_text: str | None,
    tag: str | None,
    revision: int | None,
    actor: str,
    source: str | None,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """Answer QUESTION_ID once; the answer is reused across every posting and profile.

    Pass exactly one of --answer-text / --answer-file. The answer is the
    user's (0.1.10.7: not one profile's): it is written to the gig's
    ``experience_qa`` records and reused by every later ``gigai scout
    assess`` call, for a matching question_id (the normalizer makes a
    drifted id from a different call still match the same real-world fact)
    and, through the STORY BANK lines in the prompt, for the same fact worded
    differently. An answer that holds contact details is refused. --as
    records who writes (an agent passes --as agent) and --source, in free
    text, where the answer came from. Pass
    --reassess JOB_URL_OR_ID to re-run the job's assessment immediately,
    with this answer applied. A job has one assessment (0.1.11.9);
    --profile PROFILE_ID is optional and only names the role recorded on
    the new one (left out: the selected role).
    `gigai scout answers save` is the same write without the re-assessment.
    """

    from ..private_records import PrivateRecordError
    from . import story_bank
    from .find_jobs.api.story_bank import ANSWERS_SCHEMA
    from .find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
    from .find_jobs.contracts import FindJobsContractError
    from .quick_assess import QuickAssessError, reassess_target, run_quick_assessment

    if profile_id and not reassess:
        _fail(ValueError("--profile goes with --reassess"), as_json=as_json, fallback="invalid_value")
        return
    if bool(answer_text) == bool(answer_file):
        _fail(ValueError("pass exactly one of --answer-text or --answer-file"), as_json=as_json, fallback="answer_invalid")
        return
    try:
        answer = answer_text if answer_text is not None else _read_text_option(answer_file, flag="--answer-file")  # type: ignore[arg-type]
    except OSError as exc:
        _fail(exc, as_json=as_json, fallback="input_file_unreadable")
        return

    home_root = home_value or default_home_root()
    try:
        resolved_target = _resolved_target(target_value, home_root, as_json=as_json)
        target = resolved_target.expanduser().resolve(strict=True)
    except (ScoutTargetError, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_answer_failed")
        return

    # The job to re-assess is looked up first, so the answer names the posting that asked.
    # 0.1.11.9: the job's ONE stored assessment (--profile only names the role recorded on the new one).
    previous = None
    plan = None
    if reassess:
        try:
            plan = reassess_target(home_root, target, reassess, profile_id=profile_id)
            if plan.previous is None:
                # Accept a raw job URL too (not only a stored job_identity):
                # normalize it the same way resolve_job would.
                from .find_jobs.contracts import normalize_url

                plan = reassess_target(home_root, target, normalize_url(reassess), profile_id=profile_id)
            previous = plan.previous
        except (QuickAssessError, FindJobsContractError) as exc:
            _fail(exc, as_json=as_json, fallback="scout_answer_failed")
            return

    try:
        job = None
        if previous is not None:
            job = {"job_identity": previous.job.job_identity, "title": previous.job.title, "company": previous.job.company, "url": previous.job.source_url}
        entry = story_bank.save_answer(
            home_root=home_root, target=target, question_id=question_id, answer=answer,
            question=question_text, tag=tag, job=job, actor=actor, expected_revision=revision, source=source,
        )
    except (PrivateRecordError, story_bank.StoryBankError) as exc:
        _story_fail(exc, as_json=as_json, fallback="answer_invalid")
        return

    # 0.1.10.7 PL5: who asked this question is read now, before the re-assessment answers it; queued after it.
    from .pipeline import triggers as pipeline_triggers

    asked = pipeline_triggers.pending_answer(
        home_root, target, entry, job_identity=None if previous is None else previous.job.job_identity
    )
    reassessed_payload: dict[str, object] | None = None
    if reassess:
        try:
            if previous is None:
                raise QuickAssessError("reassess_not_found", f"no stored assessment for {reassess!r}")
            if previous.job.source_url is None:
                raise QuickAssessError("reassess_unavailable", "this job was assessed from pasted text, which is never stored; run `gigai scout assess` again")
            request = AssessRequest(
                job=AssessJobInput(job_url=previous.job.source_url, title=previous.job.title or None, company=previous.job.company or None),
                resume=AssessResumeInput(profile_id=plan.profile_id if plan is not None else previous.resume.profile_id),
            )
            response = run_quick_assessment(request, home_root=home_root, target=target)
        except (QuickAssessError, FindJobsContractError) as exc:
            asked.fire()  # the answer is saved: the jobs that asked are queued whether or not the re-assessment worked
            _fail(exc, as_json=as_json, fallback="scout_answer_failed", assess=True)
            return
        reassessed_payload = response.to_json()

    payload = {
        "ok": True,
        "schema_version": ANSWERS_SCHEMA,
        "record_id": entry.record_id,
        "revision_id": entry.revision_id,
        "question_id": entry.question_id,
        "answer": entry.to_json(),
        "reassessed": reassessed_payload,
    }
    _pipeline_fired(asked, payload if as_json else None, as_json=as_json)
    if as_json:
        _emit(payload, True, "")
        return
    click.echo(f"Recorded answer for {entry.question_id} at {entry.revision_id}.")
    if reassessed_payload is not None:
        result_json = reassessed_payload.get("result")
        verdict = result_json.get("verdict") if isinstance(result_json, dict) else None
        click.echo(f"  Re-assessed: verdict = {verdict}")


# --- 0.1.10.7 C: `gigai scout answers` and `gigai scout story` ----------------------


_BANK_OPTIONS = (
    click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False)),
    click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False)),
    click.option("--json", "as_json", is_flag=True),
)
_BANK_ACTOR = click.option(
    "--as", "--actor", "actor", type=click.Choice(["operator", "agent"]), default="operator", show_default=True,
    help="Who is writing: recorded on the answer or story. An agent passes --as agent.",
)
_BANK_REVISION = click.option(
    "--revision", "revision", type=click.IntRange(min=0),
    help="The revision you read (show --json); the write is refused with revision_conflict when it changed since.",
)


def _bank_options(function):
    for option in reversed(_BANK_OPTIONS):
        function = option(function)
    return function


def _bank_context(target_value: Path | None, home_value: Path | None, *, as_json: bool) -> tuple[Path, Path] | None:
    """``(home, target)`` for an answers or story command, or ``None`` after reporting the failure."""

    home_root = home_value or default_home_root()
    try:
        return home_root, _resolved_target(target_value, home_root, as_json=as_json).expanduser().resolve(strict=True)
    except (ScoutTargetError, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_answers_failed")
        return None


def _story_fail(exc: Exception, *, as_json: bool, fallback: str = "scout_answers_failed") -> None:
    """Report an answers or story refusal; a stale or duplicate write also prints the current answer or story (as the API's 409 does)."""

    from .find_jobs.api.story_bank import error_extra

    extra = error_extra(exc)
    if as_json and extra is not None:
        click.echo(
            json.dumps(
                {"status": "error", "error": {"code": getattr(exc, "code", fallback), "message": str(exc), **extra}},
                sort_keys=True, separators=(",", ":"),
            )
        )
        raise click.exceptions.Exit(1)
    _fail(exc, as_json=as_json, fallback=fallback)


def _story_text(text: str | None, file: str | None, *, flag: str) -> str | None:
    if text is not None and file is not None:
        raise ValueError(f"pass --{flag}-text or --{flag}-file, not both")
    if file is not None:
        return _read_text_option(file, flag=f"--{flag}-file")
    return text


def _jobs_label(count: int) -> str:
    return f"{count} job{'' if count == 1 else 's'}"


def _company_shown(home_root: Path | None, company: str) -> str:
    """0110-8-11: the company index's name for a board token ("Garner Health"), else the slug rule, else the text."""

    from .find_jobs.company_names import company_display_name

    return company_display_name(home_root, company) or company


def _job_lines(jobs: object, home_root: Path | None = None) -> None:
    for job in jobs:  # type: ignore[union-attr]
        label = " at ".join(part for part in (job["title"], _company_shown(home_root, job["company"]) if job["company"] else "") if part) or job["job_identity"]
        click.echo(f"  {job['kind']}: {label} ({job['at']})")


@scout_group.group("answers")
def answers_group() -> None:
    """The user's answers: every answered question, kept once and reused by every profile.

    Local and model-free. Answers belong to the user, not to a profile.
    Every write runs the contact-data check, names the revision it read, and
    --as records who wrote (operator, or an agent working alongside).
    """


@answers_group.command("list")
@click.option("--search", "search", help="Only answers whose id, question, answer or tag holds this text.")
@click.option("--tag", "tag", help="Only answers with this tag.")
@_bank_options
@_reads_committed
def answers_list_command(search: str | None, tag: str | None, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """List every answer, with its tag and the jobs that asked or reused it."""

    from ..private_records import PrivateRecordError
    from . import story_bank
    from .find_jobs.api.story_bank import answers_response

    context = _bank_context(target_value, home_value, as_json=as_json)
    if context is None:
        return
    home_root, target = context
    try:
        body = answers_response(home_root, target, q=search, tag=tag)
    except (story_bank.StoryBankError, PrivateRecordError) as exc:
        _story_fail(exc, as_json=as_json)
        return
    if as_json:
        _emit({"ok": True, **body}, True, "")
        return
    answers = body["answers"]
    click.echo(f"Answers: {len(answers)} of {body['total']}.")  # type: ignore[arg-type]
    for entry in answers:  # type: ignore[union-attr]
        click.echo(f"  {entry['question_id']} [{entry['tag']}] {story_bank.one_line(str(entry['answer']), 90)} ({_jobs_label(len(entry['jobs']))})")


@answers_group.command("show")
@click.argument("question_id")
@_bank_options
@_reads_committed
def answers_show_command(question_id: str, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """Show one answer: the question, the full answer, who wrote it, its revision and its jobs."""

    from ..private_records import PrivateRecordError
    from . import story_bank
    from .find_jobs.api.story_bank import answer_response

    context = _bank_context(target_value, home_value, as_json=as_json)
    if context is None:
        return
    home_root, target = context
    try:
        entry = story_bank.get_answer(home_root=home_root, target=target, question_id=question_id)
        if entry is None:
            raise story_bank.StoryBankError("not_found", f"there is no answer for {question_id!r}")
    except (story_bank.StoryBankError, PrivateRecordError) as exc:
        _story_fail(exc, as_json=as_json)
        return
    if as_json:
        _emit({"ok": True, **answer_response(entry)}, True, "")
        return
    click.echo(f"{entry.question_id} [{entry.tag}]")
    if entry.question != entry.question_id:
        click.echo(f"  Question: {entry.question}")
    click.echo(f"  Answer: {entry.answer}")
    click.echo(f"  Written by {entry.written_by}, updated {entry.updated_at or 'unknown'} (revision {entry.revision}).")
    if entry.source:
        click.echo(f"  Source: {entry.source}")
    _job_lines([job.to_json() for job in entry.jobs], home_root)


@answers_group.command("save")
@click.argument("question_id")
@click.option("--answer-text", "answer_text", help="The answer, inline.")
@click.option("--answer-file", "answer_file", help="The answer FILE (or - for stdin).")
@click.option("--question", "question", help="The question's own words.")
@click.option("--tag", "tag", help="Your own tag; an empty value puts the automatic tag back.")
@click.option("--source", "source", help="Where the answer came from, in free text (e.g. \"from the user's repo, at the user's request\"); an empty value removes it.")
@_BANK_REVISION
@_BANK_ACTOR
@_bank_options
def answers_save_command(
    question_id: str, answer_text: str | None, answer_file: str | None, question: str | None, tag: str | None, source: str | None,
    revision: int | None, actor: str, target_value: Path | None, home_value: Path | None, as_json: bool,
) -> None:
    """Save an answer: a new one, or a change to its text, question words, tag and/or source.

    A new QUESTION_ID needs the answer. An existing one takes whatever is
    given; pass --revision (the revision you read) so a concurrent write is
    refused instead of overwritten. --as records who writes (an agent passes
    --as agent); --source says, in free text, where the answer came from.
    """

    from ..private_records import PrivateRecordError
    from . import story_bank
    from .find_jobs.api.story_bank import answer_response

    try:
        answer = _story_text(answer_text, answer_file, flag="answer")
    except (OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="answer_invalid")
        return
    context = _bank_context(target_value, home_value, as_json=as_json)
    if context is None:
        return
    home_root, target = context
    try:
        existing = story_bank.get_answer(home_root=home_root, target=target, question_id=question_id, with_jobs=False)
        if existing is None:
            if answer is None:
                raise story_bank.StoryBankError("invalid_value", "a new answer needs --answer-text or --answer-file")
            entry = story_bank.save_answer(
                home_root=home_root, target=target, question_id=question_id, answer=answer, question=question,
                tag=tag, actor=actor, expected_revision=revision, source=source,
            )
        else:
            entry = story_bank.edit_answer(
                home_root=home_root, target=target, question_id=question_id, answer=answer, question=question,
                tag=tag, actor=actor, expected_revision=revision, source=source,
            )
    except (story_bank.StoryBankError, PrivateRecordError) as exc:
        _story_fail(exc, as_json=as_json)
        return
    from .pipeline import triggers as pipeline_triggers

    payload: dict[str, object] = {"ok": True, **answer_response(entry)}
    if existing is None or answer is not None or question is not None:  # a tag alone answers nothing new
        _pipeline_fired(pipeline_triggers.pending_answer(home_root, target, entry), payload if as_json else None, as_json=as_json)
    if as_json:
        _emit(payload, True, "")
        return
    click.echo(f"Saved {entry.question_id} (revision {entry.revision}, written by {entry.written_by}).")


@answers_group.command("delete")
@click.argument("question_id")
@click.option("--confirm", is_flag=True, help="Required: the answer is never listed, offered or sent again.")
@_BANK_REVISION
@_BANK_ACTOR
@_bank_options
def answers_delete_command(
    question_id: str, confirm: bool, revision: int | None, actor: str, target_value: Path | None, home_value: Path | None, as_json: bool
) -> None:
    """Remove an answer."""

    from ..private_records import PrivateRecordError
    from . import story_bank
    from .find_jobs.api.story_bank import ANSWERS_SCHEMA

    if not confirm:
        _fail(ValueError("deleting an answer requires --confirm"), as_json=as_json, fallback="confirm_required")
        return
    context = _bank_context(target_value, home_value, as_json=as_json)
    if context is None:
        return
    home_root, target = context
    try:
        deleted = story_bank.delete_answer(home_root=home_root, target=target, question_id=question_id, expected_revision=revision)
    except (story_bank.StoryBankError, PrivateRecordError) as exc:
        _story_fail(exc, as_json=as_json)
        return
    if as_json:
        _emit({"ok": True, "schema_version": ANSWERS_SCHEMA, "deleted": deleted}, True, "")
        return
    click.echo(f"Deleted the answer {deleted}.")


@answers_group.command("migrate")
@_bank_options
def answers_migrate_command(target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """Move the 0.1.10.5 per-profile story bank to user-level answers (once; safe to rerun).

    Every read and write does this by itself the first time; this command
    does it now and prints the counts.
    """

    from ..private_records import PrivateRecordError
    from . import story_bank

    context = _bank_context(target_value, home_value, as_json=as_json)
    if context is None:
        return
    home_root, target = context
    try:
        report = story_bank.migrate(home_root=home_root, target=target)
    except (story_bank.StoryBankError, PrivateRecordError) as exc:
        _story_fail(exc, as_json=as_json)
        return
    if as_json:
        _emit({"ok": True, **report}, True, "")
        return
    if not report["migrated"]:
        click.echo("Already done: the answers are user-level. Nothing was changed.")
        return
    click.echo(
        f"Moved {report['answers']} answer(s) from {report['profiles']} profile(s) to the user level: "
        f"{report['merged']} said the same in two profiles (kept once), {report['conflicts']} differed "
        "(the newest is the answer; the other is kept in its history)."
    )


@scout_group.group("story")
def story_group() -> None:
    """The user's stories: experiences worth telling, offered to an assessment where they fit.

    Local and model-free. A story holds a title, where and when (company,
    role, period), the user's own words (raw), a loosely STAR narrative
    (situation, task, action, result), tags and the interview questions it
    answers. Stories belong to the user, not to a profile. Every write runs
    the contact-data check and names the revision it read.
    """


def _story_line(story: dict[str, object]) -> str:
    where = ", ".join(str(story[key]) for key in ("role", "company", "period") if story[key])
    return f"  {story['story_id']} {story['title']}{' (' + where + ')' if where else ''} [{', '.join(story['tags'])}] ({_jobs_label(len(story['jobs']))})"  # type: ignore[arg-type]


@story_group.command("list")
@click.option("--search", "search", help="Only stories whose text holds this.")
@click.option("--tag", "tag", help="Only stories with this tag.")
@_bank_options
def story_list_command(search: str | None, tag: str | None, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """List every story, with its tags and the jobs that used it."""

    from . import story_bank
    from .find_jobs.api.story_bank import stories_response

    context = _bank_context(target_value, home_value, as_json=as_json)
    if context is None:
        return
    home_root, target = context
    try:
        body = stories_response(home_root, target, q=search, tag=tag)
    except story_bank.StoryBankError as exc:
        _story_fail(exc, as_json=as_json)
        return
    if as_json:
        _emit({"ok": True, **body}, True, "")
        return
    found = body["stories"]
    click.echo(f"Stories: {len(found)} of {body['total']}.")  # type: ignore[arg-type]
    for story in found:  # type: ignore[union-attr]
        click.echo(_story_line(story))


@story_group.command("show")
@click.argument("story_id")
@_bank_options
def story_show_command(story_id: str, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """Show one story in full."""

    from . import stories, story_bank
    from .find_jobs.api.story_bank import story_response

    context = _bank_context(target_value, home_value, as_json=as_json)
    if context is None:
        return
    home_root, target = context
    try:
        story = stories.get_story(home_root=home_root, target=target, story_id=story_id)
        if story is None:
            raise story_bank.StoryBankError("not_found", f"there is no story {story_id!r}")
    except story_bank.StoryBankError as exc:
        _story_fail(exc, as_json=as_json)
        return
    if as_json:
        _emit({"ok": True, **story_response(story)}, True, "")
        return
    body = story.to_json()
    click.echo(_story_line(body).strip())
    for key in stories.NARRATIVE_PARTS:
        if body["narrative"].get(key):  # type: ignore[union-attr]
            click.echo(f"  {key.capitalize()}: {body['narrative'][key]}")  # type: ignore[index]
    if story.raw:
        click.echo(f"  In the user's words: {story.raw}")
    for question in story.answers_questions:
        click.echo(f"  Answers: {question}")
    click.echo(f"  Written by {story.written_by}, updated {story.updated_at or 'unknown'} (revision {story.revision}).")
    _job_lines(body["jobs"])


@story_group.command("save")
@click.argument("story_id", required=False)
@click.option("--file", "story_file", help="A JSON FILE (or - for stdin) with the story's fields; the options below override it.")
@click.option("--title", "title", help="\"Cut CI time 60% at Acme\".")
@click.option("--company", "company")
@click.option("--role", "role")
@click.option("--period", "period", help="Rough is fine: \"2022-2023\".")
@click.option("--raw-text", "raw_text", help="The user's own words, as said.")
@click.option("--raw-file", "raw_file", help="The user's own words FILE (or - for stdin).")
@click.option("--situation", "situation", help="Narrative: the situation.")
@click.option("--task", "task", help="Narrative: the task.")
@click.option("--action", "action", help="Narrative: what was done.")
@click.option("--result", "result", help="Narrative: the outcome.")
@click.option("--tag", "tags", multiple=True, help="A tag; repeat for more.")
@click.option("--answers", "answers_questions", multiple=True, help="An interview question this story answers; repeat for more.")
@_BANK_REVISION
@_BANK_ACTOR
@_bank_options
def story_save_command(
    story_id: str | None, story_file: str | None, title: str | None, company: str | None, role: str | None, period: str | None,
    raw_text: str | None, raw_file: str | None, situation: str | None, task: str | None, action: str | None, result: str | None,
    tags: tuple[str, ...], answers_questions: tuple[str, ...], revision: int | None, actor: str,
    target_value: Path | None, home_value: Path | None, as_json: bool,
) -> None:
    """Save a story: a new one, or a change to the given fields of STORY_ID.

    A new story needs --title (its id is STORY_ID, else story:<the title's
    first words>). For an existing STORY_ID only the given fields change;
    pass --revision (the revision you read) so a concurrent write is refused.
    --file takes the same fields as POST /api/stories.
    """

    from . import stories, story_bank
    from .find_jobs.api.story_bank import story_response

    try:
        fields: dict[str, object] = {}
        if story_file is not None:
            loaded = json.loads(_read_text_option(story_file, flag="--file"))
            if not isinstance(loaded, dict):
                raise ValueError("--file must hold one JSON object")
            if story_id is None and isinstance(loaded.get("story_id"), str):
                story_id = loaded["story_id"]
            fields = {key: value for key, value in loaded.items() if key != "story_id"}
        raw = _story_text(raw_text, raw_file, flag="raw")
        for key, value in (("title", title), ("company", company), ("role", role), ("period", period), ("raw", raw)):
            if value is not None:
                fields[key] = value
        parts = {key: value for key, value in (("situation", situation), ("task", task), ("action", action), ("result", result)) if value is not None}
        if parts:
            given = fields.get("narrative")
            fields["narrative"] = {**(given if isinstance(given, dict) else {}), **parts}
        if tags:
            fields["tags"] = list(tags)
        if answers_questions:
            fields["answers_questions"] = list(answers_questions)
    except (OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="invalid_value")
        return
    context = _bank_context(target_value, home_value, as_json=as_json)
    if context is None:
        return
    home_root, target = context
    try:
        existing = None if story_id is None else stories.get_story(home_root=home_root, target=target, story_id=story_id)
        if existing is None:
            if revision is not None:
                raise story_bank.StoryBankError("not_found", f"there is no story {story_id!r} (it was deleted since you read it)")
            story = stories.save_story(home_root=home_root, target=target, story_id=story_id, fields=fields, actor=actor)
        else:
            story = stories.edit_story(
                home_root=home_root, target=target, story_id=existing.story_id, fields=fields, actor=actor, expected_revision=revision
            )
    except story_bank.StoryBankError as exc:
        _story_fail(exc, as_json=as_json)
        return
    from .pipeline import triggers as pipeline_triggers

    payload: dict[str, object] = {"ok": True, **story_response(story)}
    _pipeline_fired(pipeline_triggers.pending_story(home_root, target, story), payload if as_json else None, as_json=as_json)
    if as_json:
        _emit(payload, True, "")
        return
    click.echo(f"Saved {story.story_id} (revision {story.revision}, written by {story.written_by}).")


@story_group.command("delete")
@click.argument("story_id")
@click.option("--confirm", is_flag=True, help="Required: the story is never listed, searched or sent again.")
@_BANK_REVISION
@_BANK_ACTOR
@_bank_options
def story_delete_command(
    story_id: str, confirm: bool, revision: int | None, actor: str, target_value: Path | None, home_value: Path | None, as_json: bool
) -> None:
    """Remove a story."""

    from . import stories, story_bank
    from .find_jobs.api.story_bank import STORIES_SCHEMA

    if not confirm:
        _fail(ValueError("deleting a story requires --confirm"), as_json=as_json, fallback="confirm_required")
        return
    context = _bank_context(target_value, home_value, as_json=as_json)
    if context is None:
        return
    home_root, target = context
    try:
        deleted = stories.delete_story(home_root=home_root, target=target, story_id=story_id, expected_revision=revision)
    except story_bank.StoryBankError as exc:
        _story_fail(exc, as_json=as_json)
        return
    if as_json:
        _emit({"ok": True, "schema_version": STORIES_SCHEMA, "deleted": deleted}, True, "")
        return
    click.echo(f"Deleted the story {deleted}.")


@story_group.command("prep")
@click.option("--job-url", "job_url", help="One job's rehearsal list from its stored assessment: each row's questions and what answers them. No model call.")
@click.option("--profile", "profile_id", help="With --job-url: the profile whose assessment of the job is read (required when two profiles assessed it).")
@_bank_options
def story_prep_command(job_url: str | None, profile_id: str | None, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """The basic interview prep list: the questions the stories answer, pooled. With --job-url, one job's rehearsal list."""

    from . import story_bank
    from .find_jobs.api.story_bank import prep_response

    context = _bank_context(target_value, home_value, as_json=as_json)
    if context is None:
        return
    home_root, target = context
    if job_url:
        _story_prep_job(job_url, profile_id, home_root, target, as_json=as_json)
        return
    if profile_id:
        _fail(ValueError("--profile goes with --job-url"), as_json=as_json, fallback="invalid_value")
        return
    try:
        body = prep_response(home_root, target)
    except story_bank.StoryBankError as exc:
        _story_fail(exc, as_json=as_json)
        return
    if as_json:
        _emit({"ok": True, **body}, True, "")
        return
    questions = body["questions"]
    click.echo(f"Interview prep: {len(questions)} question(s) your stories answer.")  # type: ignore[arg-type]
    for row in questions:  # type: ignore[union-attr]
        click.echo(f"  {row['question']}")
        for story in row["stories"]:
            click.echo(f"    {story['story_id']}: {story['title']}")


def _story_prep_job(job_url: str, profile_id: str | None, home_root: Path, target: Path, *, as_json: bool) -> None:
    """``story prep --job-url``: read the stored assessment and print the rehearsal list (0.1.11.7 T1)."""

    from . import job_brief, story_bank, story_prep_job
    from .data_labels import LabelError
    from .find_jobs.contracts import FindJobsContractError
    from .quick_assess import QuickAssessError

    try:
        body = story_prep_job.load(home_root, target, job_url, profile_id)
    except (job_brief.BriefError, story_bank.StoryBankError, QuickAssessError, LabelError, FindJobsContractError, PrivateRecordError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_story_prep_failed")
        return
    _emit({"ok": True, **body} if as_json else {}, as_json, story_prep_job.render(body).rstrip("\n"))


# --- Q1 (v0.1.9, SCOPE-ADD-2): `gigai scout watchlist add <url>` -----------


@scout_group.group("watchlist")
def watchlist_group() -> None:
    """Manage the ATS boards Scout polls directly (the watchlist)."""


@watchlist_group.command("add")
@click.argument("url")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def watchlist_add_command(url: str, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """Add a company by its board (or job) URL on any hiring system Scout reads (Greenhouse, Lever, Ashby, Workable, Rippling, Gem, Recruitee, Pinpoint, Breezy).

    Every later find-jobs run polls the board directly, so a posting no
    search engine surfaced (the UAT Kong case) is found as long as it is
    inside the publication window. Idempotent: adding a board twice keeps
    the one original entry. Any other host is refused.
    """

    from .find_jobs.watchlist import WatchlistUrlError, add_company_from_url, list_active, watchlist_entry_from_url

    # The URL rule is pure: refuse a foreign host before resolving any
    # target/gig at all, so a bad URL never surfaces as a workpad error.
    try:
        watchlist_entry_from_url(url)
    except WatchlistUrlError as exc:
        _fail(exc, as_json=as_json, fallback="watchlist_url_invalid")
        return

    home_root = home_value or default_home_root()
    try:
        resolved_target = _resolved_target(target_value, home_root, as_json=as_json)
        target = resolved_target.expanduser().resolve(strict=True)
        before = {item.watchlist_id for item in list_active(home_root, target)}
        entry = add_company_from_url(url, home_root, target)
    except (ScoutTargetError, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_watchlist_add_failed")
        return

    created = entry.watchlist_id not in before
    payload = {
        "ok": True,
        "created": created,
        "company": entry.company,
        "provider": entry.provider.value,
        "board_token": entry.board_token,
        "watchlist_id": entry.watchlist_id,
        "board_url": entry.first_seen.source_url,
    }
    if as_json:
        _emit(payload, True, "")
        return
    verb = "Added" if created else "Already watching"
    click.echo(f"{verb} {entry.company} ({entry.provider.value} board '{entry.board_token}').")


# --- 0110-022: `gigai scout profile list|update` ---------------------------


@scout_group.group("profile")
def profile_group() -> None:
    """Show profiles, set a profile's own search settings, delete a profile."""


def _profiles_context(target_value: Path | None, home_root: Path, *, as_json: bool):
    """``(resolved gig, target, profiles, selected, default's settings)`` for the profile commands."""

    from ..workpad import resolve_workpad
    from . import profile_records
    from .find_jobs.effective_config import default_search_settings

    target = _resolved_target(target_value, home_root, as_json=as_json).expanduser().resolve(strict=True)
    resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
    # Migrates the default profile on first read, like every profile-aware path.
    selected = profile_records.selected_profile(resolved, home_root=home_root, target=target)
    profiles = profile_records.list_profiles(resolved)
    return resolved, profiles, selected, default_search_settings(home_root=home_root, target=target)


def _profile_payload(record, *, default_id: str | None, selected_id: str | None) -> dict[str, object]:
    return {
        "profile_id": record.profile_id,
        "label": record.label,
        "state": record.state,
        "revision": record.revision,
        "titles": list(record.titles),
        "is_default": record.profile_id == default_id,
        "selected": record.profile_id == selected_id,
        "search_settings": None if record.search_settings is None else record.search_settings.to_json(),
    }


def _title_warnings(home_root: Path, resolved) -> dict[str, list[dict[str, object]]]:
    """0110-8-05: each active profile's generic titles with their live match count; nothing when it cannot be read."""

    from . import postings
    from .find_jobs.generic_titles import generic_title_warnings

    try:
        target = Path(resolved.target_root)
        _resolved, views = postings.active_profiles(home_root, target, resolved)
        return generic_title_warnings(home_root, target, views)
    except (postings.PostingModelError, OSError, ValueError):
        return {}


def _settings_line(settings: dict[str, object] | None) -> str:
    if settings is None:
        return "no setup settings saved yet"
    countries = ", ".join(settings["countries"]) or "any country"  # type: ignore[arg-type]
    window = settings["max_age_days"]
    return (
        f"{settings['location'] or 'no area'} · {settings['work_mode']} · {countries} · "
        + (f"last {window} days" if window is not None else "the default window")
    )


@profile_group.command("list")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
@_reads_committed
def profile_list_command(target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """List the profiles and what each one searches with.

    The default profile uses the setup settings (location, work mode,
    countries, posted window). Every other profile has its own, or uses the
    default's when it has none.
    """

    from . import profile_records

    home_root = home_value or default_home_root()
    try:
        resolved, profiles, selected, shared = _profiles_context(target_value, home_root, as_json=as_json)
    except (ScoutTargetError, WorkpadError, profile_records.ProfileRecordError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_profile_list_failed")
        return
    warnings = _title_warnings(home_root, resolved)
    default = profile_records.default_profile(profiles)
    default_id = None if default is None else default.profile_id
    selected_id = None if selected is None else selected.profile_id
    shared_json = None if shared is None else shared.to_json()
    items = [_profile_payload(item, default_id=default_id, selected_id=selected_id) for item in profiles if item.state != "deleted"]
    for item in items:
        item["title_warnings"] = warnings.get(str(item["profile_id"]), [])
    if as_json:
        _emit({"ok": True, "profiles": items, "default_profile_id": default_id, "default_search_settings": shared_json}, True, "")
        return
    if not items:
        click.echo("No profiles yet. Finish the Scout setup and add a resume.")
        return
    for item in items:
        marks = [mark for mark, on in (("default", item["is_default"]), ("selected", item["selected"]), ("archived", item["state"] == "archived")) if on]
        click.echo(f"{item['label']} ({item['profile_id']})" + (f" [{', '.join(marks)}]" if marks else ""))
        own = item["search_settings"]
        if item["is_default"]:
            click.echo(f"  setup settings: {_settings_line(shared_json)}")
        elif own is None:
            click.echo(f"  same as default: {_settings_line(shared_json)}")
        else:
            click.echo(f"  own settings: {_settings_line(own)}")  # type: ignore[arg-type]
        for warning in item["title_warnings"]:  # type: ignore[union-attr]
            click.echo(f"  warning: {warning['text']}")


@profile_group.command("update")
@click.argument("profile_id")
@click.option("--location", help="City or area this profile searches and ranks for (not printed on the resume).")
@click.option("--no-location", is_flag=True, help="No area preference.")
@click.option("--work-mode", type=click.Choice(["remote", "hybrid", "onsite", "any"]))
@click.option("--country", "countries", multiple=True, help="ISO-3166 alpha-2 code; repeat for more. Replaces the list.")
@click.option("--max-age-days", type=click.IntRange(1, 365), help="Only postings from the last N days.")
@click.option("--default-window", is_flag=True, help="Use the default profile's posted window.")
@click.option("--same-as-default", is_flag=True, help="Drop this profile's own settings; use the default profile's.")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def profile_update_command(
    profile_id: str,
    location: str | None,
    no_location: bool,
    work_mode: str | None,
    countries: tuple[str, ...],
    max_age_days: int | None,
    default_window: bool,
    same_as_default: bool,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """Set a profile's own location, work mode, countries and posted window.

    The options given replace that profile's values; the others keep theirs
    (its own, else the default profile's). The default profile uses the
    setup settings and is refused here: change those in the Scout UI under
    Settings. No other profile is changed.
    """

    from . import profile_records

    changes = any((location is not None, no_location, work_mode, countries, max_age_days is not None, default_window))
    if same_as_default and changes:
        _fail(ValueError("pass --same-as-default alone"), as_json=as_json, fallback="scout_profile_invalid")
    if not same_as_default and not changes:
        _fail(ValueError("nothing to change: pass at least one setting, or --same-as-default"), as_json=as_json, fallback="scout_profile_invalid")
    if location is not None and no_location:
        _fail(ValueError("pass --location or --no-location, not both"), as_json=as_json, fallback="scout_profile_invalid")
    if max_age_days is not None and default_window:
        _fail(ValueError("pass --max-age-days or --default-window, not both"), as_json=as_json, fallback="scout_profile_invalid")

    home_root = home_value or default_home_root()
    try:
        resolved, profiles, selected, shared = _profiles_context(target_value, home_root, as_json=as_json)
        existing = next((item for item in profiles if item.profile_id == profile_id), None)
        if existing is None:
            raise profile_records.ProfileRecordError("scout_profile_unavailable", f"profile {profile_id!r} is not committed in this gig")
        if same_as_default:
            record = profile_records.write_profile(resolved, profile_id=profile_id, clear_search_settings=True)
        else:
            base = existing.search_settings or shared
            merged: dict[str, object] = (
                base.to_json() if base is not None else {"location": None, "work_mode": "any", "countries": ["US"], "max_age_days": None}
            )
            if location is not None:
                merged["location"] = location
            if no_location:
                merged["location"] = None
            if work_mode:
                merged["work_mode"] = work_mode
            if countries:
                merged["countries"] = [code.upper() for code in countries]
            if max_age_days is not None:
                merged["max_age_days"] = max_age_days
            if default_window:
                merged["max_age_days"] = None
            record = profile_records.write_profile(
                resolved, profile_id=profile_id, search_settings=profile_records.ProfileSearchSettings.from_json(merged)
            )
    except (ScoutTargetError, WorkpadError, profile_records.ProfileRecordError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_profile_update_failed")
        return
    # 0.1.10.7 PL5: other settings re-open this profile's pipeline steps whose inputs changed (never fails the update).
    from .pipeline import triggers as pipeline_triggers

    pipeline_triggers.profile_changed(home_root, Path(resolved.target_root), profile_id)
    default = profile_records.default_profile(profiles)
    payload = _profile_payload(
        record,
        default_id=None if default is None else default.profile_id,
        selected_id=None if selected is None else selected.profile_id,
    )
    payload["title_warnings"] = _title_warnings(home_root, resolved).get(record.profile_id, [])
    if as_json:
        _emit({"ok": True, "profile": payload}, True, "")
        return
    if record.search_settings is None:
        click.echo(f"{record.label} ({record.profile_id}) now uses the default profile's settings.")
    else:
        click.echo(f"{record.label} ({record.profile_id}): {_settings_line(record.search_settings.to_json())}")
    for warning in payload["title_warnings"]:  # type: ignore[union-attr]
        click.echo(f"  warning: {warning['text']}")


@profile_group.command("delete")
@click.argument("profile_id")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def profile_delete_command(profile_id: str, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """Delete a profile: it stops showing and searching; its history stays.

    The profile leaves the profile list, the switcher, new runs and
    background tagging. Its runs and assessments stay readable (hidden from
    the default Jobs list); the story bank and answers are not touched. The
    default profile and the only active profile are refused. Deleting the
    selected profile selects the default profile.
    """

    from . import profile_records

    home_root = home_value or default_home_root()
    try:
        resolved, profiles, _selected, _shared = _profiles_context(target_value, home_root, as_json=as_json)
        existing = next((item for item in profiles if item.profile_id == profile_id and item.state != "deleted"), None)
        if existing is None:
            raise profile_records.ProfileRecordError("scout_profile_unavailable", f"profile {profile_id!r} is not committed in this gig")
        profile_records.write_profile(resolved, profile_id=profile_id, state="deleted")
        selection = profile_records.selected_profile(resolved, home_root=home_root, target=resolved.target_root)
    except (ScoutTargetError, WorkpadError, profile_records.ProfileRecordError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_profile_delete_failed")
        return
    selected_id = None if selection is None else selection.profile_id
    if as_json:
        _emit({"ok": True, "deleted": profile_id, "selected_profile_id": selected_id}, True, "")
        return
    click.echo(f"Deleted {existing.label} ({profile_id}). Its history stays; the story bank is untouched.")
    if selection is not None:
        click.echo(f"Selected profile: {selection.label} ({selection.profile_id})")


# --- N11-C (v0.1.9): `gigai scout sources update|status` -------------------


@scout_group.group("sources")
def sources_group() -> None:
    """Refresh the company boards Scout searches (stored on this machine)."""


def _sources_progress_line(snapshot: dict[str, object]) -> str:
    boards = snapshot.get("boards")
    postings = snapshot.get("postings")
    boards = boards if isinstance(boards, dict) else {}
    postings = postings if isinstance(postings, dict) else {}
    failed = f", {boards.get('failed')} failed" if boards.get("failed") else ""
    # 0110-10-11: ``total`` is the boards DUE this run, not every stored company: the line says which, and how many
    # were checked recently and are left alone (``up_to_date``).
    fresh = boards.get("up_to_date")
    left = f" ({fresh:,} more were checked recently and are not asked again)" if isinstance(fresh, int) and fresh > 0 else ""
    return (
        f"Boards due this run: {boards.get('checked', 0):,} of {boards.get('total', 0):,} checked{failed}{left}: "
        f"{postings.get('new', 0)} new, {postings.get('changed', 0)} changed, {postings.get('removed', 0)} removed"
    )


def _sources_checked_line(boards: dict[str, object], elapsed: float) -> str:
    """0110-10-11: what one update checked, out of ALL the watched companies, and why the rest were not asked.

    ``boards.total`` is the boards that were due (never checked, or checked more than a day ago); ``up_to_date`` the
    ones checked within the last day, which an update leaves alone. Their sum is every watched company: the number
    ``gigai scout sources status`` gives as stored companies, give or take a company whose board never answered.
    """

    checked, due = int(boards["checked"]), int(boards["total"])  # type: ignore[call-overload]
    fresh = boards.get("up_to_date")
    fresh = fresh if isinstance(fresh, int) and fresh > 0 else 0
    line = (
        f"Checked {checked:,} of {due + fresh:,} companies this run "
        f"({boards['cached']} unchanged, {boards['failed']} did not answer) in {elapsed:.0f}s."
    )
    if fresh:
        was = "was" if fresh == 1 else "were"
        line += f" The other {fresh:,} {was} checked within the last day and {was} not asked again."
    return line


@sources_group.command("update")
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option(
    "--budget-seconds",
    "budget_seconds",
    type=click.FloatRange(min=0),
    default=None,
    help="Stop after this many seconds (0 = no limit). Default: GIGAI_SCOUT_ACQUIRE_BUDGET_SECONDS, else 1200.",
)
@click.option("--force", is_flag=True, help="Start even when another update looks like it is still running.")
@click.option("--json", "as_json", is_flag=True)
def sources_update_command(
    target_value: Path | None,
    home_value: Path | None,
    budget_seconds: float | None,
    force: bool,
    as_json: bool,
) -> None:
    """Check every company board on the watchlist and store what changed.

    One polite conditional request per board (catalog companies and the
    ones you added), so an unchanged board costs almost nothing. New,
    changed and removed postings are recorded per company under
    <home>/cache/scout/companies/ (plain JSON; safe to delete, the next
    update rebuilds it). A find-jobs search reads that store and does not
    check the boards itself. If the time budget stops an update, run it
    again: it continues with the boards it has not reached yet.
    """

    from dataclasses import replace
    import time

    from .find_jobs.market_acquisition import AcquireLimits
    from .find_jobs.sources_update import (
        STATUS_FAILED,
        STATUS_PARTIAL,
        SourcesUpdateError,
        default_http_client,
        load_effective_config,
        run_sources_update,
    )

    home_root = home_value or default_home_root()
    limits = AcquireLimits.from_environment()
    if budget_seconds is not None:
        limits = replace(limits, time_budget_seconds=budget_seconds if budget_seconds > 0 else None)

    printed_at = [0.0]

    def _progress(snapshot: dict[str, object]) -> None:
        # The snapshot is rewritten every second; a line every five is enough.
        if as_json or snapshot.get("status") != "running" or time.monotonic() - printed_at[0] < 5.0:
            return
        printed_at[0] = time.monotonic()
        click.echo(_sources_progress_line(snapshot))

    try:
        resolved_target = _resolved_target(target_value, home_root, as_json=as_json)
        target = resolved_target.expanduser().resolve(strict=True)
        client = default_http_client()
        try:
            result = run_sources_update(
                home_root=home_root,
                target=target,
                client=client,
                config=load_effective_config(home_root, target),
                limits=limits,
                on_progress=_progress,
                force=force,
            )
        finally:
            client.close()
    except (SourcesUpdateError, ScoutTargetError, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_sources_update_failed")
        return

    snapshot = result.to_json()
    if as_json:
        _emit(snapshot, True, "")
    else:
        boards = snapshot["boards"]
        assert isinstance(boards, dict)
        click.echo(result.summary)
        click.echo(_sources_checked_line(boards, float(snapshot["elapsed_seconds"])))  # type: ignore[arg-type]
        # 0110-10-11: "new" here is every posting on every board; `gigai scout new` counts only what a profile matches.
        click.echo("New, changed and removed count every posting on every board, whatever its title; `gigai scout new` lists the new ones your profiles match.")
        asked = snapshot.get("requests_by_board")
        if isinstance(asked, dict) and asked:
            named = ", ".join(f"{board} {count}" for board, count in asked.items())
            click.echo(f"{snapshot['requests']} requests; boards that also asked for descriptions (requests each): {named}.")
        if result.status == STATUS_PARTIAL:
            click.echo(f"{snapshot['remaining']} boards are left: run `gigai scout sources update` again to continue.")
    if result.status == STATUS_FAILED:
        raise click.exceptions.Exit(1)


def _sources_waiting_lines(update: dict[str, object]) -> list[str]:
    """One line per system whose boards the last update did not check for a reason a later update may fix (0.1.11.8).

    ``Workable: 1,258 boards waiting (rate_limited; asks again at the next update)``: read from the snapshot's
    ``backoff`` (a system left for this pass: ``rate_limited``, or ``robots_unknown`` for a host whose robots.txt could
    not be read) and ``failures.by_provider`` (boards whose own host's robots.txt could not be read or does not allow
    the request). A snapshot from before those keys has no lines.
    """

    def boards(count: int) -> str:
        return f"{count:,} board{'' if count == 1 else 's'}"

    backoff = update.get("backoff")
    failures = update.get("failures")
    by_provider = failures.get("by_provider") if isinstance(failures, dict) else None
    held: dict[str, tuple[int, str]] = {}
    if isinstance(backoff, dict):
        for provider, entry in backoff.items():
            skipped = entry.get("skipped") if isinstance(entry, dict) else None
            code = entry.get("code") if isinstance(entry, dict) else None
            if isinstance(skipped, int) and skipped > 0 and isinstance(code, str):
                held[str(provider)] = (skipped, code)
    codes_by_provider = by_provider if isinstance(by_provider, dict) else {}
    lines: list[str] = []
    for provider in sorted({*held, *codes_by_provider}):
        parts: list[str] = []
        waiting = held.get(provider)
        if waiting is not None:
            parts.append(f"{boards(waiting[0])} waiting ({waiting[1]}; asks again at the next update)")
        codes = codes_by_provider.get(provider)
        codes = codes if isinstance(codes, dict) else {}
        unreadable = codes.get("robots_unknown")
        if isinstance(unreadable, int) and unreadable > 0 and (waiting is None or waiting[1] != "robots_unknown"):
            parts.append(f"{boards(unreadable)} not asked (robots_unknown: robots.txt could not be read; asked again within the hour)")
        disallowed = codes.get("robots_disallowed")
        if isinstance(disallowed, int) and disallowed > 0:
            parts.append(f"{boards(disallowed)} not asked (robots_disallowed: the host's robots.txt does not allow it)")
        if parts:
            lines.append(f"{provider.capitalize()}: " + "; ".join(parts))
    return lines


@sources_group.command("status")
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def sources_status_command(home_value: Path | None, as_json: bool) -> None:
    """Show the last sources update and whether a search can use the stored postings."""

    from .find_jobs.sources_update import read_status

    status = read_status(home_value or default_home_root())
    if as_json:
        _emit(status, True, "")
        return
    update = status["update"]
    index = status["index"]
    assert isinstance(index, dict)
    if isinstance(update, dict):
        click.echo(f"Last update {update['status']} ({update.get('finished_at') or update.get('updated_at')}): {update['summary']}")
        if update["status"] == "running":
            click.echo(_sources_progress_line(update))
        for line in _sources_waiting_lines(update):
            click.echo(line)
    else:
        click.echo("No sources update has run yet.")
    click.echo(f"Stored companies: {index['companies_indexed']} ({index['status']}).")
    if index["message"]:
        click.echo(str(index["message"]))


# --- 0.1.10.7 E: `gigai scout metrics` ------------------------------------


def _metrics_tokens(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "no token count"
    return f"{value / 1000:.1f}k tokens" if value >= 1000 else f"{int(value)} tokens"


@scout_group.command("metrics")
@click.option("--kind", "kind", help="Only this kind of call: assess, rank, tag, tailor, extract or interview.")
@click.option("--model", "model", help="Only this model target (codex_cli, claude_cli, ...) or model id.")
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def metrics_command(
    kind: str | None, model: str | None, home_value: Path | None, target_value: Path | None, as_json: bool
) -> None:
    """Show what the model calls cost on average: tokens, seconds, cost and error rate per kind of call and model."""

    from .call_metrics import CallMetricsError, metrics_report
    from .pipeline.store import PipelineStoreError

    home_root = home_value or default_home_root()
    try:
        # Resolved like every Scout command; with no GigAI home yet there is nothing to read and nothing is created.
        target = _snapshot_target(target_value, home_root, as_json=as_json)
        report = metrics_report(home_root, target, kind=kind, model=model)
    except (ScoutTargetError, CallMetricsError, PipelineStoreError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_metrics_failed")
        return
    if as_json:
        _emit(report, True, "")
        return
    rows = report["comparison"]
    assert isinstance(rows, list)
    if not rows:
        click.echo("No model call has been recorded yet.")
        return
    for row in rows:
        seconds = f"{row['avg_seconds']:.1f} s" if row["avg_seconds"] is not None else "no time"
        cost = f", ${row['avg_cost_usd']:.4f}" if row["avg_cost_usd"] is not None else ""
        click.echo(
            f"{row['kind']} on {row['model_target'] or 'unknown'}: avg {_metrics_tokens(row['avg_tokens'])}, {seconds}{cost} "
            f"per call; {row['calls']} calls, {row['errors']} failed ({row['error_rate'] * 100:.0f}%)."
        )


# --- 0.1.10.7 M3a: `gigai scout new` ---------------------------------------


@scout_group.command("new")
@click.option("--profile", "profile_id", help="Only this active profile's postings. A filtered call does not move the \"new since\" anchor.")
@click.option("--yes", "yes", is_flag=True, help="Assess the new postings no profile has assessed, without asking (one model call each): the top 50 by rank, never more in one call. Never the old assessments: that is --reassess-stale.")
@click.option("--reassess-stale", "reassess_stale", is_flag=True, help="The yes to the other question: assess again the postings that have only an old assessment (one model call each): the top 50 by rank, never more in one call. Can be combined with --yes.")
@click.option("--include-low-rank", "include_low_rank", is_flag=True, help="With --yes or --reassess-stale: also the low-ranked postings (rank below fit.assess_min_rank, 50), which a yes leaves out by default.")
@click.option("--no-assess", "no_assess", is_flag=True, help="Do not ask and do not assess: show the new postings ranked only. Never waits for an answer, in a terminal or without one: the offers are printed with their counts.")
@click.option("--yours", "yours", is_flag=True, help="The separate call: what matches, from your own resume and answers. Never shown next to posting text; never moves the anchor.")
@click.option("--peek", "peek", is_flag=True, help="Look without moving the \"new since\" anchor.")
@click.option("--process", "process", is_flag=True, help="The yes to the pipeline offer: approve what waits for an approval and run the waiting pipeline steps now (model calls, within the daily cap; at most 50 steps in one call). Does not move the anchor.")
@click.option("--since", "since", help="Measure \"new\" from this time: the since of the response that asked.")
@click.option("--us-only/--no-us-only", "us_only", default=None, help="US only, as `gigai scout jobs list`: leave out a posting only when every place its location names is clearly outside the US, so a job posted only outside the US is not new and not counted. 'Remote' alone, no location or a place Scout cannot read stays, with an 'unclear location' label. Default: on when the countries of your setup include the US, off otherwise.")
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def new_command(
    profile_id: str | None, yes: bool, reassess_stale: bool, include_low_rank: bool, no_assess: bool, yours: bool, peek: bool,
    process: bool, since: str | None, us_only: bool | None, home_value: Path | None, target_value: Path | None, as_json: bool,
) -> None:
    """Show what is new since your last check, across all active profiles.

    Read from the stored postings: no board is asked. New postings are
    assessed only on a yes: without --yes the command asks first (with the
    count and what it will cost) and shows them ranked. With nothing new it
    shows the 10 postings that still need your attention.

    One row a job: the same company, title and description posted more than
    once (only the location differs: one per country or city) is ONE new
    job, as in `gigai scout jobs list`. The row is its US posting when it
    has one, else the earliest posted, and lists the locations. Every count
    ("N new jobs", each question) counts jobs, and a yes makes one model
    call a job. US only applies as in the list (--us-only / --no-us-only).

    Postings that have only an OLD assessment (made by an old run, an older
    prompt or other settings) are a separate question with its own count and
    cost. --yes never answers it; --reassess-stale does.

    50 at a time: every yes (--yes, --reassess-stale, an answer at a prompt)
    acts on the top 50 postings by rank and never more (a posting not ranked
    yet comes after the ranked ones, the newest first). Each question says
    the real total, the 50 and what the 50 cost ("re-assess the top 50 by
    rank of 422? ~50 calls ... (372 more after these 50)"); the same command
    again does the next 50.

    The "new since" time moves when the run has done its work: a run you
    leave at a question (Ctrl-C) changes nothing, and the next run shows
    the same postings as new. --no-assess never asks.

    Without a terminal (--json, a pipe) a reply that asks is a PREVIEW: it
    moves nothing, so asking again shows the same postings and a plain
    --yes after it assesses them. The time moves with the answer: --yes,
    or --no-assess for a no.

    A yes assesses only postings ranked 50 or more (the fit.assess_min_rank
    setting). The low-ranked ones are counted and asked about separately;
    --include-low-rank beside --yes assesses them too. A posting that waits
    on your answers with few requirements met and a low rank is a "weak fit":
    it is not listed here (`gigai scout jobs list --state weak_fit` lists them).
    A new posting you already applied to is left out too, and never assessed
    here (`gigai scout jobs list --state applied` lists them).
    A posting not assessed yet and ranked below 50 is listed like the rest,
    lower by its rank: the table prints a "Ranked low (N)" line above them,
    and with --json each row says ranked_low.

    While it assesses, progress lines go to stderr ("assessed 120 of 333 ·
    ~25 min left"); with --json, stdout is still the response alone.

    The table shows each posting, its verdict, its fit number (the share of
    requirements met, must-haves weighted), "N of M" requirements and rank,
    what it still asks for and its open questions. Postings with a current
    assessment come first, then old assessments, then the ones not assessed;
    inside a group the best fit first. "What matches" comes from your own resume and answers, so
    it is a separate call, never printed next to posting text: --yours.

    Jobs that wait in the pipeline (you answered one of their questions) are
    offered with what they would cost; --process runs them now.
    """

    import sys

    from .data_labels import LabelError
    from .outbound_check import redact_payload
    from .pipeline.store import PipelineStoreError
    from .scout_new import PostingModelError, ScoutNewError, is_preview, render, scout_new, scout_new_yours, settle_anchor

    home_root = home_value or default_home_root()
    errors = (ScoutTargetError, WorkpadError, ScoutNewError, PostingModelError, PipelineStoreError, LabelError, OSError, ValueError)
    if sum((yes, no_assess, yours)) > 1:
        _fail(ValueError("pass at most one of --yes, --no-assess and --yours"), as_json=as_json, fallback="invalid_value")
        return
    if yours and (process or reassess_stale):
        _fail(ValueError("--process and --reassess-stale cannot be combined with --yours"), as_json=as_json, fallback="invalid_value")
        return
    if include_low_rank and not (yes or reassess_stale):
        _fail(ValueError("--include-low-rank goes with --yes or --reassess-stale"), as_json=as_json, fallback="invalid_value")
        return

    def progress(line: str) -> None:
        click.echo(line, err=True)  # stderr: stdout stays the response (valid JSON with --json)

    said = [0.0]

    def build_progress(phase: str, done: int, total: int) -> None:
        # 0110-9-01: the first build over a large index takes a while (once): never silent. A line every 2 s at most.
        if phase == "matching" and total >= 500 and (time.monotonic() - said[0] >= 2.0 or done == total):
            said[0] = time.monotonic()
            click.echo(f"preparing your postings: {int(100 * done / total)}% ({done} of {total} companies)", err=True)

    # 0110-10-11: --no-assess never asks ("does not ask and does not assess"), in a terminal or without one.
    asking = not as_json and not yours and not no_assess and sys.stdin.isatty()
    try:
        target = _pipeline_target(target_value, home_root, as_json=as_json)
        if yours:
            response = scout_new_yours(home_root, target, profile_id=profile_id, since=since, us_only=us_only)
        else:
            assess = True if yes else False if no_assess else None
            # 0110-10-11: a run that may ask holds the "new since" anchor until it has done its work (settle_anchor,
            # below): left at a prompt (Ctrl-C, end of input), it has consumed nothing. A run that puts no prompt
            # (--json, --no-assess, no terminal) leaves the anchor to scout_new: it moves unless the reply is a preview
            # (0.1.10.11 NA, scout_new.is_preview: a reply that asks moves nothing; the --yes or --no-assess after it does).
            response = scout_new(
                home_root, target, profile_id=profile_id, peek=peek, assess=assess, since=since, process=process,
                reassess_stale=reassess_stale, progress=progress, build_progress=build_progress,
                include_low_rank=include_low_rank, advance=not asking, us_only=us_only,
            )
        first = response
        if is_preview(response) and asking:
            sentence = response["question"]["text"]  # type: ignore[index]
            if click.confirm(str(sentence).rstrip("?"), default=False):
                click.echo("Assessing (one model call per job; this can take a few minutes)...")
                # The yes measures from the same since; the anchor moves when the run is done.
                response = scout_new(
                    home_root, target, profile_id=profile_id, peek=peek, assess=True, since=str(response["since"]), progress=progress,
                    advance=False, us_only=us_only,
                )
        low = response.get("low_rank_question")
        if isinstance(low, dict) and asking:
            # 0110-10-02: its own question, default no: a plain yes never assesses the low-ranked ones.
            if click.confirm(str(low["text"]).rstrip("?"), default=False):
                response = scout_new(
                    home_root, target, profile_id=profile_id, peek=peek, assess=True, since=str(response["since"]),
                    include_low_rank=True, progress=progress, advance=False, us_only=us_only,
                )
        old = response.get("stale_question")
        if isinstance(old, dict) and asking:
            # Its own question, default no: the yes above never answers it.
            if click.confirm(str(old["text"]).rstrip("?"), default=False):
                response = scout_new(
                    home_root, target, profile_id=profile_id, peek=peek, assess=False, since=str(response["since"]),
                    reassess_stale=True, progress=progress, advance=False, us_only=us_only,
                )
        offer = response.get("pipeline")
        if isinstance(offer, dict) and not process and asking:
            if click.confirm("Pipeline: " + str(offer["text"]).rstrip("?"), default=False):
                click.echo("Processing (the waiting pipeline steps; this can take a few minutes)...")
                response = scout_new(
                    home_root, target, profile_id=profile_id, peek=True, assess=False, since=str(response["since"]), process=True,
                    us_only=us_only,
                )
        if asking and not peek and profile_id is None and not process:
            # Every question is answered and acted on: now the anchor moves, to the time the FIRST call read the postings.
            settle_anchor(home_root, target, str(first["checked_at"]))
    except errors as exc:
        _fail(exc, as_json=as_json, fallback="scout_new_failed")
        return
    # What an agent reads: the same outbound check every API response passes (no contact data).
    response = redact_payload(response)
    _emit(response, as_json, "" if as_json else render(response))


# --- 0.1.10.7 M4a: `gigai scout jobs list|assess|import-runs` ----------------


_US_ONLY_HELP = "US only: leave out a posting only when every place its location names is clearly outside the US. 'Remote' alone, no location or a place Scout cannot read is NOT left out: it is listed with an 'unclear location' label. Default: on when the countries of your setup include the US, off otherwise."
_COLLAPSE_HELP = "The same company, title and description posted more than once (only the location differs: one per country) is ONE row that lists its locations; the page and the totals count rows. A posting with another description, or with none stored, stays its own row. --no-collapse lists every posting. Default: on."


@scout_group.group("jobs")
def jobs_group() -> None:
    """The stored postings your profiles match: search them, assess the ones you pick. No find-jobs run."""


def _jobs_errors() -> tuple[type[BaseException], ...]:
    from .data_labels import LabelError
    from .pipeline.store import PipelineStoreError
    from .posting_search import PostingModelError, PostingSearchError

    return (ScoutTargetError, WorkpadError, PostingSearchError, PostingModelError, PipelineStoreError, LabelError, OSError, ValueError)


@jobs_group.command("list")
@click.option("--profile", "profile_ids", multiple=True, help="A filter on the tags: only the jobs this active role (profile ID) found (repeatable). A job is listed once and shows its one assessment, whichever role is named.")
@click.option("--query", "query", help="Words that must all be in the title, company or location.")
@click.option("--state", "states", multiple=True, help="Keep this state (repeatable): not_assessed, needs_answers, matched, has_gap, not_a_match, tailored, assessed, recommended, applied, weak_fit, ranked_low, thin_posting (matched on no requirement at all). A weak fit (waits on answers, few requirements met, low rank) is listed only with --state weak_fit. A posting you already applied to (applied and after: interview, offer, rejected, withdrawn) is listed only with --state applied. A posting not assessed yet and ranked below 50 is always listed, lower by its rank; --state ranked_low lists only those.")
@click.option("--window", "window", type=click.Choice(["new", "7d", "30d"]), help="new: first seen since your last check. 7d / 30d: published in the last 7 or 30 days.")
@click.option("--removed", "removed", is_flag=True, help="The postings the board no longer lists, instead of the live ones.")
@click.option("--history", "history", is_flag=True, help="Also what old find-jobs runs assessed, with each run's provenance.")
@click.option("--include-hidden", "include_hidden", is_flag=True, help="With --history: also the hidden rows (a run with no profile, a profile that is not active).")
@click.option("--us-only/--no-us-only", "us_only", default=None, help=_US_ONLY_HELP)
@click.option("--collapse/--no-collapse", "collapse", default=None, help=_COLLAPSE_HELP)
@click.option("--limit", "limit", type=click.IntRange(min=1, max=200), default=50, show_default=True)
@click.option("--offset", "offset", type=click.IntRange(min=0), default=0)
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def jobs_list_command(
    profile_ids: tuple[str, ...], query: str | None, states: tuple[str, ...], window: str | None, removed: bool, history: bool,
    include_hidden: bool, us_only: bool | None, collapse: bool | None, limit: int, offset: int, home_value: Path | None,
    target_value: Path | None, as_json: bool,
) -> None:
    """Search the stored postings across your active profiles. No board is asked, no run is made, no model is called.

    Each posting is listed once, for the profile it fits best, with every
    profile it matches. The "new since" anchor of `gigai scout new` does not
    move. A posting you already applied to is left out (a line says how
    many; with --json, counts.applied): --state applied lists them.

    The same company, title and description posted more than once (one per
    country) is one row that lists its locations: one job, its US posting
    when it has one, else the earliest posted. --no-collapse lists each.
    With US only on, a posting clearly located outside the US is left out
    (a line says how many); one Scout cannot place is listed and labelled.
    """

    from .outbound_check import redact_payload
    from .posting_search import render, search_postings

    home_root = home_value or default_home_root()
    try:
        target = _pipeline_target(target_value, home_root, as_json=as_json)
        response = search_postings(
            home_root, target, profile_ids=profile_ids or None, query=query, states=states or None, window=window, removed=removed,
            history=history, include_hidden=include_hidden, limit=limit, offset=offset, us_only=us_only, collapse=collapse,
        )
    except _jobs_errors() as exc:
        _fail(exc, as_json=as_json, fallback="scout_jobs_failed")
        return
    response = redact_payload(response)
    _emit(response, as_json, "" if as_json else render(response))


@jobs_group.command("search")
@click.argument("titles", required=False)
@click.option("--company", "company", multiple=True, help="A WHOLE word of the company name (repeatable; case and accents aside): `ai` finds Example AI, not Maintain.")
@click.option("--location", "location", multiple=True, help="A WHOLE word of the posting's location (repeatable): `remote`, `denver`.")
@click.option("--all", "show_all", is_flag=True, help="Any date and work mode: drop the default profile's countries, posted window and work mode. US only still applies until --no-us-only.")
@click.option("--us-only/--no-us-only", "us_only", default=None, help=_US_ONLY_HELP + " It still applies with --all; with it off and no --all, your default countries apply.")
@click.option("--collapse/--no-collapse", "collapse", default=None, help=_COLLAPSE_HELP)
@click.option("--limit", "limit", type=click.IntRange(min=1, max=200), default=50, show_default=True)
@click.option("--offset", "offset", type=click.IntRange(min=0), default=0)
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def jobs_search_command(
    titles: str | None, company: tuple[str, ...], location: tuple[str, ...], show_all: bool, us_only: bool | None, collapse: bool | None,
    limit: int, offset: int, home_value: Path | None, target_value: Path | None, as_json: bool,
) -> None:
    """Search EVERY stored posting by title, newest first. No profile, no rank, no model call; writes nothing.

    TITLES is one or more titles, comma separated: "Senior Systems Engineer,
    Staff Systems Engineer". Every typed word must be in the title, seniority
    included ("senior engineer" lists only Senior or Sr. titles). Company and
    location words match WHOLE words, not parts of a word.

    The default profile's countries, posted window and work mode apply;
    --all drops them. US only is a switch of its own: one country rule
    decides a search (US only on: the US alone, also with --all; off: your
    default countries, or any country with --all). A posting Scout cannot
    place ("Remote" alone, no location) is never hidden by it: it is listed
    and labelled. The same company, title and description posted more than
    once is one row with its locations (one job: its US posting, else the
    earliest posted), and the total counts rows. The page is printed
    first, the total after it. Read
    from the local search index `gigai scout sources update` keeps; without
    one, every company file is read (slower, same rows).
    """

    from .find_jobs import free_search
    from .outbound_check import redact_payload

    home_root = home_value or default_home_root()
    try:
        target = _pipeline_target(target_value, home_root, as_json=as_json)
        request = free_search.SearchRequest.typed(
            titles, company=company, location=location, show_all=show_all, limit=limit, offset=offset, count=as_json,
            us_only=us_only, collapse=collapse,
        )
        for line in free_search.answer_lines(home_root, request, target=target, as_json=as_json, redact=redact_payload):
            click.echo(line)
    except (*_jobs_errors(), free_search.FreeSearchError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_jobs_failed")


@jobs_group.command("assess")
@click.argument("jobs", nargs=-1)
@click.option("--profile", "profile_id", help="A filter on the tags: only the jobs this active role (profile ID) found. A job is assessed once, whichever role is named.")
@click.option("--query", "query", help="Without JOBS: words that must all be in the title, company or location.")
@click.option("--state", "states", multiple=True, help="Without JOBS: keep this state (repeatable), as `gigai scout jobs list`.")
@click.option("--window", "window", type=click.Choice(["new", "7d", "30d"]), help="Without JOBS: new, 7d or 30d, as `gigai scout jobs list`.")
@click.option("--us-only/--no-us-only", "us_only", default=None, help="Without JOBS: as `gigai scout jobs list` (default: on for a US setup).")
@click.option("--yes", "yes", is_flag=True, help="Approve: assess without asking (one model call per posting).")
@click.option("--again", "again", is_flag=True, help="Also the postings whose assessment is current.")
@click.option("--include-low-rank", "include_low_rank", is_flag=True, help="Also the low-ranked postings (rank below fit.assess_min_rank, 50), which are left out by default.")
@click.option("--actor", "actor", type=click.Choice(["operator", "agent"]), default="operator", show_default=True, help="Who approves the batch.")
@click.option("--cancel", "cancel", is_flag=True, help="Cancel the assess batch that is running (here, in another terminal or in the Scout server): no further model call starts, the calls in flight finish and what finished is kept. Assesses nothing.")
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def jobs_assess_command(
    jobs: tuple[str, ...], profile_id: str | None, query: str | None, states: tuple[str, ...], window: str | None,
    us_only: bool | None, yes: bool, again: bool, include_low_rank: bool, actor: str, cancel: bool, home_value: Path | None, target_value: Path | None, as_json: bool,
) -> None:
    """Assess these postings: the ones named (posting URLs), or the ones the filter selects.

    Nothing is assessed without approval: without --yes the command says how
    many would be assessed and what it will cost, and asks (in a terminal) or
    stops there (--json, or no terminal). A filter selects what `gigai scout
    jobs list` shows: one posting per job (a job posted once per country is
    one model call, for its canonical posting) and, with US only on, no
    posting clearly located outside the US.
    Each posting is assessed for the
    profile it fits best, from the posting text already stored (a posting with
    none has its description fetched first, one request for it alone). One
    approval assesses the top 50 by rank and never more.

    Postings ranked below 50 (the fit.assess_min_rank setting) are left out
    and counted; they are asked about separately, and --include-low-rank
    puts them in the pool (one approval is still the top 50 by rank).

    A batch that runs can be cancelled: --cancel (from another terminal, or
    for a batch the Jobs page started). The calls in flight finish and every
    assessment already stored is kept; the same command again takes the rest.

    A posting named by its URL need not be in a role's list: one that
    `gigai scout jobs search` lists is assessed all the same, once, as the
    job it is (no role is chosen for it), and the question says so. The URL
    can be the job page's own address. A job posted once per country is one
    job: any copy's URL assesses it once. Exit code 1 when every named URL is not found or was not
    assessed (the output is the same object and says which); 0 when at
    least one was found and did not fail.
    """

    import sys

    from .outbound_check import redact_payload
    from .assess_preview import summary_lines
    from .posting_search import STATUS_ASK, assess_these, named_all_failed, render

    home_root = home_value or default_home_root()
    if cancel:
        # 0.1.11.5 (ASSESS-01): the batch is asked through its marker, whichever process runs it.
        from .pipeline import busy

        if jobs or yes or again or include_low_rank or profile_id or query or states or window or us_only is not None:
            _fail(click.UsageError("--cancel takes no posting, filter or approval option"), as_json=as_json, fallback="invalid_value")
            return
        try:
            target = _pipeline_target(target_value, home_root, as_json=as_json)
        except _jobs_errors() as exc:
            _fail(exc, as_json=as_json, fallback="scout_jobs_failed")
            return
        asked = busy.request_cancel(home_root, target)
        batch = busy.batch_status(home_root, target)
        answer: dict[str, object] = {"schema_version": "scout-assess-batch:1", "cancel_requested": asked, "running": batch is not None, "batch": batch}
        if asked and batch is not None:
            text = (
                f"Cancelling the assess batch: {batch['assessed']} of {batch['total']} assessed so far. No further model call starts; "
                f"{batch['in_flight']} in flight finish{'es' if batch['in_flight'] == 1 else ''} and what finished is kept."
            )
        else:
            text = "No assess batch is running: nothing to cancel."
        _emit(answer, as_json, "" if as_json else text)
        return

    def call(approve: bool, low_rank: bool = include_low_rank) -> dict[str, object]:
        return assess_these(
            home_root, target, jobs=list(jobs) or None, profile_id=profile_id, query=query, states=states or None, window=window,
            approve=approve, again=again, decided_by=actor, include_low_rank=low_rank, us_only=us_only,
        )

    try:
        target = _pipeline_target(target_value, home_root, as_json=as_json)
        response = call(yes)
        asking = not as_json and sys.stdin.isatty()
        if response["status"] == STATUS_ASK and asking and response["counts"]["to_assess"]:  # type: ignore[index]
            # 0110-10-13: what would be sent and where, before the y/n.
            click.echo("\n".join(summary_lines(response["model_input_summary"])))  # type: ignore[arg-type]
            if click.confirm(str(response["question"]["text"]).rstrip("?"), default=False):  # type: ignore[index]
                click.echo("Assessing (one model call per posting; this can take a few minutes)...")
                response = call(True)
        low = response.get("low_rank")
        if isinstance(low, dict) and asking:
            # 0110-10-02: its own question, default no.
            if response["status"] != STATUS_ASK and isinstance(response.get("model_input_summary"), dict):
                # Only the low-ranked ones are asked about: what THEY would send, before this y/n (0110-10-13).
                click.echo("\n".join(summary_lines(response["model_input_summary"])))  # type: ignore[arg-type]
            if click.confirm(str(low["text"]).rstrip("?"), default=False):
                click.echo("Assessing (one model call per posting; this can take a few minutes)...")
                response = call(True, True)
    except _jobs_errors() as exc:
        _fail(exc, as_json=as_json, fallback="scout_jobs_failed")
        return
    response = redact_payload(response)
    _emit(response, as_json, "" if as_json else render(response))
    if jobs and named_all_failed(response):
        raise click.exceptions.Exit(1)  # 0.1.11.8: every posting named is not found or was not assessed


@jobs_group.command("import-runs")
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def jobs_import_runs_command(home_value: Path | None, target_value: Path | None, as_json: bool) -> None:
    """Import what old find-jobs runs assessed, so `gigai scout jobs list` and `gigai scout new` show it.

    Runs are read-only history: nothing of a run is changed or copied. Each
    run is imported once; running this again imports nothing. A run made with
    no profile is kept apart ("ephemeral") and hidden by default.
    """

    from .run_history import migrate_runs

    home_root = home_value or default_home_root()
    try:
        target = _pipeline_target(target_value, home_root, as_json=as_json)
        counts = migrate_runs(home_root, target)
    except _jobs_errors() as exc:
        _fail(exc, as_json=as_json, fallback="scout_jobs_failed")
        return
    line = (
        f"Imported {counts['assessments_imported']} assessment(s) of {counts['runs_imported']} run(s); "
        f"{counts['runs_already_imported']} run(s) were already imported."
    )
    if counts["ephemeral_assessments"]:
        line += f" {counts['ephemeral_assessments']} came from runs with no profile: hidden by default (`gigai scout jobs list --history --include-hidden`)."
    if counts["runs_not_finished"] or counts["runs_unreadable"]:
        line += f" {int(counts['runs_not_finished']) + int(counts['runs_unreadable'])} run(s) were left for later (not finished, or not readable now)."  # type: ignore[call-overload]
    _emit(counts, as_json, line)


# --- 0110-026b/e: `gigai scout snapshot export|import|status` ------------


@scout_group.group("snapshot")
def snapshot_group() -> None:
    """The metadata snapshot of the company index: build one to publish, or import the published one."""


@snapshot_group.command("export")
@click.option("--out", "out_value", required=True, type=click.Path(path_type=Path, file_okay=False), help="Directory to write the snapshot into.")
@click.option("--base", "base_value", type=click.Path(path_type=Path, dir_okay=False), help="A previous export's manifest.json (its files beside it): adds a delta and a removal list.")
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def snapshot_export_command(out_value: Path, base_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """Write index, title tags and board validators (no descriptions) plus a manifest; publish nothing."""

    from .find_jobs.snapshot import SnapshotError, export_snapshot

    try:
        result = export_snapshot(home_value or default_home_root(), out_value, base_manifest=base_value)
    except SnapshotError as error:
        if as_json:
            _emit({"ok": False, "code": error.code, "message": str(error)}, True, "")
        else:
            click.echo(f"Snapshot not written: {error}", err=True)
        raise click.exceptions.Exit(1) from error
    if as_json:
        _emit(
            {"ok": True, "out_dir": str(result.out_dir), "manifest": result.manifest, "files": list(result.files), "gh_commands": list(result.gh_commands)},
            True,
            "",
        )
        return
    counts = result.manifest["counts"]
    assert isinstance(counts, dict)
    click.echo(
        f"Snapshot as of {result.manifest['as_of']}: {counts['postings']} postings, {counts['boards']} boards, {counts['tags']} title tags. "
        f"Read-back verified {len(result.files)} files in {result.out_dir}."
    )
    click.echo("Nothing was published. To publish, run (the first command only the first time):")
    for command in result.gh_commands:
        click.echo(f"  {command}")


def _snapshot_target(target_value: Path | None, home_root: Path, *, as_json: bool = False) -> Path | None:
    # The setting is per project: resolved like every Scout command (--target, else <home>/scout; the cwd never matters).
    if target_value is None and not Path(home_root).is_dir():
        return None  # no GigAI home yet: nothing to resolve, the defaults apply and nothing is created
    return _resolved_target(target_value, home_root, as_json=as_json)


@snapshot_group.command("import")
@click.option("--from", "source_value", help="A manifest URL, a snapshot directory or its manifest.json. Default: the snapshot.manifest_url setting.")
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def snapshot_import_command(source_value: str | None, home_value: Path | None, target_value: Path | None, as_json: bool) -> None:
    """Download the published snapshot and add it to the stored postings; newer local data is kept."""

    from .find_jobs.snapshot import RESULT_FAILED, RESULT_REFUSED, import_snapshot

    home_root = home_value or default_home_root()
    try:
        target = _snapshot_target(target_value, home_root, as_json=as_json)
    except (ScoutTargetError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_snapshot_failed")
        return
    result = import_snapshot(home_root, source_value, target=target)
    if as_json:
        _emit(result.to_json(), True, "")
    elif result.imported:
        counts = result.counts
        click.echo(
            f"{result.message} {counts['postings']} postings on {counts['boards']} boards and {counts['tags']} title tags were added "
            f"({result.kind}); {counts['boards_kept_local']} boards were newer here and were left alone."
        )
    else:
        click.echo(result.message)
    if result.status in (RESULT_REFUSED, RESULT_FAILED):
        raise click.exceptions.Exit(1)


@snapshot_group.command("status")
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
@_reads_committed
def snapshot_status_command(home_value: Path | None, target_value: Path | None, as_json: bool) -> None:
    """Show which snapshot is in use, the last attempt and whether the download is on. Makes no request."""

    from .find_jobs.snapshot import snapshot_status

    home_root = home_value or default_home_root()
    try:
        target = _snapshot_target(target_value, home_root, as_json=as_json)
    except (ScoutTargetError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json, fallback="scout_snapshot_failed")
        return
    status = snapshot_status(home_root, target)
    if as_json:
        _emit(status, True, "")
        return
    click.echo(f"Snapshot download: {'on' if status['enabled'] else 'off'} ({status['setting_source']}).")
    if status["as_of"]:
        click.echo(f"Snapshot in use: as of {status['as_of']}, imported {status['imported_at']} from {status['source']}.")
    else:
        click.echo("No snapshot has been imported.")
    if status["last_attempt_at"]:
        reason = f" ({status['last_reason']})" if status["last_reason"] else ""
        click.echo(f"Last attempt {status['last_attempt_at']}: {status['last_result']}{reason}.")


# --- 0.1.10.7 PL4: `gigai scout pipeline run|status|process|cancel|retry` --------------


@scout_group.group("pipeline")
def pipeline_group() -> None:
    """The background pipeline (off by default in 0.1.11): steps, queue and caps. Nothing runs while it is off."""


def _pipeline_target(target_value: Path | None, home_root: Path, *, as_json: bool) -> Path:
    return _resolved_target(target_value, home_root, as_json=as_json).expanduser().resolve(strict=True)


def _pipeline_job(job: str) -> str:
    """The job identity for ``job``: a posting URL (normalized) or ``text:sha256:<hex>`` as given."""

    from .find_jobs.contracts import normalize_url

    return job if job.startswith("text:sha256:") else normalize_url(job)


def _pipeline_profile(profile_id: str | None, home_root: Path, target: Path) -> str:
    """``--profile``, else the selected profile's id."""

    if profile_id:
        return profile_id
    from ..workpad import resolve_workpad
    from . import profile_records
    from .pipeline.steps import StepError

    resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
    selected = profile_records.selected_profile(resolved, home_root=home_root, target=target)
    if selected is None:
        raise StepError("profile_unavailable", "no scout profile is selected for this project; pass --profile")
    return selected.profile_id


def _pipeline_errors() -> tuple[type[BaseException], ...]:
    from .find_jobs.contracts import FindJobsContractError
    from .pipeline.steps import StepError
    from .pipeline.store import PipelineStoreError

    return (ScoutTargetError, WorkpadError, PrivateRecordError, FindJobsContractError, StepError, PipelineStoreError, OSError, ValueError)


def _pipeline_drain_line(drain: dict[str, object]) -> str:
    steps = drain["steps"]
    assert isinstance(steps, list)
    if drain["state"] == "disabled":
        return f"The pipeline is off ({drain['reason']}). Nothing was run."
    if drain["state"] == "yielded":
        return f"The pipeline is waiting: {str(drain['reason']).replace('_', ' ')} is running. Run it again when that is done."
    if not steps:
        return "Nothing to run."
    waiting = sum(1 for step in steps if step.get("outcome") == "waiting")
    failed = sum(1 for step in steps if step.get("error_code"))
    line = f"Ran {len(steps) - waiting} step(s), {drain['model_calls']} model call(s)."
    if failed:
        line += f" {failed} did not finish; see `gigai scout pipeline status`."
    if waiting:
        line += f" {waiting} wait for tomorrow: today's model calls for the pipeline are used up."
    return line


def _pipeline_status_lines(status: dict[str, object]) -> list[str]:
    setting = status["setting"]
    calls = status["calls_today"]
    assert isinstance(setting, dict) and isinstance(calls, dict)
    lines = [
        f"Pipeline: {'on' if setting['enabled'] else 'off'} ({setting['source']}). "
        f"Model calls today: {calls['used']}/{calls['limit']}."
    ]
    rank = setting.get("rank")
    if isinstance(rank, dict) and "enabled" in rank:
        # 0.1.11.2: ranking has its own switch; it runs with the pipeline off.
        lines.append(f"Ranking: {'on' if rank['enabled'] else 'off'} ({rank['source']}); switched separately from the pipeline.")
    if status["yielding_to"]:
        lines.append(f"Waiting: {str(status['yielding_to']).replace('_', ' ')} is running.")
    for lane in status["lanes"]:  # type: ignore[union-attr]
        lines.append(f"Lane {lane['lane']} is backed off ({lane['error_code']}) until {lane['retry_at']}.")
    steps = status["steps"]
    assert isinstance(steps, list)
    if not steps:
        lines.append("No job is in the pipeline.")
    for step in steps:
        detail = step["state"]
        if step["waiting"]:
            detail += f", waiting ({step['waiting']}" + (f" until {step['retry_at']})" if step["retry_at"] else ")")
        elif step["error_code"]:
            detail += f" ({step['error_code']})"
        lines.append(f"{step['job']} [{step['profile_id']}] {step['name']}: {detail}")
    outputs = status.get("outputs")
    if isinstance(outputs, dict):
        base, tailored, ats, label = (outputs.get(key) for key in ("base_assessment", "tailored_assessment", "ats", "label"))
        if isinstance(base, dict) and isinstance(tailored, dict):
            lines.append(
                f"Requirements met: {base['requirements_met']['percent']} -> {tailored['requirements_met']['percent']} after tailoring "
                f"(verdict {base['verdict']} -> {tailored['verdict']})."
            )
        resume = outputs.get("tailored_resume")
        if isinstance(resume, dict) and resume.get("outcome") == "tailor_kept_user_edits":
            lines.append("Tailored resume: yours, kept as you left it (tailor_kept_user_edits).")
        if isinstance(ats, dict):
            lines.append(str(ats["line"]))
        if isinstance(label, dict):
            reasons = f" ({', '.join(label['reasons'])})" if label["reasons"] else ""
            lines.append(f"{label['name']}: {label['label']}{reasons}")
    return lines


@pipeline_group.command("run")
@click.option("--once", "once", is_flag=True, help="Run the waiting steps now, then exit.")
@click.option("--max-steps", "max_steps", type=click.IntRange(min=1), help="Claim at most this many steps.")
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def pipeline_run_command(once: bool, max_steps: int | None, home_value: Path | None, target_value: Path | None, as_json: bool) -> None:
    """Run the pipeline's waiting steps once, without the Scout server (which runs them by itself)."""

    from .pipeline.runner import run_once

    home_root = home_value or default_home_root()
    if not once:
        _fail(
            ValueError("pass --once: the Scout server runs the pipeline in the background; this command runs the waiting steps one time"),
            as_json=as_json, fallback="invalid_value",
        )
        return
    try:
        target = _pipeline_target(target_value, home_root, as_json=as_json)
        drain = run_once(home_root, target, max_steps=max_steps).to_json()
    except _pipeline_errors() as exc:
        _fail(exc, as_json=as_json, fallback="scout_pipeline_failed")
        return
    _emit(drain, as_json, _pipeline_drain_line(drain))


@pipeline_group.command("rank")
@click.option("--max-calls", "max_calls", type=click.IntRange(min=1), help="Make at most this many rank calls now.")
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def pipeline_rank_command(max_calls: int | None, home_value: Path | None, target_value: Path | None, as_json: bool) -> None:
    """Rank the stored postings your active profiles match that have no rank score yet, without the Scout server.

    The Scout server does this in the background. One model call ranks up to
    50 postings; a posting ranked once is not ranked again. All profiles share
    one daily allowance (100 calls, with a warning past 60).
    """

    from .pipeline.rank_lane import rank_tick

    home_root = home_value or default_home_root()
    try:
        target = _pipeline_target(target_value, home_root, as_json=as_json)
        result = rank_tick(home_root, target, max_calls=max_calls, force_enabled=True)
    except _pipeline_errors() as exc:
        _fail(exc, as_json=as_json, fallback="scout_pipeline_failed")
        return
    today = result["calls_today"]
    assert isinstance(today, dict)
    if result["state"] == "ran":
        line = f"Ranked {result['ranked']} posting(s) in {result['calls']} call(s)."
    elif result["state"] == "idle":
        line = "Nothing to rank."
    elif result["state"] == "waiting":
        line = f"Today's rank calls are used up ({today['used']}/{today['limit']}); ranking goes on tomorrow."
    elif result["state"] == "yielded":
        line = f"Ranking is waiting: {str(result['reason']).replace('_', ' ')} is running. Run it again when that is done."
    else:
        line = f"Nothing was ranked ({result['state']}: {result['reason']})."
    line += f" Rank calls today: {today['used']}/{today['limit']}."
    if today["warning"]:
        line += f" That is past the warning level of {today['warn_at']}."
    _emit(result, as_json, line)


@pipeline_group.command("status")
@click.option("--job", "job", help="Only this job (its posting URL), with what the pipeline stored for it.")
@click.option("--profile", "profile_id", help="Scout profile ID (default with --job: the selected profile).")
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def pipeline_status_command(
    job: str | None, profile_id: str | None, home_value: Path | None, target_value: Path | None, as_json: bool
) -> None:
    """Show what the pipeline is doing: each job's steps, why a step waits, and today's model calls against the cap."""

    from .pipeline.job_identity import job_identity_for
    from .pipeline.runner import pipeline_status

    home_root = home_value or default_home_root()
    try:
        target = _pipeline_target(target_value, home_root, as_json=as_json)
        identity = None if job is None else job_identity_for(_pipeline_job(job), home_root=home_root, target=target)
        profile = _pipeline_profile(profile_id, home_root, target) if identity is not None else profile_id
        status = pipeline_status(home_root, target, profile_id=profile, job=identity)
    except _pipeline_errors() as exc:
        _fail(exc, as_json=as_json, fallback="scout_pipeline_failed")
        return
    _emit(status, as_json, "\n".join(_pipeline_status_lines(status)))


@pipeline_group.command("process")
@click.argument("job")
@click.option("--profile", "profile_id", help="The role that asks (default: the selected one). Recorded only: a job has one set of steps, whichever role asks.")
@click.option("--force", "force", is_flag=True, help="Queue it again even when nothing its steps read has changed.")
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def pipeline_process_command(
    job: str, profile_id: str | None, force: bool, home_value: Path | None, target_value: Path | None, as_json: bool
) -> None:
    """Put one assessed job through the pipeline now. Refused (pipeline_off) while the pipeline is off, the 0.1.11 default."""

    from .pipeline.job_identity import job_identity_for
    from .pipeline.runner import pipeline_status, run_once
    from .pipeline.triggers import process_now

    home_root = home_value or default_home_root()
    try:
        target = _pipeline_target(target_value, home_root, as_json=as_json)
        identity = job_identity_for(_pipeline_job(job), home_root=home_root, target=target)
        profile = _pipeline_profile(profile_id, home_root, target)
        queued = process_now(home_root, target, profile, identity, force=force)
        # 0.1.11.9 PJ6: the job's ONE set of steps, under the role that asked for them first; --profile selects nothing.
        profile = str(queued["profile_id"])
        drain = run_once(home_root, target, only=(profile, identity), force_enabled=True).to_json()
        status = pipeline_status(home_root, target, profile_id=profile, job=identity)
    except _pipeline_errors() as exc:
        _fail(exc, as_json=as_json, fallback="scout_pipeline_failed")
        return
    if as_json:
        _emit({"enqueue": queued, "drain": drain, "status": status}, True, "")
        return
    if drain["steps"] or drain["state"] in ("disabled", "yielded"):
        click.echo(_pipeline_drain_line(drain))
    elif queued["result"] == "noop_failed":
        click.echo("The tailoring failed with these inputs. Run `gigai scout pipeline retry`, or pass --force.")
    elif queued["result"] == "noop_unchanged":
        click.echo("Nothing has changed since this job was last processed.")
    else:
        click.echo("Queued. Its steps wait; see below.")
    click.echo("\n".join(_pipeline_status_lines(status)[1:]))


@pipeline_group.command("cancel")
@click.argument("job")
@click.option("--profile", "profile_id", help="Scout profile ID (default: the selected profile).")
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def pipeline_cancel_command(job: str, profile_id: str | None, home_value: Path | None, target_value: Path | None, as_json: bool) -> None:
    """Cancel a job's waiting steps; a step that is running finishes its call and its output is kept."""

    _pipeline_change(job, profile_id, home_value, target_value, as_json, "cancel", None)


@pipeline_group.command("retry")
@click.argument("job")
@click.option("--profile", "profile_id", help="Scout profile ID (default: the selected profile).")
@click.option("--step", "step", type=click.Choice(["tailor", "reassess", "ats", "label"]), help="Only this step.")
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def pipeline_retry_command(
    job: str, profile_id: str | None, step: str | None, home_value: Path | None, target_value: Path | None, as_json: bool
) -> None:
    """Open a job's failed or cancelled steps again; the server, or `pipeline run --once`, then runs them."""

    _pipeline_change(job, profile_id, home_value, target_value, as_json, "retry", step)


def _pipeline_change(
    job: str, profile_id: str | None, home_value: Path | None, target_value: Path | None, as_json: bool, action: str, step: str | None
) -> None:
    from .pipeline.job_identity import job_identity_for
    from .pipeline.store import PipelineStore, pipeline_path

    home_root = home_value or default_home_root()
    try:
        target = _pipeline_target(target_value, home_root, as_json=as_json)
        identity = job_identity_for(_pipeline_job(job), home_root=home_root, target=target)
        profile = _pipeline_profile(profile_id, home_root, target)
        path = pipeline_path(home_root, target)
        changed = 0
        if path.is_file():
            store = PipelineStore(path)
            try:
                # 0.1.11.9 PJ6: the job's ONE set of steps, under whichever role they are kept; --profile selects nothing.
                profile = store.job_role(identity) or profile
                changed = store.cancel(profile, identity) if action == "cancel" else store.retry(profile, identity, step)
            finally:
                store.close()
    except _pipeline_errors() as exc:
        _fail(exc, as_json=as_json, fallback="scout_pipeline_failed")
        return
    done = "cancelled" if action == "cancel" else "opened again"
    _emit({"ok": True, "action": action, "profile_id": profile, "job": identity, "steps": changed}, as_json, f"{changed} step(s) {done}.")


# --- 0.1.10.7 PL5: `gigai scout pipeline approvals list|approve|deny` ------------------


@pipeline_group.group("approvals")
def pipeline_approvals_group() -> None:
    """Jobs over the per-trigger cap wait here until you approve them; nothing of theirs runs before."""


def _approval_line(item: dict[str, object]) -> str:
    tokens = f", ~{item['est_tokens']} tokens" if item["est_tokens"] is not None else ""
    decided = f", {item['decided_by']} at {item['decided_at']}" if item["decided_at"] else ""
    return (
        f"{item['id']}: {item['state']}{decided}; {item['waiting_jobs']} of {item['jobs']} job(s) waiting "
        f"({item['trigger']}), ~{item['est_calls']} model calls{tokens}"
    )


@pipeline_approvals_group.command("list")
@click.option("--all", "show_all", is_flag=True, help="Also the approvals already approved or denied.")
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def pipeline_approvals_list_command(show_all: bool, home_value: Path | None, target_value: Path | None, as_json: bool) -> None:
    """Show the approvals that wait: how many jobs each holds and what running them would cost."""

    from .pipeline import triggers

    home_root = home_value or default_home_root()
    try:
        target = _pipeline_target(target_value, home_root, as_json=as_json)
        listing = triggers.approvals(home_root, target, state=None if show_all else "pending")
    except _pipeline_errors() as exc:
        _fail(exc, as_json=as_json, fallback="scout_pipeline_failed")
        return
    if as_json:
        _emit(listing, True, "")
        return
    items = listing["approvals"]
    assert isinstance(items, list)
    if not items:
        click.echo("No approval is waiting." if not show_all else "No approval yet.")
        return
    for item in items:
        click.echo(_approval_line(item))
        for waiting in item["waiting"]:
            click.echo(f"  {waiting['job_identity']} [{waiting['profile_id']}]")
    if listing["pending"]:
        click.echo("Approve: `gigai scout pipeline approvals approve ID`. Deny: `gigai scout pipeline approvals deny ID`.")


def _pipeline_decide(approval_id: str, approve: bool, actor: str, home_value: Path | None, target_value: Path | None, as_json: bool) -> None:
    from .pipeline import triggers

    home_root = home_value or default_home_root()
    try:
        target = _pipeline_target(target_value, home_root, as_json=as_json)
        approval = triggers.decide(home_root, target, approval_id, approve=approve, decided_by=actor)
    except _pipeline_errors() as exc:
        _fail(exc, as_json=as_json, fallback="scout_pipeline_failed")
        return
    if as_json:
        _emit({"ok": True, "schema_version": "scout-pipeline-approval:1", "approval": approval}, True, "")
        return
    if approval["state"] == "approved":
        click.echo(
            f"Approved {approval['id']}: {approval['decided_jobs']} job(s) opened. "
            "The Scout server runs them, or run `gigai scout pipeline run --once`."
        )
    elif approval["state"] == "declined":
        click.echo(f"Denied {approval['id']}: {approval['decided_jobs']} job(s) cancelled. No model call was made.")
    else:
        click.echo(_approval_line(approval))


_APPROVAL_ACTOR = click.option(
    "--actor", "actor", type=click.Choice(["operator", "agent"]), default="operator", show_default=True,
    help="Who decides: recorded on the approval.",
)


@pipeline_approvals_group.command("approve")
@click.argument("approval_id")
@_APPROVAL_ACTOR
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def pipeline_approvals_approve_command(
    approval_id: str, actor: str, home_value: Path | None, target_value: Path | None, as_json: bool
) -> None:
    """Approve the waiting jobs of one approval: they open, and run within the daily cap of model calls."""

    _pipeline_decide(approval_id, True, actor, home_value, target_value, as_json)


@pipeline_approvals_group.command("deny")
@click.argument("approval_id")
@_APPROVAL_ACTOR
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def pipeline_approvals_deny_command(
    approval_id: str, actor: str, home_value: Path | None, target_value: Path | None, as_json: bool
) -> None:
    """Deny one approval: its waiting jobs are cancelled and no model call is made for them."""

    _pipeline_decide(approval_id, False, actor, home_value, target_value, as_json)


__all__ = ["scout_group", "write_starter_find_jobs_config", "STARTER_FIND_JOBS_CONFIG"]

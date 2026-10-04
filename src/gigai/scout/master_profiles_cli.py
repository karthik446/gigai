"""0.1.10.9 master P3: the profile commands of ``gigai scout resume master``.

* ``init`` without ``--from`` is the MIGRATION: the master is built from the
  resumes the profiles hold, and each profile's first selection is its own
  old resume (``master_profiles.migrate``). A conflict is asked as a
  structured question (``status: needs_answers``); nothing is written until
  every question has an ``--answer``.
* ``selection status``: every profile against the master as it is now, with
  the offer for lines the master gained ("3 new master lines: refresh?").
* ``selection refresh``: select again from the whole master for a profile
  (its first selection when it has none) and make the result its resume.

``after_master_write`` is what every command that writes the master calls
last: views that show an edited or retired line are printed again, and the
offers are returned. All local: no model, no network.
"""

from __future__ import annotations

from pathlib import Path

import click

from ..setup import default_home_root
from .master_cli import _emit, _errors, _fail, _n, _options, _size, _target, selection_group

_REFRESH = "gigai scout resume master selection refresh"


class _InputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _master_json(stored) -> dict[str, object] | None:  # noqa: ANN001 - a StoredMaster or None
    if stored is None:
        return None
    return {**stored.revision.to_json(), "record_id": stored.record_id, "counts": stored.master.counts()}


def after_master_write(home_root: Path, target: Path) -> dict[str, object]:
    """Bring the profiles' views up to date after a write of the master, and list the offers. Never raises.

    ``{"synced": [...], "offers": [...]}``; ``{"error": code}`` when the
    profiles could not be read (the master write itself already happened)."""

    from .master_profiles import after_master_change

    try:
        synced, statuses = after_master_change(home_root=home_root, target=target)
    except (ValueError, RuntimeError, OSError) as exc:
        return {"synced": [], "offers": [], "error": getattr(exc, "code", "scout_master_profiles_failed")}
    return {
        "synced": [change.to_json() for change in synced],
        "offers": [
            {"profile_id": status.profile_id, "label": status.label, "new_lines": list(status.new_lines), "offer": status.offer}
            for status in statuses if status.offer
        ],
    }


def echo_after_master_write(result: dict[str, object]) -> None:
    for change in result["synced"]:  # type: ignore[union-attr]
        click.echo(
            f"Profile {change['label']}: its resume now shows the master's wording "
            f"({_n(len(change['changed']), 'line')} changed, {len(change['retired'])} retired)."
        )
    for offer in result["offers"]:  # type: ignore[union-attr]
        click.echo(f"Profile {offer['label']}: {offer['offer']} ({_REFRESH} --profile {offer['profile_id']})")


# --- the migration ------------------------------------------------------------------------------


def _echo_source_lines(plan) -> None:  # noqa: ANN001 - a master_migration.MigrationPlan
    """What became of every line of the resumes: kept, folded, or left out (by resume, line number and reason; never the text)."""

    lines = plan.source_lines.to_json()
    left = ", ".join(f"{count} {reason.replace('_', ' ')}" for reason, count in lines["left_out_by_reason"].items() if count)
    click.echo(
        f"Of {_n(lines['in'], 'line')} of resume text (headings, role lines and wrapped lines counted): {lines['kept']} kept, "
        f"{lines['folded']} folded into a line the master holds, "
        f"{lines['left_out']} left out" + (f" ({left})." if left else ".")
    )
    for resume in lines["resumes"]:
        for row in resume["left_out"]:
            if row["reason"] != "contact":  # named below, by kind
                numbers = ", ".join(str(line) for line in row["lines"])
                click.echo(f"  Left out of the resume of {', '.join(resume['profiles'])}: line {numbers}: {row['why']}.")


def _answers(values: tuple[str, ...]) -> dict[str, str]:
    answers: dict[str, str] = {}
    for value in values:
        question_id, equals, choice = value.partition("=")
        if not equals or not question_id.strip() or not choice.strip():
            raise _InputError("migration_answer_invalid", "--answer is QUESTION_ID=a, QUESTION_ID=b or QUESTION_ID=both")
        answers[question_id.strip()] = choice.strip().lower()
    return answers


def migration_payload(result) -> dict[str, object]:  # noqa: ANN001 - a master_profiles.Migration
    """What a migration found and did, as ``master init --json`` prints it and ``/api/master/migration`` returns it."""

    plan = result.plan
    questions = [question.to_json() for question in plan.unanswered] if plan is not None else []
    profiles = [
        {
            "profile_id": profile.profile_id, "label": profile.label,
            "shown": len(selection.item_ids) if selection is not None else None,
            "skills": len(selection.skills) if selection is not None else None,
            # The migration never writes a profile's resume: the pin is the one it had.
            "resume_ref": profile.resume_ref.to_json(),
        }
        for profile, selection in result.profiles
    ]
    written = result.status in ("created", "revised", "unchanged") and plan is not None
    return {
        "ok": True,
        "mode": "migration",
        "status": result.status,
        "written": written,
        "master": _master_json(result.stored),
        "migration": plan.to_json() if plan is not None else None,
        "questions": questions,
        "profiles": profiles,
        # What the privacy strip left out of a resume (the profile, the kind, the line number; never a value), or null.
        "contact_removed": [{"profile": label, "kind": kind, "line": line} for label, kind, line in result.contact_removed] or None,
        # P8: where the master went in the resumes folder (null when nothing was written).
        "file": dict(result.written.file) if result.written is not None and result.written.file is not None else None,
    }


def run_migration(
    *, answers: tuple[str, ...], dry_run: bool, revision: int | None, actor: str, target_value: Path | None, home_value: Path | None, as_json: bool,
) -> None:
    """``master init`` without ``--from``: the master from the profiles' resumes."""

    from . import scout_cli
    from .master_profiles import migrate

    home_root = home_value or default_home_root()
    # As `master init --from` and `resume add`: works as a first command on a fresh home (which then has no profile to migrate).
    scout_cli._ensure_gigai_settings(home_root, as_json=as_json)
    try:
        target = _target(target_value, home_root, as_json=as_json)
        scout_cli.install_scout(home_root=home_root, requested_target=target)
        result = migrate(home_root=home_root, target=target, answers=_answers(answers), actor=actor, revision=revision, dry_run=dry_run)
    except _errors() as exc:
        _fail(exc, as_json=as_json)
        return
    plan = result.plan
    payload = migration_payload(result)
    questions = payload["questions"]
    if as_json:
        _emit(payload)
        return
    if plan is None:
        click.echo("Nothing to migrate: every profile already has its selection of the master.")
        return
    merged = (
        f"{_n(len(plan.selections), 'resume')}, {_n(plan.lines_in, 'line')} in: {_n(plan.exact_duplicates, 'exact duplicate')} and "
        f"{_n(len(plan.near_duplicates), 'near-duplicate')} folded, {_n(len(plan.questions), 'conflict')}"
    )
    if result.status == "needs_answers":
        click.echo(f"Merged {merged}. Your resumes disagree on {_n(len(questions), 'line')}; nothing was written. Answer each, then run it again:")
        _echo_source_lines(plan)
        for question in plan.unanswered:
            click.echo(f"\n  {question.question_id}  {question.section.capitalize()}{' / ' + question.entry if question.entry else ''}")
            for key, text, labels in question.options:
                click.echo(f"    {key}) {text}" + (f"   [{', '.join(labels)}]" if labels else ""))
        again = " ".join(f"--answer {question.question_id}=a" for question in plan.unanswered)
        click.echo(f"\n  gigai scout resume master init {again}      (a, b, or both to keep the two lines)")
        return
    counts = plan.master.counts()
    if result.status == "dry_run":
        click.echo(f"Would merge {merged}. The master would hold {_size(counts)}. Nothing was written.")
    else:
        number = result.stored.revision.revision if result.stored is not None else 0
        verb = "is unchanged at" if result.status == "unchanged" else "stored as"
        click.echo(f"Merged {merged}. Master resume {verb} revision {number}: {_size(counts)}.")
    _echo_source_lines(plan)
    for near in plan.near_duplicates:
        click.echo(f"  Folded into {near.kept_id} (similarity {near.similarity}): {near.folded}")
    for profile, selection in result.profiles:
        if selection is not None:
            click.echo(
                f"Profile {profile.label}: its selection is its own resume ({_n(len(selection.item_ids), 'entry and line', 'entries and lines')}, "
                f"{_n(len(selection.skills), 'skill')}); the resume itself is unchanged, so nothing goes stale."
            )
    for label, kind, line in result.contact_removed:
        click.echo(f"Not imported from the resume of {label}: line {line} ({kind.replace('_', ' ')}).")
    if payload["file"] is not None:
        from .master_file import write_line

        file = payload["file"]
        click.echo(write_line(file) or f"It is also in your resumes folder: {file['path']}.")  # type: ignore[index,arg-type]
    if result.status != "dry_run":
        click.echo("Next: `gigai scout resume master show`, then `gigai scout resume master selection status`.")


# --- selection status / refresh -----------------------------------------------------------------


@selection_group.command("status")
@click.option("--profile", "profile_id", help="Only this stored Scout profile (default: every profile).")
@_options
def selection_status_command(profile_id: str | None, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """Show each profile's selection against the master as it is now.

    Lines the master gained since a selection was made are offered ("3 new
    master lines: refresh?"), never added by themselves: a selection is
    sticky. Reads only; nothing is written.
    """

    from ..workpad import committed_read_cache
    from .master_profiles import selection_statuses
    from .master_store import load_master

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        # Outside the read cache: resolving the profiles migrates the default one on a home that has none yet.
        statuses = selection_statuses(home_root=home_root, target=target, profile_id=profile_id)
        with committed_read_cache():
            stored = load_master(home_root=home_root, target=target)
    except _errors() as exc:
        _fail(exc, as_json=as_json)
        return
    if as_json:
        _emit({"ok": True, "master": _master_json(stored), "profiles": [status.to_json() for status in statuses]})
        return
    click.echo(f"Master resume, revision {statuses[0].current_revision if statuses else (stored.revision.revision if stored else 0)}.")
    if not statuses:
        click.echo("No profile yet.")
    for status in statuses:
        name = f"  {status.label} ({status.profile_id})"
        selection = status.selection
        if selection is None:
            click.echo(f"{name}: no selection yet; its resume is its own. Make one: {_REFRESH} --profile {status.profile_id}")
            continue
        made = f"revision {status.made_from_revision}" if status.made_from_revision is not None else "an earlier master"
        click.echo(f"{name}: {_n(len(selection.item_ids), 'entry and line', 'entries and lines')}, {_n(len(selection.skills), 'skill')}, made from {made} ({selection.source}).")
        if not status.attached:
            click.echo(f"    Its resume was replaced after this selection was made, so it no longer shows it. Select from the master again: {_REFRESH} --profile {status.profile_id}")
            continue
        if status.stale:
            click.echo(
                f"    The master changed under it ({_n(len(status.changed), 'shown line')} edited, {len(status.retired) + len(status.skills_retired)} retired): "
                f"{_REFRESH} --sync --profile {status.profile_id}"
            )
        if status.offer:
            click.echo(f"    {status.offer} ({_REFRESH} --profile {status.profile_id})")


@selection_group.command("refresh")
@click.option("--profile", "profile_id", help="A stored Scout profile ID (default: the selected profile).")
@click.option("--all", "every", is_flag=True, help="Every active profile.")
@click.option("--sync", "sync_only", is_flag=True, help="Only print the view again from the lines it already shows (edited wording in, retired lines out); select nothing new.")
@click.option("--dry-run", "dry_run", is_flag=True, help="Show what a refresh would pick; write nothing.")
@_options
def selection_refresh_command(
    profile_id: str | None, every: bool, sync_only: bool, dry_run: bool, target_value: Path | None, home_value: Path | None, as_json: bool,
) -> None:
    """Select again from the whole master for a profile and make the result its resume.

    The pick is made by code (no model) against the postings the profile's
    titles match in the local index, with the lines it shows now as the
    prior, and fitted to 2 pages. A profile with no selection gets its first
    one. The profile's resume then is this selection, so what was assessed
    or tailored on the earlier resume is re-opened as for any new resume.
    """

    from ..workpad import resolve_workpad
    from . import profile_records
    from .master_profiles import refresh_selection, sync_views
    from .master_store import load_master

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        if every and profile_id is not None:
            raise _InputError("profile_input_invalid", "pass --profile ID or --all, not both")
        if sync_only and dry_run:
            raise _InputError("selection_input_invalid", "--sync writes; it has no --dry-run (see `selection status`)")
        resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
        if every:
            profile_records.selected_profile(resolved, home_root=home_root, target=target)
            wanted = [item.profile_id for item in profile_records.list_profiles(resolved) if item.state == "active"]
        elif profile_id is not None:
            wanted = [profile_id]
        else:
            selected = profile_records.selected_profile(resolved, home_root=home_root, target=target)
            if selected is None:
                raise _InputError("profile_not_found", "this Scout home has no profile yet")
            wanted = [selected.profile_id]
        if sync_only:
            changes = [change for item in wanted for change in sync_views(home_root=home_root, target=target, profile_id=item)]
        else:
            changes = [refresh_selection(home_root=home_root, target=target, profile_id=item, dry_run=dry_run) for item in wanted]
        stored = load_master(home_root=home_root, target=target)
    except _errors() as exc:
        _fail(exc, as_json=as_json)
        return
    if as_json:
        _emit({"ok": True, "dry_run": dry_run, "master": _master_json(stored), "profiles": [change.to_json() for change in changes]})
        return
    if not changes:
        click.echo("Every view already says what the master says: nothing to do.")
    for change in changes:
        size = f"{_n(change.shown, 'entry and line', 'entries and lines')} and {_n(change.skills, 'skill')}"
        pages = f" on {_n(change.pages, 'page')}" if change.pages is not None else ""
        if change.action == "synced":
            click.echo(f"Profile {change.label}: printed again from the master ({_n(len(change.changed), 'line')} edited, {len(change.retired)} retired): {size}{pages}.")
            continue
        basis = f"{_n(change.postings, 'matching posting')} in the local index" if change.source != "titles" and change.postings else "its titles (no matching posting in the local index)"
        verb = ("would be " if dry_run else "") + ("its first selection" if change.action == "first" else "refreshed")
        moved = f"; +{len(change.added)} -{len(change.removed)} against what it showed" if change.action == "refreshed" else ""
        fit = "" if change.fits else f" (DOES NOT FIT {change.pages} pages after every cut the rules allow)"
        click.echo(f"Profile {change.label}: {verb} from {basis}: {size}{pages}{fit}{moved}.")
        if change.written:
            click.echo("    The profile's resume is now this selection.")


__all__ = ["after_master_write", "echo_after_master_write", "migration_payload", "run_migration", "selection_refresh_command", "selection_status_command"]

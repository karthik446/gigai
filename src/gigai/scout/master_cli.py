"""0.1.10.9 master P1: ``gigai scout resume master show | init --from FILE | history``.

The master resume is the one document that holds every role, bullet,
project and skill, with an id on every line and no contact data
(``master_resume`` is the format, ``master_store`` the journal record and
its revisions). These commands are local: no model, no network. Errors
name a line number and the rule, never a line's text.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..private_records import PrivateRecordError
from ..setup import default_home_root
from ..workpad import WorkpadError, committed_read_cache
from .target_resolution import ScoutTargetError
from .template import ScoutInstallError

_NO_MASTER = "there is no master resume yet; make one with: gigai scout resume master init --from FILE"


def _options(function):
    for option in reversed((
        click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False)),
        click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False)),
        click.option("--json", "as_json", is_flag=True),
    )):
        function = option(function)
    return function


def _fail(exc: Exception, *, as_json: bool, fallback: str = "scout_master_failed") -> None:
    """Report a refusal; a stale or blocked write also carries the revision the master is at now."""

    code = getattr(exc, "code", fallback)
    current = getattr(exc, "current", None)
    if as_json:
        error: dict[str, object] = {"code": code, "message": str(exc)}
        if current is not None:
            error["current"] = current.to_json()
        click.echo(json.dumps({"status": "error", "error": error}, sort_keys=True, separators=(",", ":")))
        raise click.exceptions.Exit(1)
    raise click.ClickException(str(exc))


def _errors() -> tuple[type[Exception], ...]:
    from .master_resume import MasterResumeError
    from .master_store import MasterStoreError

    return (MasterResumeError, MasterStoreError, ScoutTargetError, ScoutInstallError, PrivateRecordError, WorkpadError, OSError, ValueError)


def _target(target_value: Path | None, home_root: Path, *, as_json: bool) -> Path:
    # Imported here: scout_cli imports this module to register the group. Its resolver is the
    # one every `gigai scout ...` command shares (--target, else <home>/scout).
    from . import scout_cli

    return scout_cli._resolved_target(target_value, home_root, as_json=as_json).expanduser().resolve(strict=True)


def _n(count: object, noun: str, plural: str | None = None) -> str:
    return f"{count} {noun if count == 1 else plural or noun + 's'}"


def _size(counts: dict[str, object]) -> str:
    return f"{_n(counts['items'], 'line')}, {_n(counts['entries'], 'entry', 'entries')}, {_n(counts['skills'], 'skill')}"


def _emit(payload: dict[str, object]) -> None:
    click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))


@click.group("master")
def master_group() -> None:
    """Your master resume: every role, bullet, project and skill, an id on every line, no contact data.

    One per Scout home. Kept as one record with a revision per change; nothing is ever rewritten.
    """


@master_group.command("show")
@click.option("--section", "section", help="Only this section: summary, experience, skills, education, projects or other.")
@click.option("--entry", "entry_id", help="Only this role, project or school (its id) and its lines.")
@_options
def master_show_command(section: str | None, entry_id: str | None, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """Show the master: every entry and line with its id, tags and evidence strength."""

    from .master_resume import MASTER_FORMAT, SECTION_HEADINGS
    from .master_store import MasterStoreError, load_master

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        wanted = section.strip().lower() if section is not None else None
        if wanted is not None and wanted not in SECTION_HEADINGS:
            raise MasterStoreError("master_section_unknown", "--section is one of " + ", ".join(SECTION_HEADINGS))
        with committed_read_cache():
            stored = load_master(home_root=home_root, target=target)
        if stored is None:
            raise MasterStoreError("master_not_found", _NO_MASTER)
        master = stored.master
        if entry_id is not None and entry_id not in master.entries:
            raise MasterStoreError("master_entry_not_found", f"the master has no entry {entry_id!r}")
    except _errors() as exc:
        _fail(exc, as_json=as_json)
        return

    def shown(item_section: str, item_entry: str | None) -> bool:
        return (wanted is None or item_section == wanted) and (entry_id is None or item_entry == entry_id)

    entries = [entry for entry in master.entries.values() if shown(entry.section, entry.id)]
    items = [item for item in master.items.values() if shown(item.section, item.entry_id)]
    if as_json:
        _emit({
            "ok": True,
            "master": {
                **stored.revision.to_json(), "record_id": stored.record_id, "format": MASTER_FORMAT,
                "sections": list(master.sections), "counts": master.counts(),
                "entries": [entry.to_json() for entry in entries], "items": [item.to_json() for item in items],
            },
        })
        return
    counts = master.counts()
    click.echo(
        f"Master resume, revision {stored.revision.revision} (written by {stored.revision.written_by}, {stored.revision.updated_at}): "
        f"{_size(counts)}."
    )

    def line(item) -> str:  # noqa: ANN001 - a MasterItem
        tags = "".join(f" #{tag}" for tag in item.tags)
        return f"{item.id}  [{item.strength}]  {item.text}{tags}"

    for name in master.sections:
        if not any(entry.section == name for entry in entries) and not any(item.section == name for item in items):
            continue
        click.echo(f"\n## {name.capitalize()}")
        for entry in (entry for entry in entries if entry.section == name):
            click.echo(f"  {entry.id}  {entry.heading}" + "".join(f" / {subline}" for subline in entry.sublines))
            for item in (item for item in items if item.entry_id == entry.id):
                click.echo(f"    {line(item)}")
        for item in (item for item in items if item.section == name and item.entry_id is None):
            click.echo(f"  {line(item)}")


@master_group.command("init")
@click.option("--from", "source", type=click.Path(path_type=Path, dir_okay=False), help="The resume markdown to store as the master (.md, .markdown, .txt).")
@click.option(
    "--revision", "revision", type=click.IntRange(min=0),
    help="The revision you read (show --json): needed to replace an existing master; refused with revision_conflict when it changed since.",
)
@click.option(
    "--as", "--actor", "actor", type=click.Choice(["operator", "agent"]), default="operator", show_default=True,
    help="Who is writing: recorded on the revision. An agent passes --as agent.",
)
@_options
def master_init_command(
    source: Path | None, revision: int | None, actor: str, target_value: Path | None, home_value: Path | None, as_json: bool,
) -> None:
    """Store FILE as the master resume: contact lines removed, an id on every line.

    FILE is resume markdown in GigAI's format (## Summary, ## Experience with
    ### entries and - bullets, ## Skills, ## Education, ## Projects, ## Other).
    A line without an id gets one. With a master already stored, pass
    --revision N (the revision you read): the file becomes revision N+1.
    """

    from . import scout_cli
    from .master_store import MasterStoreError, import_master

    home_root = home_value or default_home_root()
    # As `resume add`: works as the very first command on a fresh home.
    scout_cli._ensure_gigai_settings(home_root, as_json=as_json)
    try:
        target = _target(target_value, home_root, as_json=as_json)
        if source is None:
            raise MasterStoreError("master_source_missing", "pass --from FILE: the resume markdown to store as the master")
        installed = scout_cli.install_scout(home_root=home_root, requested_target=target)
        scout_cli.write_starter_find_jobs_config(target)
        result = import_master(home_root=home_root, target=target, source=source, actor=actor, revision=revision)
    except _errors() as exc:
        _fail(exc, as_json=as_json)
        return
    master = result.stored.master
    counts = master.counts()
    removed = result.contact_removed.to_json()
    payload = {
        "ok": True,
        "status": result.status,
        "scout_installed": installed.bound or installed.approved or installed.activated,
        "master": {**result.stored.revision.to_json(), "record_id": result.stored.record_id, "counts": counts},
        "ids_assigned": result.ids_assigned,
        "ids_restored": result.ids_restored,
        "changes": result.change.to_json(),
        # What the privacy strip took out (kinds and line numbers, never a value), or null.
        "contact_removed": removed,
    }
    if as_json:
        _emit(payload)
        return
    number = result.stored.revision.revision
    if result.status == "unchanged":
        click.echo(f"The master resume is unchanged (revision {number}): the file holds what is stored.")
    else:
        click.echo(
            f"Master resume stored as revision {number}: {_size(counts)}; "
            f"{_n(result.ids_assigned, 'new id')}, {result.change.added} added, {result.change.removed} removed, {result.change.changed} changed."
        )
    if removed is not None:
        where = ", ".join(f"line {line}: {kind.replace('_', ' ')}" for kind, line in result.contact_removed.lines)
        click.echo(f"{removed['message']} Not imported: {where}.")
    click.echo("Next: `gigai scout resume master show`.")


@master_group.command("history")
@_options
def master_history_command(target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """List the master's revisions, newest first: who wrote each, when, and what it changed."""

    from .master_store import MasterStoreError, master_history

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        with committed_read_cache():
            history = master_history(home_root=home_root, target=target)
        if not history:
            raise MasterStoreError("master_not_found", _NO_MASTER)
    except _errors() as exc:
        _fail(exc, as_json=as_json)
        return
    newest_first = list(reversed(history))
    if as_json:
        _emit({"ok": True, "revision": history[-1].revision.revision, "revisions": [entry.to_json() for entry in newest_first]})
        return
    click.echo(f"Master resume: {len(history)} revision{'' if len(history) == 1 else 's'}.")
    for entry in newest_first:
        click.echo(
            f"  {entry.revision.revision}  {entry.revision.updated_at}  {entry.revision.written_by}  {_n(entry.items, 'line')}, {_n(entry.entries, 'entry', 'entries')} "
            f"(+{entry.change.added} -{entry.change.removed} ~{entry.change.changed})  {entry.revision.revision_id}"
        )


__all__ = ["master_group", "master_history_command", "master_init_command", "master_show_command"]

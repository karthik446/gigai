"""0.1.10.9 master P8: ``gigai scout resume master sync``.

The master resume is also a file in the resumes folder (``master.md``),
written again after every change of the master. The user may edit that file;
GigAI never reads it by itself. This command is the explicit import
(``master_file.sync``): the file becomes the master's next revision, ids are
kept, contact data is refused (a link in a heading goes and the heading
keeps its words, which is said), and a master that changed in Scout since the
file was written is not overwritten without ``--revision``. Local: no model,
no network.
"""

from __future__ import annotations

from pathlib import Path

import click

from ..setup import default_home_root
from .master_cli import _emit, _errors, _fail, _n, _options, _target, echo_contact_removed, master_group

_RESTORE = "gigai scout resume master add --restore"
#: How many changed lines the text output lists per kind before it says how many more there are (--json lists all).
_SHOWN = 20


def _echo_lines(verb: str, lines: tuple, *, restore: bool = False) -> None:  # noqa: ANN001 - master_file.SyncLine
    for line in lines[:_SHOWN]:
        what = "the entry " if line.what == "entry" else ""
        back = f"  (put it back: `{_RESTORE} {line.id}`)" if restore else ""
        click.echo(f"  {verb} {what}{line.id}: {line.text}{back}")
    if len(lines) > _SHOWN:
        click.echo(f"  ... and {len(lines) - _SHOWN} more ({verb.lower()}); --json lists them all.")


@master_group.command("sync")
@click.option(
    "--revision", "revision", type=click.IntRange(min=1),
    help="Import the file even though the master changed since the file was written: the revision you read (show --json). What the file does not hold is retired.",
)
@click.option(
    "--as", "--actor", "actor", type=click.Choice(["operator", "agent"]), default="operator", show_default=True,
    help="Who is importing: recorded on the revision. An agent passes --as agent.",
)
@_options
def master_sync_command(revision: int | None, actor: str, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """Import master.md from your resumes folder into the master resume.

    GigAI writes your master to the resumes folder as master.md after every
    change. Edit that file in your own editor, then run this: the file
    becomes the next revision of the master. A new line gets an id; a line
    keeps its id (also when you deleted the id comment and left the text); a
    line you removed is retired and can be restored. GigAI never imports the
    file by itself.

    Refused, with nothing imported: a file that does not read as a master
    (the line is named); a name, email, phone number, link or address in it
    (by line number: GigAI stores no contact details); and a master that
    changed in Scout or through your agent since the file was written
    (revision_conflict), unless you pass --revision N to import the file as
    it is. When master.md is missing, this writes it.

    A link in a heading is not refused: the link goes, the heading keeps its
    words, and this says so. A heading that is only a link is refused (give
    the project a name).
    """

    from .master_file import SYNC_IMPORTED, SYNC_WRITTEN, sync, write_line
    from .master_profiles_cli import echo_after_master_write
    from .master_store import MasterStoreError

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        try:
            result = sync(home_root=home_root, target=target, actor=actor, revision=revision)
        except MasterStoreError as exc:
            if exc.code != "master_contact_data":
                raise
            # The code every other write of the master refuses contact data with.
            raise MasterStoreError("personal_info_refused", str(exc)) from None
    except _errors() as exc:
        _fail(exc, as_json=as_json)
        return
    stored = result.stored
    payload = {
        "ok": True, **result.to_json(),
        "master": {**stored.revision.to_json(), "record_id": stored.record_id, "counts": stored.master.counts()},
    }
    if as_json:
        _emit(payload)
        return
    number, file = stored.revision.revision, result.file
    if result.status == SYNC_WRITTEN:
        click.echo(write_line(file) or f"Wrote revision {number} of the master to {file['path']}. Nothing was imported.")
        return
    if result.status != SYNC_IMPORTED:
        again = " The file was written again in GigAI's form (every line with its id)." if file.get("written") else ""
        click.echo(f"Nothing to import: {file['name']} says what the master holds (revision {number}).{again}")
        return
    change = result.change
    click.echo(
        f"Imported {file['name']} as revision {number} of the master: {change.added} added, {change.changed} changed, {change.removed} retired; "
        f"{_n(result.ids_kept, 'id')} kept, {_n(result.ids_assigned, 'new id')}, {result.ids_restored} restored."
    )
    _echo_lines("Added", result.added)
    _echo_lines("Changed", result.changed)
    _echo_lines("Retired", result.retired, restore=True)
    echo_contact_removed(result.contact_removed)
    if file.get("error") or file.get("not_imported"):
        click.echo(write_line(file))
    elif file.get("written"):
        click.echo(f"{file['name']} now holds revision {number}, every line with its id.")
    echo_after_master_write(dict(result.profiles or {"synced": [], "offers": []}))


__all__ = ["master_sync_command"]

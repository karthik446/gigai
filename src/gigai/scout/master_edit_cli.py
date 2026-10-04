"""0.1.10.9 master P6: ``gigai scout resume master add | edit | remove``.

The commands the user's agent (and the user) change the master with from a
chat, one line, entry or skill at a time (``master_edit``):

* ``add``: a line under an entry or in a section, a role / project / school,
  skills on a Skills line, or a retired line back (``--restore``). A line the
  master already has in other words is ASKED about (``status:
  near_duplicate``; nothing is written) unless ``--force``.
* ``edit ID``: the text, tags or evidence of a line, the heading of an entry.
  The id stays.
* ``remove ID``: the line is retired. No resume selects it any more; the
  revision before still holds it (``master show --retired``).

``--from-story ID`` / ``--from-answer ID`` promote a story or an answer to a
line: the line carries the ``backed`` link. ``--as agent`` records who wrote,
``--source`` where the evidence came from. Every write names the revision it
read, runs the contact-data check on the new text and ends with
``after_master_write`` (a profile that shows the line follows; new lines are
only offered). All local: no model, no network.
"""

from __future__ import annotations

from pathlib import Path

import click

from ..setup import default_home_root
from ..workpad import committed_read_cache
from .master_cli import _NO_MASTER, _emit, _errors, _fail, _n, _options, _target, master_group

_SHOW = "gigai scout resume master show"

_ACTOR = click.option(
    "--as", "--actor", "actor", type=click.Choice(["operator", "agent"]), default="operator", show_default=True,
    help="Who is writing: recorded on the revision and on the line. An agent passes --as agent.",
)
_SOURCE = click.option(
    "--source", "source",
    help="Where the line's evidence came from, in free text (e.g. \"from the user's repo, at the user's request\").",
)
_STORY = click.option("--from-story", "story_id", help="The story this line comes from: the line is linked to it (backed).")
_ANSWER = click.option("--from-answer", "question_id", help="The answer (its QUESTION_ID) this line or skill comes from: linked to it (backed).")


class _InputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _missing(exc: Exception) -> Exception:
    """A home with no master answers the same sentence everywhere."""

    from .master_store import MasterStoreError

    return MasterStoreError("master_not_found", _NO_MASTER) if getattr(exc, "code", None) == "master_not_found" else exc


def _payload(result, home_root: Path, target: Path, profiles: dict[str, object]) -> dict[str, object]:  # noqa: ANN001 - a MasterEdit
    from .master_edit import entry_json, item_json, provenance

    master = result.stored.master
    known = provenance(home_root=home_root, target=target, master=master)
    return {
        "ok": True,
        "action": result.action,
        "status": result.status,
        "written": result.written,
        "master": {**result.stored.revision.to_json(), "record_id": result.stored.record_id, "counts": master.counts()},
        "changes": result.change.to_json(),
        # The lines and entries this command added, changed or put back, as `master show` returns them.
        "items": [
            {"kind": "entry", **entry_json(master.entries[item_id], known)} if item_id in master.entries else item_json(master.items[item_id], known)
            for item_id in result.ids
        ],
        "retired": [item.to_json() for item in result.retired],
        "skills": {
            "line": result.skills_line, "added": list(result.skills_added), "removed": list(result.skills_removed),
            "already_listed": list(result.already_listed),
        },
        "near_duplicates": [line.to_json() for line in result.near_duplicates],
        "warnings": list(result.warnings),
        "profiles": profiles,
    }


def _finish(result, home_root: Path, target: Path, *, as_json: bool) -> None:  # noqa: ANN001 - a MasterEdit
    """Print what happened, and what the write did to the profiles (``master_edit`` ends every write with them)."""

    from .master_profiles_cli import echo_after_master_write

    profiles = dict(result.profiles) if result.profiles is not None else {"synced": [], "offers": []}
    payload = _payload(result, home_root, target, profiles)
    if as_json:
        _emit(payload)
        return
    number = result.stored.revision.revision
    master = result.stored.master
    if result.status == "near_duplicate":
        click.echo("Not added: the master already has a line that says nearly this.")
        for line in result.near_duplicates:
            numbers = "" if line.same_numbers else ", different numbers"
            click.echo(f"  {line.id} (similarity {line.similarity}{numbers}): {line.text}")
        first = result.near_duplicates[0].id
        click.echo(f"Change that line (`gigai scout resume master edit {first} --text \"...\" --revision {number}`), or add this one as well with --force.")
        return
    if result.status == "unchanged":
        said = f" ({', '.join(result.already_listed)} already listed)" if result.already_listed else ""
        click.echo(f"The master already says this{said}: nothing was written (revision {number}).")
    for item_id in result.ids if result.written else ():
        verb = {"add": "Added", "edit": "Changed", "restore": "Put back"}[result.action]
        if item_id in master.entries:
            click.echo(f"{verb} the entry {item_id}: {master.entries[item_id].heading}")
        elif item_id == result.skills_line:
            what = f"Added {', '.join(result.skills_added)} to" if result.skills_added else verb
            click.echo(f"{what} the Skills line {item_id}: {master.items[item_id].text}")
        else:
            item = master.items[item_id]
            click.echo(f"{verb} {item_id}{' under ' + item.entry_id if item.entry_id else ''}: {item.text}")
    if result.written and result.skills_removed:
        click.echo(f"Removed from the Skills: {', '.join(result.skills_removed)}.")
    if result.written and result.already_listed:
        click.echo(f"Already listed, not added again: {', '.join(result.already_listed)}.")
    for retired in result.retired:
        click.echo(
            f"Retired {retired.id}: no resume selects it any more. Revision {retired.last_revision} of the master still holds it "
            f"(`{_SHOW} --retired`); put it back with `gigai scout resume master add --restore {retired.id}`."
        )
    for warning in result.warnings:
        click.echo(f"Check: {warning}.")
    if result.written:
        click.echo(f"The master is at revision {number} (written by {result.stored.revision.written_by}).")
    echo_after_master_write(profiles)


def _evidence(home_root: Path, target: Path, story_id: str | None, question_id: str | None):  # noqa: ANN202 - tuple[Evidence, ...]
    from .master_edit import evidence_for

    if story_id is None and question_id is None:
        return ()
    return evidence_for(home_root=home_root, target=target, story_id=story_id, question_id=question_id)


@master_group.command("add")
@click.option("--text", "text", help="The line, in the user's own facts: one line, at most 400 characters (a summary or a Skills line: 1,000).")
@click.option("--entry", "entry_id", help="With --text: the role, project or school (its id) the line is a bullet of.")
@click.option("--section", "section", help="With --text: summary, skills or other. With --heading: experience, projects or education.")
@click.option("--heading", "heading", help="A new role, project or school: its heading (the employer, the project, the school).")
@click.option("--role", "sublines", multiple=True, help="With --heading: a line under it, e.g. \"Staff Engineer | Jun 2022 - Present\" (repeatable).")
@click.option("--skill", "skills", multiple=True, help="A skill to list (repeatable); one the master already lists is not added again.")
@click.option("--to", "line_id", help="With --skill: the Skills line (its id) to add to.")
@click.option("--label", "label", help="With --skill: the Skills line with this label (\"Cloud\"); made when there is none.")
@click.option("--tag", "tags", multiple=True, help="With --text: a tag for what the words do not show (leadership); repeatable.")
@_STORY
@_ANSWER
@click.option("--restore", "restore_id", help="Put a retired line or entry back under its own id (see show --retired).")
@click.option("--force", is_flag=True, help="With --text: add the line although the master has one that says nearly the same.")
@click.option(
    "--revision", "revision", type=click.IntRange(min=1),
    help="The revision you read (show --json): refused with revision_conflict when the master changed since. Without it the line is added to the master as it is now.",
)
@_SOURCE
@_ACTOR
@_options
def master_add_command(
    text: str | None, entry_id: str | None, section: str | None, heading: str | None, sublines: tuple[str, ...], skills: tuple[str, ...],
    line_id: str | None, label: str | None, tags: tuple[str, ...], story_id: str | None, question_id: str | None, restore_id: str | None,
    force: bool, revision: int | None, source: str | None, actor: str, target_value: Path | None, home_value: Path | None, as_json: bool,
) -> None:
    """Add to the master: a line, a role / project / school, skills, or a retired line back.

    \b
    A line:    --text "..." --entry ENTRY_ID          (a bullet of that entry)
               --text "..." --section summary|skills|other
    An entry:  --heading "Acme" --role "Staff Engineer | Jun 2022 - Present" --section experience
    Skills:    --skill Helm [--skill ArgoCD] [--to LINE_ID | --label Cloud]
    Put back:  --restore ID

    The new line gets its id (the output shows it). Text that looks like
    contact data is refused. --from-story / --from-answer link the line to
    its evidence. A line the master already has in other words is not added:
    the output lists what it looks like (status near_duplicate); change that
    line with `master edit`, or pass --force.
    """

    from . import master_edit

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        section_name = section.strip().lower() if section is not None else None
        modes = [name for name, given in (("--text", text is not None), ("--heading", heading is not None), ("--skill", bool(skills)), ("--restore", restore_id is not None)) if given]
        if len(modes) != 1:
            raise _InputError(
                "master_add_invalid",
                "say one thing to add: --text (a line), --heading (a role, project or school), --skill, or --restore ID"
                + (f"; got {' and '.join(modes)}" if modes else ""),
            )
        common = {"home_root": home_root, "target": target, "actor": actor, "revision": revision}
        if restore_id is not None:
            if any((entry_id, section_name, sublines, line_id, label, tags, story_id, question_id, force, source)):
                raise _InputError("master_add_invalid", "--restore ID takes only --revision and --as: the line comes back as it was")
            result = master_edit.restore(item_id=restore_id, **common)
        elif skills:
            if entry_id or sublines or tags or force or section_name not in (None, "skills"):
                raise _InputError("master_add_invalid", "--skill takes --to LINE_ID or --label LABEL (and --from-answer, --from-story, --source)")
            result = master_edit.add_skills(
                names=skills, line_id=line_id, label=label, evidence=_evidence(home_root, target, story_id, question_id), source=source, **common,
            )
        elif heading is not None:
            if entry_id or line_id or label or tags or story_id or question_id or force:
                raise _InputError("master_add_invalid", "--heading takes --section experience, projects or education and --role; tags and evidence belong to its lines")
            if section_name is None:
                raise _InputError("master_add_invalid", "--heading needs --section experience, projects or education")
            result = master_edit.add_entry(section=section_name, heading=heading, sublines=sublines, source=source, **common)
        else:
            if sublines or line_id or label:
                raise _InputError("master_add_invalid", "--text takes --entry ENTRY_ID or --section summary, skills or other")
            result = master_edit.add_line(
                text=text or "", entry_id=entry_id, section=section_name, tags=tags, evidence=_evidence(home_root, target, story_id, question_id),
                source=source, force=force, **common,
            )
    except (*_errors(), _InputError) as exc:
        _fail(_missing(exc), as_json=as_json)
        return
    _finish(result, home_root, target, as_json=as_json)


@master_group.command("edit")
@click.argument("item_id")
@click.option("--text", "text", help="A line's new text: one line, at most 400 characters (a summary or a Skills line: 1,000). Its id stays.")
@click.option("--tag", "tags", multiple=True, help="A line's tags, replacing the ones it has (repeatable); one empty value removes them all.")
@click.option("--heading", "heading", help="An entry's new heading.")
@click.option("--role", "sublines", multiple=True, help="An entry's lines under its heading, replacing the ones it has (repeatable).")
@_STORY
@_ANSWER
@click.option(
    "--revision", "revision", type=click.IntRange(min=1),
    help="Required: the revision you read (show --json). Refused with revision_conflict when the master changed since.",
)
@_SOURCE
@_ACTOR
@_options
def master_edit_command(
    item_id: str, text: str | None, tags: tuple[str, ...], heading: str | None, sublines: tuple[str, ...], story_id: str | None,
    question_id: str | None, revision: int | None, source: str | None, actor: str, target_value: Path | None, home_value: Path | None, as_json: bool,
) -> None:
    """Change a line or an entry of the master by its id; the id never changes.

    Only what is given changes: --text and --tag for a line, --heading and
    --role for an entry. --from-story / --from-answer link the line to its
    evidence; --source alone says where the evidence came from. A profile
    whose resume shows the line gets the new wording.
    """

    from . import master_edit

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        result = master_edit.edit(
            home_root=home_root, target=target, item_id=item_id, revision=revision, text=text, tags=tags or None, heading=heading,
            sublines=sublines or None, evidence=_evidence(home_root, target, story_id, question_id), actor=actor, source=source,
        )
    except _errors() as exc:
        _fail(_missing(exc), as_json=as_json)
        return
    _finish(result, home_root, target, as_json=as_json)


@master_group.command("remove")
@click.argument("item_id", required=False)
@click.option("--skill", "skills", multiple=True, help="Remove this skill from the Skills line ITEM_ID, or from wherever it is listed (repeatable).")
@click.option(
    "--revision", "revision", type=click.IntRange(min=1),
    help="Required: the revision you read (show --json). Refused with revision_conflict when the master changed since.",
)
@_ACTOR
@_options
def master_remove_command(
    item_id: str | None, skills: tuple[str, ...], revision: int | None, actor: str, target_value: Path | None, home_value: Path | None, as_json: bool,
) -> None:
    """Retire a line, an entry with its lines, or a skill.

    Retired means no resume selects it any more. It is never lost: the
    revision before still holds it (`master show --retired` lists every
    retired line with its text), and `master add --restore ID` puts a line
    or an entry back under its own id. A profile whose resume shows the line
    gets its resume printed again without it.
    """

    from . import master_edit

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        result = master_edit.remove(home_root=home_root, target=target, revision=revision, item_id=item_id, skills=skills, actor=actor)
    except _errors() as exc:
        _fail(_missing(exc), as_json=as_json)
        return
    _finish(result, home_root, target, as_json=as_json)


def show_retired(home_root: Path, target: Path, *, as_json: bool) -> None:
    """``master show --retired``: every line and entry the master no longer holds, with the revision that still does."""

    from .master_edit import retired_lines
    from .master_store import MasterStoreError, load_master

    with committed_read_cache():
        stored = load_master(home_root=home_root, target=target)
        retired = retired_lines(home_root=home_root, target=target) if stored is not None else []
    if stored is None:
        raise MasterStoreError("master_not_found", _NO_MASTER)
    if as_json:
        _emit({
            "ok": True,
            "master": {**stored.revision.to_json(), "record_id": stored.record_id, "counts": stored.master.counts()},
            "retired": [item.to_json() for item in retired],
        })
        return
    click.echo(f"Master resume, revision {stored.revision.revision}: {_n(len(retired), 'retired line or entry', 'retired lines and entries')}.")
    for item in retired:
        where = f" of {item.entry_id}" if item.entry_id else ""
        click.echo(f"  {item.id}  [{item.kind}{where}; in revision {item.last_revision}, retired by {item.retired_by} in revision {item.retired_in}]  {item.text}")
    if retired:
        click.echo("Put one back under its own id: `gigai scout resume master add --restore ID`.")


__all__ = ["master_add_command", "master_edit_command", "master_remove_command", "show_retired"]

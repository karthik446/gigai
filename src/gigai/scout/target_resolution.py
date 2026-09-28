"""Resolve the Scout target folder for every ``gigai scout ...`` command.

Scout is a personal, single-operator tool: which folder the user happens to
be sitting in must not matter (uat-bug-002, uat-bug-017). Every Scout command
that takes ``--target`` resolves it through :func:`resolve_scout_target` in
this order:

1. ``--target``, if given: the only override.
2. Otherwise ``<home>/scout``, always. When it isn't bound yet it is created
   and bound as a non-Git target the same way ``gigai init --target <dir>``
   does.

The cwd is never consulted, and a Scout project registered somewhere else is
never picked up implicitly. Such an earlier project is left exactly as it is
(no move, no delete, no migration) and named once in a notice; which projects
the notice already named is kept in a small marker file, see
:func:`earlier_project_notice_marker`.

Core (``gigai init``/``default_init``/``target_binding``) never imports this
module or anything else under ``gigai.scout``; this module is free to import
core.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import textwrap

from ..default_init import DefaultInitError, default_inventory, initialize_defaults, normalize_username
from ..registry import ProjectRecord, RegistryError, WorkspaceOwnerRecord, open_project_registry
from ..target_binding import GitTargetError, TargetBindingError
from ..workpad import WorkpadError, resolve_bound_project

SCOUT_TEMPLATE_ID = "scout"
HOME_SCOUT_DIRNAME = "scout"
EARLIER_PROJECT_NOTICE_SCHEMA = "scout-earlier-project-notice:1"


class ScoutTargetError(RuntimeError):
    """A Scout-user-facing error raised while resolving a Scout target.

    Never mentions ``--target`` as "an explicit non-Git target" the way the
    generic ``target_binding`` error does -- Scout resolves without it in the
    overwhelming majority of cases, so that phrasing would be actively
    misleading here.
    """

    code = "scout_target_unresolved"


def home_scout_target(home_root: Path) -> Path:
    """The default Scout target: ``<home>/scout``.

    Deliberately not ``<home>/workpads`` (the workpad root, see
    ``default_workpad_root``) or any other reserved home subfolder -- Scout's
    default target is a *project target* (something ``gigai init`` binds),
    not machine state, and must never collide with one.
    """

    return home_root / HOME_SCOUT_DIRNAME


def _registry_records(home_root: Path) -> tuple[ProjectRecord, ...]:
    """All registered projects, or an empty tuple if no registry exists yet."""

    try:
        registry, _created = open_project_registry(home_root, create=False)
    except RegistryError:
        return ()
    try:
        return registry.records()
    except RegistryError:
        return ()


def _scout_installed(home_root: Path, project_id: str) -> bool:
    try:
        registry, _created = open_project_registry(home_root, create=False)
        with registry.transaction() as transaction:
            return transaction.find_template_instance(project_id, SCOUT_TEMPLATE_ID) is not None
    except RegistryError:
        return False


def _existing_scout_projects(home_root: Path) -> tuple[ProjectRecord, ...]:
    """Every registered project that has Scout installed, in registry order."""

    return tuple(
        record
        for record in _registry_records(home_root)
        if _scout_installed(home_root, record.project_id)
    )


def _home_scout_is_bound(home_root: Path) -> bool:
    """Whether ``<home>/scout`` exists and is already a registered project."""

    target = home_scout_target(home_root)
    if not target.is_dir():
        return False
    try:
        resolve_bound_project(home_root=home_root, requested_target=target)
    except WorkpadError:
        return False
    return True


def earlier_project_notice_marker(home_root: Path) -> Path:
    """Where "the notice was shown" is remembered: GigAI home state, never a journal.

    ``<home>/local/scout/earlier-project-notice.json`` lists the earlier
    project folders the notice already named. Deleting the file only makes
    the notice show once more.
    """

    return home_root / "local" / "scout" / "earlier-project-notice.json"


def _same_folder(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _earlier_scout_projects(home_root: Path) -> tuple[Path, ...]:
    """Folders of Scout projects registered outside ``<home>/scout`` that still exist."""

    default = home_scout_target(home_root)
    folders = (Path(record.target_locator) for record in _existing_scout_projects(home_root))
    return tuple(
        folder
        for folder in sorted(folders)
        if folder.is_dir() and not _same_folder(folder, default)
    )


def _already_noticed(home_root: Path) -> frozenset[str]:
    try:
        saved = json.loads(earlier_project_notice_marker(home_root).read_text(encoding="utf-8"))
        return frozenset(str(item) for item in saved["shown_for"])
    except (OSError, ValueError, KeyError, TypeError):
        return frozenset()


def _display_path(path: Path) -> str:
    """``path`` the way the operator types it: ``~/scout`` when under their home folder."""

    try:
        user_home = Path.home()
        for base in (user_home, user_home.resolve()):
            if path.is_relative_to(base) and path != base:
                return "~/" + path.relative_to(base).as_posix()
    except (OSError, RuntimeError):
        pass
    return os.fspath(path)


def _terminal_columns() -> int | None:
    """Width of the terminal the notice is shown in; ``None`` when it is not a terminal."""

    try:
        return os.get_terminal_size(sys.stderr.fileno()).columns
    except (AttributeError, OSError, ValueError):
        return None


def _fit_terminal(message: str, *, keep_together: str, columns: int | None) -> str:
    """``message`` broken into lines the terminal shows without wrapping them itself.

    The notice is longer than most terminals are wide. A terminal that wraps
    it on its own can end a row on the space of ``use --target``; the row's
    trailing space is dropped when the text is copied, which reads
    ``use--target`` (uat-bug-019). Breaking the lines here, never inside
    ``keep_together``, keeps that space away from the end of a row. Output
    that is not a terminal stays one line.
    """

    if columns is None or len(message) < columns:
        return message
    joined = keep_together.replace(" ", "\0")
    lines = textwrap.wrap(
        message.replace(keep_together, joined),
        width=max(columns - 1, len(keep_together)),
        break_long_words=False,
        break_on_hyphens=False,
    )
    return "\n".join(lines).replace("\0", " ")


def _notify_earlier_projects(home_root: Path, notify: Callable[[str], None]) -> None:
    """Name each earlier Scout project once; never fails the command it rides on."""

    try:
        earlier = _earlier_scout_projects(home_root)
        if not earlier:
            return
        noticed = _already_noticed(home_root)
        unseen = tuple(folder for folder in earlier if os.fspath(folder) not in noticed)
        if not unseen:
            return
        marker = earlier_project_notice_marker(home_root)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(
                {
                    "schema_version": EARLIER_PROJECT_NOTICE_SCHEMA,
                    "shown_for": sorted(noticed | {os.fspath(folder) for folder in unseen}),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        # The marker could not be written, so showing the notice now would
        # show it again on every command. Stay quiet instead.
        return
    lives_in = _display_path(home_scout_target(home_root))
    columns = _terminal_columns()
    for folder in unseen:
        shown = _display_path(folder)
        hint = f"use --target {shown}"
        notify(
            _fit_terminal(
                f"Scout now lives in {lives_in}; your earlier data in {shown} is untouched; "
                f"{hint} to open it.",
                keep_together=hint,
                columns=columns,
            )
        )


@dataclass(frozen=True)
class _UsernameChoice:
    username: str
    source: str
    project_id: str | None = None


def _owner_rows(home_root: Path) -> tuple[WorkspaceOwnerRecord, ...]:
    try:
        registry, _created = open_project_registry(home_root, create=False)
    except RegistryError:
        return ()
    rows: list[WorkspaceOwnerRecord] = []
    try:
        for record in registry.records():
            with registry.transaction() as transaction:
                owner = transaction.find_workspace_owner(record.project_id)
            if owner is not None:
                rows.append(owner)
    except RegistryError:
        return ()
    return tuple(rows)


def _choose_username(home_root: Path, requested_username: str | None) -> _UsernameChoice:
    """Pick a workspace-owner username for a brand-new ``<home>/scout``.

    Order (operator-approved): (0) an explicit ``--username`` (only
    ``gigai scout install`` exposes the flag; other Scout commands never
    create a target from nothing without one already having been chosen);
    (1) an existing ``workspace_owners`` username, if every existing owner
    shares one -- the normal single-operator case; if they differ, prefer the
    owner of an existing Scout project (the first by ``project_id`` if there
    are several), else the first by a deterministic ``project_id`` order;
    (2) GigAI's own config stores no separate display-name field (checked:
    ``gigai.config``), so that step is a no-op; (3) the OS login name,
    normalized the same way ``gigai init --username`` validates one.
    """

    if requested_username is not None:
        return _UsernameChoice(normalize_username(requested_username), "--username")

    owners = _owner_rows(home_root)
    if owners:
        usernames = {owner.username for owner in owners}
        if len(usernames) == 1:
            chosen = next(iter(usernames))
            return _UsernameChoice(chosen, "the saved workspace owner")
        scout_owners = tuple(
            owner
            for owner in owners
            if _scout_installed(home_root, owner.project_id)
        )
        if scout_owners:
            picked = sorted(scout_owners, key=lambda row: row.project_id)[0]
            return _UsernameChoice(picked.username, "the existing Scout project's owner", picked.project_id)
        picked = sorted(owners, key=lambda row: row.project_id)[0]
        return _UsernameChoice(picked.username, "an existing project's owner", picked.project_id)

    import getpass

    try:
        login_name = getpass.getuser()
    except (OSError, KeyError):
        login_name = None
    if login_name:
        try:
            return _UsernameChoice(normalize_username(login_name), "your OS login name")
        except DefaultInitError:
            pass

    raise ScoutTargetError(
        "scout_target_owner_unresolvable: Scout has no saved workspace owner to reuse and your "
        "OS login name isn't a usable display name; run `gigai scout install --username <name>` "
        "to set one, or `gigai init --target <dir> --username <name>` first"
    )


def _create_home_scout_target(home_root: Path, choice: "_UsernameChoice") -> Path:
    """Bind ``<home>/scout`` as a non-Git target the same way ``gigai init`` does."""

    target = home_scout_target(home_root)
    target.mkdir(parents=True, exist_ok=True)
    try:
        initialize_defaults(
            home_root=home_root,
            requested_target=target,
            username=choice.username,
            inventory=default_inventory(),
        )
    except (DefaultInitError, TargetBindingError, GitTargetError, OSError) as exc:
        raise ScoutTargetError(
            f"could not create and bind the default Scout target at {target}: {exc}"
        ) from exc
    return target


def resolve_scout_target(
    *,
    home_root: Path,
    requested_target: Path | None,
    requested_username: str | None = None,
    on_created: Callable[[Path, str], None] | None = None,
    notify: Callable[[str], None] | None = None,
) -> Path:
    """Resolve the folder a Scout command should act on.

    Returns an existing, resolvable directory path -- callers pass it on as
    an explicit ``requested_target`` to the same ``resolve_bound_project`` /
    ``install_scout`` / ``run_supervisor`` helpers they already use; this
    function performs no Gig-level work itself (no proposal, no approval).

    ``requested_username`` only matters when ``<home>/scout`` has to be
    created; it's ignored otherwise. When it is created,
    ``on_created(path, username)`` is called so the caller can tell the
    operator which username was used.

    ``notify`` receives the one-time notice about a Scout project registered
    outside ``<home>/scout``. Without it nothing is shown and nothing is
    marked as shown, so a caller that cannot print (``--json``) leaves the
    notice for the next command that can.
    """

    if requested_target is not None:
        return requested_target

    target = home_scout_target(home_root)
    if not _home_scout_is_bound(home_root):
        choice = _choose_username(home_root, requested_username)
        _create_home_scout_target(home_root, choice)
        if on_created is not None:
            on_created(target, choice.username)
    if notify is not None:
        _notify_earlier_projects(home_root, notify)
    return target


__all__ = [
    "EARLIER_PROJECT_NOTICE_SCHEMA",
    "HOME_SCOUT_DIRNAME",
    "SCOUT_TEMPLATE_ID",
    "ScoutTargetError",
    "earlier_project_notice_marker",
    "home_scout_target",
    "resolve_scout_target",
]

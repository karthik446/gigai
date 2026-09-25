"""Resolve the Scout target folder for every ``gigai scout ...`` command.

Scout is a personal, single-operator tool: which folder the user happens to
be sitting in must not matter (uat-bug-002). Every Scout command that takes
``--target`` resolves it through :func:`resolve_scout_target` in this order:

1. ``--target``, if given (unchanged).
2. The cwd (or an ancestor of it), if it's already a registered project --
   Scout installed there or not (unchanged, today's ``resolve_bound_project``
   cwd-walk behavior: this is what lets ``gigai scout install`` bootstrap a
   target `gigai init --target .` just bound, run from inside it, before
   Scout exists there yet).
3. The one existing Scout project in the registry, if exactly one exists (so
   an operator's single non-Git Scout project keeps working from anywhere,
   with no migration and no new project identity). Two or more raise a
   Scout-user error naming every candidate plus a ``--target`` hint.
4. Otherwise, create ``<home>/scout``, bind it as a non-Git target the same
   way ``gigai init --target <dir>`` does, and continue.

Core (``gigai init``/``default_init``/``target_binding``) never imports this
module or anything else under ``gigai.scout``; this module is free to import
core.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..default_init import DefaultInitError, default_inventory, initialize_defaults, normalize_username
from ..registry import ProjectRecord, RegistryError, WorkspaceOwnerRecord, open_project_registry
from ..target_binding import GitTargetError, TargetBindingError
from ..workpad import BoundProject, WorkpadError, resolve_bound_project

SCOUT_TEMPLATE_ID = "scout"
HOME_SCOUT_DIRNAME = "scout"


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


def _bound_project_at_cwd(home_root: Path, cwd: Path | None) -> BoundProject | None:
    """The cwd's (or an ancestor's) registered project, Scout installed or not.

    Reuses ``resolve_bound_project``'s existing cwd-walk (git discovery, or a
    registered non-Git ancestor) unchanged -- this is what lets ``gigai scout
    install`` bootstrap a target ``gigai init --target .`` just bound, run
    from inside it, before Scout is installed there yet (operator-confirmed:
    "unchanged, today's behavior" is the operative part of the ticket, not a
    new Scout-installed gate).
    """

    try:
        return resolve_bound_project(home_root=home_root, requested_target=None, cwd=cwd)
    except WorkpadError:
        return None


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
    owner of an existing Scout project (there is at most one, since two-or-
    more already stops resolution earlier with a listing error), else the
    first by a deterministic ``project_id`` order; (2) GigAI's own config
    stores no separate display-name field (checked: ``gigai.config``), so
    that step is a no-op; (3) the OS login name, normalized the same way
    ``gigai init --username`` validates one.
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
    cwd: Path | None = None,
    requested_username: str | None = None,
    on_created: Callable[[Path, str], None] | None = None,
) -> Path:
    """Resolve the folder a Scout command should act on.

    Returns an existing, resolvable directory path -- callers pass it on as
    an explicit ``requested_target`` to the same ``resolve_bound_project`` /
    ``install_scout`` / ``run_supervisor`` helpers they already use; this
    function performs no Gig-level work itself (no proposal, no approval).

    ``requested_username`` only matters if resolution reaches step 4
    (creating ``<home>/scout``); it's ignored otherwise. When it does reach
    step 4, ``on_created(path, username)`` is called so the caller can tell
    the operator which username was used.
    """

    if requested_target is not None:
        return requested_target

    bound = _bound_project_at_cwd(home_root, cwd)
    if bound is not None:
        return bound.target_root

    scout_projects = _existing_scout_projects(home_root)
    if len(scout_projects) == 1:
        return Path(scout_projects[0].target_locator)
    if len(scout_projects) > 1:
        listed = ", ".join(sorted(record.target_locator for record in scout_projects))
        raise ScoutTargetError(
            "scout_target_ambiguous: more than one Scout project is registered "
            f"({listed}); pass --target <dir> to choose one"
        )

    choice = _choose_username(home_root, requested_username)
    created = _create_home_scout_target(home_root, choice)
    if on_created is not None:
        on_created(created, choice.username)
    return created


__all__ = [
    "HOME_SCOUT_DIRNAME",
    "SCOUT_TEMPLATE_ID",
    "ScoutTargetError",
    "home_scout_target",
    "resolve_scout_target",
]

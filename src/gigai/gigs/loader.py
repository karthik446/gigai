"""Find installed gigs and mount each one as a lazy ``gigai <gig>`` group.

A gig is a distribution with an entry point in the ``gigai.gigs`` group and a static ``gig.toml`` next to
its top-level package's ``__init__.py``. Discovery reads the entry points and each ``gig.toml`` from the
distribution's files; no gig code is imported until ``gigai <gig> ...`` runs (or that gig's ``--help`` is
rendered). A gig that fails to load answers ``gigai <gig>: cannot load (<error>)`` and exits 1; ``gigai
--help`` and every other verb are unaffected. A gig named like an existing gigai command is not mounted
(the command wins) and one warning line goes to stderr.

``[commands] executable`` (without ``group``) gigs run out of process: ``gigai <gig> ARGS`` forwards ARGS to
that executable, and when ``gig.toml`` carries no description, ``<executable> describe`` (the manifest as
JSON) supplies it. No schema validation beyond the TOML parse happens here.
"""

from __future__ import annotations

import os
import sys
import tomllib
from importlib.metadata import EntryPoint, entry_points
from typing import Any

import click

ENTRY_POINT_GROUP = "gigai.gigs"
MANIFEST_NAME = "gig.toml"


class GigLoadError(Exception):
    """A gig whose manifest or code could not be loaded."""


def mount_gigs(group: click.Group) -> tuple[str, ...]:
    """Mount every installed gig on ``group`` as a lazy subgroup; returns the mounted names."""

    # Shell completion re-imports gigai on every Tab press; its stderr must stay quiet.
    quiet = bool(os.environ.get("_GIGAI_COMPLETE"))
    try:
        # Parses entry_points.txt of every installed distribution: one malformed file anywhere raises here.
        found = tuple(entry_points(group=ENTRY_POINT_GROUP))
    except Exception as exc:  # noqa: BLE001 - broken metadata of any dist mounts no gig, never takes core down
        if not quiet:
            click.echo(f"gigai: no gigs mounted: cannot read installed entry points ({_reason(exc)})", err=True)
        return ()
    owners: dict[str, EntryPoint] = {}
    for entry_point in found:
        name = entry_point.name
        if name in group.commands:
            if not quiet:
                winner = owners.get(name)
                taken = f"already mounted from {_dist_label(winner)}" if winner else "already a gigai command"
                click.echo(f"gigai: gig {name!r} ({_dist_label(entry_point)}) not mounted: {name!r} is {taken}", err=True)
            continue
        try:
            manifest: dict[str, Any] | None = read_manifest(entry_point)
            problem = None
        except Exception as exc:  # noqa: BLE001 - an unreadable manifest marks this gig broken, never the root CLI
            manifest, problem = None, _reason(exc)
        group.add_command(LazyGig(name, entry_point, manifest, problem))
        owners[name] = entry_point
    return tuple(owners)


def read_manifest(entry_point: EntryPoint) -> dict[str, Any]:
    """Parse the gig's ``gig.toml`` without importing the gig's code."""

    return tomllib.loads(_manifest_text(entry_point))


def _manifest_text(entry_point: EntryPoint) -> str:
    if EntryPoint.pattern.match(entry_point.value) is None:
        raise GigLoadError(f"invalid entry point value {entry_point.value!r}")
    package = entry_point.module.partition(".")[0]
    dist = entry_point.dist
    for path in (dist.files if dist is not None else None) or ():
        if path.name == MANIFEST_NAME and path.parts[:-1] == (package,):
            return path.read_text(encoding="utf-8")
    # Editable installs list only their .pth/finder in RECORD; find the package directory instead.
    # find_spec of a top-level name locates it without executing it.
    from importlib.util import find_spec

    spec = find_spec(package)
    for location in (spec.submodule_search_locations if spec is not None else None) or ():
        candidate = os.path.join(location, MANIFEST_NAME)
        if os.path.isfile(candidate):
            with open(candidate, encoding="utf-8") as handle:
                return handle.read()
    raise GigLoadError(f"no {MANIFEST_NAME} next to package {package!r} in {_dist_label(entry_point)}")


class LazyGig(click.Group):
    """``gigai <gig>``: a placeholder that imports the gig only when that gig itself is invoked."""

    def __init__(
        self,
        name: str,
        entry_point: EntryPoint,
        manifest: dict[str, Any] | None,
        problem: str | None = None,
    ) -> None:
        self.entry_point = entry_point
        self.manifest = manifest
        self._problem = problem
        self._target: click.Command | None = None
        description = _manifest_description(manifest)
        if problem is not None:
            description = f"cannot load ({problem})"
        super().__init__(name=name, help=description, short_help=description)

    @property
    def executable(self) -> str | None:
        """The ``[commands] executable`` of an out-of-process gig; ``None`` for an in-process group."""

        commands = _table(self.manifest, "commands")
        executable = commands.get("executable")
        if "group" in commands or not isinstance(executable, str):
            return None
        return executable

    def load(self) -> click.Command:
        """The gig's real command, imported on first use; raises ``GigLoadError`` when it cannot load."""

        if self._target is None:
            if self._problem is not None:
                raise GigLoadError(self._problem)
            executable = self.executable
            try:
                target = _forwarding_command(self.name or "", executable) if executable else self.entry_point.load()
            except (Exception, SystemExit) as exc:  # noqa: BLE001 - any failure in gig code is reported, not raised through gigai
                raise GigLoadError(_reason(exc)) from exc
            if not isinstance(target, click.Command):
                raise GigLoadError(f"{self.entry_point.value} is not a Click command")
            self._target = target
        return self._target

    def make_context(
        self,
        info_name: str | None,
        args: list[str],
        parent: click.Context | None = None,
        **extra: Any,
    ) -> click.Context:
        try:
            target = self.load()
        except GigLoadError as exc:
            if extra.get("resilient_parsing"):
                # Shell completion: complete nothing below a broken gig instead of a traceback.
                return super().make_context(info_name, args, parent=parent, **extra)
            click.echo(f"gigai {self.name}: cannot load ({exc})", err=True)
            raise click.exceptions.Exit(1) from exc
        return target.make_context(info_name, args, parent=parent, **extra)

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        target = self._loaded_or_none()
        return target.get_command(ctx, cmd_name) if isinstance(target, click.Group) else None

    def list_commands(self, ctx: click.Context) -> list[str]:
        target = self._loaded_or_none()
        return target.list_commands(ctx) if isinstance(target, click.Group) else []

    def get_short_help_str(self, limit: int = 45) -> str:
        if not self.short_help and self.executable:
            self.short_help = self.help = _describe_description(self.executable)
        return super().get_short_help_str(limit)

    def _loaded_or_none(self) -> click.Command | None:
        try:
            return self.load()
        except GigLoadError:
            return None


def _forwarding_command(name: str, executable: str) -> click.Command:
    path = _which(executable)
    if path is None:
        raise GigLoadError(f"executable {executable!r} not found")

    def forward(args: tuple[str, ...]) -> None:
        import subprocess

        raise click.exceptions.Exit(subprocess.call([path, *args]))

    return click.Command(
        name,
        callback=forward,
        params=[click.Argument(["args"], nargs=-1, type=click.UNPROCESSED)],
        context_settings={"ignore_unknown_options": True, "allow_interspersed_args": False},
        add_help_option=False,
    )


def _describe_description(executable: str) -> str | None:
    """The description from ``<executable> describe`` (the manifest as JSON); ``None`` when it gives none."""

    import json
    import subprocess

    path = _which(executable)
    if path is None:
        return None
    try:
        completed = subprocess.run([path, "describe"], capture_output=True, text=True, timeout=10, check=False)
        document = json.loads(completed.stdout) if completed.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    return _manifest_description(document) or (
        document["description"] if isinstance(document.get("description"), str) else None
    )


def _which(executable: str) -> str | None:
    """``executable`` on PATH or next to this interpreter (a ``uv tool`` env's bin is not always on PATH)."""

    import shutil

    search = os.pathsep.join([os.path.dirname(sys.executable), os.environ.get("PATH", "")])
    return shutil.which(executable, path=search)


def _manifest_description(manifest: dict[str, Any] | None) -> str | None:
    description = _table(manifest, "gig").get("description")
    return description if isinstance(description, str) and description else None


def _table(manifest: dict[str, Any] | None, key: str) -> dict[str, Any]:
    table = manifest.get(key) if manifest else None
    return table if isinstance(table, dict) else {}


def _dist_label(entry_point: EntryPoint) -> str:
    dist = entry_point.dist
    return f"{dist.name} {dist.version}" if dist is not None else entry_point.value


def _reason(exc: BaseException) -> str:
    message = str(exc)
    if isinstance(exc, GigLoadError):
        return message
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__

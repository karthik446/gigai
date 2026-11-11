"""Find installed gigs and mount each one as a lazy ``gigai <gig>`` group.

A gig is a distribution with an entry point in the ``gigai.gigs`` group and a static ``gig.toml`` in the package of
the entry point's module (``gigai.scout.gig:cli`` -> ``gigai/scout/gig.toml``), else in one of its parent packages
up to the top-level one. Discovery reads the entry points and each ``gig.toml`` from the distribution's files; no
gig code is imported until ``gigai <gig> ...`` runs (or that gig's ``--help`` is rendered). A gig that fails to load
answers ``gigai <gig>: cannot load (<error>)`` and exits 1; ``gigai --help`` and every other verb are unaffected. A
gig named like an existing gigai command is not mounted (the command wins) and one warning line goes to stderr. The
gigs the ``gigai`` distribution itself ships (Scout) come before any other distribution's: an installed dist that
names its gig ``scout`` never replaces core's.

``[commands] executable`` (without ``group``) gigs run out of process: ``gigai <gig> ARGS`` forwards ARGS to
that executable, and when ``gig.toml`` carries no description, ``<executable> describe`` (the manifest as
JSON) supplies it. No schema validation beyond the TOML parse happens here.

A gig whose manifest says ``[server] mode = "process"`` (and whose commands are a Click group) also gets core's ``run``,
``stop`` and ``status`` (``gigai.sdk.process``, the supervisor); a command of the same name in the gig's own group wins.
"""

from __future__ import annotations

import os
import sys
import tomllib
from collections.abc import Iterable
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Any, NoReturn

import click

ENTRY_POINT_GROUP = "gigai.gigs"
MANIFEST_NAME = "gig.toml"
CORE_DIST = "gigai"

# The commands of the group ``mount_gigs`` last mounted on, before any gig: names no gig may take. ``None`` until
# ``mount_gigs`` ran in this process; ``mountable`` then reads them off ``gigai.cli``.
_core_commands: frozenset[str] | None = None


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
    global _core_commands
    _core_commands = frozenset(name for name, command in group.commands.items() if not isinstance(command, LazyGig))
    owners: dict[str, EntryPoint] = {}
    for entry_point in _core_dist_first(found):
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


def mountable(found: Iterable[EntryPoint]) -> dict[str, EntryPoint]:
    """``{name: entry point}`` of the gigs ``mount_gigs`` mounts from ``found``, the same choice without mounting.

    The ``gigai`` distribution's own entry points first, then the first one per name; a name core already has (the
    group ``mount_gigs`` last mounted on, else ``gigai.cli``'s root) is never a gig's.
    """

    core = _core_commands if _core_commands is not None else _root_core_commands()
    owners: dict[str, EntryPoint] = {}
    for entry_point in _core_dist_first(found):
        if entry_point.name not in core and entry_point.name not in owners:
            owners[entry_point.name] = entry_point
    return owners


def _root_core_commands() -> frozenset[str]:
    """Core's own root commands, for a process that never built ``gigai.cli`` (importing it imports no gig)."""

    from ..cli import cli

    return frozenset(name for name, command in cli.commands.items() if not isinstance(command, LazyGig))


def _core_dist_first(found: Iterable[EntryPoint]) -> list[EntryPoint]:
    return sorted(found, key=lambda entry_point: _dist_name(entry_point) != CORE_DIST)


def _dist_name(entry_point: EntryPoint) -> str | None:
    dist = entry_point.dist
    return dist.name if dist is not None else None


def read_manifest(entry_point: EntryPoint) -> dict[str, Any]:
    """Parse the gig's ``gig.toml`` without importing the gig's code."""

    location = manifest_location(entry_point)
    if location is None:
        package = entry_point.module.rpartition(".")[0] or entry_point.module
        raise GigLoadError(f"no {MANIFEST_NAME} next to package {package!r} in {_dist_label(entry_point)}")
    return tomllib.loads(location.read_text(encoding="utf-8"))


def manifest_location(entry_point: EntryPoint) -> Path | None:
    """The gig's ``gig.toml``: in the package of the entry point's module, else the nearest parent package that has one.

    For ``a.b.c:obj`` the candidates are ``a/b/c/``, ``a/b/``, ``a/`` (``c`` may be a module, then ``a/b/c/`` is not
    a directory). Nothing below the top-level package is imported, and that one only located (``find_spec``).
    """

    if EntryPoint.pattern.match(entry_point.value) is None:
        raise GigLoadError(f"invalid entry point value {entry_point.value!r}")
    parts = tuple(entry_point.module.split("."))
    candidates = [parts[:length] for length in range(len(parts), 0, -1)]
    dist = entry_point.dist
    in_dist = {
        path.parts[:-1]: path
        for path in (dist.files if dist is not None else None) or ()
        if path.name == MANIFEST_NAME
    }
    for candidate in candidates:
        if candidate in in_dist:
            return Path(in_dist[candidate].locate())
    # Editable installs list only their .pth/finder in RECORD; find the package directory instead.
    # find_spec of a top-level name locates it without executing it.
    from importlib.util import find_spec

    try:
        spec = find_spec(parts[0])
    except (ImportError, ValueError):
        return None
    for location in (spec.submodule_search_locations if spec is not None else None) or ():
        for candidate in candidates:
            path = Path(location, *candidate[1:], MANIFEST_NAME)
            if path.is_file():
                return path
    return None


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
            if executable is None and isinstance(target, click.Group) and _process_server(self.manifest):
                target = _with_server_verbs(target, self)
            self._target = target
        return self._target

    @property  # type: ignore[override]
    def commands(self) -> dict[str, click.Command]:
        """For a gig the ``gigai`` dist ships (Scout), its group's own commands, so a walk over ``Group.commands``
        (``agent-context``, whose prose core's ``data/cli/commands.yaml`` holds) still sees its verbs; reading it
        imports the gig. ``{}`` for any other dist's gig (core documents none of its verbs) and a broken one."""

        target = self._loaded_or_none() if _dist_name(self.entry_point) == CORE_DIST else None
        return target.commands if isinstance(target, click.Group) else {}

    @commands.setter
    def commands(self, value: dict[str, click.Command]) -> None:
        """``click.Group.__init__`` assigns ``{}``; a gig's verbs live in its own group, never on the placeholder."""

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
        context = target.make_context(info_name, args, parent=parent, **extra)
        # Reached only once the gig's own parse succeeded: `--help`, no-args help and completion build nothing.
        if self.executable is None and not extra.get("resilient_parsing"):
            context.obj = self._gig_context()
        return context

    def _gig_context(self) -> Any:
        """The ``GigContext`` the gig's commands get as ``ctx.obj``; it touches no file (the gig folder is created
        when the gig first uses it), so a gig that keeps its own paths, like Scout, runs as it did before P3c."""

        import logging

        from ..sdk.context import GigContext
        from ..setup import default_home_root

        name = self.name or ""
        try:
            return GigContext(default_home_root(), name, self.manifest or {}, logging.getLogger(f"gigai.gig.{name}"))
        except ValueError as exc:
            _cannot_create_gig_folder(name, exc)

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

        from ..setup import default_home_root
        from .home import resolve_gig_folder

        try:
            resolve_gig_folder(default_home_root(), name)  # an executable gig gets its gig folder, not a GigContext
        except (OSError, ValueError) as exc:
            _cannot_create_gig_folder(name, exc)
        raise click.exceptions.Exit(subprocess.call([path, *args]))

    return click.Command(
        name,
        callback=forward,
        params=[click.Argument(["args"], nargs=-1, type=click.UNPROCESSED)],
        context_settings={"ignore_unknown_options": True, "allow_interspersed_args": False},
        add_help_option=False,
    )


def _process_server(manifest: dict[str, Any] | None) -> bool:
    from ..sdk.process import is_process_server

    return is_process_server(manifest)


def _with_server_verbs(group: click.Group, gig: LazyGig) -> click.Group:
    """A copy of ``group`` that falls back to core's ``run``/``stop``/``status``; the gig's group object is not changed."""

    import copy

    verbs = _server_verbs(gig)

    class ProcessGig(type(group)):  # type: ignore[misc] - the gig's own Group class, so its lookup stays first
        def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
            command = super().get_command(ctx, cmd_name)
            return command if command is not None else verbs.get(cmd_name)

        def list_commands(self, ctx: click.Context) -> list[str]:
            own = super().list_commands(ctx)
            return [*own, *sorted(set(verbs) - set(own))]

    wrapped = copy.copy(group)
    wrapped.__class__ = ProcessGig
    return wrapped


def _server_verbs(gig: LazyGig) -> dict[str, click.Command]:
    """Core's ``run``/``stop``/``status`` for a process-mode gig (``gigai.sdk.process``); ``ctx.obj`` is its ``GigContext``."""

    name = gig.name or ""

    def spec_and_home() -> tuple[Any, Any]:
        from ..sdk.process import ProcessServerError, ServerSpec

        context = click.get_current_context().obj
        dist = gig.entry_point.dist
        try:
            return ServerSpec.from_manifest(name, context.manifest, dist.version if dist is not None else "unknown"), context.home
        except (ProcessServerError, ValueError) as exc:
            _server_failed(name, exc)

    def echo_server(status: Any, label: str) -> None:
        state = status.state
        click.echo(f"{label}: pid {state.pid}, port {state.port}, health {status.health}, version {state.version}")
        click.echo(f"url: http://127.0.0.1:{state.port}/")

    def echo_restart(status: Any) -> None:
        if status.restarted_from is not None:
            click.echo(f"restarted: version {status.restarted_from} -> {status.state.version}")

    @click.command("run")
    def run() -> None:
        """Start this gig's server on 127.0.0.1 and wait until it is healthy."""

        from ..sdk import process

        spec, home = spec_and_home()
        try:
            status = process.start(home, spec)
        except (process.ProcessServerError, OSError, ValueError) as exc:
            _server_failed(name, exc)
        echo_restart(status)
        echo_server(status, "already running" if status.reused else "running")

    @click.command("status")
    @click.option("--json", "as_json", is_flag=True, help="Print the status as one JSON object.")
    def status(as_json: bool) -> None:
        """Show this gig's server: pid, port, health, version."""

        import json

        from ..sdk import process

        spec, home = spec_and_home()
        try:
            result = process.status(home, spec)
        except (process.ProcessServerError, OSError, ValueError) as exc:
            _server_failed(name, exc)
        if as_json:
            click.echo(json.dumps(result.to_json(), indent=2, sort_keys=True))
        elif result.state is None:
            click.echo("stopped (removed a stale state file)" if result.cleaned_stale else "stopped")
        else:
            echo_restart(result)
            echo_server(result, "running")

    @click.command("stop")
    def stop() -> None:
        """Stop this gig's server and every process it started."""

        from ..sdk import process

        home = click.get_current_context().obj.home
        try:
            result = process.stop(home, name)
        except (OSError, ValueError) as exc:
            _server_failed(name, exc)
        if result.stopped is not None:
            click.echo(f"stopped: pid {result.stopped.pid}")
        else:
            click.echo("not running (removed a stale state file)" if result.cleaned_stale else "not running")

    return {"run": run, "status": status, "stop": stop}


def _server_failed(name: str, exc: BaseException) -> NoReturn:
    from ..sdk.process import ProcessServerError

    click.echo(f"gigai {name}: {exc if isinstance(exc, ProcessServerError) else _reason(exc)}", err=True)
    raise click.exceptions.Exit(1) from exc


def _describe_description(executable: str) -> str | None:
    """The description from ``<executable> describe`` (the manifest as JSON); ``None`` when it gives none."""

    import json
    import subprocess

    path = _which(executable)
    if path is None:
        return None
    try:
        completed = subprocess.run(
            [path, "describe"], capture_output=True, text=True, timeout=10, check=False, shell=False
        )
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


def _cannot_create_gig_folder(name: str, exc: BaseException) -> NoReturn:
    click.echo(f"gigai {name}: cannot create its gig folder ({_reason(exc)})", err=True)
    raise click.exceptions.Exit(1) from exc


def _reason(exc: BaseException) -> str:
    message = str(exc)
    if isinstance(exc, GigLoadError):
        return message
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__

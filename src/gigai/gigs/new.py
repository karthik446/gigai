"""``gigai gig new NAME``: write a ready-to-install gig project into ``./gigai-NAME/``, offline.

The tree comes from the templates in ``gigai/gigs/templates/``: ``gig/`` is the project every gig gets (a
``src/gigai_<name>/`` package with ``gig.toml``, the Click group, its config schema, its docs and the contract tests
``gigai gig check`` runs); ``server/<mode>/`` adds a ``server.py`` and the ``[server]`` table for ``--server process``
or ``--server mount``. Each template file ends in ``.tmpl`` and names its values ``{{name}}``-style; a directory named
``__package__`` becomes the package directory. Nothing is installed and nothing is fetched.
"""

from __future__ import annotations

import keyword
import re
from collections.abc import Iterator
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

import click

from . import manifest as gig_manifest

SERVER_MODES = ("process", "mount")
_SUFFIX = ".tmpl"
_PACKAGE_DIR = "__package__"
_TOKEN = re.compile(r"\{\{([a-z]+)\}\}")


class GigNewError(click.ClickException):
    """``gigai gig new`` refused: exits 2 with one line on stderr."""

    exit_code = 2

    def show(self, file: object = None) -> None:
        click.echo(f"gigai gig new: {self.format_message()}", err=True)


def name_problem(name: str) -> str | None:
    """Why ``name`` cannot name a new gig, or ``None``: the manifest's ``[gig] name`` pattern, a Python keyword (the
    group function is named after the gig), a core command, or the name of an installed gig."""

    pattern = gig_manifest._validator().schema["properties"]["gig"]["properties"]["name"]["pattern"]
    if re.search(pattern, name) is None:
        return f"invalid gig name {name!r}: use 2-32 lowercase letters, digits and '-', starting with a letter"
    if keyword.iskeyword(_identifier(name)):
        return f"invalid gig name {name!r}: it is a Python keyword"
    from .loader import _root_core_commands

    if name in _root_core_commands():
        return f"{name!r} is a gigai command"
    if any(entry_point.name == name for entry_point in gig_manifest.entry_points()):
        return f"a gig named {name!r} is already installed"
    return None


def create(name: str, server: str | None, parent: Path) -> Path:
    """Write the project of gig ``name`` (``server``: ``None``, ``"process"`` or ``"mount"``) into
    ``parent/gigai-<name>/`` and return that directory; raises ``GigNewError`` when it refuses."""

    problem = name_problem(name)
    if problem is not None:
        raise GigNewError(problem)
    target = parent / f"gigai-{name}"
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise GigNewError(f"{target} already exists and is not an empty directory")
    package = f"gigai_{_identifier(name)}"
    templates = resources.files(__package__).joinpath("templates")
    values = {
        "name": name,
        "dist": f"gigai-{name}",
        "package": package,
        "group": _identifier(name),
        "description": f"The {name} gig, made by gigai gig new: say here what it does",
        "server": "",
    }
    mode = templates.joinpath("server", server) if server is not None else None
    if mode is not None:
        values["server"] = _render(mode.joinpath(f"gig.toml{_SUFFIX}"), values)
    files = dict(_render_tree(templates.joinpath("gig"), values))
    if mode is not None:
        files[Path("src", _PACKAGE_DIR, "server.py")] = _render(mode.joinpath(f"server.py{_SUFFIX}"), values)
    target.mkdir(exist_ok=True)
    for relative, text in files.items():
        path = target.joinpath(*(package if part == _PACKAGE_DIR else part for part in relative.parts))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return target


def _identifier(name: str) -> str:
    return name.replace("-", "_")


def _render_tree(root: Traversable, values: dict[str, str], relative: Path = Path()) -> Iterator[tuple[Path, str]]:
    for entry in sorted(root.iterdir(), key=lambda entry: entry.name):
        if entry.is_dir():
            yield from _render_tree(entry, values, relative / entry.name)
        elif entry.name.endswith(_SUFFIX):
            yield relative / entry.name.removesuffix(_SUFFIX), _render(entry, values)


def _render(template: Traversable, values: dict[str, str]) -> str:
    return _TOKEN.sub(lambda match: values[match.group(1)], template.read_text(encoding="utf-8"))

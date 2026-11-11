"""The gig folder ``<home>/gigs/<gig>/``: the one place a gig writes for the user.

Every gig gets one (decision 15, no opt-in), created empty the first time the gig uses it: ``ctx.folder`` or a
``ctx.store.db(gig, name)`` call (``ensure_gig_folder``), or the run of an executable gig. Building the gig's
``GigContext`` creates nothing, so a gig that never asks (Scout keeps its own paths) never gets one, and neither do
``gig install``, ``gigai --help`` or ``gig list``. The folder holds the SQLite stores the gig opens
(``<name>.sqlite``) and any state file the gig keeps there; it may stay empty. It is not one of the registered
workpads under ``<home>/workpads/``.
"""

from __future__ import annotations

import re
from pathlib import Path

import click

GIGS_DIRNAME = "gigs"
_NAME = re.compile(r"[a-z0-9_-]+")


class GigNameError(ValueError):
    """A gig or store name that cannot name a folder or file in the gigai home."""


def check_name(kind: str, value: object) -> str:
    """``value`` when it is a safe gig or store name (``[a-z0-9_-]+``: no path separator, no ``..``)."""

    if not isinstance(value, str) or _NAME.fullmatch(value) is None:
        raise GigNameError(f"invalid {kind} name {value!r}: use lowercase letters, digits, '_' and '-' only")
    return value


def gig_folder(home: Path, gig: str) -> Path:
    """Where the gig folder of ``gig`` is under ``home``; creates nothing."""

    return Path(home) / GIGS_DIRNAME / check_name("gig", gig)


def resolve_gig_folder(home: Path, gig: str) -> Path:
    """The gig folder of ``gig`` under ``home``, created (empty) if it is missing."""

    folder = gig_folder(home, gig)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


class GigFolderError(click.ClickException):
    """The gig folder could not be created where the gig first used it: exits 1 with one line on stderr."""

    def __init__(self, gig: str, exc: Exception) -> None:
        reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        super().__init__(f"gigai {gig}: cannot create its gig folder ({reason})")

    def show(self, file: object = None) -> None:
        click.echo(self.format_message(), err=True)


def ensure_gig_folder(home: Path, gig: str) -> Path:
    """``resolve_gig_folder`` for a gig's own use of its folder; a failure is a ``GigFolderError``, not a traceback."""

    try:
        return resolve_gig_folder(home, gig)
    except OSError as exc:
        raise GigFolderError(gig, exc) from exc

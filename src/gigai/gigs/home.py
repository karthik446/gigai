"""The gig folder ``<home>/gigs/<gig>/``: the one place a gig writes for the user.

Every gig gets one (decision 15, no opt-in). ``resolve_gig_folder`` creates it, empty, the first time the gig's
home is resolved, which is when core builds the gig's ``GigContext`` for its first ``gigai <gig> ...`` run. It is
not created at ``gig install``, by ``gigai --help`` or by ``gig list``. The folder holds the SQLite stores the gig
opens through ``ctx.store.db(gig, name)`` (``<name>.sqlite``) and any state file the gig keeps there; it may stay
empty. It is not one of the registered workpads under ``<home>/workpads/``.
"""

from __future__ import annotations

import re
from pathlib import Path

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

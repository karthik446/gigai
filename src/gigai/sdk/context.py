"""The context core hands a gig: where it runs, what its manifest says, and its stores."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..gigs.home import check_name, ensure_gig_folder
from .store import GigStore


@dataclass(frozen=True)
class GigContext:
    """What a gig gets from core: the gigai home, its name, its parsed ``gig.toml`` and a logger.

    Building one checks the name and touches no file. The gig folder ``<home>/gigs/<name>/`` is created the first
    time the gig uses it: ``folder``, or ``store.db`` opening one of its SQLite stores there.
    """

    home: Path
    name: str
    manifest: dict[str, Any]
    log: logging.Logger

    def __post_init__(self) -> None:
        check_name("gig", self.name)

    @property
    def folder(self) -> Path:
        """The gig folder ``<home>/gigs/<name>/``, created (empty) on first use; ``GigFolderError`` when it cannot be."""

        return ensure_gig_folder(self.home, self.name)

    @property
    def store(self) -> GigStore:
        """``ctx.store.db(gig, name)`` opens ``<home>/gigs/<gig>/<name>.sqlite`` (see ``gigai.sdk.store``)."""

        return GigStore(self.home, self.name, self.manifest)

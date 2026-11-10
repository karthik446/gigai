"""The context core hands a gig: where it runs, what its manifest says, and its stores."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..gigs.home import resolve_gig_folder
from .store import GigStore


@dataclass(frozen=True)
class GigContext:
    """What a gig gets from core: the gigai home, its name, its parsed ``gig.toml`` and a logger.

    Building one creates the gig's gig folder ``<home>/gigs/<name>/`` when it is missing; ``store`` opens the
    gig's SQLite stores in that folder.
    """

    home: Path
    name: str
    manifest: dict[str, Any]
    log: logging.Logger

    def __post_init__(self) -> None:
        resolve_gig_folder(self.home, self.name)

    @property
    def store(self) -> GigStore:
        """``ctx.store.db(gig, name)`` opens ``<home>/gigs/<gig>/<name>.sqlite`` (see ``gigai.sdk.store``)."""

        return GigStore(self.home, self.name, self.manifest)

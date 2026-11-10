"""The context core hands a gig: where it runs and what its manifest says."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GigContext:
    """What a gig gets from core: the gigai home, its name, its parsed ``gig.toml`` and a logger."""

    home: Path
    name: str
    manifest: dict[str, Any]
    log: logging.Logger

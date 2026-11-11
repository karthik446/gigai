"""The context core hands a gig: where it runs, what its manifest says, its stores and its eval traces."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..gigs.home import check_name, ensure_gig_folder
from .store import GigStore

if TYPE_CHECKING:
    from ..evals.records import Postprocess, ReplayRow, TraceRecord


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

    def trace(self, step_id: str, input: str, output: str, **fields: Any) -> TraceRecord:
        """Record one trace of step ``step_id`` in ``<home>/gigs/<name>/evals.sqlite`` and return it.

        ``fields`` are the keyword arguments of ``gigai.evals.records.trace``: ``run`` and ``item`` (required),
        ``prompt``, ``model`` (``model_call(binding, request, result)``), ``repeat_index``, ``attempt_index``,
        ``output_parsed``, ``parse_status``, ``outcome``, ``error``.
        """

        from ..evals.records import trace

        return trace(self.home, self.name, step_id, input, output, **fields)

    def replay(self, run_id: str, postprocess: Postprocess, *, step_id: str | None = None) -> list[ReplayRow]:
        """Re-score this gig's stored traces of run ``run_id`` (one step, or all) through ``postprocess``.

        Read-only and no model call (see ``gigai.evals.records.replay``).
        """

        from ..evals.records import replay

        return replay(self.home, self.name, run_id, postprocess, step_id=step_id)

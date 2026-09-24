"""Shared path/atomic-write helpers for the discovery package.

Storage lives under the GigAI home, per project id (never inside the
operator's target directory) -- the same convention
``run_supervisor.py``'s ``<home>/run/scout/<project_id>.json`` uses, keyed
by ``BoundProject.project_id`` so it survives a target rename:

- ``<home>/scout/<project_id>/discovery/prefs.json`` (interview answers)
- ``<home>/scout/<project_id>/discovery/runs/<discovery_id>.json`` (results)
- ``<home>/scout/<project_id>/discovery/evidence.json`` (sidecar sponsorship
  evidence, keyed by (provider, board_token) -- see ``merge.py``; the
  coordinator-approved Option B, since ``WatchlistEntry``/``SourceKind`` are
  a versioned journal contract this task must not change, see
  ``.orchestrator/workers/s2a-discovery-engine.md``).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ....workpad import resolve_bound_project


def project_id(home_root: Path, target: Path) -> str:
    return resolve_bound_project(home_root=home_root, requested_target=target).project_id


def discovery_dir(home_root: Path, target: Path) -> Path:
    return home_root / "scout" / project_id(home_root, target) / "discovery"


def runs_dir(home_root: Path, target: Path) -> Path:
    return discovery_dir(home_root, target) / "runs"


def evidence_path(home_root: Path, target: Path) -> Path:
    return discovery_dir(home_root, target) / "evidence.json"


def h1b_cache_dir(home_root: Path) -> Path:
    """Shared across projects, not per-project (DOL's file isn't project data)."""

    return home_root / "cache" / "scout" / "h1b"


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


__all__ = [
    "atomic_write",
    "discovery_dir",
    "evidence_path",
    "h1b_cache_dir",
    "project_id",
    "runs_dir",
]

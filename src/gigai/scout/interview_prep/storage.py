"""Path/atomic-write helpers for interview prep.

Storage shape (coordinator decision, 2026-09-24, ``.orchestrator/workers/
interview-prep-engine.md``): plain atomic-write JSON under the GigAI home,
same reasoning as ``find_jobs/discovery/storage.py`` -- interview prep is
Scout's own *automated* pipeline output (web-search + a model call), not
operator-curated content, so it does not fit either existing journal record
kind (``scout_interview_preparation`` requires operator-selected
evidence-backed stories; the research-packet record kind requires a sealed
Graph Run origin). No new journal record kind, no schema registration.

``<home>/scout/<project_id>/interview_prep/<sha256(posting_id)>.json`` --
one file per posting, holding the latest prep for that posting (any resume
revision); ``prep.py`` decides idempotency/refresh by comparing the stored
resume identity, not this module. The filename is a digest of the
normalized posting URL, not the URL itself (a URL contains ``/`` and other
characters that are not a safe single path segment); the prep's own
``posting_id`` field inside the JSON keeps the readable URL.
"""

from __future__ import annotations

from pathlib import Path

from ...canonical import digest_imported_bytes
from ..find_jobs.discovery.storage import atomic_write, project_id


def interview_prep_dir(home_root: Path, target: Path) -> Path:
    return home_root / "scout" / project_id(home_root, target) / "interview_prep"


def _filename(posting_id: str) -> str:
    digest = digest_imported_bytes(posting_id.encode("utf-8")).removeprefix("sha256:")
    return f"{digest}.json"


def prep_path(home_root: Path, target: Path, posting_id: str) -> Path:
    return interview_prep_dir(home_root, target) / _filename(posting_id)


__all__ = ["atomic_write", "interview_prep_dir", "prep_path"]

"""Path/atomic-write helpers for interview prep.

Storage shape (coordinator decision, 2026-09-24, ``.orchestrator/workers/
interview-prep-engine.md``): plain atomic-write JSON under the GigAI home,
same reasoning as ``find_jobs/discovery/storage.py`` -- interview prep is
Scout's own *automated* pipeline output (web-search + a model call), not
operator-curated content, so it does not fit either existing journal record
kind (``scout_interview_preparation`` requires operator-selected
evidence-backed stories; the research-packet record kind requires a sealed
Graph Run origin). No new journal record kind, no schema registration.

``<home>/scout/<project_id>/interview_prep/<profile_id>/<sha256(posting_id)>.json``
(S25 F1-b2, operator decision: interview prep is keyed by profile -- see the
spike's Q5) -- one file per (profile, posting), holding the latest prep for
that pair (any resume revision); ``prep.py`` decides idempotency/refresh by
comparing the stored resume identity, not this module. The digest filename
itself is UNCHANGED from before F1-b2 (it still digests the posting URL
alone, now uniquely scoped by its parent ``<profile_id>/`` directory rather
than needing the profile id folded into the hash input); the prep's own
``posting_id`` field inside the JSON keeps the readable URL.

**Legacy read fallback (F1-b2 migration, no file move):** a prep built
before F1-b2 shipped lives at the OLD flat path,
``interview_prep/<sha256(posting_id)>.json`` (no ``<profile_id>/`` segment).
Per the S25 spike's exclusion ("no schema/storage migration -- legacy prep
files are read, not moved"), that file is never renamed or copied; it is
read as belonging to the workpad's migrated DEFAULT profile only (the same
"there is only ever one profile before F1 ships" reasoning the profile
migration itself relies on) -- ``resolve_prep_read_path`` returns
the new per-profile path when a prep already exists there, otherwise the
old flat path when ONE exists there AND the caller is asking for the
default profile, otherwise the new per-profile path (where a fresh prep for
that profile is written).
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


def _legacy_prep_path(home_root: Path, target: Path, posting_id: str) -> Path:
    """The pre-F1-b2 flat path (no ``<profile_id>/`` segment) -- read-only."""

    return interview_prep_dir(home_root, target) / _filename(posting_id)


def prep_path(home_root: Path, target: Path, profile_id: str, posting_id: str) -> Path:
    """The CURRENT (post-F1-b2) per-profile path -- always used for WRITES."""

    return interview_prep_dir(home_root, target) / profile_id / _filename(posting_id)


def resolve_prep_read_path(
    home_root: Path, target: Path, profile_id: str, posting_id: str, *, is_default_profile: bool
) -> Path:
    """The path to READ this (profile, posting)'s prep from, if any exists.

    Prefers the new per-profile path; a legacy flat file (pre-F1-b2, never
    moved) is read as belonging to the migrated default profile ONLY --
    ``is_default_profile`` is the caller's own already-resolved fact (this
    module has no idea which profile is "default"), matching the Legacy-run
    policy's own "attributed to the default profile only" shape elsewhere
    in S25. Returns the per-profile path even when nothing exists yet there
    (the natural "doesn't exist" case ``prep.py``'s callers already handle).
    """

    per_profile = prep_path(home_root, target, profile_id, posting_id)
    if per_profile.is_file():
        return per_profile
    if is_default_profile:
        legacy = _legacy_prep_path(home_root, target, posting_id)
        if legacy.is_file():
            return legacy
    return per_profile


__all__ = [
    "atomic_write",
    "interview_prep_dir",
    "prep_path",
    "resolve_prep_read_path",
]

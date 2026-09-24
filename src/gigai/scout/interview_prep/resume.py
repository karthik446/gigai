"""Find and read the current pinned resume for a bound Scout project.

Reuses ``gigai.private_records.list_imports`` (family ``"reference"``) --
the same store ``gigai scout resume add`` writes to (``kind="resume"``) --
rather than re-deriving resume lookup. There is no "current" pointer kept
elsewhere (Scout's own convention is explicit record/revision selection
everywhere -- see ``projection.py``'s "no 'latest' projection is accepted
from a caller or model"), so this picks the most recently imported
reference of kind ``resume``, matching the one-resume-per-project shape
``scout resume add`` produces today (re-running it replaces the reference
content by digest, not by adding a second one for the same bytes; a
genuinely different resume file does add a second reference, and this
picks the newest).

Privacy: this module returns resume bytes to the caller (``prep.py``), which
must send them only to the configured assess-equivalent model call, never to
the web-search request -- enforced in ``prep.py``/``websearch.py``, not here.
"""

from __future__ import annotations

from pathlib import Path

from ...private_records import PrivateRecordError, list_imports
from ...workpad import resolve_workpad
from ..interview_prep.types import ResumeIdentity


class ResumeUnavailableError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def current_resume(
    *, home_root: Path, requested_target: Path | None, gig_id: str | None
) -> tuple[ResumeIdentity, bytes]:
    """Return the current resume's identity and bytes, or raise if none is set."""

    try:
        references = list_imports(home_root=home_root, requested_target=requested_target, family="reference", gig_id=gig_id)
    except PrivateRecordError as exc:
        raise ResumeUnavailableError("resume_unavailable", "resume references are unavailable") from exc
    resumes = [item for item in references if item.get("kind") == "resume"]
    if not resumes:
        raise ResumeUnavailableError(
            "resume_missing", "no resume is set for this project; run `gigai scout resume add <file>`"
        )
    resumes.sort(key=lambda item: str(item.get("created_at", "")))
    latest = resumes[-1]
    reference_id = str(latest["reference_id"])
    content_sha256 = str(latest["content_sha256"])
    resolved = resolve_workpad(home_root=home_root, requested_target=requested_target, gig_id=gig_id, allow_semantic_state=True)
    from ...canonical import digest_imported_bytes
    from ...journal import read_committed_artifact

    source_path = f"references/{reference_id}/source.txt"
    try:
        data, _commit = read_committed_artifact(
            workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, path=source_path,
        )
    except Exception as exc:
        raise ResumeUnavailableError("resume_unavailable", "resume content is not committed authority") from exc
    if digest_imported_bytes(data) != content_sha256:
        raise ResumeUnavailableError("resume_digest_mismatch", "resume content changed since it was imported")
    return ResumeIdentity(reference_id=reference_id, content_sha256=content_sha256), data


__all__ = ["ResumeUnavailableError", "current_resume"]

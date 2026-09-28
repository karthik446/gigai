"""The one resume import path (uat-bug-020).

``gigai scout resume add <file>`` and the setup wizard's ``POST
/api/resumes`` both store a resume through ``import_resume_file``: the
reference import (kind ``resume``) plus the ``g45_reference`` record wrapper
find-jobs' resume resolution reads. The function body is the sequence
``scout_cli.resume_add_command`` used to carry inline, moved here unchanged
(same operation keys, same actor, same origin), so a resume stored from the
wizard and the same file added from the CLI are one reference and one
record.

Idempotent by content: the reference import reuses the receipt for the same
operation key, and otherwise the reference already committed for the same
kind, content digest and media type; the record's operation key is derived
from the reference id. Storing the same bytes again creates nothing.

``import_resume_bytes`` is for a caller that holds the resume in memory (the
API: pasted text, or a file the browser read). ``private_records.
import_reference`` reads its source from one regular file, so the bytes are
written to a private temporary directory (mode 0700, removed before this
returns, also on failure) and imported from there. Nothing here logs,
prints or returns a byte of the resume.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import tempfile

from ..canonical import digest_imported_bytes
from ..private_records import create_record, import_reference

#: ``private_records.import_reference``'s own size limit for a reference.
RESUME_MAX_BYTES = 1_048_576
#: The suffixes ``import_reference`` accepts (plain text or Markdown).
RESUME_SUFFIXES = (".txt", ".md", ".markdown")
#: The file name (and so the label) a pasted resume is stored under.
PASTED_RESUME_FILE_NAME = "pasted-resume.txt"

# An operation key is at most 160 characters of [A-Za-z0-9._:-]
# (private_records._receipt_path); "scout-resume-add:" + name + ":" + the
# digest leaves 71 for the name.
_MAX_STEM_CHARS = 60


class ResumeImportError(ValueError):
    """A resume that cannot be imported; ``code`` is stable, the message is for a person."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ImportedResume:
    reference_id: str
    reference_created: bool
    record_id: str
    revision_id: str
    record_created: bool
    content_sha256: str
    label: str

    @property
    def created(self) -> bool:
        return self.reference_created or self.record_created


def import_resume_file(
    *,
    home_root: Path,
    requested_target: Path | None,
    source: Path,
    gig_id: str | None = None,
) -> ImportedResume:
    """Import ``source`` as the resume reference and create the record find-jobs reads."""

    # Key by name + content digest (not name alone) so re-adding the
    # SAME bytes under the same file name stays idempotent (identical
    # key -> the existing receipt is reused) while re-adding EDITED
    # bytes under the same file name creates a new resume revision
    # instead of conflicting on a stale operation key (P0-4).
    content_digest = digest_imported_bytes(source.read_bytes())
    imported = import_reference(
        home_root=home_root,
        requested_target=requested_target,
        gig_id=gig_id,
        kind="resume",
        source=source,
        operation_key=f"scout-resume-add:{source.name}:{content_digest}",
    )
    record = create_record(
        home_root=home_root,
        requested_target=requested_target,
        gig_id=gig_id,
        kind="imported_reference",
        content_family="g45_reference",
        content_id=imported.item_id,
        actor={"kind": "operator", "id": "local-user"},
        origin="imported",
        operation_key=f"scout-resume-record:{imported.item_id}",
    )
    return ImportedResume(
        reference_id=imported.item_id,
        reference_created=imported.created,
        record_id=record.record_id,
        revision_id=record.revision_id,
        record_created=record.created,
        content_sha256=str(imported.record["content_sha256"]),
        label=str(imported.record.get("label", source.name)),
    )


def safe_resume_file_name(file_name: str) -> str:
    """A file name the import path accepts, from whatever the browser sent.

    Keeps the base name's suffix (which decides the media type) and replaces
    every other character outside ``[A-Za-z0-9._-]``: an operation key
    cannot carry a space or a non-ASCII letter. A name with no usable stem
    becomes ``resume``. Refuses a suffix the import does not accept.
    """

    base = file_name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    suffix = Path(base).suffix.lower()
    if suffix not in RESUME_SUFFIXES:
        raise ResumeImportError(
            "resume_media_type_unsupported",
            "the resume must be plain text or Markdown (.txt, .md, .markdown)",
        )
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", base[: -len(suffix)]).strip("-.")
    return f"{stem[:_MAX_STEM_CHARS].rstrip('-.') or 'resume'}{suffix}"


def import_resume_bytes(
    *,
    home_root: Path,
    requested_target: Path | None,
    data: bytes,
    file_name: str = PASTED_RESUME_FILE_NAME,
    gig_id: str | None = None,
) -> ImportedResume:
    """Import resume ``data`` held in memory, through ``import_resume_file``."""

    name = safe_resume_file_name(file_name)
    # Resolved: the import refuses a source below a redirected (symlinked)
    # parent, and the system temp directory is one on macOS (/var).
    directory = Path(tempfile.mkdtemp(prefix="gigai-resume-")).resolve()
    try:
        source = directory / name
        source.write_bytes(data)
        return import_resume_file(
            home_root=home_root,
            requested_target=requested_target,
            source=source,
            gig_id=gig_id,
        )
    finally:
        shutil.rmtree(directory, ignore_errors=True)


__all__ = [
    "ImportedResume",
    "PASTED_RESUME_FILE_NAME",
    "RESUME_MAX_BYTES",
    "RESUME_SUFFIXES",
    "ResumeImportError",
    "import_resume_bytes",
    "import_resume_file",
    "safe_resume_file_name",
]

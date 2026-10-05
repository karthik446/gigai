"""The one resume import path (uat-bug-020).

``gigai scout resume add <file>`` and the setup wizard's ``POST
/api/resumes`` both store a resume through ``import_resume_file``: the
reference import (kind ``resume``) plus the ``g45_reference`` record wrapper
find-jobs' resume resolution reads. The function body is the sequence
``scout_cli.resume_add_command`` used to carry inline, moved here unchanged
(same operation keys, same actor, same origin), so a resume stored from the
wizard and the same file added from the CLI are one reference and one
record. uat-bug-023: a file name an operation key cannot carry ("My
Resume.md") is named in the key by ``operation_key_name``; the label stored
with the reference is the file's own name.

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

0110-046: GigAI stores no name or contact details. Every import runs
``resume_pii.strip_contact_lines`` first (the name line, the header's
contact lines, contact-only lines, emails / phone numbers / links inside
other lines, the name's words elsewhere) and stores only what is left; the
removed lines are discarded. ``ImportedResume.contact_removed`` counts what
went, by kind (never a value), for the message the CLI, the API and the UI
show (``resume_pii.REMOVED_MESSAGE``). A file name that holds the removed
name's words is stored as ``resume<suffix>``.

0.1.10.11: a link in a heading (``### [Driftwatch](https://...)``) goes and
the heading keeps its words (``resume_privacy.heading_links``, the one rule;
``ImportedResume.heading_links`` says where, by line number and the
heading's words). A heading that is only a link is refused by line number
(``resume_heading_only_link``): nothing is imported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import shutil
import tempfile

from ..canonical import digest_imported_bytes
from ..private_records import create_record, import_reference
from ..workpad import one_operation
from .resume_pii import ContactStrip, strip_contact_lines
from .resume_privacy import HeadingOnlyLink

#: ``private_records.import_reference``'s own size limit for a reference.
RESUME_MAX_BYTES = 1_048_576
#: The suffixes ``import_reference`` accepts (plain text or Markdown).
RESUME_SUFFIXES = (".txt", ".md", ".markdown")
RESUME_MEDIA_TYPE_MESSAGE = (
    "the resume must be plain text or Markdown (.txt, .md, .markdown); convert a PDF or DOCX with: "
    "uvx --from 'markitdown[pdf,docx]' markitdown resume.pdf > resume.md"
)
#: The file name (and so the label) a pasted resume is stored under.
PASTED_RESUME_FILE_NAME = "pasted-resume.txt"

# An operation key is at most 160 characters of [A-Za-z0-9._:-]
# (private_records._receipt_path); "scout-resume-add:" + name + ":" + the
# digest leaves 71 for the name.
_MAX_KEY_NAME_CHARS = 71
_MAX_STEM_CHARS = 60
_NAME_TAG_CHARS = 12
_NOT_KEY_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


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
    #: 0110-046: what the import removed, by kind (``{}`` when nothing): counts only.
    contact_removed: dict[str, int] = field(default_factory=dict)
    #: 0.1.10.11: the links taken out of a heading that is kept (``ContactStrip.headings``): the file line, the heading's words, never the address.
    heading_links: tuple[tuple[int, str, bool], ...] = ()

    @property
    def created(self) -> bool:
        return self.reference_created or self.record_created


def _strip(data: bytes) -> ContactStrip | None:
    """The import's strip of ``data``; ``None`` when it is too large or not UTF-8 (``import_reference`` refuses both).

    ``ResumeImportError`` (``resume_heading_only_link``) for a heading that is only a link: by line number, never its text."""

    if len(data) > RESUME_MAX_BYTES:
        return None
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    try:
        return strip_contact_lines(text, headings=True)
    except HeadingOnlyLink as exc:
        raise ResumeImportError("resume_heading_only_link", f"{exc}, then add the resume again. Nothing was imported.") from None


def _stored_name(file_name: str, name_words: frozenset[str]) -> str:
    """``file_name``, or ``resume<suffix>`` when it holds a word of the removed name (``Jane_Doe_CV.md``)."""

    if not name_words:
        return file_name
    stem_words = {word.lower() for word in re.split(r"[^0-9A-Za-z\u00C0-\uFFFF]+", Path(file_name).stem) if word}
    return f"resume{Path(file_name).suffix.lower()}" if stem_words & name_words else file_name


@one_operation()
def import_resume_file(
    *,
    home_root: Path,
    requested_target: Path | None,
    source: Path,
    gig_id: str | None = None,
) -> ImportedResume:
    """Import ``source``, contact lines removed, as the resume reference and the record find-jobs reads."""

    if source.suffix.lower() not in RESUME_SUFFIXES:
        raise ResumeImportError("resume_media_type_unsupported", RESUME_MEDIA_TYPE_MESSAGE)
    stripped = _strip(source.read_bytes())
    if stripped is None or (not stripped.changed and _stored_name(source.name, stripped.name_words) == source.name):
        return _import_file(home_root=home_root, requested_target=requested_target, source=source, gig_id=gig_id)
    # 0110-046: the stripped text is what is stored; the original bytes never reach the workpad.
    directory = Path(tempfile.mkdtemp(prefix="gigai-resume-")).resolve()
    try:
        clean = directory / _stored_name(source.name, stripped.name_words)
        clean.write_text(stripped.text if stripped.text.endswith("\n") or not stripped.text else stripped.text + "\n", encoding="utf-8")
        imported = _import_file(home_root=home_root, requested_target=requested_target, source=clean, gig_id=gig_id)
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    return ImportedResume(**{**imported.__dict__, "contact_removed": dict(stripped.removed), "heading_links": stripped.headings})


def _import_file(*, home_root: Path, requested_target: Path | None, source: Path, gig_id: str | None) -> ImportedResume:
    """The reference import plus its ``g45_reference`` record (the pre-0110-046 body of ``import_resume_file``)."""

    # Key by name + content digest (not name alone) so re-adding the
    # SAME bytes under the same file name stays idempotent (identical
    # key -> the existing receipt is reused) while re-adding EDITED
    # bytes under the same file name creates a new resume revision
    # instead of conflicting on a stale operation key (P0-4).
    #
    # uat-bug-023: the name in the key is operation_key_name(): a name with
    # a space or a non-ASCII letter ("My Resume.md") is not an operation
    # key. The stored label is still the file's own name (import_reference
    # takes it from ``source``).
    content_digest = digest_imported_bytes(source.read_bytes())
    imported = import_reference(
        home_root=home_root,
        requested_target=requested_target,
        gig_id=gig_id,
        kind="resume",
        source=source,
        operation_key=f"scout-resume-add:{operation_key_name(source.name)}:{content_digest}",
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


def reduce_file_name(file_name: str) -> str:
    """``file_name``'s base name in ``[A-Za-z0-9._-]``, the one reduction.

    Every run of other characters in the stem becomes one ``-``; the suffix
    is kept, in lower case. A name with no usable stem becomes ``resume``.
    ``safe_resume_file_name`` (the name ``POST /api/resumes`` imports under)
    and ``operation_key_name`` (the name in the operation key) both reduce a
    name through this function.
    """

    base = file_name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    suffix = Path(base).suffix
    stem = _NOT_KEY_SAFE.sub("-", base[: len(base) - len(suffix)]).strip("-.")
    suffix = _NOT_KEY_SAFE.sub("-", suffix.lower())
    return f"{stem[:_MAX_STEM_CHARS].rstrip('-.') or 'resume'}{suffix}"


def operation_key_name(file_name: str) -> str:
    """The part of the import's operation key that names the file (uat-bug-023).

    A name an operation key can carry is returned as it is, so the keys
    written before this function existed are still the keys. Any other name
    is reduced (``reduce_file_name``) and followed by 12 hex characters of
    the name's own digest: the stored label is the file's own name and is
    part of what the receipt seals, and the same key with another label is
    refused (``private_operation_conflict``), so two names never share a key.
    """

    if file_name and len(file_name) <= _MAX_KEY_NAME_CHARS and not _NOT_KEY_SAFE.search(file_name):
        return file_name
    tag = digest_imported_bytes(file_name.encode("utf-8")).removeprefix("sha256:")[:_NAME_TAG_CHARS]
    reduced = reduce_file_name(file_name)[: _MAX_KEY_NAME_CHARS - _NAME_TAG_CHARS - 1].rstrip("-.")
    return f"{reduced}-{tag}"


def safe_resume_file_name(file_name: str) -> str:
    """A file name the import path accepts, from whatever the browser sent.

    Keeps the base name's suffix (which decides the media type) and replaces
    every other character outside ``[A-Za-z0-9._-]``: an operation key
    cannot carry a space or a non-ASCII letter. A name with no usable stem
    becomes ``resume``. Refuses a suffix the import does not accept.
    """

    base = file_name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if Path(base).suffix.lower() not in RESUME_SUFFIXES:
        raise ResumeImportError("resume_media_type_unsupported", RESUME_MEDIA_TYPE_MESSAGE)
    return reduce_file_name(base)


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
    "operation_key_name",
    "reduce_file_name",
    "safe_resume_file_name",
]

"""Bounded, private Tailor document revisions and explicit final selection.

The host remains responsible for journal writes and authoritative record IDs.
These functions accept exact bytes and immutable lineage, run advisory checks,
and never mark an application or perform an external action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Mapping, Sequence

from ..canonical import EntityPrefix, digest_imported_bytes, validate_entity_id
from .checks import check_document
from .tailoring import decode_tailoring_bundle
from .tailor_selection import TailorSelection

_KINDS = frozenset({"resume", "cover_letter"})
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_BYTES = 262_144


class ScoutDocumentError(ValueError):
    """Stable, content-free document refusal."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise ScoutDocumentError(code, message)


def _id(value: object, prefix: EntityPrefix, name: str) -> str:
    try:
        return validate_entity_id(value, expected_prefix=prefix)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        _fail("document_input_invalid", f"{name} is invalid")


@dataclass(frozen=True)
class SourceLineage:
    source_id: str
    content_sha256: str
    identity: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.source_id) is not str or not self.source_id or len(self.source_id) > 64:
            _fail("document_input_invalid", "source lineage is invalid")
        if not isinstance(self.content_sha256, str) or _SHA256.fullmatch(self.content_sha256) is None:
            _fail("document_input_invalid", "source lineage digest is invalid")
        if not isinstance(self.identity, Mapping):
            _fail("document_input_invalid", "source lineage identity is invalid")


@dataclass(frozen=True)
class DocumentRevision:
    opportunity_id: str
    snapshot_id: str
    document_kind: str
    record_id: str
    revision_id: str
    content: bytes
    content_sha256: str
    source_lineage: tuple[SourceLineage, ...]
    checks: Mapping[str, object]
    parent_revision_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.opportunity_id) is not str or not re.fullmatch(r"opportunity_[0-9a-f]{32}", self.opportunity_id):
            _fail("document_input_invalid", "opportunity identity is invalid")
        if type(self.snapshot_id) is not str or not re.fullmatch(r"snapshot_[0-9a-f]{32}", self.snapshot_id):
            _fail("document_input_invalid", "snapshot identity is invalid")
        if type(self.document_kind) is not str or self.document_kind not in _KINDS:
            _fail("document_input_invalid", "document kind is invalid")
        _id(self.record_id, EntityPrefix.RECORD, "record_id")
        _id(self.revision_id, EntityPrefix.REVISION, "revision_id")
        if self.parent_revision_id is not None:
            _id(self.parent_revision_id, EntityPrefix.REVISION, "parent_revision_id")
            if self.parent_revision_id == self.revision_id:
                _fail("document_input_invalid", "document revision parent is self")
        if type(self.content) is not bytes or not self.content or len(self.content) > _MAX_BYTES:
            _fail("document_input_invalid", "document content is invalid")
        try:
            self.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ScoutDocumentError("document_input_invalid", "document content is not UTF-8") from exc
        if not isinstance(self.content_sha256, str) or _SHA256.fullmatch(self.content_sha256) is None or digest_imported_bytes(self.content) != self.content_sha256:
            _fail("document_input_invalid", "document digest does not match content")
        if type(self.source_lineage) is not tuple or len(self.source_lineage) > 64 or any(not isinstance(item, SourceLineage) for item in self.source_lineage):
            _fail("document_input_invalid", "source lineage is invalid")
        if not isinstance(self.checks, Mapping):
            _fail("document_input_invalid", "document checks are invalid")

    def to_json(self, *, include_content: bool = False) -> dict[str, object]:
        value: dict[str, object] = {
            "revision_version": "scout-document-revision:1",
            "opportunity": {"opportunity_id": self.opportunity_id, "snapshot_id": self.snapshot_id},
            "document_kind": self.document_kind,
            "record_id": self.record_id,
            "revision_id": self.revision_id,
            "content_sha256": self.content_sha256,
            "source_lineage": [{"source_id": item.source_id, "content_sha256": item.content_sha256, "identity": dict(item.identity)} for item in self.source_lineage],
            "checks": dict(self.checks),
            "parent_revision_id": self.parent_revision_id,
        }
        if include_content:
            value["content_utf8"] = self.content.decode("utf-8")
        return value


@dataclass(frozen=True)
class FinalDocumentSelection:
    opportunity_id: str
    snapshot_id: str
    documents: tuple[DocumentRevision, ...]
    selected_by: str

    def __post_init__(self) -> None:
        if type(self.opportunity_id) is not str or not re.fullmatch(r"opportunity_[0-9a-f]{32}", self.opportunity_id):
            _fail("document_selection_invalid", "opportunity identity is invalid")
        if type(self.snapshot_id) is not str or not re.fullmatch(r"snapshot_[0-9a-f]{32}", self.snapshot_id):
            _fail("document_selection_invalid", "snapshot identity is invalid")
        if type(self.documents) is not tuple or not self.documents or len(self.documents) > 2:
            _fail("document_selection_invalid", "final document selection is invalid")
        if any(not isinstance(item, DocumentRevision) for item in self.documents):
            _fail("document_selection_invalid", "final document selection is invalid")
        if any(item.opportunity_id != self.opportunity_id or item.snapshot_id != self.snapshot_id for item in self.documents):
            _fail("document_selection_invalid", "final document source opportunity is inconsistent")
        kinds = [item.document_kind for item in self.documents]
        if len(set(kinds)) != len(kinds):
            _fail("document_selection_invalid", "final document kinds are duplicated")
        if type(self.selected_by) is not str or not self.selected_by.strip() or len(self.selected_by) > 128:
            _fail("document_selection_invalid", "selected_by is invalid")

    def to_json(self) -> dict[str, object]:
        return {
            "selection_version": "scout-document-selection:1",
            "opportunity": {"opportunity_id": self.opportunity_id, "snapshot_id": self.snapshot_id},
            "documents": [{"document_kind": item.document_kind, "record_id": item.record_id, "revision_id": item.revision_id, "content_sha256": item.content_sha256} for item in self.documents],
            "selected_by": {"kind": "operator", "id": self.selected_by},
        }


def prepare_document_revision(
    selection: TailorSelection,
    document_kind: str,
    record_id: str,
    revision_id: str,
    content: bytes,
    *,
    parent_revision_id: str | None = None,
    checks_options: Mapping[str, object] | None = None,
) -> DocumentRevision:
    """Prepare an immutable revision DTO; the host later journals it."""
    if not isinstance(selection, TailorSelection):
        _fail("document_input_invalid", "selection is invalid")
    if type(document_kind) is not str or document_kind not in selection.requested_outputs:
        _fail("document_input_invalid", "document kind was not requested")
    if type(content) is not bytes:
        _fail("document_input_invalid", "document content must be bytes")
    if checks_options is not None and not isinstance(checks_options, Mapping):
        _fail("document_input_invalid", "document checks are invalid")
    options = dict(checks_options or {})
    try:
        checks = check_document(content, document_kind, **options)
    except (TypeError, ValueError):
        _fail("document_input_invalid", "document checks are invalid")
    return DocumentRevision(
        opportunity_id=selection.opportunity_id,
        snapshot_id=selection.snapshot_id,
        document_kind=document_kind,
        record_id=record_id,
        revision_id=revision_id,
        content=content,
        content_sha256=digest_imported_bytes(content),
        source_lineage=tuple(SourceLineage(item.source_id, item.content_sha256, dict(item.identity)) for item in selection.sources),
        checks=checks,
        parent_revision_id=parent_revision_id,
    )


def select_final_documents(selection: TailorSelection, documents: Sequence[DocumentRevision], *, selected_by: str) -> FinalDocumentSelection:
    if not isinstance(selection, TailorSelection) or type(documents) not in (list, tuple):
        _fail("document_selection_invalid", "selection inputs are invalid")
    chosen = tuple(documents)
    if any(not isinstance(item, DocumentRevision) for item in chosen):
        _fail("document_selection_invalid", "final selection contains an invalid document")
    if {item.document_kind for item in chosen} != set(selection.requested_outputs):
        _fail("document_selection_invalid", "final selection must cover requested outputs exactly")
    return FinalDocumentSelection(selection.opportunity_id, selection.snapshot_id, chosen, selected_by)


def materialize_document_revision(root: Path, revision: DocumentRevision) -> Path:
    """Write one exact Markdown revision below a caller-owned private root."""
    if not isinstance(root, Path) or not isinstance(revision, DocumentRevision):
        _fail("document_materialization_invalid", "materialization inputs are invalid")
    try:
        root = root.resolve()
        destination = (root / revision.record_id / revision.revision_id / f"{revision.document_kind}.md").resolve()
        if root not in destination.parents:
            _fail("document_materialization_invalid", "document destination escapes private root")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            _fail("document_materialization_invalid", "document revision already exists")
        destination.write_bytes(revision.content)
    except (OSError, ValueError) as exc:
        raise ScoutDocumentError("document_materialization_invalid", "document revision could not be materialized") from exc
    return destination


def validate_generated_bundle(bundle: bytes, selection: TailorSelection) -> dict[str, object]:
    """Decode a model bundle and return bounded advisory checks per document."""
    if not isinstance(selection, TailorSelection) or type(bundle) is not bytes:
        _fail("document_output_invalid", "generated bundle inputs are invalid")
    try:
        docs = decode_tailoring_bundle(bundle, selection.requested_outputs)
    except Exception as exc:
        raise ScoutDocumentError("document_output_invalid", "generated document bundle is invalid") from exc
    result: dict[str, object] = {"status": "valid", "documents": {}}
    for kind, content in docs.items():
        result["documents"][kind] = {"content_sha256": digest_imported_bytes(content), "checks": check_document(content, kind)}  # type: ignore[index]
    return result


def render_safe_markdown(revision: DocumentRevision) -> str:
    """Render a local plaintext report; model text cannot activate links/assets."""
    if not isinstance(revision, DocumentRevision):
        _fail("document_render_invalid", "document revision is invalid")
    text = revision.content.decode("utf-8", errors="replace")
    # Keep a readable plain-text report rather than interpreting Markdown.
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    escaped = escaped.replace("[", "\\[").replace("]", "\\]").replace("(", "\\(").replace(")", "\\)")
    return f"Document: {revision.document_kind}\nDigest: {revision.content_sha256}\n\n{escaped}"


__all__ = ["DocumentRevision", "FinalDocumentSelection", "ScoutDocumentError", "SourceLineage", "materialize_document_revision", "prepare_document_revision", "render_safe_markdown", "select_final_documents", "validate_generated_bundle"]

"""Recognize private Scout provenance that cannot cross a clean package edge.

This is intentionally a narrow provenance gate, not a content classifier.  It
uses the accepted workpad path map and strict persisted-record contracts; an
arbitrary Markdown file or a file merely named ``text`` remains portable.
"""

from __future__ import annotations

import re

from .canonical import parse_json_bytes
from .validators import validate_serialized_contract


# These are private workpad roots in the accepted map.  They are not a ban on
# similarly named material below an arbitrary documentation path.
_PRIVATE_ROOTS = (
    "references/",
    "run-inputs/",
    "records/",
    "runs/",
    "run-plans/",
    "review-inputs/",
    "handoffs/",
    "scratch/",
    "reports/scout/",
    "manifests/",
)
_PRIVATE_EXACT_PATHS = frozenset({"indexes/context.json", "state.sqlite"})
_PRIVATE_DOCUMENT_PATH = re.compile(
    r"^docs/record_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-"
    r"[0-9a-f]{12}/revision_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab]"
    r"[0-9a-f]{3}-[0-9a-f]{12}/"
)

# A valid payload of one of these schemas is typed private provenance even if a
# caller renamed it into an otherwise legitimate portable source tree.
_PRIVATE_SCHEMAS = (
    "reference-record.schema.json",
    "run-input-record.schema.json",
    "private-record-revision.schema.json",
    "native-record-content.schema.json",
    "scout-operation-receipt.schema.json",
    "external-recording-invocation.schema.json",
    "external-recording-plan.schema.json",
    "external-recording-run.schema.json",
    "external-recording-checkpoint.schema.json",
    "external-recording-receipt.schema.json",
    "report.schema.json",
)


def private_package_provenance(relative_path: str, data: bytes) -> bool:
    """Return whether one inventoried file is recognizable private material.

    Callers deliberately receive no parsed payload or source excerpt.  A false
    result means only that this bounded guard has no recognized provenance; it
    is not a claim that transformed private prose is safe to export.
    """

    if (
        relative_path in _PRIVATE_EXACT_PATHS
        or relative_path.startswith(_PRIVATE_ROOTS)
        or _PRIVATE_DOCUMENT_PATH.match(relative_path) is not None
    ):
        return True
    try:
        parse_json_bytes(data)
    except (UnicodeDecodeError, ValueError):
        return False
    return any(
        validate_serialized_contract(schema, data).valid for schema in _PRIVATE_SCHEMAS
    )


__all__ = ["private_package_provenance"]

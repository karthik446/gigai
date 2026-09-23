"""Tiny tool-facing constructor for the C3 native-record operation shape.

Gig-owned Python may call this only after an explicit invocation.  It performs
no authority lookup, source loading, SQL work, or journal publication; those
remain in :mod:`gigai.scout.tools` and the native C1 publisher.
"""

from __future__ import annotations

from collections.abc import Mapping


class ScoutToolAdapterError(ValueError):
    """The Gig-owned entry returned an unsupported typed operation."""


def native_record_create_operation(*, operation_key: object, content: object) -> dict[str, object]:
    """Build the only operation shape currently admitted for a C3 tool entry."""
    if not isinstance(operation_key, str) or not operation_key:
        raise ScoutToolAdapterError("operation key is required")
    if not isinstance(content, Mapping):
        raise ScoutToolAdapterError("native content must be an object")
    return {"operation_key": operation_key, "content": dict(content)}


def _revision_target(*, operation_key: object, record_id: object, parent_revision: object) -> dict[str, str]:
    if not isinstance(operation_key, str) or not operation_key:
        raise ScoutToolAdapterError("operation key is required")
    if not isinstance(record_id, str) or not record_id:
        raise ScoutToolAdapterError("record ID is required")
    if not isinstance(parent_revision, str) or not parent_revision:
        raise ScoutToolAdapterError("parent revision is required")
    return {
        "operation_key": operation_key,
        "record_id": record_id,
        "parent_revision": parent_revision,
    }


def native_record_update_operation(
    *, operation_key: object, record_id: object, parent_revision: object, content: object
) -> dict[str, object]:
    """Build a CAS-bound append revision request for the approved entry."""
    operation = _revision_target(
        operation_key=operation_key,
        record_id=record_id,
        parent_revision=parent_revision,
    )
    if not isinstance(content, Mapping):
        raise ScoutToolAdapterError("native content must be an object")
    return {**operation, "content": dict(content)}


def native_record_archive_operation(
    *, operation_key: object, record_id: object, parent_revision: object
) -> dict[str, object]:
    """Build a tombstone append request; records are never destructively deleted."""
    return _revision_target(
        operation_key=operation_key,
        record_id=record_id,
        parent_revision=parent_revision,
    )


__all__ = [
    "ScoutToolAdapterError",
    "native_record_archive_operation",
    "native_record_create_operation",
    "native_record_update_operation",
]

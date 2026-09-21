"""Bundled Scout CRUD operation constructor.

This per-Gig source returns only the typed operation consumed by GigAI's
approved native-record publisher. It does not inspect authority, write SQL or
journal files, or create approval; the shared service owns all of those steps.
"""

from gigai.scout_tool_adapter import (
    native_record_archive_operation,
    native_record_create_operation,
    native_record_update_operation,
)


def build_native_record_operation(context):
    """Build one closed create, update, or archive native-record operation."""

    operation = context["operation"]
    values = context["input"]
    if operation == "record_create":
        return native_record_create_operation(
            operation_key=context["operation_key"], content=values["content"]
        )
    if operation == "record_update":
        return native_record_update_operation(
            operation_key=context["operation_key"],
            record_id=values["record_id"],
            parent_revision=values["parent_revision"],
            content=values["content"],
        )
    if operation == "record_archive":
        return native_record_archive_operation(
            operation_key=context["operation_key"],
            record_id=values["record_id"],
            parent_revision=values["parent_revision"],
        )
    raise ValueError("unsupported Scout native-record operation")

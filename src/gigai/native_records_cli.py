"""Unregistered Click group for Lane A native record operations.

The coordinator mounts :data:`native_record_group` on the top-level CLI after
the shared command registration and schema inventory review settle.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import click

from .private_records import PrivateRecordError
from .native_records import archive_native_record, create_native_record, create_task_override, list_native_records, native_context, read_native_record, update_native_record
from .setup import default_home_root
from .workpad import WorkpadError


def _fail(exc: Exception, *, as_json: bool) -> None:
    code = getattr(exc, "code", "native_record_invalid")
    if as_json:
        click.echo(json.dumps({"status": "error", "error": {"code": code, "message": str(exc)}}, sort_keys=True, separators=(",", ":")))
        raise click.exceptions.Exit(1)
    raise click.ClickException(str(exc))


def _content_file(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise PrivateRecordError("native_record_source_unsafe", "content must be one explicit regular JSON file")
    try:
        raw = path.read_bytes()
        parsed = json.loads(raw.decode("utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise PrivateRecordError("native_record_source_unsafe", "content file is not valid UTF-8 JSON") from exc
    if not isinstance(parsed, dict):
        raise PrivateRecordError("native_record_source_unsafe", "content file must contain one JSON object")
    return parsed


def _options(function: Any) -> Any:
    function = click.option("--gig")(function)
    function = click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))(function)
    function = click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))(function)
    return click.option("--json", "as_json", is_flag=True)(function)


@click.group("record", help="Create native private records through the settled C1 journal.")
def native_record_group() -> None:
    pass


@native_record_group.command("create")
@click.option("--content-file", required=True, type=click.Path(path_type=Path, dir_okay=False))
@click.option("--operation-key", required=True)
@click.option("--origin", default="user_reported", type=click.Choice(["user_reported", "imported", "inferred", "agent_supplied"]))
@click.option("--record-id")
@_options
def create_command(content_file: Path, operation_key: str, origin: str, record_id: str | None, gig: str | None, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    try:
        result = create_native_record(home_root=home_value or default_home_root(), requested_target=target_value, gig_id=gig, content=_content_file(content_file), actor={"kind": "operator", "id": "local-user"}, origin=origin, operation_key=operation_key, record_id=record_id)
    except (PrivateRecordError, WorkpadError, OSError, TypeError, ValueError) as exc:
        _fail(exc, as_json=as_json)
        return
    payload = {"ok": True, "record_id": result.record_id, "revision_id": result.revision_id, "created": result.created, "state": result.state, "task_context_id": result.task_context_id, "projection_pending": result.projection_pending, "rebuild_action": result.rebuild_action}
    click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")) if as_json else f"Committed native record {result.record_id} at {result.revision_id}.")


@native_record_group.command("override")
@click.option("--content-file", required=True, type=click.Path(path_type=Path, dir_okay=False))
@click.option("--base-record", required=True)
@click.option("--base-revision", required=True)
@click.option("--task-context")
@click.option("--operation-key", required=True)
@click.option("--origin", default="user_reported", type=click.Choice(["user_reported", "imported", "inferred", "agent_supplied"]))
@_options
def override_command(content_file: Path, base_record: str, base_revision: str, task_context: str | None, operation_key: str, origin: str, gig: str | None, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    try:
        scope = {"mode": "run_override", "task_context_id": task_context, "base": {"record_id": base_record, "revision_id": base_revision}} if task_context else None
        result = create_task_override(home_root=home_value or default_home_root(), requested_target=target_value, gig_id=gig, content=_content_file(content_file), actor={"kind": "operator", "id": "local-user"}, origin=origin, operation_key=operation_key, base={"record_id": base_record, "revision_id": base_revision}, scope=scope)
    except (PrivateRecordError, WorkpadError, OSError, TypeError, ValueError) as exc:
        _fail(exc, as_json=as_json)
        return
    payload = {"ok": True, "record_id": result.record_id, "revision_id": result.revision_id, "task_context_id": result.task_context_id, "created": result.created, "projection_pending": result.projection_pending}
    click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")) if as_json else f"Committed isolated task override {result.record_id}.")


@native_record_group.command("update")
@click.option("--id", "record_id", required=True)
@click.option("--parent-revision", required=True)
@click.option("--content-file", required=True, type=click.Path(path_type=Path, dir_okay=False))
@click.option("--operation-key", required=True)
@click.option("--origin", default="user_reported", type=click.Choice(["user_reported", "imported", "inferred", "agent_supplied"]))
@_options
def update_command(record_id: str, parent_revision: str, content_file: Path, operation_key: str, origin: str, gig: str | None, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    try:
        result = update_native_record(home_root=home_value or default_home_root(), requested_target=target_value, gig_id=gig, record_id=record_id, parent_revision=parent_revision, content=_content_file(content_file), actor={"kind": "operator", "id": "local-user"}, origin=origin, operation_key=operation_key)
    except (PrivateRecordError, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json)
        return
    click.echo(json.dumps({"ok": True, "record_id": result.record_id, "revision_id": result.revision_id, "created": result.created}, sort_keys=True, separators=(",", ":")) if as_json else f"Updated native record {record_id}.")


@native_record_group.command("archive")
@click.option("--id", "record_id", required=True)
@click.option("--parent-revision", required=True)
@click.option("--operation-key", required=True)
@_options
def archive_command(record_id: str, parent_revision: str, operation_key: str, gig: str | None, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    try:
        result = archive_native_record(home_root=home_value or default_home_root(), requested_target=target_value, gig_id=gig, record_id=record_id, parent_revision=parent_revision, actor={"kind": "operator", "id": "local-user"}, operation_key=operation_key)
    except (PrivateRecordError, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json)
        return
    click.echo(json.dumps({"ok": True, "record_id": result.record_id, "revision_id": result.revision_id, "state": result.state}, sort_keys=True, separators=(",", ":")) if as_json else f"Archived native record {record_id}.")


@native_record_group.command("list")
@click.option("--include-archived", is_flag=True)
@_options
def list_command(include_archived: bool, gig: str | None, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    try:
        records = list_native_records(home_root=home_value or default_home_root(), requested_target=target_value, gig_id=gig, include_archived=include_archived)
    except (PrivateRecordError, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json)
        return
    click.echo(json.dumps({"ok": True, "records": records}, sort_keys=True, separators=(",", ":")) if as_json else "\n".join(str(item["record_id"]) for item in records))


@native_record_group.command("read")
@click.option("--id", "record_id", required=True)
@click.option("--revision", "revision_id")
@click.option("--content", "include_content", is_flag=True)
@_options
def read_command(record_id: str, revision_id: str | None, include_content: bool, gig: str | None, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    try:
        value = read_native_record(home_root=home_value or default_home_root(), requested_target=target_value, gig_id=gig, record_id=record_id, revision_id=revision_id, content=include_content)
    except (PrivateRecordError, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json)
        return
    if include_content:
        raw = value.pop("content")
        assert isinstance(raw, bytes)
        sys.stdout.buffer.write(raw)
    else:
        click.echo(json.dumps(value, sort_keys=True, separators=(",", ":")) if as_json else f"{value['record_id']} {value['revision_id']} {value['kind']}")


@native_record_group.command("context")
@_options
def context_command(gig: str | None, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    try:
        value = native_context(home_root=home_value or default_home_root(), requested_target=target_value, gig_id=gig)
    except (PrivateRecordError, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json)
        return
    click.echo(json.dumps(value, sort_keys=True, separators=(",", ":")))


__all__ = ["native_record_group"]

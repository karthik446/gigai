"""Standalone, local Scout interview command adapter.

The main CLI registers this group in the release lane.  Keeping the adapter
usable as ``python -m gigai.scout.interview_cli`` gives fixtures and copied
wrappers a supported entry without adding another agent loop.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from .interview_records import (
    ScoutInterviewError,
    list_interview_preparations,
    prepare_interview,
    read_interview_preparation,
    revise_interview,
    save_interview_feedback,
)


def _json_file(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise click.ClickException("input file must be one regular local file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise click.ClickException("input file must be UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise click.ClickException("input file must contain one JSON object")
    return value


def _emit(value: Any, as_json: bool) -> None:
    click.echo(json.dumps(value, ensure_ascii=False, sort_keys=True) if as_json else json.dumps(value, ensure_ascii=False, indent=2))


@click.group("scout-interview")
def interview_group() -> None:
    """Prepare and continue a local Scout interview."""


@interview_group.command("prepare")
@click.option("--selection-file", required=True, type=click.Path(path_type=Path, dir_okay=False))
@click.option("--content-file", required=True, type=click.Path(path_type=Path, dir_okay=False))
@click.option("--operation-key", required=True)
@click.option("--home", required=True, type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", type=click.Path(path_type=Path, file_okay=False))
@click.option("--gig", required=True)
@click.option("--run", "execute_run", is_flag=True, help="Execute the approved prepare-interview Graph and persist a completed Run.")
@click.option("--confirm", is_flag=True, help="Confirm the local Graph-owned Run.")
@click.option("--json", "as_json", is_flag=True)
def prepare_command(selection_file: Path, content_file: Path, operation_key: str, home: Path, target: Path | None, gig: str, execute_run: bool, confirm: bool, as_json: bool) -> None:
    """Create preparation, or execute it through the approved prepare-interview Graph."""
    try:
        selection = _json_file(selection_file)
        content = _json_file(content_file)
        if execute_run:
            if not confirm:
                raise click.ClickException("consent_required: Graph execution requires --confirm")
            from ..run import InterviewRunRequest, launch_run
            run = launch_run(
                home_root=home,
                requested_target=target,
                gig_id=gig,
                wait=True,
                interview_execution=InterviewRunRequest(
                    graph_selector="prepare-interview",
                    selection=selection,
                    content=content,
                    operation_key=operation_key,
                ),
                operator_consent={
                    "schema_version": "1.0",
                    "kind": "operator_run_consent",
                    "action": "run",
                    "actor": {"kind": "operator", "id": "local-user"},
                    "source": "direct_cli_confirm",
                },
            )
            _emit({"status": run.status, "run_id": run.run_id}, as_json)
            return
        result = prepare_interview(
            home_root=home, requested_target=target, gig_id=gig,
            selection=selection, content=content, operation_key=operation_key,
        )
        _emit({"status": "recorded", "record_id": result.record_id, "revision_id": result.revision_id, "created": result.created}, as_json)
    except ScoutInterviewError as exc:
        raise click.ClickException(f"{exc.code}: {exc}") from exc


@interview_group.command("read")
@click.option("--record-id", required=True)
@click.option("--revision-id")
@click.option("--home", required=True, type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", type=click.Path(path_type=Path, file_okay=False))
@click.option("--gig", required=True)
@click.option("--json", "as_json", is_flag=True)
def read_command(record_id: str, revision_id: str | None, home: Path, target: Path | None, gig: str, as_json: bool) -> None:
    """Read one committed preparation revision."""
    try:
        result = read_interview_preparation(home_root=home, requested_target=target, gig_id=gig, record_id=record_id, revision_id=revision_id)
        _emit(result, as_json)
    except ScoutInterviewError as exc:
        raise click.ClickException(f"{exc.code}: {exc}") from exc


@interview_group.command("list")
@click.option("--home", required=True, type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", type=click.Path(path_type=Path, file_okay=False))
@click.option("--gig", required=True)
@click.option("--json", "as_json", is_flag=True)
def list_command(home: Path, target: Path | None, gig: str, as_json: bool) -> None:
    """List preparation metadata without private content."""
    try:
        _emit(list_interview_preparations(home_root=home, requested_target=target, gig_id=gig), as_json)
    except ScoutInterviewError as exc:
        raise click.ClickException(f"{exc.code}: {exc}") from exc


@interview_group.command("feedback")
@click.option("--record-id", required=True)
@click.option("--parent-revision", required=True)
@click.option("--feedback-file", required=True, type=click.Path(path_type=Path, dir_okay=False))
@click.option("--operation-key", required=True)
@click.option("--home", required=True, type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", type=click.Path(path_type=Path, file_okay=False))
@click.option("--gig", required=True)
@click.option("--json", "as_json", is_flag=True)
def feedback_command(record_id: str, parent_revision: str, feedback_file: Path, operation_key: str, home: Path, target: Path | None, gig: str, as_json: bool) -> None:
    """Save user practice feedback as a new immutable revision."""
    value = _json_file(feedback_file)
    entries = value.get("feedback")
    if not isinstance(entries, list):
        raise click.ClickException("feedback file must contain a feedback list")
    try:
        result = save_interview_feedback(
            home_root=home, requested_target=target, gig_id=gig, record_id=record_id,
            parent_revision=parent_revision, feedback=entries, operation_key=operation_key,
        )
        _emit({"status": "recorded", "record_id": result.record_id, "revision_id": result.revision_id, "created": result.created}, as_json)
    except ScoutInterviewError as exc:
        raise click.ClickException(f"{exc.code}: {exc}") from exc


@interview_group.command("revise")
@click.option("--record-id", required=True)
@click.option("--parent-revision", required=True)
@click.option("--content-file", required=True, type=click.Path(path_type=Path, dir_okay=False))
@click.option("--operation-key", required=True)
@click.option("--home", required=True, type=click.Path(path_type=Path, file_okay=False))
@click.option("--target", type=click.Path(path_type=Path, file_okay=False))
@click.option("--gig", required=True)
@click.option("--json", "as_json", is_flag=True)
def revise_command(record_id: str, parent_revision: str, content_file: Path, operation_key: str, home: Path, target: Path | None, gig: str, as_json: bool) -> None:
    """Append revised preparation content after a fresh session."""
    try:
        result = revise_interview(
            home_root=home, requested_target=target, gig_id=gig, record_id=record_id,
            parent_revision=parent_revision, content=_json_file(content_file), operation_key=operation_key,
        )
        _emit({"status": "recorded", "record_id": result.record_id, "revision_id": result.revision_id, "created": result.created}, as_json)
    except ScoutInterviewError as exc:
        raise click.ClickException(f"{exc.code}: {exc}") from exc


if __name__ == "__main__":
    interview_group()


__all__ = ["interview_group"]

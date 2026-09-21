"""Explicit local CLI for Scout definition and private transfer archives."""

from __future__ import annotations

import json
from pathlib import Path

import click

from .private_transfer import (
    PrivateTransferError,
    backup_private,
    export_definition,
    import_definition,
    restore_private,
)


def _emit(value: object, as_json: bool) -> None:
    click.echo(json.dumps(value, sort_keys=True) if as_json else json.dumps(value, indent=2, sort_keys=True))


def _result(result: object) -> dict[str, object]:
    return {
        "archive": str(result.archive), "kind": result.kind, "files": list(result.files),
        "project_id": result.project_id, "gig_id": result.gig_id,
    }


@click.group("scout-transfer")
def transfer_group() -> None:
    """Export/import definitions or explicitly transfer private Scout history."""


@transfer_group.command("export-definition")
@click.option("--workpad", required=True, type=click.Path(path_type=Path, file_okay=False))
@click.option("--archive", required=True, type=click.Path(path_type=Path, dir_okay=False))
@click.option("--json", "as_json", is_flag=True)
def export_definition_command(workpad: Path, archive: Path, as_json: bool) -> None:
    try:
        _emit(_result(export_definition(workpad=workpad, destination=archive)), as_json)
    except PrivateTransferError as exc:
        raise click.ClickException(f"{exc.code}: {exc}") from exc


@transfer_group.command("import-definition")
@click.option("--archive", required=True, type=click.Path(path_type=Path, dir_okay=False))
@click.option("--destination", required=True, type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def import_definition_command(archive: Path, destination: Path, as_json: bool) -> None:
    try:
        _emit(_result(import_definition(archive=archive, destination=destination)), as_json)
    except PrivateTransferError as exc:
        raise click.ClickException(f"{exc.code}: {exc}") from exc


@transfer_group.command("backup-private")
@click.option("--workpad", required=True, type=click.Path(path_type=Path, file_okay=False))
@click.option("--archive", required=True, type=click.Path(path_type=Path, dir_okay=False))
@click.option("--project-id")
@click.option("--gig-id")
@click.option("--path", "selected_paths", multiple=True)
@click.option("--confirm", is_flag=True, help="Confirm that selected history is private and local-only.")
@click.option("--json", "as_json", is_flag=True)
def backup_private_command(workpad: Path, archive: Path, project_id: str | None, gig_id: str | None, selected_paths: tuple[str, ...], confirm: bool, as_json: bool) -> None:
    if not confirm:
        raise click.ClickException("backup-private requires --confirm")
    try:
        _emit(_result(backup_private(workpad=workpad, destination=archive, project_id=project_id, gig_id=gig_id, selected_paths=selected_paths or None)), as_json)
    except PrivateTransferError as exc:
        raise click.ClickException(f"{exc.code}: {exc}") from exc


@transfer_group.command("restore-private")
@click.option("--archive", required=True, type=click.Path(path_type=Path, dir_okay=False))
@click.option("--destination", required=True, type=click.Path(path_type=Path, file_okay=False))
@click.option("--expected-project-id")
@click.option("--expected-gig-id")
@click.option("--confirm", is_flag=True, help="Confirm that this is an explicit local private restore.")
@click.option("--json", "as_json", is_flag=True)
def restore_private_command(archive: Path, destination: Path, expected_project_id: str | None, expected_gig_id: str | None, confirm: bool, as_json: bool) -> None:
    if not confirm:
        raise click.ClickException("restore-private requires --confirm")
    try:
        _emit(_result(restore_private(archive=archive, destination=destination, expected_project_id=expected_project_id, expected_gig_id=expected_gig_id)), as_json)
    except PrivateTransferError as exc:
        raise click.ClickException(f"{exc.code}: {exc}") from exc


if __name__ == "__main__":
    transfer_group()


__all__ = ["transfer_group"]

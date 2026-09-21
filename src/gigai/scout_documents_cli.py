"""Explicit local Scout document review actions.

Selection is an operator action, not a model output.  The command accepts
only committed revision identities and a host-owned Tailor result identity;
``record_final_selection`` reauthenticates both before it writes anything.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from .canonical import parse_json_bytes
from .scout_document_records import (
    ScoutDocumentRecordError,
    read_document_revision,
    read_final_selection,
    record_final_selection,
)
from .scout_documents import FinalDocumentSelection
from .setup import default_home_root
from .workpad import WorkpadError, resolve_workpad


@click.group("scout-documents")
def document_group() -> None:
    """Review and explicitly select authenticated Scout Tailor documents."""


def _selector(raw: str) -> dict[str, object]:
    try:
        value = parse_json_bytes(raw.encode("utf-8"))
    except Exception as exc:
        raise click.ClickException("document selector must be one JSON object") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"document_kind", "record_id", "revision_id"}
        or not all(isinstance(value.get(key), str) for key in ("document_kind", "record_id", "revision_id"))
    ):
        raise click.ClickException("document selector must name document_kind, record_id, and revision_id")
    return value


@document_group.command("final-select")
@click.option("--document", "document_selectors", multiple=True, required=True, help="JSON selector; repeat for resume and cover_letter.")
@click.option("--run-id", required=True, help="The committed Tailor Run that generated the selected revisions.")
@click.option("--goal-id", required=True, help="The committed Tailor Goal that generated the selected revisions.")
@click.option("--invocation-id", required=True, help="The committed local invocation that generated the selected revisions.")
@click.option("--output-sha256", required=True, help="Digest of the committed Tailor output bundle.")
@click.option("--operation-key", required=True)
@click.option("--selected-by", default="local-user", show_default=True)
@click.option("--confirm", is_flag=True, help="Record explicit operator selection.")
@click.option("--gig", required=True)
@click.option("--target", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def final_select_command(
    document_selectors: tuple[str, ...], run_id: str, goal_id: str,
    invocation_id: str, output_sha256: str, operation_key: str,
    selected_by: str, confirm: bool, gig: str, target: Path | None,
    home: Path | None, as_json: bool,
) -> None:
    """Persist a final selection after authenticating its completed Tailor Run."""
    if not confirm:
        raise click.ClickException("final document selection requires --confirm")
    try:
        resolved = resolve_workpad(
            home_root=home or default_home_root(), requested_target=target,
            gig_id=gig, allow_semantic_state=True,
        )
        selectors = [_selector(item) for item in document_selectors]
        docs = tuple(
            read_document_revision(
                resolved=resolved, record_id=str(item["record_id"]),
                revision_id=str(item["revision_id"]), document_kind=str(item["document_kind"]),
            )
            for item in selectors
        )
        if not docs:
            raise ScoutDocumentRecordError("document_selection_invalid", "at least one document is required")
        selection = FinalDocumentSelection(docs[0].opportunity_id, docs[0].snapshot_id, docs, selected_by)
        result = record_final_selection(
            resolved=resolved, selection=selection,
            invocation={
                "authority": "tailor_run", "run_id": run_id, "goal_id": goal_id,
                "invocation_id": invocation_id, "output_sha256": output_sha256,
            }, operation_key=operation_key,
        )
        # Return the committed descriptor rather than the pre-write DTO.  The
        # strict v2 source-run metadata is host-authenticated by the writer.
        persisted = read_final_selection(
            resolved=resolved,
            opportunity_id=selection.opportunity_id,
            snapshot_id=selection.snapshot_id,
        )
        payload = {"status": "recorded", "created": result.created, "selection": persisted}
    except (ScoutDocumentRecordError, WorkpadError, OSError, ValueError) as exc:
        payload = {"status": "error", "error": {"code": getattr(exc, "code", "document_selection_invalid"), "message": str(exc)}}
        if as_json:
            click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
            raise click.exceptions.Exit(1)
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")) if as_json else f"Final Scout documents selected ({'replayed' if not result.created else 'recorded'}).")


@document_group.command("read-selection")
@click.option("--opportunity", required=True)
@click.option("--snapshot", required=True)
@click.option("--gig", required=True)
@click.option("--target", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def read_selection_command(opportunity: str, snapshot: str, gig: str, target: Path | None, home: Path | None, as_json: bool) -> None:
    """Read one authenticated immutable final selection."""
    try:
        resolved = resolve_workpad(home_root=home or default_home_root(), requested_target=target, gig_id=gig, allow_semantic_state=True)
        value = read_final_selection(resolved=resolved, opportunity_id=opportunity, snapshot_id=snapshot)
    except (ScoutDocumentRecordError, WorkpadError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(value, sort_keys=True, separators=(",", ":")) if as_json else str(value))


__all__ = ["document_group", "final_select_command", "read_selection_command"]

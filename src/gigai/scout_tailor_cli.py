"""Small local CLI façade for caller-supplied Tailor DTOs.

Commands accept canonical JSON strings rather than paths or URLs. They wrap
pure helpers and do not resolve sources, invoke a model, write journal events,
or submit an application.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import click

from .canonical import canonical_json_bytes, digest_imported_bytes
from .scout_documents import validate_generated_bundle
from .scout_tailor_selection import TailorSelection, TailorSource


def _json(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise click.ClickException("input JSON is invalid") from exc
    if not isinstance(parsed, dict):
        raise click.ClickException("input JSON must be an object")
    return parsed


@click.group("tailor")
def tailor_cli() -> None:
    """Prepare or select private Tailor documents from supplied DTOs."""


@tailor_cli.command("digest")
@click.argument("content_base64")
def digest_command(content_base64: str) -> None:
    """Return a digest for caller-supplied bytes (no path reads)."""
    try:
        content = base64.b64decode(content_base64, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise click.ClickException("content encoding is invalid") from exc
    click.echo(json.dumps({"content_sha256": digest_imported_bytes(content)}, sort_keys=True))


@tailor_cli.command("canonical")
@click.argument("value_json")
def canonical_command(value_json: str) -> None:
    """Canonicalize one JSON DTO supplied on the command line."""
    value = _json(value_json)
    click.echo(canonical_json_bytes(value).decode("utf-8"))


def _selection(value: str) -> TailorSelection:
    raw = _json(value)
    try:
        opportunity = raw["opportunity"]
        sources = tuple(
            TailorSource(
                item["source_id"], item["purpose"],
                base64.b64decode(item["content_base64"], validate=True),
                item["content_sha256"], item["identity"],
            )
            for item in raw["sources"]
        )
        return TailorSelection(
            opportunity["opportunity_id"], opportunity["snapshot_id"],
            tuple(raw["requested_outputs"]), sources,
        )
    except Exception as exc:
        raise click.ClickException("service selection DTO is invalid") from exc


@tailor_cli.command("validate-bundle")
@click.argument("selection_json")
@click.argument("bundle_base64")
def validate_bundle_command(selection_json: str, bundle_base64: str) -> None:
    """Validate one supplied model bundle through the document service.

    Source descriptors are caller-supplied for validation only and do not
    establish journal authority; production callers must hydrate them first.
    """
    selection = _selection(selection_json)
    try:
        bundle = base64.b64decode(bundle_base64, validate=True)
        report = validate_generated_bundle(bundle, selection)
    except Exception as exc:
        raise click.ClickException("generated bundle is invalid") from exc
    click.echo(json.dumps(report, sort_keys=True))


def main() -> None:
    tailor_cli()


__all__ = ["main", "tailor_cli"]

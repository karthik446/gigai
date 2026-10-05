"""0.1.10.8 PA: ``gigai scout resume check|clean`` -- the local contact-details gate for a resume FILE.

Both commands read and write ONLY the paths given: no GigAI home, no Scout folder, no model, no
network. They answer by kind and line number and never print a value. ``check`` uses the import's
own detector (``resume_pii.contact_findings``); ``clean`` writes what the import would store
(``resume_pii.strip_contact_lines``, plus any line the check still flags).
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from .resume_import import RESUME_MAX_BYTES, RESUME_MEDIA_TYPE_MESSAGE, RESUME_SUFFIXES
from .resume_pii import RESUME_WARNING, ContactFinding, clean_contact_text, contact_findings
from .resume_privacy import HeadingOnlyLink

EXIT_FOUND = 2
#: The one sentence that says the check is pattern-based (the import warning's own words).
CAN_MISS = RESUME_WARNING.split(". ")[1] + "."
CLEAN_REMINDER = "Glance at the cleaned file before you use it."


class _FileError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _read(path: Path) -> str:
    if path.suffix.lower() not in RESUME_SUFFIXES:
        raise _FileError("resume_media_type_unsupported", RESUME_MEDIA_TYPE_MESSAGE)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise _FileError("resume_file_missing", f"{path} does not exist") from None
    except OSError as exc:
        raise _FileError("resume_file_unreadable", f"{path} cannot be read ({exc.strerror or type(exc).__name__})") from None
    if len(data) > RESUME_MAX_BYTES:
        raise _FileError("resume_file_too_large", f"{path} is larger than {RESUME_MAX_BYTES} bytes")
    if b"\0" in data:
        raise _FileError("resume_file_binary", f"{path} is not a text file")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise _FileError("resume_file_binary", f"{path} is not UTF-8 text") from None


def _counts(findings: list[ContactFinding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.kind] = counts.get(finding.kind, 0) + 1
    return counts


def _rows(findings: list[ContactFinding]) -> list[dict[str, object]]:
    return [{"kind": finding.kind, "line": finding.line} for finding in findings]


def _lines(findings: list[ContactFinding]) -> str:
    return "\n".join(f"  line {finding.line}: {finding.kind.replace('_', ' ')}" for finding in findings)


def _error(exc: _FileError, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps({"ok": False, "error": {"code": exc.code, "message": str(exc)}}, sort_keys=True, separators=(",", ":")))
        raise click.exceptions.Exit(1)
    raise click.ClickException(str(exc))


@click.command("check")
@click.argument("path", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--json", "as_json", is_flag=True)
def resume_check_command(path: Path, as_json: bool) -> None:
    """Check a resume FILE (.md/.txt) for contact details: kinds and line numbers, never values.

    Local: no model, no network, no GigAI home. Exit 0 when nothing is found, 2 when something is.
    """

    try:
        findings = contact_findings(_read(path))
    except _FileError as exc:
        _error(exc, as_json)
        return
    if as_json:
        click.echo(json.dumps(
            {"ok": True, "clean": not findings, "findings": _rows(findings), "counts": _counts(findings), "note": CAN_MISS},
            sort_keys=True, separators=(",", ":"),
        ))
    elif findings:
        click.echo(f"Contact details found in {path.name}:\n{_lines(findings)}\n{CAN_MISS}")
    else:
        click.echo(f"No contact details found in {path.name}. {CAN_MISS}")
    if findings:
        raise click.exceptions.Exit(EXIT_FOUND)


@click.command("clean")
@click.argument("path", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--out", "out", required=True, type=click.Path(path_type=Path, dir_okay=False), help="The cleaned copy to write.")
@click.option("--force", is_flag=True, help="Replace --out if it exists.")
@click.option("--json", "as_json", is_flag=True)
def resume_clean_command(path: Path, out: Path, force: bool, as_json: bool) -> None:
    """Write a copy of a resume FILE with its contact lines removed (the import's own strip).

    Prints kinds and line numbers removed, never values. The input is never modified.
    """

    try:
        text = _read(path)
        if out.resolve(strict=False) == path.resolve(strict=False):
            raise _FileError("resume_out_is_input", "--out must be a different file than the input")
        if out.exists() and not force:
            raise _FileError("resume_out_exists", f"{out} already exists; pass --force to replace it")
        findings = contact_findings(text)
        try:
            cleaned = clean_contact_text(text)
        except HeadingOnlyLink as exc:
            # The import's own refusal (``resume add`` gives the same one): by line number, never the heading's text.
            raise _FileError("resume_heading_only_link", f"{exc}, then clean the file again. Nothing was written.") from None
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(cleaned, encoding="utf-8")
        except OSError as exc:
            raise _FileError("resume_out_unwritable", f"{out} cannot be written ({exc.strerror or type(exc).__name__})") from None
    except _FileError as exc:
        _error(exc, as_json)
        return
    if as_json:
        click.echo(json.dumps(
            {"ok": True, "out": str(out), "removed": _rows(findings), "counts": _counts(findings), "note": CLEAN_REMINDER},
            sort_keys=True, separators=(",", ":"),
        ))
        return
    removed = f"Removed from {path.name}:\n{_lines(findings)}" if findings else f"Nothing to remove in {path.name}."
    click.echo(f"{removed}\nWrote {out}. {CLEAN_REMINDER}")

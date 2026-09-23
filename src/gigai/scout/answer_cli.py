"""Explicit local saved-question/answer command for Scout.

Answers are revisions of the authenticated native ``experience_qa`` record;
the command never stores an answer in a separate mutable table.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import click

from ..canonical import parse_json_bytes
from ..native_records import read_native_record, update_native_record
from ..private_records import PrivateRecordError
from ..setup import default_home_root
from ..workpad import WorkpadError


@click.group("scout-answer")
def answer_group() -> None:
    """Save explicit user answers as immutable native experience revisions."""


@answer_group.command("save")
@click.option("--record-id", required=True)
@click.option("--parent-revision", required=True)
@click.option("--question-id", required=True)
@click.option("--answer-file", required=True, type=click.Path(path_type=Path, dir_okay=False), help="UTF-8 answer text file; content is never echoed.")
@click.option("--operation-key", required=True)
@click.option("--confirm", is_flag=True, help="Record explicit operator answer.")
@click.option("--gig", required=True)
@click.option("--target", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def save_answer_command(
    record_id: str, parent_revision: str, question_id: str, answer_file: Path,
    operation_key: str, confirm: bool, gig: str, target: Path | None,
    home: Path | None, as_json: bool,
) -> None:
    """Append one answered question to an existing native experience record."""
    if not confirm:
        raise click.ClickException("saving a Scout answer requires --confirm")
    try:
        if answer_file.is_symlink() or not answer_file.is_file():
            raise PrivateRecordError("answer_source_unsafe", "answer file must be one regular local file")
        answer = answer_file.read_text(encoding="utf-8")
        if not answer.strip() or len(answer) > 16_000:
            raise PrivateRecordError("answer_invalid", "answer text is empty or exceeds the bound")
        base = read_native_record(
            home_root=home or default_home_root(), requested_target=target,
            gig_id=gig, record_id=record_id, revision_id=parent_revision, content=True,
        )
        raw = base.get("content")
        if not isinstance(raw, bytes):
            raise PrivateRecordError("answer_invalid", "native answer source is unavailable")
        value = parse_json_bytes(raw)
        if not isinstance(value, dict) or value.get("kind") != "experience_qa" or not isinstance(value.get("payload"), dict):
            raise PrivateRecordError("answer_source_invalid", "selected native record is not experience_qa")
        updated = copy.deepcopy(value)
        questions = updated["payload"].get("questions")
        if not isinstance(questions, list):
            raise PrivateRecordError("answer_source_invalid", "experience questions are unavailable")
        question = next((item for item in questions if isinstance(item, dict) and item.get("question_id") == question_id), None)
        if question is None:
            raise PrivateRecordError("answer_question_missing", "question is not present in the selected revision")
        question["state"] = "answered"
        question["answer"] = answer
        question["provenance"] = {"kind": "user_reported", "source_refs": []}
        result = update_native_record(
            home_root=home or default_home_root(), requested_target=target,
            gig_id=gig, record_id=record_id, parent_revision=parent_revision,
            content=updated, actor={"kind": "operator", "id": "local-user"},
            origin="user_reported", operation_key=operation_key,
        )
    except (PrivateRecordError, WorkpadError, OSError, UnicodeError, ValueError) as exc:
        payload = {"status": "error", "error": {"code": getattr(exc, "code", "answer_invalid"), "message": str(exc)}}
        if as_json:
            click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
            raise click.exceptions.Exit(1)
        raise click.ClickException(str(exc)) from exc
    payload = {"status": "recorded", "record_id": result.record_id, "revision_id": result.revision_id, "parent_revision": parent_revision, "question_id": question_id}
    click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")) if as_json else f"Saved answer for {question_id} at {result.revision_id}.")


__all__ = ["answer_group", "save_answer_command"]

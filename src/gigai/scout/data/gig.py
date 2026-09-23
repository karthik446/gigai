#!/usr/bin/env python3
"""Small user-facing Scout command copied into each private Gig.

This wrapper owns command discovery and typed argument decoding only.  It does
not contain Scout domain logic, SQL, journal publication, or HTTP transport;
the wrapper authenticates its registered Gig location and the shared GigAI
service owns validation, locking, and publication.  A copied wrapper is
editable per Gig, but its ``create`` operation remains eligible only when the
selected capability has separate explicit approval.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import stat
from typing import Any


class CommandError(RuntimeError):
    """A typed, machine-readable wrapper refusal or invalid input."""

    def __init__(self, code: str, message: str, next_action: str) -> None:
        super().__init__(message)
        self.code = code
        self.next_action = next_action


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read or explicitly invoke this private Scout Gig. "
            "The copied wrapper location selects the Gig; the target validates its project."
        )
    )
    parser.add_argument("--home", required=True, type=Path, help="GigAI home root")
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help="bound project target (defaults to the current working directory)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    list_command = commands.add_parser("list", help="list current native Scout records")
    list_command.add_argument(
        "--include-archived", action="store_true", help="include archived records"
    )

    read_command = commands.add_parser("read", help="read one validated native record")
    read_command.add_argument("record_id", help="record identity from list/context output")
    read_command.add_argument("--revision-id", default=None)
    read_command.add_argument(
        "--content", action="store_true", help="include authenticated content bytes"
    )

    commands.add_parser("context", help="read the payload-free current Scout context")

    create_command = commands.add_parser(
        "create", help="invoke one already-approved agent tool record-create entry"
    )
    create_command.add_argument("--capability-id", required=True)
    create_command.add_argument("--actor-id", required=True, help="agent actor identity")
    create_command.add_argument("--operation-key", required=True)
    create_command.add_argument(
        "--input-json", required=True, help="JSON object passed to the approved entry"
    )

    update_command = commands.add_parser(
        "update", help="invoke one already-approved agent tool record-update entry"
    )
    archive_command = commands.add_parser(
        "archive", help="invoke one already-approved agent tool record-archive entry"
    )
    for command, include_content in ((update_command, True), (archive_command, False)):
        command.add_argument("record_id", help="record identity from list/context output")
        command.add_argument("--parent-revision", required=True)
        command.add_argument("--capability-id", required=True)
        command.add_argument("--actor-id", required=True, help="agent actor identity")
        command.add_argument("--operation-key", required=True)
        if include_content:
            command.add_argument(
                "--input-json", required=True, help="JSON object passed to the approved entry"
            )
    proposal_command = commands.add_parser(
        "proposal", help="run one explicitly confirmed local proposal assessment"
    )
    proposal_command.add_argument("--model-target", required=True)
    proposal_command.add_argument("--posting-selector", required=True)
    proposal_command.add_argument("--private-selector", action="append", required=True)
    proposal_command.add_argument("--confirm", action="store_true", help="confirm private local assessment")
    proposal_command.add_argument("--wait", action="store_true")

    tailor_command = commands.add_parser(
        "tailor", help="generate explicitly requested private Tailor documents"
    )
    tailor_command.add_argument("--model-target", required=True)
    tailor_command.add_argument("--posting-selector", required=True)
    tailor_command.add_argument("--private-selector", action="append", required=True)
    tailor_command.add_argument("--answer-selector", action="append", default=[])
    tailor_command.add_argument("--output", action="append", choices=("resume", "cover_letter"), default=["resume"])
    tailor_command.add_argument("--proposal-record")
    tailor_command.add_argument("--proposal-revision")
    tailor_command.add_argument("--confirm", action="store_true", help="confirm private local Tailor run")
    tailor_command.add_argument("--wait", action="store_true")

    answer_command = commands.add_parser(
        "answer", help="save one explicit answer as an experience_qa revision"
    )
    answer_command.add_argument("--record-id", required=True)
    answer_command.add_argument("--parent-revision", required=True)
    answer_command.add_argument("--question-id", required=True)
    answer_command.add_argument("--answer-file", required=True)
    answer_command.add_argument("--operation-key", required=True)
    answer_command.add_argument("--confirm", action="store_true")

    selection_command = commands.add_parser(
        "final-select", help="select committed Tailor documents for review"
    )
    selection_command.add_argument("--document", action="append", required=True, help="JSON document selector; repeat per output")
    selection_command.add_argument("--run-id", required=True)
    selection_command.add_argument("--goal-id", required=True)
    selection_command.add_argument("--invocation-id", required=True)
    selection_command.add_argument("--output-sha256", required=True)
    selection_command.add_argument("--operation-key", required=True)
    selection_command.add_argument("--selected-by", default="local-user")
    selection_command.add_argument("--confirm", action="store_true")

    report_command = commands.add_parser("report", help="generate or inspect the local journal-derived Scout report")
    report_command.add_argument("operation", choices=("generate", "status"))

    acquisition_command = commands.add_parser(
        "acquisition", help="import or inspect already-acquired public Scout rows"
    )
    acquisition_subcommands = acquisition_command.add_subparsers(dest="acquisition_operation", required=True)
    acquisition_import = acquisition_subcommands.add_parser("import")
    acquisition_import.add_argument("--batch-id", required=True)
    acquisition_import.add_argument("--rows-file", "--input-file", dest="rows_file", required=True, type=Path)
    acquisition_import.add_argument("--deadline-seconds", type=float, default=30.0)
    acquisition_resume = acquisition_subcommands.add_parser("resume")
    acquisition_resume.add_argument("--batch-id", required=True)
    acquisition_resume.add_argument("--deadline-seconds", type=float, default=30.0)
    acquisition_status = acquisition_subcommands.add_parser("status")
    acquisition_status.add_argument("--batch-id", required=True)

    interview_command = commands.add_parser(
        "interview", help="prepare, read, revise, or save feedback for interview practice"
    )
    interview_subcommands = interview_command.add_subparsers(dest="interview_operation", required=True)
    interview_prepare = interview_subcommands.add_parser("prepare")
    interview_prepare.add_argument("--selection-file", required=True, type=Path)
    interview_prepare.add_argument("--content-file", required=True, type=Path)
    interview_prepare.add_argument("--operation-key", required=True)
    interview_read = interview_subcommands.add_parser("read")
    interview_read.add_argument("--record-id", required=True)
    interview_read.add_argument("--revision-id")
    interview_subcommands.add_parser("list")
    interview_feedback = interview_subcommands.add_parser("feedback")
    interview_feedback.add_argument("--record-id", required=True)
    interview_feedback.add_argument("--parent-revision", required=True)
    interview_feedback.add_argument("--feedback-file", required=True, type=Path)
    interview_feedback.add_argument("--operation-key", required=True)
    interview_revise = interview_subcommands.add_parser("revise")
    interview_revise.add_argument("--record-id", required=True)
    interview_revise.add_argument("--parent-revision", required=True)
    interview_revise.add_argument("--content-file", required=True, type=Path)
    interview_revise.add_argument("--operation-key", required=True)

    transfer_command = commands.add_parser(
        "transfer", help="export a definition or explicitly transfer private history"
    )
    transfer_subcommands = transfer_command.add_subparsers(dest="transfer_operation", required=True)
    transfer_export = transfer_subcommands.add_parser("export-definition")
    transfer_export.add_argument("--archive", required=True, type=Path)
    transfer_import = transfer_subcommands.add_parser("import-definition")
    transfer_import.add_argument("--archive", required=True, type=Path)
    transfer_import.add_argument("--destination", required=True, type=Path)
    transfer_backup = transfer_subcommands.add_parser("backup-private")
    transfer_backup.add_argument("--archive", required=True, type=Path)
    transfer_backup.add_argument("--path", dest="selected_paths", action="append", default=[])
    transfer_backup.add_argument("--confirm", action="store_true")
    transfer_restore = transfer_subcommands.add_parser("restore-private")
    transfer_restore.add_argument("--archive", required=True, type=Path)
    transfer_restore.add_argument("--destination", required=True, type=Path)
    transfer_restore.add_argument("--expected-project-id")
    transfer_restore.add_argument("--expected-gig-id")
    transfer_restore.add_argument("--confirm", action="store_true")
    return parser


def _service_context(args: argparse.Namespace) -> tuple[Path, Path | None]:
    return args.home.expanduser(), args.target.expanduser() if args.target else None


def _ownership_refused(message: str) -> None:
    raise CommandError(
        "wrapper_ownership_refused",
        message,
        "run this copied wrapper from its registered Gig workpad with its bound project target",
    )


def _wrapper_parent() -> Path:
    """Return this wrapper's registered workpad, rejecting redirected source."""

    raw = Path(__file__)
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    # Normalize ``./`` and ``..`` without resolving symlinks.  The latter are
    # rejected below because a copied Gig entry must remain at its own root.
    script = Path(os.path.normpath(os.fspath(raw.absolute())))
    if script.name != "gig.py":
        _ownership_refused("copied Scout wrapper must be named gig.py")
    try:
        status = script.lstat()
        if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
            _ownership_refused("copied Scout wrapper is not a regular local file")
        parent = script.parent
        resolved_parent = parent.resolve(strict=True)
        resolved_script = script.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        _ownership_refused("copied Scout wrapper location is unavailable")
        raise AssertionError("unreachable") from exc
    if resolved_parent != parent or resolved_script != script:
        _ownership_refused("copied Scout wrapper or its workpad path is redirected")
    return parent


def _owning_workpad(*, home_root: Path, requested_target: Path | None):
    """Authenticate wrapper location, project target, and registered Gig ID."""

    wrapper_parent = _wrapper_parent()
    from gigai.registry import open_project_registry
    from gigai.workpad import resolve_workpad

    registry, _ = open_project_registry(home_root.expanduser().resolve(strict=False), create=False)
    try:
        records = registry.workpad_records()
    except Exception as exc:
        _ownership_refused("registered Gig workpad identity is unavailable")
        raise AssertionError("unreachable") from exc

    candidates = [
        record
        for record in records
        if Path(record.workpad_locator) == wrapper_parent
    ]
    if len(candidates) != 1:
        _ownership_refused(
            "copied Scout wrapper is not owned by exactly one registered Gig workpad"
        )
    record = candidates[0]
    try:
        # Supplying the identity found in the registry is intentional: this
        # explicit path never consults or changes active_gig_id.
        resolved = resolve_workpad(
            home_root=home_root,
            requested_target=requested_target,
            gig_id=record.gig_id,
            allow_semantic_state=True,
        )
    except Exception as exc:
        _ownership_refused(
            "registered Gig workpad does not validate against this project target"
        )
        raise AssertionError("unreachable") from exc
    try:
        same_filesystem = os.path.samefile(resolved.path, wrapper_parent)
    except OSError as exc:
        _ownership_refused("registered Gig workpad cannot be revalidated")
        raise AssertionError("unreachable") from exc
    if resolved.path != wrapper_parent or not same_filesystem:
        _ownership_refused(
            "registered Gig workpad differs from this copied wrapper parent"
        )
    return resolved


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"encoding": "base64", "value": __import__("base64").b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _input_object(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise CommandError(
            "input_invalid",
            "--input-json must be a JSON object",
            "provide one bounded JSON object for the approved entry",
        ) from exc
    if not isinstance(parsed, dict):
        raise CommandError(
            "input_invalid",
            "--input-json must be a JSON object",
            "provide one bounded JSON object for the approved entry",
        )
    return parsed


def _input_file(value: Path) -> dict[str, object]:
    """Read one explicit local JSON object without echoing private input."""
    if value.is_symlink() or not value.is_file():
        raise CommandError("input_source_unsafe", "input file must be one regular local file", "select a local JSON input file")
    try:
        parsed = json.loads(value.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CommandError("input_invalid", "input file must be UTF-8 JSON", "provide one JSON object") from exc
    if not isinstance(parsed, dict):
        raise CommandError("input_invalid", "input file must contain one JSON object", "provide one JSON object")
    return parsed


def _selector_object(value: str, label: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise CommandError(
            "input_invalid", f"{label} must be one JSON object", "provide a canonical selector object"
        ) from exc
    if not isinstance(parsed, dict):
        raise CommandError(
            "input_invalid", f"{label} must be one JSON object", "provide a canonical selector object"
        )
    return parsed


def _run(args: argparse.Namespace) -> dict[str, Any]:
    home_root, requested_target = _service_context(args)
    resolved = _owning_workpad(
        home_root=home_root,
        requested_target=requested_target,
    )
    # Reuse the target root that just authenticated the wrapper's project
    # binding.  The exact Gig ID is passed to every service call below.
    service_target = resolved.target_root
    if args.command == "report":
        from gigai.scout.report import publish_report, read_current_report

        result = (
            publish_report(resolved=resolved)
            if args.operation == "generate"
            else read_current_report(resolved=resolved)
        )
        return {"schema_version": "1.0", "operation": f"report_{args.operation}", "result": _jsonable(result)}
    if args.command == "acquisition":
        from gigai.scout.acquisition_records import import_public_rows, read_public_acquisition_status, resume_public_acquisition

        if args.acquisition_operation == "import":
            try:
                from gigai.scout.acquisition_records import read_public_rows_file
                rows = read_public_rows_file(args.rows_file)
            except Exception as exc:
                if getattr(exc, "code", "") in {"acquisition_source_unsafe", "acquisition_source_invalid"}:
                    raise
                raise CommandError("acquisition_source_invalid", "rows file must be valid UTF-8 JSON", "provide a JSON array of public rows") from exc
            result = import_public_rows(resolved=resolved, batch_id=args.batch_id, rows=rows, deadline_seconds=args.deadline_seconds)
        elif args.acquisition_operation == "resume":
            result = resume_public_acquisition(resolved=resolved, batch_id=args.batch_id, deadline_seconds=args.deadline_seconds)
        else:
            result = read_public_acquisition_status(resolved=resolved, batch_id=args.batch_id)
        return {"schema_version": "1.0", "operation": f"acquisition_{args.acquisition_operation}", "result": _jsonable(result.to_json())}
    if args.command == "interview":
        from gigai.scout.interview_records import (
            list_interview_preparations, prepare_interview, read_interview_preparation,
            revise_interview, save_interview_feedback,
        )
        operation = args.interview_operation
        if operation == "prepare":
            selection = _input_file(args.selection_file)
            content = _input_file(args.content_file)
            result = prepare_interview(home_root=home_root, requested_target=service_target, gig_id=resolved.gig_id, selection=selection, content=content, operation_key=args.operation_key)
            return {"schema_version": "1.0", "operation": "interview_prepare", "result": _jsonable({"record_id": result.record_id, "revision_id": result.revision_id, "created": result.created})}
        if operation == "read":
            result = read_interview_preparation(home_root=home_root, requested_target=service_target, gig_id=resolved.gig_id, record_id=args.record_id, revision_id=args.revision_id)
            return {"schema_version": "1.0", "operation": "interview_read", "result": _jsonable(result)}
        if operation == "list":
            result = list_interview_preparations(home_root=home_root, requested_target=service_target, gig_id=resolved.gig_id)
            return {"schema_version": "1.0", "operation": "interview_list", "result": _jsonable(result)}
        if operation == "feedback":
            feedback_value = _input_file(args.feedback_file)
            entries = feedback_value.get("feedback")
            if not isinstance(entries, list):
                raise CommandError("interview_feedback_invalid", "feedback file must contain a feedback list", "provide bounded feedback entries")
            result = save_interview_feedback(home_root=home_root, requested_target=service_target, gig_id=resolved.gig_id, record_id=args.record_id, parent_revision=args.parent_revision, feedback=entries, operation_key=args.operation_key)
            return {"schema_version": "1.0", "operation": "interview_feedback", "result": _jsonable({"record_id": result.record_id, "revision_id": result.revision_id, "created": result.created})}
        content = _input_file(args.content_file)
        result = revise_interview(home_root=home_root, requested_target=service_target, gig_id=resolved.gig_id, record_id=args.record_id, parent_revision=args.parent_revision, content=content, operation_key=args.operation_key)
        return {"schema_version": "1.0", "operation": "interview_revise", "result": _jsonable({"record_id": result.record_id, "revision_id": result.revision_id, "created": result.created})}
    if args.command == "transfer":
        from gigai.private_transfer import backup_private, export_definition, import_definition, restore_private
        operation = args.transfer_operation
        if operation == "export-definition":
            result = export_definition(workpad=resolved.path, destination=args.archive)
        elif operation == "import-definition":
            result = import_definition(archive=args.archive, destination=args.destination)
        elif operation == "backup-private":
            if not args.confirm:
                raise CommandError("consent_required", "backup-private requires --confirm", "confirm the selected private history")
            result = backup_private(workpad=resolved.path, destination=args.archive, project_id=resolved.project_id, gig_id=resolved.gig_id, selected_paths=args.selected_paths or None)
        else:
            if not args.confirm:
                raise CommandError("consent_required", "restore-private requires --confirm", "confirm the explicit private restore")
            result = restore_private(archive=args.archive, destination=args.destination, expected_project_id=args.expected_project_id, expected_gig_id=args.expected_gig_id)
        return {"schema_version": "1.0", "operation": f"transfer_{operation.replace('-', '_')}", "result": _jsonable({"archive": str(result.archive), "kind": result.kind, "files": list(result.files), "project_id": result.project_id, "gig_id": result.gig_id})}
    if args.command == "proposal":
        if not args.confirm:
            raise CommandError(
                "consent_required", "proposal requires --confirm", "review the private local assessment and retry with --confirm"
            )
        from gigai.run import ProposalRunRequest, launch_run

        result = launch_run(
            home_root=home_root,
            requested_target=service_target,
            gig_id=resolved.gig_id,
            wait=args.wait,
            operator_consent={
                "schema_version": "1.0", "kind": "operator_run_consent", "action": "run",
                "actor": {"kind": "operator", "id": "local-user"}, "source": "direct_copied_gig_confirm",
            },
            proposal_execution=ProposalRunRequest(
                graph_selector="proposal-assessment",
                model_target=args.model_target,
                posting_selector=_selector_object(args.posting_selector, "--posting-selector"),
                private_selectors=tuple(_selector_object(item, "--private-selector") for item in args.private_selector),
            ),
        )
        return {"schema_version": "1.0", "operation": "proposal", "result": _jsonable({"run_id": result.run_id, "gig_id": result.gig_id, "status": result.status})}
    if args.command == "tailor":
        if not args.confirm:
            raise CommandError(
                "consent_required", "tailor requires --confirm", "review the private local Tailor run and retry with --confirm"
            )
        if (args.proposal_record is None) != (args.proposal_revision is None):
            raise CommandError(
                "input_invalid", "proposal record and revision must be supplied together", "provide both proposal selectors or neither"
            )
        from gigai.run import TailorRunRequest, launch_run

        result = launch_run(
            home_root=home_root,
            requested_target=service_target,
            gig_id=resolved.gig_id,
            wait=args.wait,
            operator_consent={
                "schema_version": "1.0", "kind": "operator_run_consent", "action": "run",
                "actor": {"kind": "operator", "id": "local-user"}, "source": "direct_copied_gig_confirm",
            },
            tailor_execution=TailorRunRequest(
                graph_selector="tailor-application",
                model_target=args.model_target,
                posting_selector=_selector_object(args.posting_selector, "--posting-selector"),
                private_selectors=tuple(_selector_object(item, "--private-selector") for item in args.private_selector),
                answer_selectors=tuple(_selector_object(item, "--answer-selector") for item in args.answer_selector),
                requested_outputs=tuple(args.output),
                proposal_selector=(
                    {"record_id": args.proposal_record, "revision_id": args.proposal_revision}
                    if args.proposal_record is not None else None
                ),
            ),
        )
        return {"schema_version": "1.0", "operation": "tailor", "result": _jsonable({"run_id": result.run_id, "gig_id": result.gig_id, "status": result.status})}
    if args.command == "answer":
        if not args.confirm:
            raise CommandError(
                "consent_required", "answer requires --confirm", "review the saved answer and retry with --confirm"
            )
        from gigai.canonical import parse_json_bytes
        from gigai.native_records import read_native_record, update_native_record

        answer_path = args.answer_file.expanduser()
        if answer_path.is_symlink() or not answer_path.is_file():
            raise CommandError("answer_source_unsafe", "answer file must be one regular local file", "select a local answer file")
        try:
            answer = answer_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise CommandError("answer_invalid", "answer file cannot be read", "select a readable UTF-8 answer file") from exc
        if not answer.strip() or len(answer) > 16_000:
            raise CommandError("answer_invalid", "answer text is empty or exceeds the bound", "provide bounded answer text")
        try:
            base = read_native_record(
                home_root=home_root, requested_target=service_target,
                gig_id=resolved.gig_id, record_id=args.record_id,
                revision_id=args.parent_revision, content=True,
            )
            raw = base.get("content")
            if not isinstance(raw, bytes):
                raise ValueError("native answer source is unavailable")
            value = parse_json_bytes(raw)
            if not isinstance(value, dict) or value.get("kind") != "experience_qa" or not isinstance(value.get("payload"), dict):
                raise ValueError("selected native record is not experience_qa")
            updated = json.loads(json.dumps(value))
            questions = updated["payload"].get("questions")
            if not isinstance(questions, list):
                raise ValueError("experience questions are unavailable")
            question = next((item for item in questions if isinstance(item, dict) and item.get("question_id") == args.question_id), None)
            if question is None:
                raise ValueError("question is not present in selected revision")
            question["state"] = "answered"
            question["answer"] = answer
            question["provenance"] = {"kind": "user_reported", "source_refs": []}
            result = update_native_record(
                home_root=home_root, requested_target=service_target,
                gig_id=resolved.gig_id, record_id=args.record_id,
                parent_revision=args.parent_revision, content=updated,
                actor={"kind": "operator", "id": "local-user"}, origin="user_reported",
                operation_key=args.operation_key,
            )
        except Exception as exc:
            raise CommandError("answer_invalid", str(exc), "inspect the selected experience_qa revision and retry") from exc
        return {"schema_version": "1.0", "operation": "answer", "result": _jsonable(asdict(result))}
    if args.command == "final-select":
        if not args.confirm:
            raise CommandError(
                "consent_required", "final-select requires --confirm", "review the selected documents and retry with --confirm"
            )
        from gigai.scout.document_records import read_document_revision, read_final_selection, record_final_selection
        from gigai.scout.documents import FinalDocumentSelection

        selectors = [_selector_object(item, "--document") for item in args.document]
        try:
            docs = tuple(
                read_document_revision(
                    resolved=resolved, record_id=str(item["record_id"]),
                    revision_id=str(item["revision_id"]), document_kind=str(item["document_kind"]),
                ) for item in selectors
            )
            if not docs:
                raise ValueError("at least one document is required")
            selection = FinalDocumentSelection(docs[0].opportunity_id, docs[0].snapshot_id, docs, args.selected_by)
            record_final_selection(
                resolved=resolved, selection=selection,
                invocation={"authority": "tailor_run", "run_id": args.run_id,
                            "goal_id": args.goal_id, "invocation_id": args.invocation_id,
                            "output_sha256": args.output_sha256},
                operation_key=args.operation_key,
            )
            persisted = read_final_selection(
                resolved=resolved, opportunity_id=selection.opportunity_id,
                snapshot_id=selection.snapshot_id,
            )
        except Exception as exc:
            raise CommandError("document_selection_invalid", str(exc), "inspect committed Tailor documents and retry") from exc
        return {"schema_version": "1.0", "operation": "final-select", "result": {"selection": persisted}}
    if args.command == "list":
        from gigai.native_records import list_native_records

        records = list_native_records(
            home_root=home_root,
            requested_target=service_target,
            gig_id=resolved.gig_id,
            include_archived=args.include_archived,
        )
        return {"schema_version": "1.0", "operation": "list", "records": records}
    if args.command == "read":
        from gigai.native_records import read_native_record

        record = read_native_record(
            home_root=home_root,
            requested_target=service_target,
            record_id=args.record_id,
            revision_id=args.revision_id,
            content=args.content,
            gig_id=resolved.gig_id,
        )
        return {"schema_version": "1.0", "operation": "read", "record": _jsonable(record)}
    if args.command == "context":
        from gigai.native_records import native_context

        return {
            "schema_version": "1.0",
            "operation": "context",
            "context": native_context(
                home_root=home_root,
                requested_target=service_target,
                gig_id=resolved.gig_id,
            ),
        }
    if args.command in {"create", "update", "archive"}:
        operation = f"record_{args.command}"
        tool_input: dict[str, object]
        if args.command == "archive":
            tool_input = {
                "record_id": args.record_id,
                "parent_revision": args.parent_revision,
            }
        else:
            tool_input = _input_object(args.input_json)
            if args.command == "update":
                tool_input = {
                    **tool_input,
                    "record_id": args.record_id,
                    "parent_revision": args.parent_revision,
                }
        from gigai.scout.tools import invoke_approved_tool_entry
        from gigai.canonical import digest_imported_bytes

        wrapper = resolved.path / "gig.py"
        wrapper_bytes = wrapper.read_bytes()
        wrapper_ref = {
            "path": "gig.py",
            "content_sha256": digest_imported_bytes(wrapper_bytes),
            "media_type": "text/x-python",
            "size_bytes": len(wrapper_bytes),
        }

        result = invoke_approved_tool_entry(
            home_root=home_root,
            requested_target=service_target,
            capability_id=args.capability_id,
            actor={"kind": "agent", "id": args.actor_id},
            operation_key=args.operation_key,
            tool_input=tool_input,
            operation=operation,
            gig_id=resolved.gig_id,
            wrapper_ref=wrapper_ref,
        )
        return {
            "schema_version": "1.0",
            "operation": args.command,
            "origin": "agent_supplied",
            "actor": {"kind": "agent", "id": args.actor_id},
            "result": asdict(result),
        }
    raise CommandError(
        "command_invalid",
        "unsupported Scout command",
        "run `python gig.py --help` to list supported operations",
    )


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        output = {"ok": True, "result": _run(args)}
    except CommandError as exc:
        output = {
            "ok": False,
            "error": {"code": exc.code, "message": str(exc)},
            "next_action": {"kind": "operator_review", "message": exc.next_action},
        }
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
        return 2
    except Exception as exc:
        code = getattr(exc, "code", "scout_operation_refused")
        output = {
            "ok": False,
            "error": {"code": code, "message": str(exc)},
            "next_action": {
                "kind": "proposal_required" if code.startswith("tool_") else "inspect_error",
                "message": (
                    "propose and obtain explicit capability approval, then retry"
                    if code.startswith("tool_")
                    else "inspect the selected Gig context and retry"
                ),
            },
        }
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Thin public CLI adapter for explicit local capability review.

The command deliberately supplies no source paths, execution callbacks, or
approval authority.  It only turns one bounded reviewer JSON document and an
explicit terminal confirmation into the already-validated review service call.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shlex

import click

from .capability_review import CapabilityReviewError, CapabilityReviewResult, review_local_tool
from .capability_successor import CapabilitySuccessorError, CapabilitySuccessorResult, prepare_capability_successor
from .capabilities import validate_capability_manifest
from .canonical import CanonicalizationError, digest_imported_bytes, parse_json_bytes
from .journal import JournalArtifactMissingError, JournalConflictError, read_committed_artifact
from .setup import default_home_root
from .workpad import WorkpadError, resolve_workpad


_MAX_INPUT_BYTES = 64 * 1024
_INPUT_KEYS = frozenset(
    {"reviewer", "outcome", "rationale", "evidence_refs", "selected_option_id"}
)
_OPERATOR = {"kind": "operator", "id": "local-user"}
_GIG_ID = re.compile(
    r"^gig_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_PROPOSAL_ID = re.compile(
    r"^gp_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_CAPABILITY_ID = re.compile(
    r"^cap_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_MANIFEST_ID = re.compile(
    r"^capmanifest_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class CapabilityCliError(ValueError):
    """Typed, redacted refusal for a CLI-owned review input boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _read_reviewer_input(path: Path) -> dict[str, object]:
    """Read one small regular UTF-8 JSON object without following links."""

    if path.is_symlink() or not path.is_file():
        raise CapabilityCliError(
            "capability_review_input_unsafe",
            "review input must be one explicit regular JSON file",
        )
    current = path.parent
    while current != current.parent:
        if current.is_symlink():
            raise CapabilityCliError(
                "capability_review_input_unsafe",
                "review input parent is redirected",
            )
        current = current.parent
    try:
        with path.open("rb") as stream:
            payload = stream.read(_MAX_INPUT_BYTES + 1)
    except OSError as exc:
        raise CapabilityCliError(
            "capability_review_input_unsafe", "review input is unavailable"
        ) from exc
    if not payload:
        raise CapabilityCliError(
            "capability_review_input_invalid", "review input must not be empty"
        )
    if len(payload) > _MAX_INPUT_BYTES:
        raise CapabilityCliError(
            "capability_review_input_too_large", "review input exceeds the size limit"
        )
    try:
        value = parse_json_bytes(payload)
    except (CanonicalizationError, RecursionError) as exc:
        raise CapabilityCliError(
            "capability_review_input_invalid", "review input must be valid UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict) or set(value) - _INPUT_KEYS:
        raise CapabilityCliError(
            "capability_review_input_invalid", "review input has unsupported fields"
        )
    if not {"reviewer", "outcome", "rationale", "evidence_refs"} <= set(value):
        raise CapabilityCliError(
            "capability_review_input_invalid", "review input is missing required fields"
        )
    if not isinstance(value["reviewer"], dict):
        raise CapabilityCliError(
            "capability_review_input_invalid", "review input reviewer is invalid"
        )
    if not isinstance(value["outcome"], str) or not isinstance(value["rationale"], str):
        raise CapabilityCliError(
            "capability_review_input_invalid", "review input outcome or rationale is invalid"
        )
    if not isinstance(value["evidence_refs"], list) or any(
        not isinstance(item, str) for item in value["evidence_refs"]
    ):
        raise CapabilityCliError(
            "capability_review_input_invalid", "review input evidence references are invalid"
        )
    if "selected_option_id" in value and not isinstance(value["selected_option_id"], str):
        raise CapabilityCliError(
            "capability_review_input_invalid", "review input selected option is invalid"
        )
    return value


def _fail(exc: Exception, *, as_json: bool) -> None:
    """Use the established typed JSON error shape without echoing input bytes."""

    code = getattr(exc, "code", "capability_review_refused")
    message = (
        str(exc)
        if isinstance(
            exc,
            (
                CapabilityCliError,
                CapabilityReviewError,
                CapabilitySuccessorError,
                JournalArtifactMissingError,
                JournalConflictError,
                WorkpadError,
            ),
        )
        else "capability review is unavailable"
    )
    if as_json:
        click.echo(
            json.dumps(
                {"ok": False, "error": {"code": code, "message": message}},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        raise click.exceptions.Exit(1)
    raise click.ClickException(f"{code}: {message}")


def _result_payload(result: CapabilityReviewResult) -> dict[str, object]:
    return {
        "ok": True,
        "status": "review_recorded",
        "decision": result.decision,
        "decision_ref": result.decision_ref,
        "reviewed_manifest_ref": result.reviewed_manifest_ref,
        "parent_manifest_ref": result.parent_manifest_ref,
        "replayed": result.replayed,
        "review_note": (
            "This review does not approve, activate, install, or execute the "
            "capability; a separately proposed successor and direct approval remain required."
        ),
    }


def _validate_identity(value: str, pattern: re.Pattern[str], label: str) -> str:
    """Reject malformed identities before constructing any workpad paths."""

    if pattern.fullmatch(value) is None:
        raise CapabilityCliError("capability_successor_identity_invalid", f"{label} identity is invalid")
    return value


def _artifact_ref(path: str, payload: bytes) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": digest_imported_bytes(payload),
        "media_type": "application/json",
        "size_bytes": len(payload),
    }


def _read_review_authority(
    *,
    workpad: Path,
    project_id: str,
    gig_id: str,
    base_version: int,
    base_proposal_id: str,
    capability_id: str,
    manifest_id: str,
) -> tuple[dict[str, object], dict[str, object]]:
    """Read exactly the manifest-keyed decision and reviewed manifest."""

    reviewed_path = f"manifests/capabilities/{manifest_id}.json"
    decision_path = f"manifests/capability-reviews/{manifest_id}.json"
    try:
        reviewed_bytes, _ = read_committed_artifact(
            workpad=workpad, project_id=project_id, gig_id=gig_id, path=reviewed_path
        )
        decision_bytes, _ = read_committed_artifact(
            workpad=workpad, project_id=project_id, gig_id=gig_id, path=decision_path
        )
    except (JournalArtifactMissingError, JournalConflictError) as exc:
        raise CapabilityCliError(
            "capability_successor_review_unavailable",
            "the exact committed reviewed manifest or decision is unavailable",
        ) from exc
    if not validate_capability_manifest(reviewed_bytes).valid:
        raise CapabilityCliError(
            "capability_successor_review_invalid",
            "the committed reviewed manifest is invalid",
        )
    try:
        reviewed = parse_json_bytes(reviewed_bytes)
        decision = parse_json_bytes(decision_bytes)
    except (CanonicalizationError, RecursionError) as exc:
        raise CapabilityCliError(
            "capability_successor_review_invalid",
            "the committed review authority is malformed",
        ) from exc
    if not isinstance(reviewed, dict) or not isinstance(decision, dict):
        raise CapabilityCliError(
            "capability_successor_review_invalid",
            "the committed review authority is malformed",
        )
    reviewed_ref = _artifact_ref(reviewed_path, reviewed_bytes)
    if (
        reviewed.get("manifest_id") != manifest_id
        or reviewed.get("gig_id") != gig_id
        or decision.get("project_id") != project_id
        or decision.get("gig_id") != gig_id
        or decision.get("base_version") != base_version
        or decision.get("base_proposal_id") != base_proposal_id
        or decision.get("capability_id") != capability_id
        or decision.get("reviewer_outcome") != "passed"
        or decision.get("reviewed_manifest_ref") != reviewed_ref
    ):
        raise CapabilityCliError(
            "capability_successor_review_invalid",
            "the committed review decision does not match the requested successor",
        )
    return _artifact_ref(decision_path, decision_bytes), reviewed_ref


def _successor_payload(
    result: CapabilitySuccessorResult,
    *,
    home_root: Path,
    target: Path,
    gig_id: str,
    manifest_id: str,
) -> dict[str, object]:
    proposal_id = str(result.proposal["proposal_id"])
    approval_argv = [
        "gigai",
        "approve",
        proposal_id,
        "--gig",
        gig_id,
        "--capability-manifest-id",
        manifest_id,
        "--home",
        str(home_root),
        "--target",
        str(target),
        "--json",
    ]
    return {
        "ok": True,
        "status": "successor_pending",
        "proposal": result.proposal,
        "proposal_ref": result.proposal_ref,
        "binding": result.binding,
        "binding_ref": result.binding_ref,
        "decision_ref": result.decision_ref,
        "reviewed_manifest_ref": result.reviewed_manifest_ref,
        "replayed": result.replayed,
        "approval_argv": approval_argv,
        "next_action": "run the approval_argv separately for explicit operator approval; preparation does not approve or activate",
    }


@click.group(
    "capability",
    help=(
        "Review one bounded local capability. No command here executes, installs, "
        "activates, or approves a Gig."
    ),
)
def capability_group() -> None:
    """Public capability-review surface; no inspection or execution adapter."""


@capability_group.command(
    "review",
    help=(
        "Record reviewer judgment for exactly write_workpad; filesystem is "
        "write_isolated, network is none, and credentials are none."
    ),
)
@click.option("--gig", "gig_id", required=True, help="Explicit provisioned Gig ID.")
@click.option("--base-version", type=click.IntRange(min=1), required=True)
@click.option("--base-proposal-id", required=True)
@click.option("--manifest-id", required=True)
@click.option("--capability-id", required=True)
@click.option("--operation-key", required=True)
@click.option(
    "--input",
    "review_input",
    required=True,
    type=click.Path(path_type=Path, dir_okay=False),
    help=(
        "One local reviewer JSON object with only reviewer, outcome, rationale, "
        "evidence_refs, and optional selected_option_id."
    ),
)
@click.option(
    "--confirm",
    is_flag=True,
    help="Directly confirm the exact write_workpad effect; JSON cannot provide this consent.",
)
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def review_command(
    gig_id: str,
    base_version: int,
    base_proposal_id: str,
    manifest_id: str,
    capability_id: str,
    operation_key: str,
    review_input: Path,
    confirm: bool,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """Persist one explicit local review; it never approves a successor."""

    try:
        if confirm is not True:
            raise CapabilityReviewError(
                "direct operator effect consent is required",
                code="capability_review_operator_consent_required",
            )
        input_value = _read_reviewer_input(review_input)
        resolved = resolve_workpad(
            home_root=home_value or default_home_root(),
            requested_target=target_value,
            gig_id=gig_id,
            allow_semantic_state=True,
        )
        result = review_local_tool(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            base_version=base_version,
            base_proposal_id=base_proposal_id,
            manifest_id=manifest_id,
            capability_id=capability_id,
            reviewer=input_value["reviewer"],  # checked as a mapping above
            reviewer_outcome=input_value["outcome"],
            reviewer_rationale=input_value["rationale"],
            evidence_refs=input_value["evidence_refs"],
            operator_actor=_OPERATOR,
            operator_confirmed=True,
            selected_option_id=input_value.get("selected_option_id", "A"),
            operation_key=operation_key,
        )
    except (CapabilityCliError, CapabilityReviewError, WorkpadError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json)
        return
    payload = _result_payload(result)
    click.echo(
        json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if as_json
        else "Capability review was recorded; it does not approve or activate the Gig."
    )


@capability_group.command(
    "prepare-successor",
    help=(
        "Prepare a pending successor from one exact committed passed review; "
        "this command never approves, activates, installs, or executes."
    ),
)
@click.option("--gig", "gig_id", required=True, help="Explicit provisioned Gig ID.")
@click.option("--base-version", type=click.IntRange(min=1), required=True)
@click.option("--base-proposal-id", required=True)
@click.option("--capability-id", required=True)
@click.option("--reviewed-manifest-id", required=True)
@click.option("--operation-key", required=True)
@click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def prepare_successor_command(
    gig_id: str,
    base_version: int,
    base_proposal_id: str,
    capability_id: str,
    reviewed_manifest_id: str,
    operation_key: str,
    target_value: Path | None,
    home_value: Path | None,
    as_json: bool,
) -> None:
    """Stage a reviewed-capability successor; approval remains separate."""

    try:
        # Validate all caller identities before constructing any authority path.
        _validate_identity(gig_id, _GIG_ID, "Gig")
        _validate_identity(base_proposal_id, _PROPOSAL_ID, "base proposal")
        _validate_identity(capability_id, _CAPABILITY_ID, "capability")
        _validate_identity(reviewed_manifest_id, _MANIFEST_ID, "reviewed manifest")
        resolved = resolve_workpad(
            home_root=home_value or default_home_root(),
            requested_target=target_value,
            gig_id=gig_id,
            allow_semantic_state=True,
        )
        decision_ref, reviewed_ref = _read_review_authority(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            base_version=base_version,
            base_proposal_id=base_proposal_id,
            capability_id=capability_id,
            manifest_id=reviewed_manifest_id,
        )
        result = prepare_capability_successor(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            base_version=base_version,
            base_proposal_id=base_proposal_id,
            capability_id=capability_id,
            decision_ref=decision_ref,
            reviewed_manifest_ref=reviewed_ref,
            operation_key=operation_key,
        )
    except (
        CapabilityCliError,
        CapabilityReviewError,
        CapabilitySuccessorError,
        JournalArtifactMissingError,
        JournalConflictError,
        WorkpadError,
        OSError,
        ValueError,
    ) as exc:
        _fail(exc, as_json=as_json)
        return
    target = target_value or resolved.target_root
    payload = _successor_payload(
        result,
        home_root=home_value or default_home_root(),
        target=target,
        gig_id=resolved.gig_id,
        manifest_id=reviewed_manifest_id,
    )
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        approval_argv = shlex.join(str(part) for part in payload["approval_argv"])
        click.echo(
            f"Prepared pending successor {result.proposal['proposal_id']}; approval remains separate.\n"
            f"Approval command: {approval_argv}"
        )


__all__ = ["capability_group"]

from __future__ import annotations

import copy

from gigai.canonical import canonical_json_bytes
from gigai.validators import (
    SCHEMA_NAMES,
    validate_serialized_contract,
    validate_target_effect,
    validate_target_effect_transition,
)


SHA = "sha256:" + "0" * 64
NOW = "2026-08-10T00:00:00Z"
PROJECT = "project_00000000-0000-4000-8000-000000000001"
GIG = "gig_00000000-0000-4000-8000-000000000002"
PROPOSAL = "gp_00000000-0000-4000-8000-000000000003"
EFFECT = "effect_00000000-0000-4000-8000-000000000004"
OPERATOR = {"kind": "operator", "id": "local-user"}


def _artifact(path: str, media_type: str = "application/json") -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": SHA,
        "canonical_sha256": None,
        "media_type": media_type,
        "size_bytes": 1,
    }


def _record(state: str = "effect_authorized") -> dict[str, object]:
    before = _artifact("manifests/before.json") if state in {"prepared", "exposed", "verified", "applied"} else None
    after = _artifact("manifests/after.json") if state in {"verified", "applied"} else None
    terminal_reason = None if state in {"effect_authorized", "prepared", "exposed", "verified", "applied"} else "fixture_terminal"
    return {
        "schema_version": "1.0",
        "effect_id": EFFECT,
        "effect_version": 1,
        "state": state,
        "project_id": PROJECT,
        "gig_id": GIG,
        "gig_proposal_id": PROPOSAL,
        "target": {
            "kind": "git",
            "binding_sha256": SHA,
            "repository_identity_sha256": SHA,
            "git_head": "0" * 40,
        },
        "operator": OPERATOR,
        "effect_kind": "write_target",
        "operation": "replace_file",
        "relative_target_path": "README.md",
        "source_artifact": _artifact("workpad/reviewed.md", "text/markdown"),
        "expected_before_sha256": SHA,
        "expected_after_sha256": SHA,
        "expected_file_mode": 420,
        "authorization": {
            "gig_proposal_id": PROPOSAL,
            "operator": OPERATOR,
            "target_binding_sha256": SHA,
            "relative_target_path": "README.md",
            "source_artifact_sha256": SHA,
            "expected_before_sha256": SHA,
            "expected_after_sha256": SHA,
            "authorized_at": NOW,
            "cancellation_policy": "before_exposure_only",
            "commit_policy": "leave_uncommitted",
            "authorization_sha256": SHA,
        },
        "cancellation_policy": "before_exposure_only",
        "commit_policy": "leave_uncommitted",
        "patch_identity": {
            "relative_target_path": "README.md",
            "source_artifact_sha256": SHA,
            "expected_before_sha256": SHA,
            "expected_after_sha256": SHA,
            "expected_file_mode": 420,
            "descriptor_sha256": SHA,
        },
        "target_before_manifest": before,
        "target_after_manifest": after,
        "created_at": NOW,
        "updated_at": NOW,
        "terminal_reason": terminal_reason,
    }


def test_g19_adds_the_twenty_third_schema_resource() -> None:
    assert len(SCHEMA_NAMES) == 23
    assert "target-effect.schema.json" in SCHEMA_NAMES
    assert validate_target_effect(_record()).valid


def test_each_target_effect_state_has_the_declared_manifest_and_reason_shape() -> None:
    for state in (
        "effect_authorized",
        "prepared",
        "exposed",
        "verified",
        "applied",
        "refused",
        "failed",
        "cancelled",
        "rolled_back",
        "blocked",
    ):
        assert validate_target_effect(_record(state)).valid, state


def test_target_effect_rejects_unknown_commit_retry_and_credential_fields() -> None:
    for field, value in (
        ("automatic_commit", True),
        ("retry_count", 1),
        ("credential_value", "secret-canary"),
    ):
        invalid = _record()
        invalid[field] = value
        report = validate_serialized_contract(
            "target-effect.schema.json", canonical_json_bytes(invalid)
        )
        assert not report.valid, field


def test_target_effect_rejects_unsupported_effect_actor_and_path() -> None:
    for field, value in (
        ("effect_kind", "write_workpad"),
        ("operator", {"kind": "model", "id": "model"}),
        ("relative_target_path", "../outside.txt"),
    ):
        invalid = _record()
        invalid[field] = value
        report = validate_target_effect(invalid)
        assert not report.valid, field


def test_target_effect_rejects_authorization_and_patch_drift() -> None:
    invalid = _record()
    invalid["authorization"]["relative_target_path"] = "other.md"  # type: ignore[index]
    report = validate_target_effect(invalid)
    assert "authorization/path" in {finding.location for finding in report.findings}

    invalid = _record()
    invalid["patch_identity"]["expected_after_sha256"] = "sha256:" + "1" * 64  # type: ignore[index]
    report = validate_target_effect(invalid)
    assert "patch_identity/after" in {finding.location for finding in report.findings}


def test_target_effect_transition_graph_is_closed_and_terminal() -> None:
    previous = _record("effect_authorized")
    current = copy.deepcopy(previous)
    current["state"] = "prepared"
    current["effect_version"] = 2
    assert validate_target_effect_transition(previous, current).valid

    terminal = _record("applied")
    invalid = copy.deepcopy(terminal)
    invalid["state"] = "prepared"
    report = validate_target_effect_transition(terminal, invalid)
    assert "terminal_transition_forbidden" in {finding.code for finding in report.findings}

    invalid = copy.deepcopy(previous)
    invalid["state"] = "applied"
    report = validate_target_effect_transition(previous, invalid)
    assert "invalid_target_effect_transition" in {finding.code for finding in report.findings}

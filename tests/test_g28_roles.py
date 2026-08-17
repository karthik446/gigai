from __future__ import annotations

import pytest

from gigai.adapters.port import InvocationRequest
from gigai.canonical import canonical_json_bytes
from gigai.roles import (
    RoleError,
    RoleReference,
    register_extension,
    registered_roles,
    require_registered,
    resolve_role,
)
from gigai.validators import validate_serialized_contract
from gigai.validators import validate_review_bundle
from tests.test_g15_review_substrate import _bundle


def test_registry_is_namespaced_and_structured() -> None:
    reference = require_registered(
        {"namespace": "model_invocation", "id": "proposal-questioner", "version": 1},
        namespace="model_invocation",
    )
    assert reference == RoleReference("model_invocation", "proposal-questioner")
    assert any(item.id == "primary-source" for item in registered_roles("reference"))
    assert resolve_role("primary-source", namespace="model_invocation").status == "legacy_unresolved"


def test_legacy_roles_are_classified_without_cross_namespace_guessing() -> None:
    assert resolve_role("diagnostic", namespace="model_invocation").status == "legacy_unresolved"
    assert resolve_role("doctor", namespace="model_invocation").reference is None
    assert resolve_role("user", namespace="protocol").status == "registered"
    assert resolve_role("user", namespace="model_invocation").status == "legacy_unresolved"
    assert require_registered("local-capability", namespace="executor").id == "local-capability"
    assert require_registered("input", namespace="occurrence").id == "input"


def test_unknown_structured_roles_and_namespace_mismatch_fail_closed() -> None:
    with pytest.raises(RoleError):
        require_registered(
            {"namespace": "model_invocation", "id": "network-admin", "version": 1},
            namespace="model_invocation",
        )
    with pytest.raises(RoleError):
        require_registered(
            {"namespace": "reference", "id": "primary", "version": 1},
            namespace="model_invocation",
        )


def test_invocation_request_exposes_registered_reference_but_replays_legacy_string() -> None:
    request = InvocationRequest(
        target_name="offline-default",
        endpoint_name="offline",
        model="fixture-v1",
        role="proposal-questioner",
        prompt="hello",
        target_capabilities=frozenset({"text"}),
    )
    assert request.role_reference == RoleReference("model_invocation", "proposal-questioner")
    legacy = InvocationRequest(
        target_name="offline-default",
        endpoint_name="offline",
        model="fixture-v1",
        role="diagnostic",
        prompt="hello",
        target_capabilities=frozenset({"text"}),
    )
    assert legacy.role_reference is None


def test_role_reference_schema_is_closed_and_versioned() -> None:
    valid = {
        "schema_version": "1.0",
        "namespace": "model_invocation",
        "id": "proposal-questioner",
        "version": 1,
    }
    assert validate_serialized_contract(
        "role-reference.schema.json", canonical_json_bytes(valid)
    ).valid

    for invalid in (
        {**valid, "namespace": "unknown"},
        {**valid, "version": 0},
        {**valid, "extra": "not allowed"},
    ):
        assert not validate_serialized_contract(
            "role-reference.schema.json", canonical_json_bytes(invalid)
        ).valid


def test_owner_declared_role_extensions_are_versioned_and_not_capabilities() -> None:
    extension = register_extension(
        RoleReference("executor", "resume-reviewer"),
        owner="g28-test",
        purpose="Review a resume fixture.",
        allowed_callers=("g28-test",),
        required_evidence=("review-report",),
    )
    assert extension.reference.id == "resume-reviewer"
    assert resolve_role(
        {"namespace": "executor", "id": "resume-reviewer", "version": 1},
        namespace="executor",
    ).status == "registered"
    with pytest.raises(RoleError):
        register_extension(
            RoleReference("executor", "resume-reviewer"),
            owner="other",
            purpose="Different meaning.",
            allowed_callers=("other",),
            required_evidence=("other-report",),
        )


def test_review_bundle_roles_use_the_reference_namespace() -> None:
    bundle, _payloads = _bundle()
    assert validate_review_bundle(bundle).valid
    invalid = dict(bundle)
    invalid["references"] = [dict(bundle["references"][0], role="gig-builder")]  # type: ignore[index]
    report = validate_review_bundle(invalid)
    assert "reference_role_unregistered" in {finding.code for finding in report.findings}

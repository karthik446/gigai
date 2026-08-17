from __future__ import annotations

import pytest

from gigai.adapters.port import InvocationRequest
from gigai.roles import RoleError, RoleReference, registered_roles, require_registered, resolve_role


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
